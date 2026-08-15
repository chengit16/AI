"""校验 PostgreSQL 中全部对象键都能在当前 S3-compatible Bucket 读取。"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from minio import Minio
from minio.error import S3Error
from sqlalchemy import Connection, create_engine, text


@dataclass(frozen=True)
class ObjectReferenceReport:
    """只暴露引用数量，避免在普通运维输出中打印工作空间对象键。"""

    referenced_count: int
    missing_count: int


def collect_object_keys(connection: Connection) -> tuple[str, ...]:
    """合并上传原件、解析产物和索引产物引用，并确定性去重排序。"""

    rows = connection.execute(
        text(
            """
            SELECT original_object_key AS object_key
              FROM document_sources
             WHERE original_object_key IS NOT NULL
               AND content_hash IS NOT NULL
            UNION
            SELECT source_object_key AS object_key
              FROM ingestion_jobs
             WHERE source_object_key IS NOT NULL
            UNION
            SELECT artifact_object_key AS object_key
              FROM ingestion_jobs
             WHERE artifact_object_key IS NOT NULL
            UNION
            SELECT artifact_object_key AS object_key
              FROM index_versions
             WHERE artifact_object_key IS NOT NULL
             ORDER BY object_key
            """
        )
    ).scalars()
    return tuple(value for value in rows if isinstance(value, str))


def verify_object_references(
    connection: Connection,
    object_exists: Callable[[str], bool],
) -> ObjectReferenceReport:
    """检查全部唯一对象引用；任一缺失都会反映在报告中而不打印对象键。"""

    object_keys = collect_object_keys(connection)
    missing_count = sum(not object_exists(object_key) for object_key in object_keys)
    return ObjectReferenceReport(len(object_keys), missing_count)


def _minio_client(endpoint: str, access_key: str, secret_key: str) -> Minio:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"}:
        raise ValueError("对象存储 endpoint 必须是无路径的 HTTP(S) 地址")
    return Minio(
        parsed.netloc,
        access_key=access_key,
        secret_key=secret_key,
        secure=parsed.scheme == "https",
    )


def check_local_object_references(
    *,
    database_url: str,
    endpoint: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    schema: str = "public",
) -> ObjectReferenceReport:
    """通过短数据库连接和对象 HEAD 请求验证本地实例引用一致性。"""

    if re.fullmatch(r"[a-z_][a-z0-9_]*", schema) is None:
        raise ValueError("数据库 Schema 名称不安全")
    client = _minio_client(endpoint, access_key, secret_key)

    def object_exists(object_key: str) -> bool:
        try:
            client.stat_object(bucket, object_key)
            return True
        except S3Error as error:
            if error.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                return False
            raise

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            return verify_object_references(connection, object_exists)
    finally:
        engine.dispose()


def _required_environment(name: str, fallback: str | None = None) -> str:
    value = os.environ.get(name, fallback)
    if not value:
        raise ValueError(f"缺少必需环境变量: {name}")
    return value


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    report = check_local_object_references(
        database_url=_required_environment("AI_PLATFORM_DATABASE_URL"),
        endpoint=_required_environment("AI_PLATFORM_MINIO_ENDPOINT", "http://127.0.0.1:9000"),
        access_key=_required_environment("AI_PLATFORM_MINIO_ACCESS_KEY"),
        secret_key=_required_environment("AI_PLATFORM_MINIO_SECRET_KEY"),
        bucket=_required_environment("AI_PLATFORM_MINIO_BUCKET"),
        schema=_required_environment("AI_PLATFORM_SCHEMA", "public"),
    )
    print(f"对象引用检查完成: referenced={report.referenced_count}, missing={report.missing_count}")
    return 0 if report.missing_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_main())
