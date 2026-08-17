"""使用真实 PostgreSQL、MinIO 与本地密钥执行有界 L3 迁移和恢复验证。"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from minio import Minio
from sqlalchemy import Engine, MetaData, Table, create_engine, delete, insert, select
from sqlalchemy.engine import make_url

from ai_platform_api.modules.isolation.domain.l3 import (
    L3CheckpointEvidence,
    L3CheckpointStatus,
    L3CheckpointType,
    L3ExecutionEvidence,
    L3MigrationCommand,
    L3RecoveryEvidence,
)


@dataclass(frozen=True)
class L3DatabaseTableSpec:
    """声明一张可按工作空间复制的事实表及其派生/删除语义。"""

    table: str
    derived: bool = False
    tombstone_object_column: str | None = None

    def validate(self) -> None:
        for value in (self.table, self.tombstone_object_column):
            if value is not None and (not value or not value.replace("_", "a").isalnum()):
                raise ValueError("L3 迁移表名和列名必须是安全标识符")


@dataclass(frozen=True)
class L3MigrationInfrastructureSettings:
    """保存一次执行所需连接信息；这些值只驻留进程内且绝不进入迁移事实。"""

    source_database_url: str
    target_database_url: str
    recovery_database_url: str
    source_schema: str
    target_schema: str
    recovery_schema: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    source_bucket: str
    target_bucket: str
    recovery_bucket: str
    source_key_path: Path
    target_key_path: Path
    tables: tuple[L3DatabaseTableSpec, ...]
    max_rows: int = 100_000
    max_object_bytes: int = 256 * 1024 * 1024

    def validate(self) -> None:
        database_identities = {
            _database_resource_identity(self.source_database_url),
            _database_resource_identity(self.target_database_url),
            _database_resource_identity(self.recovery_database_url),
        }
        buckets = {self.source_bucket, self.target_bucket, self.recovery_bucket}
        if len(database_identities) != 3 or len(buckets) != 3:
            raise ValueError("L3 源、目标和恢复数据库/Bucket 必须彼此独立")
        if not self.tables or len({item.table for item in self.tables}) != len(self.tables):
            raise ValueError("L3 迁移表清单不能为空或重复")
        if self.max_rows < 1 or self.max_object_bytes < 1:
            raise ValueError("L3 迁移容量上限必须为正数")
        parsed = urlsplit(self.minio_endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("L3 MinIO endpoint 无效")
        for spec in self.tables:
            spec.validate()


class L3DerivedIndexRebuilder(Protocol):
    """在目标数据库基于事实数据重建派生索引，禁止直接复制旧索引。"""

    def rebuild(self, engine: Engine, schema: str, workspace_id: UUID) -> None: ...


@dataclass(frozen=True)
class _DatabaseSnapshot:
    rows: dict[str, tuple[dict[str, object], ...]]

    @property
    def item_count(self) -> int:
        return sum(len(rows) for rows in self.rows.values())

    @property
    def digest(self) -> str:
        return _digest(self.rows)


@dataclass(frozen=True)
class _ObjectSnapshot:
    objects: dict[str, bytes]

    @property
    def item_count(self) -> int:
        return len(self.objects)

    @property
    def digest(self) -> str:
        return _digest(
            {key: hashlib.sha256(content).hexdigest() for key, content in self.objects.items()}
        )


class PostgresMinioL3MigrationExecutor:
    """按工作空间复制真实资源，并以第三份恢复副本证明备份可用。"""

    def __init__(
        self,
        settings: L3MigrationInfrastructureSettings,
        index_rebuilder: L3DerivedIndexRebuilder,
    ) -> None:
        settings.validate()
        self._settings = settings
        self._index_rebuilder = index_rebuilder
        parsed = urlsplit(settings.minio_endpoint)
        self._minio = Minio(
            parsed.netloc,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=parsed.scheme == "https",
        )

    def execute(self, command: L3MigrationCommand) -> L3ExecutionEvidence:
        """执行可重放的复制、重建和恢复；切换前源端始终拥有写入权。"""

        if not command.source_is_authoritative or command.target_writes_enabled:
            raise ValueError("L3 迁移执行期间必须保持源端单一写入")
        source_engine = create_engine(self._settings.source_database_url)
        target_engine = create_engine(self._settings.target_database_url)
        recovery_engine = create_engine(self._settings.recovery_database_url)
        try:
            # 1. 读取一致的源事实与对象快照，目标只接收当前工作空间数据。
            source_primary = self._read_database(source_engine, command.workspace_id, derived=False)
            source_derived = self._read_database(source_engine, command.workspace_id, derived=True)
            source_objects = self._read_objects(
                self._settings.source_bucket,
                command.workspace_id,
            )
            source_snapshot = _digest(
                {
                    "primary": source_primary.digest,
                    "derived": source_derived.digest,
                    "objects": source_objects.digest,
                }
            )
            self._assert_limits(source_primary, source_derived, source_objects)

            # 2. 复制事实和对象后重建派生索引，不把旧派生索引当作权威事实复制。
            self._replace_database(target_engine, command.workspace_id, source_primary)
            self._replace_objects(
                self._settings.target_bucket,
                command.workspace_id,
                source_objects,
            )
            self._index_rebuilder.rebuild(
                target_engine,
                self._settings.target_schema,
                command.workspace_id,
            )
            target_primary = self._read_database(target_engine, command.workspace_id, derived=False)
            target_derived = self._read_database(target_engine, command.workspace_id, derived=True)
            target_objects = self._read_objects(
                self._settings.target_bucket,
                command.workspace_id,
            )

            # 3. 把目标全量快照恢复到第三个干净资源，避免用原目标自读冒充恢复演练。
            self._replace_database(
                recovery_engine,
                command.workspace_id,
                _DatabaseSnapshot({**target_primary.rows, **target_derived.rows}),
                include_derived=True,
            )
            self._replace_objects(
                self._settings.recovery_bucket,
                command.workspace_id,
                target_objects,
            )
            recovered = self._read_all_recovery(recovery_engine, command.workspace_id)
            recovered_objects = self._read_objects(
                self._settings.recovery_bucket,
                command.workspace_id,
            )
            checkpoints = self._checkpoints(
                command.workspace_id,
                source_snapshot,
                source_primary,
                source_derived,
                source_objects,
                target_primary,
                target_derived,
                target_objects,
                recovered,
                recovered_objects,
            )
            return L3ExecutionEvidence(
                workspace_id=command.workspace_id,
                migration_plan_id=command.migration_plan_id,
                database_identity_digest=_database_identity(
                    self._settings.target_database_url,
                    self._settings.target_schema,
                ),
                object_storage_identity_digest=_digest(
                    {
                        "endpoint": self._settings.minio_endpoint,
                        "bucket": self._settings.target_bucket,
                    }
                ),
                encryption_key_fingerprint=_key_fingerprint(self._settings.target_key_path),
                source_key_fingerprint=_key_fingerprint(self._settings.source_key_path),
                source_is_authoritative=True,
                target_writes_enabled=False,
                checkpoints=checkpoints,
            )
        finally:
            source_engine.dispose()
            target_engine.dispose()
            recovery_engine.dispose()

    def rollback(self, command: L3MigrationCommand) -> L3RecoveryEvidence:
        """幂等清理目标与恢复资源，不触碰仍权威的源数据库、Bucket 或密钥。"""

        target_engine = create_engine(self._settings.target_database_url)
        recovery_engine = create_engine(self._settings.recovery_database_url)
        try:
            self._delete_database(target_engine, command.workspace_id, include_derived=True)
            self._delete_database(recovery_engine, command.workspace_id, include_derived=True)
            self._delete_objects(self._settings.target_bucket, command.workspace_id)
            self._delete_objects(self._settings.recovery_bucket, command.workspace_id)
            target = self._read_all_recovery(target_engine, command.workspace_id)
            recovery = self._read_all_recovery(recovery_engine, command.workspace_id)
            target_objects = self._read_objects(
                self._settings.target_bucket,
                command.workspace_id,
            )
            recovery_objects = self._read_objects(
                self._settings.recovery_bucket,
                command.workspace_id,
            )
            cleanup = {
                "target_rows": target.item_count,
                "recovery_rows": recovery.item_count,
                "target_objects": target_objects.item_count,
                "recovery_objects": recovery_objects.item_count,
            }
            passed = all(value == 0 for value in cleanup.values())
            cleanup_digest = _digest(cleanup)
            return L3RecoveryEvidence(
                workspace_id=command.workspace_id,
                migration_plan_id=command.migration_plan_id,
                status="passed" if passed else "failed",
                source_is_authoritative=True,
                target_writes_enabled=False,
                cleanup_digest=cleanup_digest,
                evidence_digest=_digest({"cleanup": cleanup_digest, "passed": passed}),
            )
        finally:
            target_engine.dispose()
            recovery_engine.dispose()

    def _read_database(
        self,
        engine: Engine,
        workspace_id: UUID,
        *,
        derived: bool,
    ) -> _DatabaseSnapshot:
        schema = (
            self._settings.source_schema
            if str(engine.url) == str(make_url(self._settings.source_database_url))
            else self._settings.target_schema
        )
        specs = tuple(item for item in self._settings.tables if item.derived is derived)
        return _read_rows(engine, schema, specs, workspace_id)

    def _read_all_recovery(self, engine: Engine, workspace_id: UUID) -> _DatabaseSnapshot:
        schema = (
            self._settings.recovery_schema
            if str(engine.url) == str(make_url(self._settings.recovery_database_url))
            else self._settings.target_schema
        )
        return _read_rows(engine, schema, self._settings.tables, workspace_id)

    def _replace_database(
        self,
        engine: Engine,
        workspace_id: UUID,
        snapshot: _DatabaseSnapshot,
        *,
        include_derived: bool = False,
    ) -> None:
        schema = (
            self._settings.recovery_schema
            if str(engine.url) == str(make_url(self._settings.recovery_database_url))
            else self._settings.target_schema
        )
        specs = tuple(item for item in self._settings.tables if include_derived or not item.derived)
        _replace_rows(engine, schema, specs, workspace_id, snapshot.rows)

    def _delete_database(
        self,
        engine: Engine,
        workspace_id: UUID,
        *,
        include_derived: bool,
    ) -> None:
        self._replace_database(
            engine,
            workspace_id,
            _DatabaseSnapshot({}),
            include_derived=include_derived,
        )

    def _read_objects(self, bucket: str, workspace_id: UUID) -> _ObjectSnapshot:
        prefix = f"workspaces/{workspace_id}/"
        if not self._minio.bucket_exists(bucket):
            return _ObjectSnapshot({})
        objects: dict[str, bytes] = {}
        for item in self._minio.list_objects(bucket, prefix=prefix, recursive=True):
            if not item.object_name.startswith(prefix):
                raise ValueError("MinIO 返回了工作空间前缀外对象")
            response = self._minio.get_object(bucket, item.object_name)
            try:
                objects[item.object_name] = response.read()
            finally:
                response.close()
                response.release_conn()
        return _ObjectSnapshot(dict(sorted(objects.items())))

    def _replace_objects(
        self,
        bucket: str,
        workspace_id: UUID,
        snapshot: _ObjectSnapshot,
    ) -> None:
        self._delete_objects(bucket, workspace_id)
        if not self._minio.bucket_exists(bucket):
            self._minio.make_bucket(bucket)
        for key, content in snapshot.objects.items():
            self._minio.put_object(bucket, key, io.BytesIO(content), len(content))

    def _delete_objects(self, bucket: str, workspace_id: UUID) -> None:
        prefix = f"workspaces/{workspace_id}/"
        if not self._minio.bucket_exists(bucket):
            return
        names = tuple(
            item.object_name
            for item in self._minio.list_objects(bucket, prefix=prefix, recursive=True)
        )
        for name in names:
            if not name.startswith(prefix):
                raise ValueError("MinIO 返回了工作空间前缀外对象")
            self._minio.remove_object(bucket, name)

    def _assert_limits(
        self,
        primary: _DatabaseSnapshot,
        derived: _DatabaseSnapshot,
        objects: _ObjectSnapshot,
    ) -> None:
        if primary.item_count + derived.item_count > self._settings.max_rows:
            raise ValueError("L3 迁移快照超过行数上限")
        if sum(len(content) for content in objects.objects.values()) > (
            self._settings.max_object_bytes
        ):
            raise ValueError("L3 迁移快照超过对象字节上限")

    def _checkpoints(
        self,
        workspace_id: UUID,
        source_snapshot: str,
        source_primary: _DatabaseSnapshot,
        source_derived: _DatabaseSnapshot,
        source_objects: _ObjectSnapshot,
        target_primary: _DatabaseSnapshot,
        target_derived: _DatabaseSnapshot,
        target_objects: _ObjectSnapshot,
        recovered: _DatabaseSnapshot,
        recovered_objects: _ObjectSnapshot,
    ) -> tuple[L3CheckpointEvidence, ...]:
        target_snapshot = _digest(
            {
                "primary": target_primary.digest,
                "derived": target_derived.digest,
                "objects": target_objects.digest,
            }
        )
        target_all = _digest(
            {
                "rows": {**target_primary.rows, **target_derived.rows},
                "objects": target_objects.digest,
            }
        )
        recovered_all = _digest({"rows": recovered.rows, "objects": recovered_objects.digest})
        deletion_source, deletion_target, deletion_count = self._deletion_digests(
            workspace_id,
            source_primary,
            source_derived,
            source_objects,
            target_objects,
        )
        pairs: tuple[tuple[L3CheckpointType, str, str, int], ...] = (
            ("source_snapshot", source_snapshot, target_snapshot, source_primary.item_count),
            (
                "database_copy",
                source_primary.digest,
                target_primary.digest,
                source_primary.item_count,
            ),
            (
                "object_copy",
                source_objects.digest,
                target_objects.digest,
                source_objects.item_count,
            ),
            (
                "derived_index_rebuild",
                source_derived.digest,
                target_derived.digest,
                source_derived.item_count,
            ),
            ("backup_restore", target_all, recovered_all, recovered.item_count),
            ("deletion_propagation", deletion_source, deletion_target, deletion_count),
        )
        return tuple(_checkpoint(*pair) for pair in pairs)

    def _deletion_digests(
        self,
        workspace_id: UUID,
        primary: _DatabaseSnapshot,
        derived: _DatabaseSnapshot,
        source_objects: _ObjectSnapshot,
        target_objects: _ObjectSnapshot,
    ) -> tuple[str, str, int]:
        prefix = f"workspaces/{workspace_id}/"
        keys: list[str] = []
        rows = {**primary.rows, **derived.rows}
        for spec in self._settings.tables:
            if spec.tombstone_object_column is None:
                continue
            for row in rows.get(spec.table, ()):
                value = row.get(spec.tombstone_object_column)
                if not isinstance(value, str) or not value.startswith(prefix):
                    raise ValueError("删除墓碑必须引用本工作空间对象键")
                keys.append(value)
        source_state = {key: key not in source_objects.objects for key in sorted(keys)}
        target_state = {key: key not in target_objects.objects for key in sorted(keys)}
        return _digest(source_state), _digest(target_state), len(keys)


def _read_rows(
    engine: Engine,
    schema: str,
    specs: tuple[L3DatabaseTableSpec, ...],
    workspace_id: UUID,
) -> _DatabaseSnapshot:
    rows: dict[str, tuple[dict[str, object], ...]] = {}
    with engine.connect() as connection:
        metadata = MetaData()
        for spec in specs:
            table = Table(spec.table, metadata, schema=schema, autoload_with=connection)
            if "workspace_id" not in table.c:
                raise ValueError(f"L3 迁移表缺少 workspace_id: {spec.table}")
            values = tuple(
                dict(row)
                for row in connection.execute(
                    select(table).where(table.c.workspace_id == workspace_id)
                ).mappings()
            )
            rows[spec.table] = tuple(sorted(values, key=_canonical_row))
    return _DatabaseSnapshot(dict(sorted(rows.items())))


def _replace_rows(
    engine: Engine,
    schema: str,
    specs: tuple[L3DatabaseTableSpec, ...],
    workspace_id: UUID,
    rows: dict[str, tuple[dict[str, object], ...]],
) -> None:
    with engine.begin() as connection:
        metadata = MetaData()
        tables = {
            spec.table: Table(spec.table, metadata, schema=schema, autoload_with=connection)
            for spec in specs
        }
        # 先逆序清理再正序写入，允许调用方用表清单表达外键依赖顺序。
        for spec in reversed(specs):
            table = tables[spec.table]
            connection.execute(delete(table).where(table.c.workspace_id == workspace_id))
        for spec in specs:
            values = rows.get(spec.table, ())
            if values:
                connection.execute(insert(tables[spec.table]), list(values))


def _checkpoint(
    checkpoint_type: L3CheckpointType,
    source_digest: str,
    target_digest: str,
    item_count: int,
) -> L3CheckpointEvidence:
    status: L3CheckpointStatus = "passed" if source_digest == target_digest else "failed"
    evidence_digest = _digest(
        {
            "checkpoint_type": checkpoint_type,
            "source_digest": source_digest,
            "target_digest": target_digest,
            "item_count": item_count,
            "status": status,
        }
    )
    return L3CheckpointEvidence(
        checkpoint_type=checkpoint_type,
        status=status,
        source_digest=source_digest,
        target_digest=target_digest,
        evidence_digest=evidence_digest,
        item_count=item_count,
    )


def _database_identity(database_url: str, schema: str) -> str:
    sanitized = make_url(database_url).set(password=None).render_as_string(hide_password=True)
    return _digest({"database_url": sanitized, "schema": schema})


def _database_resource_identity(
    database_url: str,
) -> tuple[str, str | None, int | None, str | None]:
    url = make_url(database_url)
    return url.drivername, url.host, url.port, url.database


def _key_fingerprint(path: Path) -> str:
    payload = path.read_bytes()
    if len(payload) < 32:
        raise ValueError("L3 加密密钥至少需要 32 字节")
    return hashlib.sha256(payload).hexdigest()


def _canonical_row(row: dict[str, object]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=_json_value)


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_value,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _json_value(value: object) -> str:
    if isinstance(value, (UUID, datetime, date, Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    raise TypeError(f"无法序列化 L3 迁移值: {type(value).__name__}")
