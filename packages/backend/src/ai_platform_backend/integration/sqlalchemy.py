"""实现审计、Outbox 租约和幂等投影事务的 SQLAlchemy Adapter。"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import TracebackType
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import CursorResult, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session, sessionmaker

from ai_platform_backend.integration.domain import (
    AuditRecord,
    ClaimedOutboxEvent,
    IntegrationEvent,
    OutboxClaimBatch,
)
from ai_platform_backend.integration.persistence import (
    audit_records,
    consumer_receipts,
    outbox_events,
    resource_projections,
)

SessionFactory = sessionmaker[Session]


class SqlAlchemyOutboxWriter:
    """把集成事件写入当前 Session，事件发布由事务提交后的 Dispatcher 负责。"""

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
                actor_id=event.actor_id,
                user_id=event.user_id,
                request_id=event.request_id,
                payload=event.payload,
                status="pending",
                attempt_count=0,
                available_at=event.occurred_at,
            )
        )


class SqlAlchemyAuditWriter:
    """把已投影审计记录写入当前 Session，不执行独立提交。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: AuditRecord) -> None:
        self._session.execute(
            insert(audit_records).values(
                audit_id=record.audit_id,
                workspace_id=record.workspace_id,
                actor_id=record.actor_id,
                user_id=record.user_id,
                action=record.action,
                resource_type=record.resource_type,
                resource_id=record.resource_id,
                outcome=record.outcome,
                occurred_at=record.occurred_at,
                request_id=record.request_id,
                trace_id=record.trace_id,
                traceparent=record.traceparent,
                attributes=record.attributes,
            )
        )


class SqlAlchemyOutboxLeaseStore:
    """使用行锁和租约防止并发重复认领，并允许进程崩溃后自动恢复。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
        max_attempts: int,
    ) -> OutboxClaimBatch:
        claim_until = now + timedelta(seconds=lease_seconds)
        with self._session_factory() as session, session.begin():
            # 1. Worker 在发布期间退出也算一次尝试；达到上限的过期租约直接进入死信，
            # 避免仅靠发布异常路径限制次数而形成无限崩溃循环。
            expired_result = cast(
                CursorResult[object],
                session.execute(
                    update(outbox_events)
                    .where(
                        outbox_events.c.status == "publishing",
                        outbox_events.c.claim_until <= now,
                        outbox_events.c.attempt_count >= max_attempts,
                    )
                    .values(
                        status="dead_letter",
                        claimed_by=None,
                        claim_until=None,
                        last_error_code="WORKER_LEASE_EXPIRED",
                    )
                ),
            )
            # 2. 使用 SKIP LOCKED 批量认领到期事件，并在返回前写入尝试次数与新租约。
            rows = session.execute(
                select(outbox_events)
                .where(
                    and_(
                        or_(
                            (outbox_events.c.status == "pending")
                            & (outbox_events.c.available_at <= now),
                            (outbox_events.c.status == "publishing")
                            & (outbox_events.c.claim_until <= now),
                        ),
                        outbox_events.c.attempt_count < max_attempts,
                    )
                )
                .order_by(outbox_events.c.occurred_at, outbox_events.c.event_id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            claimed: list[ClaimedOutboxEvent] = []
            for row in rows:
                attempt_count = cast(int, row.attempt_count) + 1
                session.execute(
                    update(outbox_events)
                    .where(outbox_events.c.event_id == row.event_id)
                    .values(
                        status="publishing",
                        attempt_count=attempt_count,
                        claimed_by=worker_id,
                        claim_until=claim_until,
                        last_error_code=None,
                    )
                )
                claimed.append(
                    ClaimedOutboxEvent(
                        event=_event_from_row(row),
                        attempt_count=attempt_count,
                        claimed_by=worker_id,
                        claim_until=claim_until,
                    )
                )
            # 3. 在同一快照记录待处理与持租约事件的积压，不读取 Payload 或按空间分组。
            pending_count, oldest_pending_at = session.execute(
                select(func.count(), func.min(outbox_events.c.occurred_at)).where(
                    outbox_events.c.status.in_(("pending", "publishing"))
                )
            ).one()
            oldest_age = (
                0.0
                if oldest_pending_at is None
                else max(0.0, (now - oldest_pending_at).total_seconds())
            )
        return OutboxClaimBatch(
            tuple(claimed),
            dead_lettered=expired_result.rowcount,
            pending_count=int(pending_count),
            oldest_pending_age_seconds=oldest_age,
        )

    def mark_published(
        self,
        *,
        event_id: UUID,
        worker_id: str,
        published_at: datetime,
    ) -> bool:
        with self._session_factory() as session, session.begin():
            result = cast(
                CursorResult[object],
                session.execute(
                    update(outbox_events)
                    .where(
                        outbox_events.c.event_id == event_id,
                        outbox_events.c.status == "publishing",
                        outbox_events.c.claimed_by == worker_id,
                    )
                    .values(
                        status="published",
                        published_at=published_at,
                        claimed_by=None,
                        claim_until=None,
                        last_error_code=None,
                    )
                ),
            )
        return result.rowcount == 1

    def mark_failed(
        self,
        *,
        event_id: UUID,
        worker_id: str,
        error_code: str,
        next_attempt_at: datetime,
        max_attempts: int,
    ) -> Literal["pending", "dead_letter", "lost_claim"]:
        with self._session_factory() as session, session.begin():
            row = session.execute(
                select(outbox_events.c.attempt_count)
                .where(
                    outbox_events.c.event_id == event_id,
                    outbox_events.c.status == "publishing",
                    outbox_events.c.claimed_by == worker_id,
                )
                .with_for_update()
            ).one_or_none()
            if row is None:
                return "lost_claim"
            status: Literal["pending", "dead_letter"] = (
                "dead_letter" if row.attempt_count >= max_attempts else "pending"
            )
            session.execute(
                update(outbox_events)
                .where(outbox_events.c.event_id == event_id)
                .values(
                    status=status,
                    available_at=next_attempt_at,
                    claimed_by=None,
                    claim_until=None,
                    last_error_code=error_code,
                )
            )
        return status


class SqlAlchemyConsumerUnitOfWork:
    """保证消费位置和业务投影使用同一 SQLAlchemy Session 提交。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> SqlAlchemyConsumerUnitOfWork:
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

    def claim(
        self,
        consumer_name: str,
        event: IntegrationEvent,
        *,
        task_id: UUID,
        trace_id: str,
        traceparent: str,
        processed_at: datetime,
    ) -> bool:
        statement = (
            insert(consumer_receipts)
            .values(
                consumer_name=consumer_name,
                event_id=event.event_id,
                task_id=task_id,
                processed_at=processed_at,
                trace_id=trace_id,
                traceparent=traceparent,
            )
            .on_conflict_do_nothing()
            .returning(consumer_receipts.c.event_id)
        )
        return self.session.execute(statement).scalar_one_or_none() is not None

    def apply_projection(self, event: IntegrationEvent, *, traceparent: str) -> None:
        statement = insert(resource_projections).values(
            aggregate_id=event.aggregate_id,
            workspace_id=event.workspace_id,
            applied_event_id=event.event_id,
            apply_count=1,
            last_traceparent=traceparent,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[resource_projections.c.aggregate_id],
            set_={
                "applied_event_id": statement.excluded.applied_event_id,
                "apply_count": resource_projections.c.apply_count + 1,
                "last_traceparent": traceparent,
            },
        )
        self.session.execute(statement)

    def commit(self) -> None:
        self.session.commit()


def get_outbox_event(session: Session, event_id: UUID) -> IntegrationEvent:
    """获取Outbox事件，并保持调用方可依赖的稳定返回语义。"""

    row = session.execute(select(outbox_events).where(outbox_events.c.event_id == event_id)).one()
    return _event_from_row(row)


def _event_from_row(row: Any) -> IntegrationEvent:
    mapping = cast(RowMapping, row._mapping)
    return IntegrationEvent(
        event_id=mapping["event_id"],
        event_type=mapping["event_type"],
        schema_version=mapping["schema_version"],
        workspace_id=mapping["workspace_id"],
        aggregate_id=mapping["aggregate_id"],
        aggregate_version=mapping["aggregate_version"],
        occurred_at=mapping["occurred_at"],
        trace_id=mapping["trace_id"],
        traceparent=mapping["traceparent"],
        actor_id=mapping["actor_id"],
        user_id=mapping["user_id"],
        request_id=mapping["request_id"],
        payload=mapping["payload"],
    )
