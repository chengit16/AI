"""验证 P2-07 审计、用量、Outbox 重放和消费者幂等运营事实。"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.entitlement_errors import (
    EntitlementValidationError,
)
from ai_platform_api.modules.identity.application.entitlements import EntitlementService
from ai_platform_api.modules.identity.infrastructure.entitlements_sqlalchemy import (
    SqlAlchemyEntitlementUnitOfWork,
)
from ai_platform_api.modules.integration.api.schemas import OutboxEventResponse
from ai_platform_api.modules.integration.application.operations import (
    IntegrationOperationsService,
    OperationsValidationError,
)
from ai_platform_api.modules.integration.infrastructure.operations_sqlalchemy import (
    SqlAlchemyIntegrationOperationsUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    accounts,
    audit_records,
    consumer_receipts,
    menu_releases,
    outbox_events,
    registered_menu_api_bindings,
    resource_projections,
    role_permission_grants,
    roles,
    workspace_menu_publications,
    workspace_usage_counters,
    workspace_usage_records,
    workspaces,
)
from ai_platform_backend.integration.consumer import IdempotentProjectionConsumer
from ai_platform_backend.integration.domain import IntegrationEvent
from ai_platform_backend.integration.persistence import outbox_replay_requests
from ai_platform_backend.integration.sqlalchemy import SqlAlchemyConsumerUnitOfWork
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext.continue_from("00-7123456789abcdef0123456789abcdef-7123456789abcdef-01")
NOW = datetime(2026, 8, 15, 7, 0, tzinfo=UTC)


@dataclass(frozen=True)
class OperationsHarness:
    engine: Engine
    sessions: sessionmaker[Session]
    workspace_id: UUID
    other_workspace_id: UUID
    account_id: UUID
    event_id: UUID
    other_event_id: UUID
    other_audit_id: UUID
    other_usage_id: UUID
    historical_workspace_id: UUID
    historical_role_id: UUID
    historical_event_id: UUID


@pytest.fixture(scope="module")
def operations_database() -> Iterator[OperationsHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p207_test_{uuid4().hex}"
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
    command.upgrade(config, "20260815_0038")

    pre_migration_engine = create_platform_engine(database_url, schema)
    pre_migration_sessions = create_session_factory(pre_migration_engine)
    historical_identifiers = _seed_pre_migration_facts(pre_migration_sessions, schema)
    pre_migration_engine.dispose()
    command.upgrade(config, "head")

    engine = create_platform_engine(database_url, schema)
    sessions = create_session_factory(engine)
    identifiers = _seed_facts(sessions)
    try:
        yield OperationsHarness(engine, sessions, *identifiers, *historical_identifiers)
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _seed_facts(
    sessions: sessionmaker[Session],
) -> tuple[UUID, UUID, UUID, UUID, UUID, UUID, UUID]:
    """写入全合成的两个空间及可分页运营事实。"""

    workspace_id, other_workspace_id = uuid4(), uuid4()
    account_id, other_account_id = uuid4(), uuid4()
    event_id, second_event_id, other_event_id = uuid4(), uuid4(), uuid4()
    first_audit_id, other_audit_id = uuid4(), uuid4()
    first_usage_id, second_usage_id, other_usage_id = uuid4(), uuid4(), uuid4()
    with sessions.begin() as session:
        session.execute(
            insert(accounts),
            [
                _account(account_id, "synthetic.p207.owner@example.com"),
                _account(other_account_id, "synthetic.p207.other@example.com"),
            ],
        )
        session.execute(
            insert(workspaces),
            [
                _workspace(workspace_id, account_id, "合成运营空间"),
                _workspace(other_workspace_id, other_account_id, "合成隔离空间"),
            ],
        )
        session.execute(
            insert(workspace_usage_counters).values(
                workspace_id=workspace_id,
                metric="knowledge_bases",
                period_key="lifetime",
                used_value=5,
                updated_at=NOW + timedelta(minutes=2),
                version=2,
            )
        )
        session.execute(
            insert(workspace_usage_records),
            [
                _usage(first_usage_id, workspace_id, "p207-usage-first", 3, 3, NOW),
                _usage(
                    second_usage_id,
                    workspace_id,
                    "p207-usage-second",
                    2,
                    5,
                    NOW + timedelta(minutes=2),
                ),
                _usage(
                    other_usage_id,
                    other_workspace_id,
                    "p207-usage-other",
                    1,
                    1,
                    NOW + timedelta(minutes=1),
                ),
            ],
        )
        session.execute(
            insert(audit_records),
            [
                _audit(first_audit_id, workspace_id, account_id, NOW),
                _audit(other_audit_id, other_workspace_id, other_account_id, NOW),
            ],
        )
        session.execute(
            insert(outbox_events),
            [
                _outbox(event_id, workspace_id, account_id, NOW),
                _outbox(second_event_id, workspace_id, account_id, NOW + timedelta(minutes=1)),
                _outbox(
                    other_event_id,
                    other_workspace_id,
                    other_account_id,
                    NOW + timedelta(minutes=2),
                ),
            ],
        )
    return (
        workspace_id,
        other_workspace_id,
        account_id,
        event_id,
        other_event_id,
        other_audit_id,
        other_usage_id,
    )


def _seed_pre_migration_facts(
    sessions: sessionmaker[Session],
    schema: str,
) -> tuple[UUID, UUID, UUID]:
    """在 0038 表结构写入旧审计和消费回执，验证 0039 诚实回填。"""

    workspace_id, account_id, role_id, event_id = uuid4(), uuid4(), uuid4(), uuid4()
    with sessions.begin() as session:
        session.execute(
            insert(accounts).values(**_account(account_id, "synthetic.p207.historical@example.com"))
        )
        session.execute(
            insert(workspaces).values(**_workspace(workspace_id, account_id, "合成历史运营空间"))
        )
        session.execute(
            insert(roles).values(
                role_id=role_id,
                workspace_id=workspace_id,
                role_key="workspace_owner",
                name="空间所有者",
                status="active",
                system_managed=True,
                created_at=NOW,
                updated_at=NOW,
                version=1,
            )
        )
        source_snapshot = _historical_menu_snapshot(workspace_id)
        snapshot_digest = hashlib.sha256(
            json.dumps(
                source_snapshot,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        source_release_id = uuid4()
        session.execute(
            insert(menu_releases).values(
                release_id=source_release_id,
                workspace_id=workspace_id,
                release_number=1,
                release_kind="standard",
                source_release_id=None,
                status="published",
                snapshot=source_snapshot,
                snapshot_digest=snapshot_digest,
                validation_errors=[],
                rejection_reason=None,
                created_by_account_id=account_id,
                decided_by_account_id=account_id,
                created_at=NOW,
                validated_at=NOW,
                decided_at=NOW,
                published_at=NOW,
                version=1,
            )
        )
        session.execute(
            insert(workspace_menu_publications).values(
                workspace_id=workspace_id,
                current_release_id=source_release_id,
                published_at=NOW,
            )
        )
        session.execute(
            insert(outbox_events).values(**_outbox(event_id, workspace_id, account_id, NOW))
        )
        # 0038 尚无授权关联和重复接收列，迁移不得伪造未知历史数据。
        session.execute(
            text(
                f'INSERT INTO "{schema}".audit_records ('
                "audit_id, workspace_id, actor_id, user_id, action, resource_type, "
                "resource_id, outcome, occurred_at, request_id, trace_id, traceparent, attributes"
                ") VALUES ("
                ":audit_id, :workspace_id, :actor_id, :user_id, :action, :resource_type, "
                ":resource_id, :outcome, :occurred_at, :request_id, :trace_id, :traceparent, "
                "CAST(:attributes AS jsonb))"
            ),
            {
                "audit_id": uuid4(),
                "workspace_id": workspace_id,
                "actor_id": account_id,
                "user_id": account_id,
                "action": "synthetic.historical.read",
                "resource_type": "synthetic_resource",
                "resource_id": uuid4(),
                "outcome": "succeeded",
                "occurred_at": NOW,
                "request_id": uuid4(),
                "trace_id": TRACE.trace_id,
                "traceparent": TRACE.traceparent,
                "attributes": "{}",
            },
        )
        session.execute(
            text(
                f'INSERT INTO "{schema}".consumer_receipts ('
                "consumer_name, event_id, task_id, processed_at, trace_id, traceparent"
                ") VALUES ("
                ":consumer_name, :event_id, :task_id, :processed_at, :trace_id, :traceparent)"
            ),
            {
                "consumer_name": "p207-historical-consumer",
                "event_id": event_id,
                "task_id": uuid4(),
                "processed_at": NOW,
                "trace_id": TRACE.trace_id,
                "traceparent": TRACE.traceparent,
            },
        )
    return workspace_id, role_id, event_id


def _historical_menu_snapshot(workspace_id: UUID) -> dict[str, object]:
    """构造注册表 15 的最小运行状态页面快照，供 0039 复制升级。"""

    return {
        "schema_version": 1,
        "registry_version": 15,
        "workspace_id": str(workspace_id),
        "menu_version": 1,
        "menus": [
            {
                "menu_id": "82000000-0000-4000-8000-000000000005",
                "menu_key": "navigation.workspace.status",
                "parent_menu_id": None,
                "name": "运行状态",
                "menu_type": "page",
                "page_resource_id": "80000000-0000-4000-8000-000000000005",
                "permission_code": "system.runtime.access",
                "icon_key": "activity",
                "sort_order": 400,
                "source": "system",
                "status": "active",
                "visible": True,
            }
        ],
        "role_menus": [],
        "menu_api_bindings": [],
    }


def _account(account_id: UUID, login_name: str) -> dict[str, object]:
    return {
        "account_id": account_id,
        "login_name": login_name,
        "display_name": "合成用户",
        "password_hash": "synthetic-password-hash",
        "status": "active",
        "auth_version": 1,
        "created_at": NOW,
        "created_by_actor_id": account_id,
        "updated_at": NOW,
        "updated_by_actor_id": account_id,
        "version": 1,
    }


def _workspace(workspace_id: UUID, account_id: UUID, name: str) -> dict[str, object]:
    return {
        "workspace_id": workspace_id,
        "workspace_type": "personal",
        "name": name,
        "owner_account_id": account_id,
        "entitlement_version": 1,
        "role_version": 1,
        "menu_version": 1,
        "status": "active",
        "created_at": NOW,
        "created_by_actor_id": account_id,
        "updated_at": NOW,
        "updated_by_actor_id": account_id,
        "version": 1,
    }


def _usage(
    usage_record_id: UUID,
    workspace_id: UUID,
    idempotency_key: str,
    delta_value: int,
    resulting_value: int,
    occurred_at: datetime,
) -> dict[str, object]:
    return {
        "usage_record_id": usage_record_id,
        "workspace_id": workspace_id,
        "metric": "knowledge_bases",
        "period_key": "lifetime",
        "idempotency_key": idempotency_key,
        "delta_value": delta_value,
        "resulting_value": resulting_value,
        "occurred_at": occurred_at,
    }


def _audit(
    audit_id: UUID,
    workspace_id: UUID,
    account_id: UUID,
    occurred_at: datetime,
) -> dict[str, object]:
    return {
        "audit_id": audit_id,
        "workspace_id": workspace_id,
        "actor_id": account_id,
        "user_id": account_id,
        "action": "knowledge.base.create",
        "resource_type": "knowledge_base",
        "resource_id": uuid4(),
        "outcome": "succeeded",
        "occurred_at": occurred_at,
        "request_id": uuid4(),
        "trace_id": TRACE.trace_id,
        "traceparent": TRACE.traceparent,
        "permission_code": "knowledge.base.create",
        "policy_decision_id": uuid4(),
        "policy_version": 7,
        "attributes": {"source": "synthetic-p207"},
    }


def _outbox(
    event_id: UUID,
    workspace_id: UUID,
    account_id: UUID,
    occurred_at: datetime,
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "event_type": "synthetic.resource.changed",
        "schema_version": 1,
        "workspace_id": workspace_id,
        "aggregate_id": uuid4(),
        "aggregate_version": 1,
        "occurred_at": occurred_at,
        "trace_id": TRACE.trace_id,
        "traceparent": TRACE.traceparent,
        "actor_id": account_id,
        "user_id": account_id,
        "request_id": uuid4(),
        "payload": {"secret": "must-not-escape"},
        "status": "published",
        "attempt_count": 2,
        "available_at": occurred_at,
        "claimed_by": None,
        "claim_until": None,
        "last_error_code": None,
        "published_at": occurred_at + timedelta(seconds=1),
    }


def _context(
    harness: OperationsHarness,
    permission_code: str,
) -> RequestContext:
    base = RequestContext.trusted(
        actor_id=harness.account_id,
        user_id=harness.account_id,
        workspace_id=harness.workspace_id,
        trace=TRACE,
        authentication_method="browser_session",
    )
    return replace(
        base,
        authorized_permission_code=permission_code,
        authorized_policy_decision_id=uuid4(),
        authorized_policy_version=9,
        authorized_workspace=True,
        authorized_maximum_security_level="RESTRICTED",
    )


def test_migration_backfills_historical_receipts_and_existing_owner_permissions(
    operations_database: OperationsHarness,
) -> None:
    with operations_database.sessions() as session:
        receipt = session.execute(
            select(
                consumer_receipts.c.delivery_count,
                consumer_receipts.c.last_received_at,
            ).where(
                consumer_receipts.c.consumer_name == "p207-historical-consumer",
                consumer_receipts.c.event_id == operations_database.historical_event_id,
            )
        ).one()
        historical_audit = session.execute(
            select(
                audit_records.c.permission_code,
                audit_records.c.policy_decision_id,
                audit_records.c.policy_version,
            ).where(
                audit_records.c.workspace_id == operations_database.historical_workspace_id,
                audit_records.c.action == "synthetic.historical.read",
            )
        ).one()
        permissions = set(
            session.execute(
                select(role_permission_grants.c.permission_code).where(
                    role_permission_grants.c.workspace_id
                    == operations_database.historical_workspace_id,
                    role_permission_grants.c.role_id == operations_database.historical_role_id,
                    role_permission_grants.c.permission_code.in_(
                        ("operations.records.read", "operations.outbox.replay")
                    ),
                )
            ).scalars()
        )
        binding_count = session.scalar(
            select(text("count(*)"))
            .select_from(registered_menu_api_bindings)
            .where(
                registered_menu_api_bindings.c.api_resource_id.in_(
                    [UUID(f"81000000-0000-4000-8000-{number:012d}") for number in range(102, 108)]
                )
            )
        )
        releases = session.execute(
            select(
                menu_releases.c.release_number,
                menu_releases.c.source_release_id,
                menu_releases.c.snapshot,
            )
            .where(menu_releases.c.workspace_id == operations_database.historical_workspace_id)
            .order_by(menu_releases.c.release_number)
        ).all()

    assert tuple(receipt) == (1, NOW)
    assert tuple(historical_audit) == (None, None, None)
    assert permissions == {"operations.records.read", "operations.outbox.replay"}
    assert binding_count == 6
    assert len(releases) >= 3
    assert releases[0].snapshot["registry_version"] == 15
    assert len(releases[0].snapshot["menus"]) == 1
    assert releases[1].source_release_id is not None
    assert releases[1].snapshot["registry_version"] == 16
    assert {item["menu_id"] for item in releases[1].snapshot["menus"]}.issuperset(
        {
            "82000000-0000-4000-8000-000000000189",
            "82000000-0000-4000-8000-000000000190",
        }
    )
    assert len(releases[1].snapshot["menu_api_bindings"]) == 6
    lifecycle_release = next(
        release for release in releases if release.snapshot["registry_version"] == 17
    )
    assert lifecycle_release.source_release_id is not None
    assert {item["menu_id"] for item in lifecycle_release.snapshot["menus"]}.issuperset(
        {
            "82000000-0000-4000-8000-000000000191",
            "82000000-0000-4000-8000-000000000192",
            "82000000-0000-4000-8000-000000000193",
        }
    )
    assert len(lifecycle_release.snapshot["menu_api_bindings"]) == 9


def test_usage_ledger_reconciles_and_rejects_cross_workspace_cursor(
    operations_database: OperationsHarness,
) -> None:
    service = EntitlementService(SqlAlchemyEntitlementUnitOfWork(operations_database.sessions))
    context = _context(operations_database, "operations.records.read")

    page = service.list_usage_records(
        context,
        workspace_id=operations_database.workspace_id,
        limit=1,
    )
    reconciliation = service.reconcile_usage(
        context,
        workspace_id=operations_database.workspace_id,
    )

    assert len(page.items) == 1
    assert page.next_cursor == page.items[0].usage_record_id
    assert reconciliation[0].record_count == 2
    assert reconciliation[0].record_delta_total == 5
    assert reconciliation[0].latest_resulting_value == 5
    assert reconciliation[0].consistent is True
    with pytest.raises(EntitlementValidationError):
        service.list_usage_records(
            context,
            workspace_id=operations_database.workspace_id,
            limit=10,
            cursor=operations_database.other_usage_id,
        )


def test_audit_and_outbox_queries_are_isolated_and_payload_free(
    operations_database: OperationsHarness,
) -> None:
    service = IntegrationOperationsService(
        SqlAlchemyIntegrationOperationsUnitOfWork(operations_database.sessions)
    )
    context = _context(operations_database, "operations.records.read")

    audits = service.list_audit_records(
        context,
        workspace_id=operations_database.workspace_id,
        limit=10,
    )
    events = service.list_outbox_events(
        context,
        workspace_id=operations_database.workspace_id,
        limit=10,
    )

    assert audits.items[0].permission_code == "knowledge.base.create"
    assert audits.items[0].policy_version == 7
    assert audits.items[0].trace_id == TRACE.trace_id
    assert len(events.items) == 2
    assert "payload" not in OutboxEventResponse.from_domain(events.items[0]).model_dump()
    with pytest.raises(OperationsValidationError):
        service.list_audit_records(
            context,
            workspace_id=operations_database.workspace_id,
            limit=10,
            cursor=operations_database.other_audit_id,
        )
    with pytest.raises(OperationsValidationError):
        service.list_outbox_events(
            context,
            workspace_id=operations_database.workspace_id,
            limit=10,
            cursor=operations_database.other_event_id,
        )


def test_replay_keeps_event_id_and_request_and_audit_are_immutable(
    operations_database: OperationsHarness,
) -> None:
    service = IntegrationOperationsService(
        SqlAlchemyIntegrationOperationsUnitOfWork(operations_database.sessions)
    )
    context = _context(operations_database, "operations.outbox.replay")
    request = service.replay_outbox_event(
        context,
        workspace_id=operations_database.workspace_id,
        event_id=operations_database.event_id,
        idempotency_key="p207-replay-0001",
        reason_code="MANUAL_RECOVERY",
        requested_at=NOW + timedelta(hours=1),
    )
    replayed = service.replay_outbox_event(
        context,
        workspace_id=operations_database.workspace_id,
        event_id=operations_database.event_id,
        idempotency_key="p207-replay-0001",
        reason_code="MANUAL_RECOVERY",
        requested_at=NOW + timedelta(hours=2),
    )

    assert replayed == request
    assert request.event_id == operations_database.event_id
    with operations_database.sessions() as session:
        event = session.execute(
            select(outbox_events.c.event_id, outbox_events.c.status).where(
                outbox_events.c.event_id == operations_database.event_id
            )
        ).one()
        replay_count = session.scalar(
            select(text("count(*)"))
            .select_from(outbox_replay_requests)
            .where(outbox_replay_requests.c.event_id == operations_database.event_id)
        )
        replay_audit = session.execute(
            select(audit_records).where(
                audit_records.c.action == "operations.outbox.replay",
                audit_records.c.resource_id == operations_database.event_id,
            )
        ).one()
        assert tuple(event) == (operations_database.event_id, "pending")
        assert replay_count == 1
        assert replay_audit.permission_code == "operations.outbox.replay"
        assert replay_audit.policy_version == 9
        with pytest.raises(DBAPIError):
            session.execute(
                update(outbox_replay_requests)
                .where(outbox_replay_requests.c.replay_request_id == request.replay_request_id)
                .values(reason_code="TAMPERED")
            )
            session.commit()
        session.rollback()


def test_duplicate_delivery_updates_receipt_without_reapplying_projection(
    operations_database: OperationsHarness,
) -> None:
    with operations_database.sessions() as session:
        row = session.execute(
            select(outbox_events).where(outbox_events.c.event_id == operations_database.event_id)
        ).one()
    event = IntegrationEvent(
        event_id=row.event_id,
        event_type=row.event_type,
        workspace_id=row.workspace_id,
        aggregate_id=row.aggregate_id,
        aggregate_version=row.aggregate_version,
        occurred_at=row.occurred_at,
        trace_id=row.trace_id,
        traceparent=row.traceparent,
        payload=row.payload,
        actor_id=row.actor_id,
        user_id=row.user_id,
        request_id=row.request_id,
        schema_version=row.schema_version,
    )
    consumer = IdempotentProjectionConsumer(
        "p207-synthetic-projection",
        SqlAlchemyConsumerUnitOfWork(operations_database.sessions),
    )

    assert consumer.handle(
        event,
        task_id=uuid4(),
        trace_id=TRACE.trace_id,
        traceparent=TRACE.traceparent,
        processed_at=NOW + timedelta(hours=3),
    )
    assert not consumer.handle(
        event,
        task_id=uuid4(),
        trace_id=TRACE.trace_id,
        traceparent=TRACE.traceparent,
        processed_at=NOW + timedelta(hours=4),
    )

    with operations_database.sessions() as session:
        receipt = session.execute(
            select(
                consumer_receipts.c.delivery_count,
                consumer_receipts.c.last_received_at,
            ).where(
                consumer_receipts.c.consumer_name == "p207-synthetic-projection",
                consumer_receipts.c.event_id == operations_database.event_id,
            )
        ).one()
        projection = session.execute(
            select(resource_projections.c.apply_count).where(
                resource_projections.c.aggregate_id == event.aggregate_id
            )
        ).one()
    assert tuple(receipt) == (2, NOW + timedelta(hours=4))
    assert projection.apply_count == 1

    inspection = IntegrationOperationsService(
        SqlAlchemyIntegrationOperationsUnitOfWork(operations_database.sessions)
    ).inspect(
        _context(operations_database, "operations.records.read"),
        workspace_id=operations_database.workspace_id,
        now=NOW + timedelta(hours=5),
    )
    assert inspection.duplicate_delivery_count == 1
    assert inspection.idempotency_issue_count == 0
