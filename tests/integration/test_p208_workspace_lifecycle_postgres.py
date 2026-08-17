"""验证 P2-08 在 PostgreSQL、MinIO 与 Valkey 上的工作空间生命周期闭环。"""

from __future__ import annotations

import io
import json
import os
import zipfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.domain.grants import OWNER_PERMISSION_CODES
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.modules.lifecycle.application.service import (
    EXPORT_PERMISSION,
    PURGE_PERMISSION,
    RETENTION_PERMISSION,
    WorkspaceLifecycleService,
)
from ai_platform_api.modules.lifecycle.infrastructure.cache import ValkeyWorkspaceCacheCleaner
from ai_platform_api.modules.lifecycle.infrastructure.sqlalchemy import (
    SqlAlchemyWorkspaceLifecycleStore,
)
from ai_platform_api.modules.lifecycle.infrastructure.storage import (
    MinioLifecycleObjectStorage,
)
from ai_platform_api.modules.quality.application.service import QualitySampleService
from ai_platform_api.modules.quality.domain.models import QualitySampleCapture
from ai_platform_api.modules.quality.infrastructure.sqlalchemy import (
    SqlAlchemyQualityUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    audit_records,
    lifecycle_deletion_certificates,
    lifecycle_retention_runs,
    open_api_keys,
    outbox_events,
    quality_dataset_members,
    quality_dataset_versions,
    quality_sample_versions,
    role_permission_grants,
    stream_events,
    workspace_memberships,
    workspace_resources,
    workspace_usage_records,
    workspaces,
)
from alembic import command
from alembic.config import Config
from minio import Minio
from redis import Redis
from sqlalchemy import Engine, create_engine, delete, func, insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
REGISTRY_PATH = ROOT / "contracts/lifecycle/workspace-table-registry.v1.json"
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
DEFAULT_VALKEY_URL = "redis://127.0.0.1:6379/11"
DEFAULT_MINIO_ENDPOINT = "127.0.0.1:9000"
MINIO_ACCESS_KEY = "ai-platform-local"
MINIO_SECRET_KEY = "local-development-only"
TRACE = TraceContext.continue_from("00-a123456789abcdef0123456789abcdef-a123456789abcdef-01")


@dataclass(frozen=True)
class RegisteredAccount:
    account_id: UUID
    workspace_id: UUID
    workspace_name: str


@dataclass(frozen=True)
class LifecycleHarness:
    engine: Engine
    sessions: sessionmaker[Session]
    registration: RegistrationService
    quality: QualitySampleService
    service: WorkspaceLifecycleService
    minio: Minio
    bucket: str
    valkey: Redis


@pytest.fixture(scope="module")
def lifecycle_database() -> Iterator[LifecycleHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    valkey_url = os.environ.get("AI_PLATFORM_TEST_VALKEY_URL", DEFAULT_VALKEY_URL)
    minio_endpoint = os.environ.get("AI_PLATFORM_TEST_MINIO_ENDPOINT", DEFAULT_MINIO_ENDPOINT)
    schema = f"p208_test_{uuid4().hex}"
    bucket = f"p208-lifecycle-{uuid4().hex}"
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
    registration = RegistrationService(
        SqlAlchemyIdentityReader(sessions),
        SqlAlchemyRegistrationUnitOfWork(sessions),
        Argon2idPasswordAdapter(),
    )
    minio = Minio(
        minio_endpoint,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False,
    )
    minio.make_bucket(bucket)
    valkey = Redis.from_url(valkey_url, decode_responses=True)
    valkey.flushdb()
    cache = ValkeyWorkspaceCacheCleaner(valkey_url)
    service = WorkspaceLifecycleService(
        SqlAlchemyWorkspaceLifecycleStore(sessions),
        MinioLifecycleObjectStorage(
            endpoint=f"http://{minio_endpoint}",
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            bucket=bucket,
        ),
        cache,
        REGISTRY_PATH,
    )
    try:
        yield LifecycleHarness(
            engine,
            sessions,
            registration,
            QualitySampleService(SqlAlchemyQualityUnitOfWork(sessions)),
            service,
            minio,
            bucket,
            valkey,
        )
    finally:
        cache.close()
        for item in minio.list_objects(bucket, recursive=True):
            minio.remove_object(bucket, item.object_name)
        minio.remove_bucket(bucket)
        valkey.flushdb()
        valkey.close()
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _register(harness: LifecycleHarness, suffix: str) -> RegisteredAccount:
    result = harness.registration.register(
        login_name=f"synthetic.p208.{suffix}@example.com",
        display_name=f"合成生命周期用户{suffix}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    with harness.sessions() as session:
        workspace_name = session.scalar(
            select(workspaces.c.name).where(
                workspaces.c.workspace_id == result.personal_workspace_id
            )
        )
    assert isinstance(workspace_name, str)
    return RegisteredAccount(result.account_id, result.personal_workspace_id, workspace_name)


def _context(account: RegisteredAccount, permission: str) -> RequestContext:
    return replace(
        RequestContext.trusted(
            actor_id=account.account_id,
            user_id=account.account_id,
            workspace_id=account.workspace_id,
            trace=TRACE,
            authentication_method="browser_session",
        ),
        authorized_permission_code=permission,
        authorized_policy_decision_id=uuid4(),
        authorized_policy_version=17,
        authorized_workspace=True,
    )


def _put_object(harness: LifecycleHarness, workspace_id: UUID, content: bytes) -> str:
    object_key = f"workspaces/{workspace_id}/uploads/synthetic-p208.md"
    harness.minio.put_object(
        harness.bucket,
        object_key,
        io.BytesIO(content),
        len(content),
        content_type="text/markdown",
    )
    return object_key


def _read_object(harness: LifecycleHarness, object_key: str) -> bytes:
    response = harness.minio.get_object(harness.bucket, object_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def _seed_export_and_purge_facts(
    harness: LifecycleHarness,
    target: RegisteredAccount,
    other: RegisteredAccount,
) -> tuple[UUID, UUID, str]:
    resource_id, other_resource_id, key_id = uuid4(), uuid4(), uuid4()
    with harness.sessions.begin() as session:
        session.execute(
            insert(workspace_resources),
            [
                {
                    "resource_id": resource_id,
                    "workspace_id": target.workspace_id,
                    "title": "合成 P2-08 目标资源",
                    "sensitive_value": "synthetic-target-sensitive",
                    "version": 1,
                },
                {
                    "resource_id": other_resource_id,
                    "workspace_id": other.workspace_id,
                    "title": "合成 P2-08 隔离资源",
                    "sensitive_value": "synthetic-other-sensitive",
                    "version": 1,
                },
            ],
        )
        session.execute(
            insert(open_api_keys).values(
                key_id=key_id,
                actor_id=uuid4(),
                workspace_id=target.workspace_id,
                created_by_account_id=target.account_id,
                name="合成待清理 Key",
                secret_digest="d" * 64,
                last_four="0208",
                scopes=["workspace.lifecycle.export"],
                created_at=datetime.now(UTC),
                expires_at=None,
                revoked_at=None,
            )
        )
        session.execute(
            insert(workspace_usage_records).values(
                usage_record_id=uuid4(),
                workspace_id=target.workspace_id,
                metric="questions_monthly",
                period_key="2026-08",
                idempotency_key=f"p208-usage-{uuid4().hex}",
                delta_value=1,
                resulting_value=1,
                occurred_at=datetime.now(UTC),
            )
        )
    object_key = _put_object(harness, target.workspace_id, b"synthetic-p208-export-object")
    for account, suffix in ((target, "target"), (other, "other")):
        harness.quality.capture(
            _context(account, "assistant.feedback.manage"),
            QualitySampleCapture(
                source_type="user_feedback",
                source_workspace_id=account.workspace_id,
                source_id=uuid4(),
                source_version=1,
                resource_id=uuid4(),
                signal_code="unhelpful",
                reason_codes=("incorrect",),
                input_text=f"synthetic-p208-quality-{suffix}-input",
                output_text=f"synthetic-p208-quality-{suffix}-output",
                feedback_text=None,
                correction_text=None,
                source_security_level="PUBLIC",
            ),
        )
    cache_key = f"effective-roles:v1:{target.workspace_id}:{uuid4()}:1"
    harness.valkey.set(cache_key, "synthetic-p208-cache", ex=300)
    return resource_id, other_resource_id, object_key


def test_export_and_purge_are_isolated_complete_and_idempotent(
    lifecycle_database: LifecycleHarness,
) -> None:
    target = _register(lifecycle_database, uuid4().hex)
    other = _register(lifecycle_database, uuid4().hex)
    resource_id, other_resource_id, object_key = _seed_export_and_purge_facts(
        lifecycle_database,
        target,
        other,
    )

    # 1. 新注册 Owner 必须立即拥有三项权限，导出包只含目标空间且排除 Key 摘要。
    with lifecycle_database.sessions() as session:
        grants = set(
            session.scalars(
                select(role_permission_grants.c.permission_code).where(
                    role_permission_grants.c.workspace_id == target.workspace_id,
                    role_permission_grants.c.permission_code.in_(
                        (EXPORT_PERMISSION, PURGE_PERMISSION, RETENTION_PERMISSION)
                    ),
                )
            )
        )
    assert grants == {EXPORT_PERMISSION, PURGE_PERMISSION, RETENTION_PERMISSION}
    assert set((EXPORT_PERMISSION, PURGE_PERMISSION, RETENTION_PERMISSION)).issubset(
        OWNER_PERMISSION_CODES
    )
    exported = lifecycle_database.service.export_workspace(
        _context(target, EXPORT_PERMISSION),
        workspace_id=target.workspace_id,
        idempotency_key="synthetic-export-p208",
    )
    assert exported.status == "completed" and exported.object_key is not None
    assert exported == lifecycle_database.service.export_workspace(
        _context(target, EXPORT_PERMISSION),
        workspace_id=target.workspace_id,
        idempotency_key="synthetic-export-p208",
    )
    with zipfile.ZipFile(
        io.BytesIO(_read_object(lifecycle_database, exported.object_key))
    ) as archive:
        resource_rows = [
            json.loads(line)
            for line in archive.read("tables/workspace_resources.jsonl").splitlines()
        ]
        key_rows = [
            json.loads(line) for line in archive.read("tables/open_api_keys.jsonl").splitlines()
        ]
        manifest = json.loads(archive.read("manifest.json"))
        quality_rows = [
            json.loads(line)
            for line in archive.read("tables/quality_sample_versions.jsonl").splitlines()
        ]
    assert {row["resource_id"] for row in resource_rows} == {str(resource_id)}
    assert str(other_resource_id) not in json.dumps(resource_rows)
    assert key_rows[0]["last_four"] == "0208"
    assert "secret_digest" not in key_rows[0]
    assert len(quality_rows) == 1
    assert quality_rows[0]["workspace_id"] == str(target.workspace_id)
    assert "synthetic-p208-quality-target-input" not in json.dumps(quality_rows)
    assert "synthetic-p208-quality-other-input" not in json.dumps(quality_rows)
    assert {item["object_key"] for item in manifest["objects"]} == {object_key}

    # 2. 普通事务仍不能删审计；正式清除后业务、对象、缓存为空而空间壳层和证明保留。
    with pytest.raises(DBAPIError), lifecycle_database.sessions.begin() as session:
        session.execute(
            delete(audit_records).where(audit_records.c.workspace_id == target.workspace_id)
        )
    purge, certificate = lifecycle_database.service.purge_workspace_business_data(
        _context(target, PURGE_PERMISSION),
        workspace_id=target.workspace_id,
        idempotency_key="synthetic-purge-p208",
        confirmed_workspace_name=target.workspace_name,
        reason_code="OWNER_REQUEST",
    )
    assert purge.status == "completed"
    assert (
        purge
        == lifecycle_database.service.purge_workspace_business_data(
            _context(target, PURGE_PERMISSION),
            workspace_id=target.workspace_id,
            idempotency_key="synthetic-purge-p208",
            confirmed_workspace_name=target.workspace_name,
            reason_code="OWNER_REQUEST",
        )[0]
    )
    with lifecycle_database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(workspace_resources)
                .where(workspace_resources.c.workspace_id == target.workspace_id)
            )
            == 0
        )
        for quality_table in (
            quality_sample_versions,
            quality_dataset_versions,
            quality_dataset_members,
        ):
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(quality_table)
                    .where(quality_table.c.workspace_id == target.workspace_id)
                )
                == 0
            )
        assert (
            session.scalar(
                select(func.count())
                .select_from(open_api_keys)
                .where(open_api_keys.c.workspace_id == target.workspace_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(workspaces)
                .where(workspaces.c.workspace_id == target.workspace_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(workspace_memberships)
                .where(workspace_memberships.c.workspace_id == target.workspace_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(workspace_usage_records)
                .where(workspace_usage_records.c.workspace_id == target.workspace_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(lifecycle_deletion_certificates)
                .where(
                    lifecycle_deletion_certificates.c.certificate_id == certificate.certificate_id
                )
            )
            == 1
        )
        target_events = tuple(
            session.scalars(
                select(outbox_events.c.event_type).where(
                    outbox_events.c.workspace_id == target.workspace_id
                )
            )
        )
        other_resource = session.scalar(
            select(workspace_resources.c.resource_id).where(
                workspace_resources.c.resource_id == other_resource_id
            )
        )
        other_quality_count = session.scalar(
            select(func.count())
            .select_from(quality_sample_versions)
            .where(quality_sample_versions.c.workspace_id == other.workspace_id)
        )
    assert target_events == ("workspace.lifecycle.purge_completed",)
    assert other_resource == other_resource_id
    assert other_quality_count == 1
    assert (
        tuple(
            lifecycle_database.minio.list_objects(
                lifecycle_database.bucket,
                prefix=f"workspaces/{target.workspace_id}/",
                recursive=True,
            )
        )
        == ()
    )
    assert (
        tuple(
            lifecycle_database.valkey.scan_iter(match=f"effective-roles:v1:{target.workspace_id}:*")
        )
        == ()
    )


def test_retention_respects_frozen_time_boundaries(lifecycle_database: LifecycleHarness) -> None:
    account = _register(lifecycle_database, uuid4().hex)
    now = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
    old_audit_id, current_audit_id = uuid4(), uuid4()
    old_usage_id, current_usage_id = uuid4(), uuid4()
    old_event_id, current_event_id = uuid4(), uuid4()
    old_stream_id, current_stream_id = uuid4(), uuid4()
    with lifecycle_database.sessions.begin() as session:
        session.execute(
            insert(audit_records),
            [
                _audit(old_audit_id, account, now - timedelta(days=365)),
                _audit(current_audit_id, account, now - timedelta(days=365) + timedelta(seconds=1)),
            ],
        )
        session.execute(
            insert(workspace_usage_records),
            [
                _usage(old_usage_id, account.workspace_id, now - timedelta(days=365)),
                _usage(
                    current_usage_id,
                    account.workspace_id,
                    now - timedelta(days=365) + timedelta(seconds=1),
                ),
            ],
        )
        session.execute(
            insert(outbox_events),
            [
                _published_event(old_event_id, account, now - timedelta(days=30)),
                _published_event(
                    current_event_id,
                    account,
                    now - timedelta(days=30) + timedelta(seconds=1),
                ),
            ],
        )
        session.execute(
            insert(stream_events),
            [
                _stream_event(old_stream_id, account.workspace_id, now),
                _stream_event(current_stream_id, account.workspace_id, now + timedelta(seconds=1)),
            ],
        )

    run = lifecycle_database.service.execute_retention(
        _context(account, RETENTION_PERMISSION),
        workspace_id=account.workspace_id,
        idempotency_key="synthetic-retention-p208",
        requested_at=now,
    )

    assert run.status == "completed"
    assert run.deleted_table_counts["audit_records"] == 1
    assert run.deleted_table_counts["workspace_usage_records"] == 1
    assert run.deleted_table_counts["outbox_events"] >= 1
    assert run.deleted_table_counts["stream_events"] == 1
    with lifecycle_database.sessions() as session:
        assert (
            session.scalar(select(func.count()).where(audit_records.c.audit_id == old_audit_id))
            == 0
        )
        assert (
            session.scalar(select(func.count()).where(audit_records.c.audit_id == current_audit_id))
            == 1
        )
        assert (
            session.scalar(
                select(func.count()).where(
                    workspace_usage_records.c.usage_record_id == old_usage_id
                )
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count()).where(
                    workspace_usage_records.c.usage_record_id == current_usage_id
                )
            )
            == 1
        )
        assert (
            session.scalar(select(func.count()).where(outbox_events.c.event_id == current_event_id))
            == 1
        )
        assert (
            session.scalar(
                select(func.count()).where(stream_events.c.event_id == current_stream_id)
            )
            == 1
        )


def test_concurrent_retention_reuses_one_idempotent_run(
    lifecycle_database: LifecycleHarness,
) -> None:
    account = _register(lifecycle_database, uuid4().hex)
    context = _context(account, RETENTION_PERMISSION)
    now = datetime(2026, 8, 15, 11, 0, tzinfo=UTC)

    def execute() -> UUID:
        result = lifecycle_database.service.execute_retention(
            context,
            workspace_id=account.workspace_id,
            idempotency_key="synthetic-concurrent-retention-p208",
            requested_at=now,
        )
        assert result.status == "completed"
        return result.retention_run_id

    # 两个线程使用独立 Session 竞争同一唯一键，数据库必须只认领一个运行事实。
    with ThreadPoolExecutor(max_workers=2) as executor:
        run_ids = tuple(executor.map(lambda _: execute(), range(2)))

    assert len(set(run_ids)) == 1
    with lifecycle_database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(lifecycle_retention_runs)
                .where(
                    lifecycle_retention_runs.c.workspace_id == account.workspace_id,
                    lifecycle_retention_runs.c.idempotency_key
                    == "synthetic-concurrent-retention-p208",
                )
            )
            == 1
        )


def _audit(audit_id: UUID, account: RegisteredAccount, occurred_at: datetime) -> dict[str, object]:
    return {
        "audit_id": audit_id,
        "workspace_id": account.workspace_id,
        "actor_id": account.account_id,
        "user_id": account.account_id,
        "action": "synthetic.lifecycle.retained",
        "resource_type": "synthetic_resource",
        "resource_id": uuid4(),
        "outcome": "succeeded",
        "occurred_at": occurred_at,
        "request_id": uuid4(),
        "trace_id": TRACE.trace_id,
        "traceparent": TRACE.traceparent,
        "permission_code": None,
        "policy_decision_id": None,
        "policy_version": None,
        "attributes": {"synthetic": True},
    }


def _usage(record_id: UUID, workspace_id: UUID, occurred_at: datetime) -> dict[str, object]:
    return {
        "usage_record_id": record_id,
        "workspace_id": workspace_id,
        "metric": "questions_monthly",
        "period_key": "2025-08",
        "idempotency_key": f"p208-retention-{record_id}",
        "delta_value": 1,
        "resulting_value": 1,
        "occurred_at": occurred_at,
    }


def _published_event(
    event_id: UUID,
    account: RegisteredAccount,
    published_at: datetime,
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "event_type": "synthetic.lifecycle.published",
        "schema_version": 1,
        "workspace_id": account.workspace_id,
        "aggregate_id": uuid4(),
        "aggregate_version": 1,
        "occurred_at": published_at,
        "trace_id": TRACE.trace_id,
        "traceparent": TRACE.traceparent,
        "actor_id": account.account_id,
        "user_id": account.account_id,
        "request_id": uuid4(),
        "payload": {"synthetic": True},
        "status": "published",
        "attempt_count": 1,
        "available_at": published_at,
        "claimed_by": None,
        "claim_until": None,
        "last_error_code": None,
        "published_at": published_at,
    }


def _stream_event(event_id: UUID, workspace_id: UUID, expires_at: datetime) -> dict[str, object]:
    return {
        "event_id": event_id,
        "workspace_id": workspace_id,
        "conversation_id": uuid4(),
        "message_id": uuid4(),
        "run_id": uuid4(),
        "event_type": "completed",
        "sequence_no": 1,
        "occurred_at": expires_at - timedelta(hours=24),
        "expires_at": expires_at,
        "trace_id": TRACE.trace_id,
        "traceparent": TRACE.traceparent,
        "payload": {"synthetic": True},
    }
