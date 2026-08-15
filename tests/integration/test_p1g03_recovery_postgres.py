"""验证 P1G-03 主密钥轮换和 PostgreSQL/MinIO 对象引用恢复边界。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.security import (
    EncryptedSecret,
    EnvelopeSecretCipher,
    MasterKeyFile,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    document_sources,
    model_provider_configurations,
    model_provider_credentials,
)
from alembic import command
from alembic.config import Config
from minio import Minio
from sqlalchemy import Engine, create_engine, func, insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from scripts.check_object_references import check_local_object_references
from scripts.rotate_master_key import rewrap_provider_credentials
from tests.support.p1g01_loader import LoadedP1G01Dataset, load_p1g01_dataset

ROOT = Path(__file__).parents[2]
FIXTURE_PATH = ROOT / "tests/fixtures/e2e/p1g01-v1.json"
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
DEFAULT_MINIO_ENDPOINT = "http://127.0.0.1:9000"
DEFAULT_MINIO_ACCESS_KEY = "ai-platform-local"
DEFAULT_MINIO_SECRET_KEY = "local-development-only"


@dataclass(frozen=True)
class RecoveryHarness:
    """持有隔离 Schema、固定数据摘要和本机基础设施配置。"""

    engine: Engine
    sessions: sessionmaker[Session]
    database_url: str
    schema: str
    dataset: dict[str, Any]
    summary: LoadedP1G01Dataset


@pytest.fixture(scope="module")
def recovery_database() -> Iterator[RecoveryHarness]:
    """从空 Schema 迁移并装入 P1G-01，模块结束后删除全部合成事实。"""

    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1g03_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "infra/migrations"))
    config.set_main_option(
        "prepend_sys_path",
        f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
    )
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("ai_platform_schema", schema)
    command.upgrade(config, "head")

    engine = create_platform_engine(database_url, schema)
    sessions = create_session_factory(engine)
    document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError("P1G-01 Fixture 顶层必须是对象")
    dataset = cast(dict[str, Any], document)
    with sessions.begin() as session:
        summary = load_p1g01_dataset(
            session,
            dataset,
            local_password="synthetic-password-123",
        )
    try:
        yield RecoveryHarness(engine, sessions, database_url, schema, dataset, summary)
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _write_key(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def _migration_config(database_url: str, schema: str = "public") -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "infra/migrations"))
    config.set_main_option(
        "prepend_sys_path",
        f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
    )
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("ai_platform_schema", schema)
    return config


def _database_url(database_url: str, database_name: str) -> str:
    return make_url(database_url).set(database=database_name).render_as_string(hide_password=False)


def _create_database(admin_url: str, database_name: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
    finally:
        engine.dispose()


def _drop_database(admin_url: str, database_name: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
    finally:
        engine.dispose()


def _compose_postgres(*arguments: str, stdin: Any = None, stdout: Any = None) -> None:
    command_line = [
        "docker",
        "compose",
        "--env-file",
        str(ROOT / ".env"),
        "-f",
        str(ROOT / "infra/compose/compose.yaml"),
        "exec",
        "-T",
        "postgres",
        *arguments,
    ]
    subprocess.run(
        command_line,
        cwd=ROOT,
        stdin=stdin,
        stdout=stdout,
        check=True,
    )


def _insert_provider_credential(
    harness: RecoveryHarness,
    old_key_path: Path,
) -> tuple[UUID, UUID, bytes, bytes]:
    provider_id = uuid4()
    credential_id = uuid4()
    now = datetime.now(UTC)
    associated_data = (f"model-provider-credential:v1:{provider_id}:{credential_id}:1").encode()
    envelope = EnvelopeSecretCipher(MasterKeyFile(str(old_key_path), 1)).encrypt(
        "synthetic-provider-key-p1g03",
        associated_data=associated_data,
    )
    with harness.sessions.begin() as session:
        session.execute(
            insert(model_provider_configurations).values(
                provider_id=provider_id,
                provider_key=f"p1g03_{provider_id.hex}",
                display_name="P1G-03 合成供应商",
                adapter_kind="openai_compatible",
                base_url="https://api.synthetic.example/v1",
                probe_model_id="synthetic-model",
                location="external",
                declared_capabilities=["generation"],
                policy_review_status="pending",
                max_security_level="PUBLIC",
                retention_days=None,
                training_usage_allowed=False,
                policy_url=None,
                policy_version=None,
                policy_reviewed_by_account_id=None,
                policy_reviewed_at=None,
                probe_status="not_run",
                probed_capabilities=[],
                last_probe_error_code=None,
                last_probed_at=None,
                status="draft",
                created_by_account_id=harness.summary.owner_account_id,
                created_at=now,
                updated_by_account_id=harness.summary.owner_account_id,
                updated_at=now,
                version=1,
            )
        )
        session.execute(
            insert(model_provider_credentials).values(
                credential_id=credential_id,
                provider_id=provider_id,
                credential_version=1,
                master_key_version=envelope.key_version,
                encrypted_data_key=envelope.encrypted_data_key,
                data_key_nonce=envelope.data_key_nonce,
                ciphertext=envelope.ciphertext,
                data_nonce=envelope.data_nonce,
                last_four=envelope.last_four,
                status="active",
                created_by_account_id=harness.summary.owner_account_id,
                created_at=now,
                revoked_at=None,
            )
        )
    return provider_id, credential_id, associated_data, envelope.ciphertext


def test_provider_data_keys_rewrap_transactionally_without_reencrypting_plaintext(
    recovery_database: RecoveryHarness,
    tmp_path: Path,
) -> None:
    old_key_path = tmp_path / "master-old.key"
    new_key_path = tmp_path / "master-new.key"
    _write_key(old_key_path, b"o" * 32)
    _write_key(new_key_path, b"n" * 32)
    provider_id, credential_id, associated_data, original_ciphertext = _insert_provider_credential(
        recovery_database, old_key_path
    )

    with recovery_database.engine.begin() as connection:
        assert (
            rewrap_provider_credentials(
                connection,
                old_key=MasterKeyFile(str(old_key_path), 1),
                new_key=MasterKeyFile(str(new_key_path), 2),
            )
            == 1
        )

    with recovery_database.sessions() as session:
        row = session.execute(
            select(model_provider_credentials).where(
                model_provider_credentials.c.credential_id == credential_id,
                model_provider_credentials.c.provider_id == provider_id,
            )
        ).one()
    restored = EncryptedSecret(
        key_version=row.master_key_version,
        encrypted_data_key=bytes(row.encrypted_data_key),
        data_key_nonce=bytes(row.data_key_nonce),
        ciphertext=bytes(row.ciphertext),
        data_nonce=bytes(row.data_nonce),
        last_four=row.last_four,
    )

    assert restored.key_version == 2
    assert restored.ciphertext == original_ciphertext
    assert (
        EnvelopeSecretCipher(MasterKeyFile(str(new_key_path), 2)).decrypt(
            restored,
            associated_data=associated_data,
        )
        == "synthetic-provider-key-p1g03"
    )


def test_database_object_references_match_minio_and_detect_missing_objects(
    recovery_database: RecoveryHarness,
) -> None:
    endpoint = os.environ.get("AI_PLATFORM_TEST_MINIO_ENDPOINT", DEFAULT_MINIO_ENDPOINT)
    access_key = os.environ.get("AI_PLATFORM_TEST_MINIO_ACCESS_KEY", DEFAULT_MINIO_ACCESS_KEY)
    secret_key = os.environ.get("AI_PLATFORM_TEST_MINIO_SECRET_KEY", DEFAULT_MINIO_SECRET_KEY)
    client = Minio(
        endpoint.removeprefix("http://").removeprefix("https://"),
        access_key=access_key,
        secret_key=secret_key,
        secure=endpoint.startswith("https://"),
    )
    bucket = f"p1g03-{uuid4().hex}"
    object_key = (
        f"workspaces/{recovery_database.summary.enterprise_workspace_id}/"
        "documents/p1g03-synthetic.md"
    )
    payload = b"P1G-03 synthetic object"
    now = datetime.now(UTC)
    client.make_bucket(bucket)
    try:
        client.put_object(bucket, object_key, io.BytesIO(payload), len(payload))
        with recovery_database.sessions.begin() as session:
            source_ids = tuple(session.scalars(select(document_sources.c.source_id).limit(2)))
            assert len(source_ids) == 2
            session.execute(
                update(document_sources)
                .where(document_sources.c.source_id == source_ids[0])
                .values(
                    source_kind="upload",
                    original_object_key=object_key,
                    media_type="text/markdown",
                    size_bytes=len(payload),
                    content_hash=hashlib.sha256(payload).hexdigest(),
                    scan_status="clean",
                    scanner_version="p1g03-synthetic-scanner-v1",
                    scanned_at=now,
                )
            )
            # 只有路径但没有内容摘要和上传安全事实的历史占位不代表可恢复对象。
            session.execute(
                update(document_sources)
                .where(document_sources.c.source_id == source_ids[1])
                .values(
                    source_kind="upload",
                    original_object_key="synthetic-only/placeholder.pdf",
                )
            )

        report = check_local_object_references(
            database_url=recovery_database.database_url,
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
            schema=recovery_database.schema,
        )
        assert report.referenced_count == 1
        assert report.missing_count == 0

        client.remove_object(bucket, object_key)
        missing = check_local_object_references(
            database_url=recovery_database.database_url,
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
            schema=recovery_database.schema,
        )
        assert missing.referenced_count == 1
        assert missing.missing_count == 1
    finally:
        for item in client.list_objects(bucket, recursive=True):
            client.remove_object(bucket, item.object_name)
        client.remove_bucket(bucket)


def test_logical_database_dump_restores_to_clean_database_and_survives_migration_cycle(
    recovery_database: RecoveryHarness,
    tmp_path: Path,
) -> None:
    source_name = f"p1g03_source_{uuid4().hex}"
    target_name = f"p1g03_target_{uuid4().hex}"
    source_url = _database_url(recovery_database.database_url, source_name)
    target_url = _database_url(recovery_database.database_url, target_name)
    dump_path = tmp_path / "database.dump"

    # 1. 在两个全新数据库中建立源端和恢复端，源端只装入固定合成数据。
    _create_database(recovery_database.database_url, source_name)
    _create_database(recovery_database.database_url, target_name)
    try:
        # 该用例属于阶段 1 发布证据，必须固定在当时的发布 Revision，不能随开发 head 漂移。
        command.upgrade(_migration_config(source_url), "20260815_0035")
        source_engine = create_platform_engine(source_url, "public")
        source_sessions = create_session_factory(source_engine)
        try:
            with source_sessions.begin() as session:
                load_p1g01_dataset(
                    session,
                    recovery_database.dataset,
                    local_password="synthetic-password-123",
                )
        finally:
            source_engine.dispose()

        # 2. 使用与平台命令相同的容器内 PG16 工具生成 Custom Dump 并恢复到干净数据库。
        with dump_path.open("wb") as output:
            _compose_postgres(
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "-U",
                "ai_platform",
                "-d",
                source_name,
                stdout=output,
            )
        with dump_path.open("rb") as source:
            _compose_postgres(
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "-U",
                "ai_platform",
                "-d",
                target_name,
                stdin=source,
            )

        # 3. 恢复库降级一个 Revision 后重新升级，核心事实计数和最终 Revision 必须保持一致。
        target_config = _migration_config(target_url)
        command.downgrade(target_config, "-1")
        command.upgrade(target_config, "20260815_0035")
        target_engine = create_platform_engine(target_url, "public")
        try:
            with target_engine.connect() as connection:
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                    "20260815_0035"
                )
                assert connection.scalar(select(func.count()).select_from(document_sources)) == 388
                assert (
                    connection.scalar(text("SELECT count(*) FROM accounts"))
                    == recovery_database.summary.account_count
                )
        finally:
            target_engine.dispose()
    finally:
        _drop_database(recovery_database.database_url, target_name)
        _drop_database(recovery_database.database_url, source_name)
