from collections.abc import Callable
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ai_platform_api.modules.integration.domain.events import IntegrationEvent
from ai_platform_api.persistence.tables import (
    consumer_receipts,
    outbox_events,
    resource_projections,
)


class SqlAlchemyOutboxWriter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, event: IntegrationEvent) -> None:
        self._session.execute(
            insert(outbox_events).values(
                event_id=event.event_id,
                event_type=event.event_type,
                schema_version=event.schema_version,
                workspace_id=event.workspace_id,
                aggregate_id=event.aggregate_id,
                aggregate_version=event.aggregate_version,
                occurred_at=event.occurred_at,
                trace_id=event.trace_id,
                traceparent=event.traceparent,
                payload=event.payload,
            )
        )


class SqlAlchemyConsumerUnitOfWork:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyConsumerUnitOfWork":
        self._session = self._session_factory()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is not None:
            if exc_type is not None:
                self._session.rollback()
            self._session.close()
            self._session = None

    @property
    def session(self) -> Session:
        if self._session is None:
            raise RuntimeError("Consumer Unit of Work 尚未进入事务范围")
        return self._session

    def claim(self, consumer_name: str, event: IntegrationEvent) -> bool:
        statement = (
            insert(consumer_receipts)
            .values(
                consumer_name=consumer_name,
                event_id=event.event_id,
                processed_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing()
            .returning(consumer_receipts.c.event_id)
        )
        return self.session.execute(statement).scalar_one_or_none() is not None

    def apply_projection(self, event: IntegrationEvent) -> None:
        statement = insert(resource_projections).values(
            aggregate_id=event.aggregate_id,
            workspace_id=event.workspace_id,
            applied_event_id=event.event_id,
            apply_count=1,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[resource_projections.c.aggregate_id],
            set_={
                "applied_event_id": statement.excluded.applied_event_id,
                "apply_count": resource_projections.c.apply_count + 1,
            },
        )
        self.session.execute(statement)

    def commit(self) -> None:
        self.session.commit()


def get_outbox_event(session: Session, event_id: UUID) -> IntegrationEvent:
    row = session.execute(select(outbox_events).where(outbox_events.c.event_id == event_id)).one()
    return IntegrationEvent(
        event_id=row.event_id,
        event_type=row.event_type,
        schema_version=row.schema_version,
        workspace_id=row.workspace_id,
        aggregate_id=row.aggregate_id,
        aggregate_version=row.aggregate_version,
        occurred_at=row.occurred_at,
        trace_id=row.trace_id,
        traceparent=row.traceparent,
        payload=row.payload,
    )
