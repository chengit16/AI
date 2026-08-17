"""验证 P5-08 在真实 PostgreSQL、MinIO 和合成密钥上的迁移、恢复与失败关闭。"""

from __future__ import annotations

import hashlib
import io
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.enterprise import EnterpriseWorkspaceService
from ai_platform_api.modules.identity.infrastructure.enterprise_sqlalchemy import (
    SqlAlchemyEnterpriseUnitOfWork,
)
from ai_platform_api.modules.isolation.application.errors import IsolationNotFoundError
from ai_platform_api.modules.isolation.application.l3 import L3IsolationMigrationService
from ai_platform_api.modules.isolation.application.service import (
    MANAGE_PERMISSION,
    IsolationGovernanceService,
)
from ai_platform_api.modules.isolation.domain.l3 import (
    L3_CHECKPOINT_TYPES,
    L3CheckpointEvidence,
    L3ExecutionEvidence,
    L3MigrationCommand,
    L3RecoveryEvidence,
)
from ai_platform_api.modules.isolation.infrastructure.l3_migration import (
    L3DatabaseTableSpec,
    L3MigrationInfrastructureSettings,
    PostgresMinioL3MigrationExecutor,
)
from ai_platform_api.modules.isolation.infrastructure.sqlalchemy import (
    SqlAlchemyIsolationUnitOfWork,
)
from ai_platform_api.persistence.tables import (
    l3_isolation_migration_checkpoints,
    l3_isolation_recovery_records,
    l3_isolation_resource_profiles,
    workspace_entitlements,
    workspace_isolation_migration_plans,
    workspace_isolation_route_versions,
    workspaces,
)
from minio import Minio
from sqlalchemy import (
    Column,
    Engine,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from tests.support.p502_quality import TRACE, RegisteredAccount, authorized_context
from tests.support.p503_quality import QualityEvaluationHarness
from tests.support.p507_isolation import StaticIsolationComplianceSource, isolation_service

pytest_plugins = ("tests.support.p503_quality_plugin",)

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
DEFAULT_MINIO_ENDPOINT = "http://127.0.0.1:9000"
DEFAULT_MINIO_ACCESS_KEY = "ai-platform-local"
DEFAULT_MINIO_SECRET_KEY = "local-development-only"
INDEX_NAMESPACE = UUID("58000000-0000-4000-8000-000000000580")


@dataclass(frozen=True)
class L3ResourceHarness:
    """保存三套独立数据库、Bucket、密钥和合成表。"""

    source_url: str
    target_url: str
    recovery_url: str
    minio: Minio
    source_bucket: str
    target_bucket: str
    recovery_bucket: str
    source_key: Path
    target_key: Path
    facts: Table
    tombstones: Table
    indexes: Table


class DeterministicIndexRebuilder:
    """只根据目标事实重建合成索引，结果必须与源索引摘要一致。"""

    def __init__(self, facts: Table, indexes: Table) -> None:
        self._facts = facts
        self._indexes = indexes

    def rebuild(self, engine: Engine, schema: str, workspace_id: UUID) -> None:
        del schema
        with engine.begin() as connection:
            contents = tuple(
                connection.scalars(
                    select(self._facts.c.content)
                    .where(self._facts.c.workspace_id == workspace_id)
                    .order_by(self._facts.c.content)
                )
            )
            digest = hashlib.sha256(
                json.dumps(contents, ensure_ascii=False, separators=(",", ":")).encode()
            ).hexdigest()
            connection.execute(
                delete(self._indexes).where(self._indexes.c.workspace_id == workspace_id)
            )
            connection.execute(
                insert(self._indexes).values(
                    index_id=uuid5(INDEX_NAMESPACE, str(workspace_id)),
                    workspace_id=workspace_id,
                    index_digest=digest,
                )
            )


class FailedCheckpointExecutor:
    """生成结构合法但对象摘要不一致的证据，用于验证恢复状态机。"""

    def __init__(self) -> None:
        self.rollback_calls = 0

    def execute(self, command: L3MigrationCommand) -> L3ExecutionEvidence:
        checkpoints = tuple(
            L3CheckpointEvidence(
                checkpoint_type=checkpoint_type,
                status="failed" if checkpoint_type == "object_copy" else "passed",
                source_digest="a" * 64,
                target_digest="b" * 64 if checkpoint_type == "object_copy" else "a" * 64,
                evidence_digest=hashlib.sha256(checkpoint_type.encode()).hexdigest(),
                item_count=1,
            )
            for checkpoint_type in L3_CHECKPOINT_TYPES
        )
        return L3ExecutionEvidence(
            workspace_id=command.workspace_id,
            migration_plan_id=command.migration_plan_id,
            database_identity_digest="c" * 64,
            object_storage_identity_digest="d" * 64,
            encryption_key_fingerprint="e" * 64,
            source_key_fingerprint="f" * 64,
            source_is_authoritative=True,
            target_writes_enabled=False,
            checkpoints=checkpoints,
        )

    def rollback(self, command: L3MigrationCommand) -> L3RecoveryEvidence:
        self.rollback_calls += 1
        cleanup_digest = hashlib.sha256(b"synthetic-l3-cleanup").hexdigest()
        return L3RecoveryEvidence(
            workspace_id=command.workspace_id,
            migration_plan_id=command.migration_plan_id,
            status="passed",
            source_is_authoritative=True,
            target_writes_enabled=False,
            cleanup_digest=cleanup_digest,
            evidence_digest=hashlib.sha256(cleanup_digest.encode()).hexdigest(),
        )


@pytest.fixture
def l3_resources(tmp_path: Path) -> Iterator[L3ResourceHarness]:
    """建立三个真实临时数据库和三个独立 Bucket，结束后完整清理合成资源。"""

    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    names = tuple(f"p508_{kind}_{uuid4().hex}" for kind in ("source", "target", "recovery"))
    urls = tuple(_database_url(database_url, name) for name in names)
    for name in names:
        _create_database(database_url, name)
    metadata = MetaData()
    facts = Table(
        "workspace_facts",
        metadata,
        Column("fact_id", PGUUID(as_uuid=True), primary_key=True),
        Column("workspace_id", PGUUID(as_uuid=True), nullable=False),
        Column("content", String(255), nullable=False),
    )
    tombstones = Table(
        "workspace_tombstones",
        metadata,
        Column("tombstone_id", PGUUID(as_uuid=True), primary_key=True),
        Column("workspace_id", PGUUID(as_uuid=True), nullable=False),
        Column("object_key", String(512), nullable=False),
    )
    indexes = Table(
        "workspace_indexes",
        metadata,
        Column("index_id", PGUUID(as_uuid=True), primary_key=True),
        Column("workspace_id", PGUUID(as_uuid=True), nullable=False),
        Column("index_digest", String(64), nullable=False),
    )
    for url in urls:
        engine = create_engine(url)
        metadata.create_all(engine)
        engine.dispose()
    endpoint = os.environ.get("AI_PLATFORM_TEST_MINIO_ENDPOINT", DEFAULT_MINIO_ENDPOINT)
    access_key = os.environ.get("AI_PLATFORM_TEST_MINIO_ACCESS_KEY", DEFAULT_MINIO_ACCESS_KEY)
    secret_key = os.environ.get("AI_PLATFORM_TEST_MINIO_SECRET_KEY", DEFAULT_MINIO_SECRET_KEY)
    parsed_endpoint = endpoint.removeprefix("http://").removeprefix("https://")
    minio = Minio(
        parsed_endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=endpoint.startswith("https://"),
    )
    buckets = tuple(f"p508-{kind}-{uuid4().hex}" for kind in ("source", "target", "recovery"))
    for bucket in buckets:
        minio.make_bucket(bucket)
    source_key = tmp_path / "source.key"
    target_key = tmp_path / "target.key"
    source_key.write_bytes(b"s" * 32)
    target_key.write_bytes(b"t" * 32)
    harness = L3ResourceHarness(
        urls[0],
        urls[1],
        urls[2],
        minio,
        buckets[0],
        buckets[1],
        buckets[2],
        source_key,
        target_key,
        facts,
        tombstones,
        indexes,
    )
    try:
        yield harness
    finally:
        for bucket in buckets:
            for item in minio.list_objects(bucket, recursive=True):
                minio.remove_object(bucket, item.object_name)
            minio.remove_bucket(bucket)
        for name in names:
            _drop_database(database_url, name)


def test_p508_real_l3_migration_rebuild_restore_and_atomic_route(
    quality_evaluation_database: QualityEvaluationHarness,
    l3_resources: L3ResourceHarness,
) -> None:
    owner = _register(quality_evaluation_database, "real-migration")
    enterprise = _enterprise(quality_evaluation_database, owner)
    outsider = uuid4()
    _prepare_source(l3_resources, enterprise.workspace_id, outsider)
    service, plan_id, context = _approved_l3_plan(quality_evaluation_database, enterprise)
    executor = _executor(l3_resources)
    l3_service = L3IsolationMigrationService(
        SqlAlchemyIsolationUnitOfWork(quality_evaluation_database.sessions),
        executor,
    )

    # 缺少资源档案与检查点时，数据库不能因调用方伪造路由而提前切换。
    with pytest.raises(DBAPIError), quality_evaluation_database.sessions.begin() as session:
        session.execute(
            insert(workspace_isolation_route_versions).values(
                route_id=uuid4(),
                workspace_id=enterprise.workspace_id,
                route_version=1,
                isolation_level="L3",
                database_route_key=f"database.l3.{enterprise.workspace_id.hex}",
                object_storage_route_key=f"objects.l3.{enterprise.workspace_id.hex}",
                encryption_key_route_key=f"key.l3.{enterprise.workspace_id.hex}",
                search_namespace=f"workspace.{enterprise.workspace_id.hex}",
                deployment_route_key="shared.runtime",
                migration_plan_id=plan_id,
                route_digest="a" * 64,
                activated_by_actor_id=owner.account_id,
                activated_at=service.transition_migration(context, plan_id, "approved").updated_at,
            )
        )

    verification = l3_service.execute_and_verify(context, plan_id)
    assert verification.migration_plan.status == "switch_ready"
    assert tuple(item.checkpoint_type for item in verification.checkpoints) == L3_CHECKPOINT_TYPES
    assert all(item.status == "passed" for item in verification.checkpoints)
    assert service.resolve_route(context).isolation_level == "L1"
    with pytest.raises(DBAPIError), quality_evaluation_database.sessions.begin() as session:
        session.execute(
            insert(workspace_isolation_route_versions).values(
                route_id=uuid4(),
                workspace_id=enterprise.workspace_id,
                route_version=1,
                isolation_level="L3",
                database_route_key=f"database.l3.{enterprise.workspace_id.hex}.wrong",
                object_storage_route_key=f"objects.l3.{enterprise.workspace_id.hex}",
                encryption_key_route_key=f"key.l3.{enterprise.workspace_id.hex}",
                search_namespace=f"workspace.{enterprise.workspace_id.hex}",
                deployment_route_key="shared.runtime",
                migration_plan_id=plan_id,
                route_digest="b" * 64,
                activated_by_actor_id=owner.account_id,
                activated_at=verification.migration_plan.updated_at,
            )
        )
    route = l3_service.activate_route(context, plan_id)
    assert route.isolation_level == "L3"
    assert service.resolve_route(context) == route

    target_engine = create_engine(l3_resources.target_url)
    recovery_engine = create_engine(l3_resources.recovery_url)
    try:
        with target_engine.connect() as connection:
            assert (
                connection.scalar(
                    select(func.count())
                    .select_from(l3_resources.facts)
                    .where(l3_resources.facts.c.workspace_id == enterprise.workspace_id)
                )
                == 2
            )
            assert (
                connection.scalar(
                    select(func.count())
                    .select_from(l3_resources.facts)
                    .where(l3_resources.facts.c.workspace_id == outsider)
                )
                == 0
            )
        with recovery_engine.connect() as connection:
            assert (
                connection.scalar(
                    select(func.count())
                    .select_from(l3_resources.indexes)
                    .where(l3_resources.indexes.c.workspace_id == enterprise.workspace_id)
                )
                == 1
            )
    finally:
        target_engine.dispose()
        recovery_engine.dispose()
    target_objects = tuple(
        item.object_name
        for item in l3_resources.minio.list_objects(
            l3_resources.target_bucket,
            recursive=True,
        )
    )
    assert target_objects == (f"workspaces/{enterprise.workspace_id}/documents/synthetic-p508.txt",)

    with quality_evaluation_database.sessions() as session:
        profile = (
            session.execute(
                select(l3_isolation_resource_profiles).where(
                    l3_isolation_resource_profiles.c.migration_plan_id == plan_id
                )
            )
            .mappings()
            .one()
        )
        assert profile["database_route_key"] == route.database_route_key
        assert "password" not in json.dumps(dict(profile), default=str).lower()
        assert (
            session.scalar(
                select(func.count())
                .select_from(l3_isolation_migration_checkpoints)
                .where(l3_isolation_migration_checkpoints.c.migration_plan_id == plan_id)
            )
            == 6
        )
    with pytest.raises(DBAPIError), quality_evaluation_database.sessions.begin() as session:
        session.execute(
            update(l3_isolation_resource_profiles)
            .where(l3_isolation_resource_profiles.c.migration_plan_id == plan_id)
            .values(database_identity_digest="f" * 64)
        )
    with pytest.raises(DBAPIError), quality_evaluation_database.sessions.begin() as session:
        session.execute(
            delete(l3_isolation_migration_checkpoints).where(
                l3_isolation_migration_checkpoints.c.migration_plan_id == plan_id
            )
        )


def test_p508_failed_checkpoint_requires_recovery_and_blocks_cross_workspace(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = _register(quality_evaluation_database, "failed-checkpoint")
    outsider = _register(quality_evaluation_database, "cross-workspace")
    enterprise = _enterprise(quality_evaluation_database, owner)
    governance, plan_id, context = _approved_l3_plan(quality_evaluation_database, enterprise)
    governance.transition_migration(context, plan_id, "approved")
    executor = FailedCheckpointExecutor()
    service = L3IsolationMigrationService(
        SqlAlchemyIsolationUnitOfWork(quality_evaluation_database.sessions),
        executor,
    )

    verification = service.execute_and_verify(context, plan_id)
    assert verification.migration_plan.status == "rollback_required"
    assert any(item.status == "failed" for item in verification.checkpoints)
    with pytest.raises(IsolationNotFoundError):
        service.activate_route(authorized_context(outsider, MANAGE_PERMISSION), plan_id)
    with pytest.raises(IsolationNotFoundError):
        service.recover(authorized_context(outsider, MANAGE_PERMISSION), plan_id)
    assert executor.rollback_calls == 0

    recovery = service.recover(context, plan_id)
    assert recovery.status == "passed"
    assert executor.rollback_calls == 1
    with quality_evaluation_database.sessions() as session:
        assert (
            session.scalar(
                select(workspace_isolation_migration_plans.c.status).where(
                    workspace_isolation_migration_plans.c.migration_plan_id == plan_id
                )
            )
            == "rolled_back"
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(l3_isolation_recovery_records)
                .where(l3_isolation_recovery_records.c.migration_plan_id == plan_id)
            )
            == 1
        )
    with pytest.raises(DBAPIError), quality_evaluation_database.sessions.begin() as session:
        session.execute(
            delete(l3_isolation_recovery_records).where(
                l3_isolation_recovery_records.c.migration_plan_id == plan_id
            )
        )


def _register(harness: QualityEvaluationHarness, identity: str) -> RegisteredAccount:
    result = harness.registration.register(
        login_name=f"synthetic.p508.{identity}.{uuid4().hex}@example.com",
        display_name=f"合成 L3 隔离用户 {identity}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return RegisteredAccount(result.account_id, result.personal_workspace_id)


def _enterprise(
    harness: QualityEvaluationHarness,
    owner: RegisteredAccount,
) -> RegisteredAccount:
    summary = EnterpriseWorkspaceService(SqlAlchemyEnterpriseUnitOfWork(harness.sessions)).create(
        RequestContext.trusted(
            actor_id=owner.account_id,
            user_id=owner.account_id,
            workspace_id=owner.workspace_id,
            trace=TRACE,
            authentication_method="browser_session",
        ),
        name=f"合成 P5-08 高合规企业 {uuid4().hex[:8]}",
    )
    with harness.sessions.begin() as session:
        session.execute(
            update(workspace_entitlements)
            .where(workspace_entitlements.c.workspace_id == summary.workspace_id)
            .values(plan_code="enterprise_isolated", version=2)
        )
        session.execute(
            update(workspaces)
            .where(workspaces.c.workspace_id == summary.workspace_id)
            .values(entitlement_version=2)
        )
    return RegisteredAccount(owner.account_id, summary.workspace_id)


def _approved_l3_plan(
    harness: QualityEvaluationHarness,
    enterprise: RegisteredAccount,
) -> tuple[IsolationGovernanceService, UUID, RequestContext]:
    compliance = StaticIsolationComplianceSource()
    compliance.statuses["L3"] = "approved"
    governance = isolation_service(harness.sessions, compliance)
    context = authorized_context(enterprise, MANAGE_PERMISSION)
    result = governance.request_upgrade(context, "L3")
    assert result.migration_plan is not None
    return governance, result.migration_plan.migration_plan_id, context


def _prepare_source(
    harness: L3ResourceHarness,
    workspace_id: UUID,
    outsider_id: UUID,
) -> None:
    source_engine = create_engine(harness.source_url)
    try:
        contents = ("synthetic high-compliance fact A", "synthetic high-compliance fact B")
        with source_engine.begin() as connection:
            connection.execute(
                insert(harness.facts),
                [
                    {"fact_id": uuid4(), "workspace_id": workspace_id, "content": content}
                    for content in contents
                ]
                + [
                    {
                        "fact_id": uuid4(),
                        "workspace_id": outsider_id,
                        "content": "synthetic outsider fact",
                    }
                ],
            )
            connection.execute(
                insert(harness.tombstones).values(
                    tombstone_id=uuid4(),
                    workspace_id=workspace_id,
                    object_key=f"workspaces/{workspace_id}/documents/deleted-p508.txt",
                )
            )
            digest = hashlib.sha256(
                json.dumps(contents, ensure_ascii=False, separators=(",", ":")).encode()
            ).hexdigest()
            connection.execute(
                insert(harness.indexes).values(
                    index_id=uuid5(INDEX_NAMESPACE, str(workspace_id)),
                    workspace_id=workspace_id,
                    index_digest=digest,
                )
            )
    finally:
        source_engine.dispose()
    object_key = f"workspaces/{workspace_id}/documents/synthetic-p508.txt"
    payload = b"P5-08 synthetic high-compliance object"
    harness.minio.put_object(
        harness.source_bucket,
        object_key,
        io.BytesIO(payload),
        len(payload),
    )
    outsider_key = f"workspaces/{outsider_id}/documents/outsider.txt"
    harness.minio.put_object(
        harness.source_bucket,
        outsider_key,
        io.BytesIO(b"synthetic outsider object"),
        len(b"synthetic outsider object"),
    )


def _executor(harness: L3ResourceHarness) -> PostgresMinioL3MigrationExecutor:
    endpoint = os.environ.get("AI_PLATFORM_TEST_MINIO_ENDPOINT", DEFAULT_MINIO_ENDPOINT)
    settings = L3MigrationInfrastructureSettings(
        source_database_url=harness.source_url,
        target_database_url=harness.target_url,
        recovery_database_url=harness.recovery_url,
        source_schema="public",
        target_schema="public",
        recovery_schema="public",
        minio_endpoint=endpoint,
        minio_access_key=os.environ.get(
            "AI_PLATFORM_TEST_MINIO_ACCESS_KEY",
            DEFAULT_MINIO_ACCESS_KEY,
        ),
        minio_secret_key=os.environ.get(
            "AI_PLATFORM_TEST_MINIO_SECRET_KEY",
            DEFAULT_MINIO_SECRET_KEY,
        ),
        source_bucket=harness.source_bucket,
        target_bucket=harness.target_bucket,
        recovery_bucket=harness.recovery_bucket,
        source_key_path=harness.source_key,
        target_key_path=harness.target_key,
        tables=(
            L3DatabaseTableSpec("workspace_facts"),
            L3DatabaseTableSpec(
                "workspace_tombstones",
                tombstone_object_column="object_key",
            ),
            L3DatabaseTableSpec("workspace_indexes", derived=True),
        ),
    )
    return PostgresMinioL3MigrationExecutor(
        settings,
        DeterministicIndexRebuilder(harness.facts, harness.indexes),
    )


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
