from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.infrastructure.static_policy import (
    PolicyGrant,
    StaticPolicyDecisionPoint,
)
from ai_platform_api.modules.workspace.application.resources import CreateWorkspaceResource
from ai_platform_api.modules.workspace.domain.resource import WorkspaceResource
from ai_platform_api.modules.workspace.infrastructure.sqlalchemy import (
    SqlAlchemyWorkspaceUnitOfWork,
)
from ai_platform_backend.database import create_platform_engine, create_session_factory
from ai_platform_backend.integration.application import OutboxDispatcher
from ai_platform_backend.integration.consumer import IdempotentProjectionConsumer
from ai_platform_backend.integration.domain import IntegrationEvent
from ai_platform_backend.integration.persistence import (
    audit_records,
    consumer_receipts,
    outbox_events,
    resource_projections,
)
from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyConsumerUnitOfWork,
    SqlAlchemyOutboxLeaseStore,
    SqlAlchemyOutboxWriter,
    get_outbox_event,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
ACTOR_ID = UUID("10000000-0000-4000-8000-000000000031")
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000031")
RESOURCE_ID = UUID("30000000-0000-4000-8000-000000000031")
EVENT_ID = UUID("40000000-0000-4000-8000-000000000031")
TRACEPARENT = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"


@dataclass(frozen=True)
class RuntimeDatabase:
    schema: str
    engine: Engine
    sessions: sessionmaker[Session]


@pytest.fixture(scope="module")
def runtime_database() -> Iterator[RuntimeDatabase]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1a05_test_{uuid4().hex}"
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
    try:
        yield RuntimeDatabase(schema, engine, create_session_factory(engine))
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def request_context() -> RequestContext:
    return RequestContext.trusted(
        actor_id=ACTOR_ID,
        user_id=ACTOR_ID,
        workspace_id=WORKSPACE_ID,
        trace=TraceContext.continue_from(TRACEPARENT),
        authentication_method="synthetic_test",
        request_id=UUID("50000000-0000-4000-8000-000000000031"),
    )


@pytest.fixture(scope="module")
def stored_event(runtime_database: RuntimeDatabase) -> IntegrationEvent:
    policy = StaticPolicyDecisionPoint(
        {
            (ACTOR_ID, WORKSPACE_ID): PolicyGrant(
                permissions=frozenset({"workspace.resource.create"}),
                field_masks={},
                resource_ids={},
            )
        }
    )
    service = CreateWorkspaceResource(
        policy,
        SqlAlchemyWorkspaceUnitOfWork(
            runtime_database.sessions,
            SqlAlchemyOutboxWriter,
            SqlAlchemyAuditWriter,
        ),
        event_id_factory=iter(
            [
                EVENT_ID,
                UUID("60000000-0000-4000-8000-000000000031"),
            ]
        ).__next__,
    )
    return service.execute(
        request_context(),
        WorkspaceResource(
            resource_id=RESOURCE_ID,
            workspace_id=WORKSPACE_ID,
            title="合成 Outbox 资源",
            sensitive_value="synthetic-sensitive-value",
        ),
    )


class RecordingPublisher:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.events: list[IntegrationEvent] = []

    def publish(self, event: IntegrationEvent) -> None:
        self.events.append(event)
        if self.error is not None:
            raise self.error


def test_business_audit_and_outbox_share_one_transaction(
    runtime_database: RuntimeDatabase,
    stored_event: IntegrationEvent,
) -> None:
    with runtime_database.sessions() as session:
        audit = session.execute(
            select(audit_records).where(audit_records.c.resource_id == RESOURCE_ID)
        ).one()
        outbox = get_outbox_event(session, stored_event.event_id)

    assert audit.actor_id == ACTOR_ID
    assert audit.trace_id == stored_event.trace_id
    assert audit.attributes == {"resource_version": 1}
    assert "synthetic-sensitive-value" not in repr(audit)
    assert outbox.actor_id == ACTOR_ID
    assert outbox.request_id == request_context().request_id

    with pytest.raises(DBAPIError), runtime_database.sessions.begin() as session:
        session.execute(
            update(audit_records)
            .where(audit_records.c.audit_id == audit.audit_id)
            .values(outcome="failed")
        )


def test_dispatch_failure_is_retried_and_lease_expiry_recovers(
    runtime_database: RuntimeDatabase,
    stored_event: IntegrationEvent,
) -> None:
    first_time = stored_event.occurred_at + timedelta(seconds=1)
    failed = OutboxDispatcher(
        SqlAlchemyOutboxLeaseStore(runtime_database.sessions),
        RecordingPublisher(ConnectionError("synthetic broker unavailable")),
        worker_id="worker-a",
        lease_seconds=10,
        max_attempts=3,
        base_retry_seconds=1,
        clock=lambda: first_time,
    ).dispatch_once()
    assert failed.retried == 1

    second_time = first_time + timedelta(seconds=2)
    claimed = SqlAlchemyOutboxLeaseStore(runtime_database.sessions).claim_due(
        worker_id="worker-b",
        now=second_time,
        limit=1,
        lease_seconds=10,
        max_attempts=4,
    )
    assert claimed.events[0].attempt_count == 2

    # 模拟发布进程在确认前退出；租约未过期时不能被其他 Worker 抢占。
    not_reclaimed = SqlAlchemyOutboxLeaseStore(runtime_database.sessions).claim_due(
        worker_id="worker-c",
        now=second_time + timedelta(seconds=5),
        limit=1,
        lease_seconds=10,
        max_attempts=4,
    )
    assert not_reclaimed.events == ()

    recovered_time = second_time + timedelta(seconds=11)
    publisher = RecordingPublisher()
    recovered = OutboxDispatcher(
        SqlAlchemyOutboxLeaseStore(runtime_database.sessions),
        publisher,
        worker_id="worker-c",
        lease_seconds=10,
        max_attempts=4,
        clock=lambda: recovered_time,
    ).dispatch_once()

    assert recovered.published == 1
    assert publisher.events == [stored_event]
    with runtime_database.sessions() as session:
        row = session.execute(
            select(outbox_events).where(outbox_events.c.event_id == stored_event.event_id)
        ).one()
    assert row.status == "published"
    assert row.attempt_count == 3


def test_duplicate_task_is_consumed_once_with_continued_trace(
    runtime_database: RuntimeDatabase,
    stored_event: IntegrationEvent,
) -> None:
    trace = TraceContext.continue_from(stored_event.traceparent)
    consumer = IdempotentProjectionConsumer(
        "p1a05-synthetic-projection-v1",
        SqlAlchemyConsumerUnitOfWork(runtime_database.sessions),
    )
    processed_at = datetime.now(UTC)
    assert (
        consumer.handle(
            stored_event,
            task_id=uuid4(),
            trace_id=trace.trace_id,
            traceparent=trace.traceparent,
            processed_at=processed_at,
        )
        is True
    )
    assert (
        consumer.handle(
            stored_event,
            task_id=uuid4(),
            trace_id=trace.trace_id,
            traceparent=trace.traceparent,
            processed_at=processed_at,
        )
        is False
    )

    with runtime_database.sessions() as session:
        receipt_count = session.scalar(
            select(func.count())
            .select_from(consumer_receipts)
            .where(consumer_receipts.c.event_id == stored_event.event_id)
        )
        projection = session.execute(
            select(resource_projections).where(
                resource_projections.c.aggregate_id == stored_event.aggregate_id
            )
        ).one()

    assert receipt_count == 1
    assert projection.apply_count == 1
    assert projection.last_traceparent == trace.traceparent
    assert trace.trace_id == stored_event.trace_id
    assert trace.traceparent != stored_event.traceparent


def test_dead_letter_stops_automatic_reclaim(runtime_database: RuntimeDatabase) -> None:
    dead_event_id = uuid4()
    now = datetime.now(UTC)
    with runtime_database.sessions.begin() as session:
        session.execute(
            outbox_events.insert().values(
                event_id=dead_event_id,
                event_type="workspace.resource.created",
                schema_version=1,
                workspace_id=WORKSPACE_ID,
                aggregate_id=uuid4(),
                aggregate_version=1,
                occurred_at=now,
                trace_id="0123456789abcdef0123456789abcdef",
                traceparent=TRACEPARENT,
                actor_id=ACTOR_ID,
                user_id=ACTOR_ID,
                request_id=uuid4(),
                payload={"synthetic": True},
                status="pending",
                attempt_count=0,
                available_at=now,
            )
        )

    for attempt in range(1, 3):
        current = now + timedelta(seconds=attempt * 10)

        def current_time(value: datetime = current) -> datetime:
            return value

        result = OutboxDispatcher(
            SqlAlchemyOutboxLeaseStore(runtime_database.sessions),
            RecordingPublisher(RuntimeError("synthetic permanent failure")),
            worker_id="worker-dead",
            batch_size=1,
            max_attempts=2,
            base_retry_seconds=1,
            clock=current_time,
        ).dispatch_once()
        if attempt == 1:
            assert result.retried == 1
        else:
            assert result.dead_lettered == 1

    with runtime_database.sessions() as session:
        row = session.execute(
            select(outbox_events).where(outbox_events.c.event_id == dead_event_id)
        ).one()
    assert row.status == "dead_letter"
    assert row.attempt_count == 2
    assert row.last_error_code == "PUBLISH_RUNTIMEERROR"
    assert (
        SqlAlchemyOutboxLeaseStore(runtime_database.sessions)
        .claim_due(
            worker_id="worker-late",
            now=now + timedelta(days=1),
            limit=10,
            lease_seconds=10,
            max_attempts=2,
        )
        .events
        == ()
    )


def test_expired_lease_at_attempt_limit_is_dead_lettered(
    runtime_database: RuntimeDatabase,
) -> None:
    event_id = uuid4()
    now = datetime.now(UTC)
    with runtime_database.sessions.begin() as session:
        session.execute(
            outbox_events.insert().values(
                event_id=event_id,
                event_type="workspace.resource.created",
                schema_version=1,
                workspace_id=WORKSPACE_ID,
                aggregate_id=uuid4(),
                aggregate_version=1,
                occurred_at=now,
                trace_id="0123456789abcdef0123456789abcdef",
                traceparent=TRACEPARENT,
                actor_id=ACTOR_ID,
                user_id=ACTOR_ID,
                request_id=uuid4(),
                payload={"synthetic": True},
                status="publishing",
                attempt_count=3,
                available_at=now,
                claimed_by="synthetic-crashed-worker",
                claim_until=now - timedelta(seconds=1),
            )
        )

    result = OutboxDispatcher(
        SqlAlchemyOutboxLeaseStore(runtime_database.sessions),
        RecordingPublisher(),
        worker_id="worker-recovery",
        max_attempts=3,
        clock=lambda: now,
    ).dispatch_once()

    assert result.claimed == 0
    assert result.dead_lettered == 1
    with runtime_database.sessions() as session:
        row = session.execute(
            select(outbox_events).where(outbox_events.c.event_id == event_id)
        ).one()
    assert row.status == "dead_letter"
    assert row.last_error_code == "WORKER_LEASE_EXPIRED"
