import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.infrastructure.static_policy import (
    PolicyGrant,
    StaticPolicyDecisionPoint,
)
from ai_platform_api.modules.integration.application.consumer import (
    IdempotentProjectionConsumer,
)
from ai_platform_api.modules.integration.infrastructure.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyConsumerUnitOfWork,
    SqlAlchemyOutboxWriter,
    get_outbox_event,
)
from ai_platform_api.modules.workspace.application.resources import (
    AuthorizationDeniedError,
    CreateWorkspaceResource,
    ReadWorkspaceResource,
    ResourceNotFoundError,
)
from ai_platform_api.modules.workspace.domain.resource import WorkspaceResource
from ai_platform_api.modules.workspace.infrastructure.sqlalchemy import (
    SqlAlchemyWorkspaceUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    consumer_receipts,
    outbox_events,
    resource_projections,
    workspace_resources,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)


@dataclass(frozen=True)
class DatabaseHarness:
    schema: str
    engine: Engine
    sessions: sessionmaker[Session]


@dataclass(frozen=True)
class StoredResourceEvent:
    actor_id: UUID
    workspace_id: UUID
    resource_id: UUID
    event_id: UUID
    request_context: RequestContext


@pytest.fixture(scope="module")
def database() -> Iterator[DatabaseHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p006_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)

    # Schema 名称由本进程生成且只含字母数字和下划线，可安全用于 DDL 标识符。
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
    try:
        yield DatabaseHarness(
            schema=schema,
            engine=engine,
            sessions=create_session_factory(engine),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def context(actor_id: UUID, workspace_id: UUID) -> RequestContext:
    return RequestContext.trusted(
        actor_id=actor_id,
        user_id=actor_id,
        workspace_id=workspace_id,
        trace=TraceContext.continue_from("00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"),
        authentication_method="synthetic_test",
    )


@pytest.fixture(scope="module")
def stored_resource_event(database: DatabaseHarness) -> StoredResourceEvent:
    actor_id = UUID("10000000-0000-4000-8000-000000000001")
    workspace_id = UUID("20000000-0000-4000-8000-000000000001")
    resource_id = UUID("30000000-0000-4000-8000-000000000001")
    event_id = UUID("40000000-0000-4000-8000-000000000001")
    request_context = context(actor_id, workspace_id)
    service = CreateWorkspaceResource(
        grant(actor_id, workspace_id),
        SqlAlchemyWorkspaceUnitOfWork(
            database.sessions,
            SqlAlchemyOutboxWriter,
            SqlAlchemyAuditWriter,
        ),
        event_id_factory=lambda: event_id,
    )
    service.execute(
        request_context,
        WorkspaceResource(
            resource_id=resource_id,
            workspace_id=workspace_id,
            title="合成资源 A",
            sensitive_value="synthetic-secret-a",
        ),
    )
    return StoredResourceEvent(
        actor_id=actor_id,
        workspace_id=workspace_id,
        resource_id=resource_id,
        event_id=event_id,
        request_context=request_context,
    )


def grant(
    actor_id: UUID,
    workspace_id: UUID,
    *,
    field_masks: dict[str, frozenset[str]] | None = None,
    resource_ids: dict[str, frozenset[UUID]] | None = None,
) -> StaticPolicyDecisionPoint:
    return StaticPolicyDecisionPoint(
        {
            (actor_id, workspace_id): PolicyGrant(
                permissions=frozenset({"workspace.resource.create", "workspace.resource.read"}),
                field_masks=field_masks or {},
                resource_ids=resource_ids or {},
            )
        }
    )


def test_migration_upgrades_an_empty_schema(database: DatabaseHarness) -> None:
    with database.engine.connect() as connection:
        table_names = set(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = :schema"
                ),
                {"schema": database.schema},
            ).scalars()
        )

    assert {
        "alembic_version",
        "workspace_resources",
        "outbox_events",
        "consumer_receipts",
        "resource_projections",
    }.issubset(table_names)


def test_business_record_and_outbox_commit_with_same_trace(
    database: DatabaseHarness,
    stored_resource_event: StoredResourceEvent,
) -> None:
    with database.sessions() as session:
        stored_resource = session.execute(
            select(workspace_resources).where(
                workspace_resources.c.resource_id == stored_resource_event.resource_id
            )
        ).one()
        stored_event = get_outbox_event(session, stored_resource_event.event_id)

    assert stored_resource.workspace_id == stored_resource_event.workspace_id
    assert stored_event.trace_id == stored_resource_event.request_context.trace.trace_id
    assert stored_event.traceparent == stored_resource_event.request_context.trace.traceparent


def test_outbox_constraint_failure_rolls_back_business_record(
    database: DatabaseHarness,
    stored_resource_event: StoredResourceEvent,
) -> None:
    actor_id = UUID("10000000-0000-4000-8000-000000000002")
    workspace_id = UUID("20000000-0000-4000-8000-000000000002")
    resource_id = UUID("30000000-0000-4000-8000-000000000002")
    service = CreateWorkspaceResource(
        grant(actor_id, workspace_id),
        SqlAlchemyWorkspaceUnitOfWork(
            database.sessions,
            SqlAlchemyOutboxWriter,
            SqlAlchemyAuditWriter,
        ),
        event_id_factory=lambda: stored_resource_event.event_id,
    )

    with pytest.raises(IntegrityError):
        service.execute(
            context(actor_id, workspace_id),
            WorkspaceResource(
                resource_id=resource_id,
                workspace_id=workspace_id,
                title="合成回滚资源",
                sensitive_value=None,
            ),
        )

    with database.sessions() as session:
        stored_count = session.scalar(
            select(func.count())
            .select_from(workspace_resources)
            .where(workspace_resources.c.resource_id == resource_id)
        )
    assert stored_count == 0


def test_cross_workspace_read_does_not_reveal_resource(
    database: DatabaseHarness,
    stored_resource_event: StoredResourceEvent,
) -> None:
    actor_id = UUID("10000000-0000-4000-8000-000000000003")
    other_workspace_id = UUID("20000000-0000-4000-8000-000000000003")
    service = ReadWorkspaceResource(
        grant(actor_id, other_workspace_id),
        SqlAlchemyWorkspaceUnitOfWork(
            database.sessions,
            SqlAlchemyOutboxWriter,
            SqlAlchemyAuditWriter,
        ),
    )

    with pytest.raises(ResourceNotFoundError):
        service.execute(context(actor_id, other_workspace_id), stored_resource_event.resource_id)

    assert stored_resource_event.workspace_id != other_workspace_id


def test_policy_scope_and_field_mask_are_enforced(
    database: DatabaseHarness,
    stored_resource_event: StoredResourceEvent,
) -> None:
    actor_id = UUID("10000000-0000-4000-8000-000000000004")
    workspace_id = stored_resource_event.workspace_id
    resource_id = stored_resource_event.resource_id
    request_context = context(actor_id, workspace_id)
    masked_policy = grant(
        actor_id,
        workspace_id,
        field_masks={"workspace.resource.read": frozenset({"sensitive_value"})},
        resource_ids={"workspace.resource.read": frozenset({resource_id})},
    )

    resource = ReadWorkspaceResource(
        masked_policy,
        SqlAlchemyWorkspaceUnitOfWork(
            database.sessions,
            SqlAlchemyOutboxWriter,
            SqlAlchemyAuditWriter,
        ),
    ).execute(request_context, resource_id)

    assert resource.title == "合成资源 A"
    assert resource.sensitive_value is None

    out_of_scope_policy = grant(
        actor_id,
        workspace_id,
        resource_ids={"workspace.resource.read": frozenset({uuid4()})},
    )
    with pytest.raises(AuthorizationDeniedError):
        ReadWorkspaceResource(
            out_of_scope_policy,
            SqlAlchemyWorkspaceUnitOfWork(
                database.sessions,
                SqlAlchemyOutboxWriter,
                SqlAlchemyAuditWriter,
            ),
        ).execute(request_context, resource_id)


def test_unavailable_policy_defaults_to_deny(
    database: DatabaseHarness,
    stored_resource_event: StoredResourceEvent,
) -> None:
    actor_id = UUID("10000000-0000-4000-8000-000000000005")
    workspace_id = stored_resource_event.workspace_id
    resource_id = stored_resource_event.resource_id
    unavailable_policy = StaticPolicyDecisionPoint({}, available=False)

    with pytest.raises(AuthorizationDeniedError):
        ReadWorkspaceResource(
            unavailable_policy,
            SqlAlchemyWorkspaceUnitOfWork(
                database.sessions,
                SqlAlchemyOutboxWriter,
                SqlAlchemyAuditWriter,
            ),
        ).execute(context(actor_id, workspace_id), resource_id)


def test_duplicate_event_is_consumed_once(
    database: DatabaseHarness,
    stored_resource_event: StoredResourceEvent,
) -> None:
    event_id = stored_resource_event.event_id
    with database.sessions() as session:
        event = get_outbox_event(session, event_id)
    consumer = IdempotentProjectionConsumer(
        "synthetic-resource-projection",
        SqlAlchemyConsumerUnitOfWork(database.sessions),
    )

    processed_at = datetime.now(UTC)
    assert (
        consumer.handle(
            event,
            task_id=uuid4(),
            trace_id=event.trace_id,
            traceparent=event.traceparent,
            processed_at=processed_at,
        )
        is True
    )
    assert (
        consumer.handle(
            event,
            task_id=uuid4(),
            trace_id=event.trace_id,
            traceparent=event.traceparent,
            processed_at=processed_at,
        )
        is False
    )

    with database.sessions() as session:
        receipt_count = session.scalar(
            select(func.count())
            .select_from(consumer_receipts)
            .where(
                consumer_receipts.c.consumer_name == "synthetic-resource-projection",
                consumer_receipts.c.event_id == event_id,
            )
        )
        projection = session.execute(
            select(resource_projections).where(
                resource_projections.c.aggregate_id == event.aggregate_id
            )
        ).one()
        event_count = session.scalar(
            select(func.count())
            .select_from(outbox_events)
            .where(outbox_events.c.event_id == event_id)
        )

    assert receipt_count == 1
    assert event_count == 1
    assert projection.apply_count == 1
    assert projection.applied_event_id == event_id
