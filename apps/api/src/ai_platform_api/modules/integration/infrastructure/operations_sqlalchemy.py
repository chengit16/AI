"""实现审计、Outbox 重放和消费者回执运营查询的 PostgreSQL Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime
from types import TracebackType
from typing import Any, Literal, cast
from uuid import UUID

from ai_platform_backend.integration.persistence import (
    audit_export_requests,
    audit_records,
    consumer_receipts,
    outbox_events,
    outbox_replay_requests,
)
from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import CursorResult, func, insert, not_, or_, select, tuple_, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.integration.domain.operations import (
    AuditExportRequest,
    AuditOperationsPage,
    AuditOperationsRecord,
    AuditOutcome,
    IntegrationInspection,
    IntegrationOperationsCursorError,
    IntegrationOperationsWriteConflictError,
    OutboxOperationsPage,
    OutboxOperationsRecord,
    OutboxReplayRequest,
    OutboxStatus,
    OutboxStatusCount,
)

SessionFactory = Callable[[], Session]
OUTBOX_STATUSES: tuple[OutboxStatus, ...] = (
    "pending",
    "publishing",
    "published",
    "dead_letter",
)
EVENT_TYPE_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){2,}$"
TRACEPARENT_PATTERN = r"^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$"


class SqlAlchemyIntegrationOperationsRepository:
    """使用短查询和行锁维护工作空间隔离的运营事实。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_audit_records(
        self,
        workspace_id: UUID,
        *,
        limit: int,
        cursor: UUID | None,
        actor_id: UUID | None,
        action: str | None,
        resource_type: str | None,
        outcome: AuditOutcome | None,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
    ) -> AuditOperationsPage:
        # 1. 游标必须属于同一工作空间，防止用其他空间的时间位置探测记录分布。
        statement = select(audit_records).where(audit_records.c.workspace_id == workspace_id)
        if cursor is not None:
            cursor_position = self._audit_cursor(workspace_id, cursor)
            statement = statement.where(
                tuple_(audit_records.c.occurred_at, audit_records.c.audit_id) < cursor_position
            )
        # 2. 所有筛选都作用于可信空间条件之后，并按稳定复合键倒序分页。
        if actor_id is not None:
            statement = statement.where(audit_records.c.actor_id == actor_id)
        if action is not None:
            statement = statement.where(audit_records.c.action == action)
        if resource_type is not None:
            statement = statement.where(audit_records.c.resource_type == resource_type)
        if outcome is not None:
            statement = statement.where(audit_records.c.outcome == outcome)
        statement = _time_window(
            statement,
            audit_records.c.occurred_at,
            occurred_from,
            occurred_to,
        )
        rows = self._session.execute(
            statement.order_by(
                audit_records.c.occurred_at.desc(),
                audit_records.c.audit_id.desc(),
            ).limit(limit + 1)
        ).mappings()
        items = tuple(_audit_record(row) for row in rows)
        return AuditOperationsPage(
            items=items[:limit],
            next_cursor=items[limit - 1].audit_id if len(items) > limit else None,
        )

    def get_audit_record(self, workspace_id: UUID, audit_id: UUID) -> AuditOperationsRecord | None:
        """按空间和审计标识读取单条事实，跨空间标识不可探测。"""

        row = (
            self._session.execute(
                select(audit_records).where(
                    audit_records.c.workspace_id == workspace_id,
                    audit_records.c.audit_id == audit_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _audit_record(row)

    def list_audit_export_requests(
        self, workspace_id: UUID, *, limit: int
    ) -> tuple[AuditExportRequest, ...]:
        rows = self._session.execute(
            select(audit_export_requests)
            .where(audit_export_requests.c.workspace_id == workspace_id)
            .order_by(
                audit_export_requests.c.created_at.desc(),
                audit_export_requests.c.audit_export_request_id.desc(),
            )
            .limit(limit)
        ).mappings()
        return tuple(_audit_export_request(row) for row in rows)

    def get_audit_export_request(
        self, workspace_id: UUID, audit_export_request_id: UUID
    ) -> AuditExportRequest | None:
        row = (
            self._session.execute(
                select(audit_export_requests).where(
                    audit_export_requests.c.workspace_id == workspace_id,
                    audit_export_requests.c.audit_export_request_id == audit_export_request_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _audit_export_request(row)

    def get_audit_export_request_by_key(
        self, workspace_id: UUID, idempotency_key: str
    ) -> AuditExportRequest | None:
        row = (
            self._session.execute(
                select(audit_export_requests).where(
                    audit_export_requests.c.workspace_id == workspace_id,
                    audit_export_requests.c.idempotency_key == idempotency_key,
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _audit_export_request(row)

    def add_audit_export_request(self, request: AuditExportRequest) -> None:
        self._session.execute(
            insert(audit_export_requests).values(
                audit_export_request_id=request.audit_export_request_id,
                workspace_id=request.workspace_id,
                idempotency_key=request.idempotency_key,
                request_hash=request.request_hash,
                actor_id=request.actor_id,
                action=request.action,
                resource_type=request.resource_type,
                outcome=request.outcome,
                occurred_from=request.occurred_from,
                occurred_to=request.occurred_to,
                field_mask=sorted(request.field_mask),
                requested_by_actor_id=request.requested_by_actor_id,
                requested_by_user_id=request.requested_by_user_id,
                request_id=request.request_id,
                trace_id=request.trace_id,
                traceparent=request.traceparent,
                status=request.status,
                attempt_count=request.attempt_count,
                last_error_code=request.last_error_code,
                row_count=request.row_count,
                result_sha256=request.result_sha256,
                result_summary=request.result_summary,
                created_at=request.created_at,
                updated_at=request.updated_at,
                completed_at=request.completed_at,
            )
        )

    def list_outbox_events(
        self,
        workspace_id: UUID,
        *,
        limit: int,
        cursor: UUID | None,
        status: OutboxStatus | None,
        event_type: str | None,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
    ) -> OutboxOperationsPage:
        # 1. 重放次数只按事件 ID 聚合，不读取或暴露事件 Payload。
        replay_count = (
            select(func.count())
            .select_from(outbox_replay_requests)
            .where(outbox_replay_requests.c.event_id == outbox_events.c.event_id)
            .correlate(outbox_events)
            .scalar_subquery()
            .label("replay_count")
        )
        statement = select(outbox_events, replay_count).where(
            outbox_events.c.workspace_id == workspace_id
        )
        # 2. 游标与全部筛选始终受可信工作空间约束，并以稳定复合键生成下一页位置。
        if cursor is not None:
            cursor_position = self._outbox_cursor(workspace_id, cursor)
            statement = statement.where(
                tuple_(outbox_events.c.occurred_at, outbox_events.c.event_id) < cursor_position
            )
        if status is not None:
            statement = statement.where(outbox_events.c.status == status)
        if event_type is not None:
            statement = statement.where(outbox_events.c.event_type == event_type)
        statement = _time_window(
            statement,
            outbox_events.c.occurred_at,
            occurred_from,
            occurred_to,
        )
        rows = self._session.execute(
            statement.order_by(
                outbox_events.c.occurred_at.desc(),
                outbox_events.c.event_id.desc(),
            ).limit(limit + 1)
        ).mappings()
        items = tuple(_outbox_record(row) for row in rows)
        return OutboxOperationsPage(
            items=items[:limit],
            next_cursor=items[limit - 1].event_id if len(items) > limit else None,
        )

    def inspect(self, workspace_id: UUID, *, now: datetime) -> IntegrationInspection:
        # 1. 从 Outbox 当前事实计算状态、积压和过期租约，Payload 不离开数据库边界。
        status_rows = self._session.execute(
            select(outbox_events.c.status, func.count())
            .where(outbox_events.c.workspace_id == workspace_id)
            .group_by(outbox_events.c.status)
        ).all()
        counts = {cast(str, status): int(count) for status, count in status_rows}
        oldest_pending_at = self._session.scalar(
            select(func.min(outbox_events.c.occurred_at)).where(
                outbox_events.c.workspace_id == workspace_id,
                outbox_events.c.status.in_(("pending", "publishing")),
            )
        )
        expired_claim_count = int(
            self._session.scalar(
                select(func.count())
                .select_from(outbox_events)
                .where(
                    outbox_events.c.workspace_id == workspace_id,
                    outbox_events.c.status == "publishing",
                    outbox_events.c.claim_until <= now,
                )
            )
            or 0
        )
        incompatible_schema_count = int(
            self._session.scalar(
                select(func.count())
                .select_from(outbox_events)
                .where(
                    outbox_events.c.workspace_id == workspace_id,
                    _incompatible_event_clause(),
                )
            )
            or 0
        )
        # 2. 回执与重复接收次数在同一行聚合；异常计数只表达结构损坏，不猜测业务副作用。
        receipt_count, duplicate_count, issue_count = self._session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(consumer_receipts.c.delivery_count - 1), 0),
                func.count().filter(
                    or_(
                        consumer_receipts.c.delivery_count < 1,
                        consumer_receipts.c.last_received_at < consumer_receipts.c.processed_at,
                    )
                ),
            )
            .select_from(
                consumer_receipts.join(
                    outbox_events,
                    outbox_events.c.event_id == consumer_receipts.c.event_id,
                )
            )
            .where(outbox_events.c.workspace_id == workspace_id)
        ).one()
        replay_count = int(
            self._session.scalar(
                select(func.count())
                .select_from(outbox_replay_requests)
                .where(outbox_replay_requests.c.workspace_id == workspace_id)
            )
            or 0
        )
        # 3. 最终结果只返回计数和有界时长，避免运营巡检复制事件或消费业务数据。
        oldest_age = (
            0.0
            if oldest_pending_at is None
            else max(0.0, (now - oldest_pending_at).total_seconds())
        )
        return IntegrationInspection(
            checked_at=now,
            status_counts=tuple(
                OutboxStatusCount(status, counts.get(status, 0)) for status in OUTBOX_STATUSES
            ),
            oldest_pending_age_seconds=oldest_age,
            expired_claim_count=expired_claim_count,
            incompatible_schema_count=incompatible_schema_count,
            replay_request_count=replay_count,
            consumer_receipt_count=int(receipt_count),
            duplicate_delivery_count=int(duplicate_count),
            idempotency_issue_count=int(issue_count),
        )

    def get_replay_request(
        self,
        workspace_id: UUID,
        idempotency_key: str,
    ) -> OutboxReplayRequest | None:
        row = (
            self._session.execute(
                select(outbox_replay_requests).where(
                    outbox_replay_requests.c.workspace_id == workspace_id,
                    outbox_replay_requests.c.idempotency_key == idempotency_key,
                )
            )
            .mappings()
            .one_or_none()
        )
        return _replay_request(row) if row is not None else None

    def get_replay_source(
        self,
        workspace_id: UUID,
        event_id: UUID,
    ) -> OutboxOperationsRecord | None:
        row = (
            self._session.execute(
                select(outbox_events)
                .where(
                    outbox_events.c.workspace_id == workspace_id,
                    outbox_events.c.event_id == event_id,
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        return _outbox_record(row, replay_count=0) if row is not None else None

    def add_replay_request(self, request: OutboxReplayRequest) -> None:
        self._session.execute(
            insert(outbox_replay_requests).values(
                replay_request_id=request.replay_request_id,
                workspace_id=request.workspace_id,
                event_id=request.event_id,
                idempotency_key=request.idempotency_key,
                reason_code=request.reason_code,
                source_status=request.source_status,
                source_attempt_count=request.source_attempt_count,
                source_published_at=request.source_published_at,
                source_error_code=request.source_error_code,
                requested_by_actor_id=request.requested_by_actor_id,
                requested_by_user_id=request.requested_by_user_id,
                request_id=request.request_id,
                trace_id=request.trace_id,
                traceparent=request.traceparent,
                requested_at=request.requested_at,
            )
        )

    def requeue_event(
        self,
        *,
        workspace_id: UUID,
        event_id: UUID,
        source_status: Literal["published", "dead_letter"],
        available_at: datetime,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(outbox_events)
                .where(
                    outbox_events.c.workspace_id == workspace_id,
                    outbox_events.c.event_id == event_id,
                    outbox_events.c.status == source_status,
                )
                .values(
                    status="pending",
                    attempt_count=0,
                    available_at=available_at,
                    claimed_by=None,
                    claim_until=None,
                    last_error_code=None,
                    published_at=None,
                )
            ),
        )
        return result.rowcount == 1

    def _audit_cursor(self, workspace_id: UUID, audit_id: UUID) -> tuple[datetime, UUID]:
        row = self._session.execute(
            select(audit_records.c.occurred_at, audit_records.c.audit_id).where(
                audit_records.c.workspace_id == workspace_id,
                audit_records.c.audit_id == audit_id,
            )
        ).one_or_none()
        if row is None:
            raise IntegrationOperationsCursorError
        return row.occurred_at, row.audit_id

    def _outbox_cursor(self, workspace_id: UUID, event_id: UUID) -> tuple[datetime, UUID]:
        row = self._session.execute(
            select(outbox_events.c.occurred_at, outbox_events.c.event_id).where(
                outbox_events.c.workspace_id == workspace_id,
                outbox_events.c.event_id == event_id,
            )
        ).one_or_none()
        if row is None:
            raise IntegrationOperationsCursorError
        return row.occurred_at, row.event_id


class SqlAlchemyIntegrationOperationsUnitOfWork:
    """为查询提供短 Session，并原子提交重放请求、事件状态和审计。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyIntegrationOperationsRepository,
                SqlAlchemyAuditWriter,
                SqlAlchemyOutboxWriter,
            ]
            | None
        ] = ContextVar("integration_operations_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyIntegrationOperationsUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Integration Operations Unit of Work 不允许重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyIntegrationOperationsRepository(session),
                SqlAlchemyAuditWriter(session),
                SqlAlchemyOutboxWriter(session),
            )
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        state = self._state.get()
        if state is not None:
            if exc_type is not None:
                state[0].rollback()
            state[0].close()
            self._state.set(None)

    @property
    def operations(self) -> SqlAlchemyIntegrationOperationsRepository:
        return self._current()[1]

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        return self._current()[2]

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        return self._current()[3]

    def commit(self) -> None:
        try:
            self._current()[0].commit()
        except IntegrityError as error:
            self._current()[0].rollback()
            raise IntegrationOperationsWriteConflictError from error

    def _current(
        self,
    ) -> tuple[
        Session,
        SqlAlchemyIntegrationOperationsRepository,
        SqlAlchemyAuditWriter,
        SqlAlchemyOutboxWriter,
    ]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Integration Operations Unit of Work 尚未进入事务范围")
        return state


def _time_window(
    statement: Any,
    column: Any,
    occurred_from: datetime | None,
    occurred_to: datetime | None,
) -> Any:
    if occurred_from is not None:
        statement = statement.where(column >= occurred_from)
    if occurred_to is not None:
        statement = statement.where(column < occurred_to)
    return statement


def _incompatible_event_clause() -> Any:
    return or_(
        outbox_events.c.schema_version != 1,
        not_(outbox_events.c.event_type.op("~")(EVENT_TYPE_PATTERN)),
        not_(outbox_events.c.traceparent.op("~")(TRACEPARENT_PATTERN)),
        func.json_typeof(outbox_events.c.payload) != "object",
    )


def _audit_record(row: RowMapping) -> AuditOperationsRecord:
    return AuditOperationsRecord(
        audit_id=row["audit_id"],
        workspace_id=row["workspace_id"],
        actor_id=row["actor_id"],
        user_id=row["user_id"],
        action=row["action"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        outcome=cast(AuditOutcome, row["outcome"]),
        occurred_at=row["occurred_at"],
        request_id=row["request_id"],
        trace_id=row["trace_id"],
        permission_code=row["permission_code"],
        policy_decision_id=row["policy_decision_id"],
        policy_version=row["policy_version"],
        attributes=cast(dict[str, object], row["attributes"]),
    )


def _audit_export_request(row: RowMapping) -> AuditExportRequest:
    return AuditExportRequest(
        audit_export_request_id=row["audit_export_request_id"],
        workspace_id=row["workspace_id"],
        idempotency_key=row["idempotency_key"],
        request_hash=row["request_hash"],
        actor_id=row["actor_id"],
        action=row["action"],
        resource_type=row["resource_type"],
        outcome=cast(AuditOutcome | None, row["outcome"]),
        occurred_from=row["occurred_from"],
        occurred_to=row["occurred_to"],
        field_mask=frozenset(row["field_mask"]),
        requested_by_actor_id=row["requested_by_actor_id"],
        requested_by_user_id=row["requested_by_user_id"],
        request_id=row["request_id"],
        trace_id=row["trace_id"],
        traceparent=row["traceparent"],
        status=cast(Any, row["status"]),
        attempt_count=row["attempt_count"],
        last_error_code=row["last_error_code"],
        row_count=row["row_count"],
        result_sha256=row["result_sha256"],
        result_summary=row["result_summary"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def _outbox_record(
    row: RowMapping,
    *,
    replay_count: int | None = None,
) -> OutboxOperationsRecord:
    return OutboxOperationsRecord(
        event_id=row["event_id"],
        workspace_id=row["workspace_id"],
        event_type=row["event_type"],
        schema_version=row["schema_version"],
        aggregate_id=row["aggregate_id"],
        aggregate_version=row["aggregate_version"],
        occurred_at=row["occurred_at"],
        status=cast(OutboxStatus, row["status"]),
        attempt_count=row["attempt_count"],
        available_at=row["available_at"],
        claim_until=row["claim_until"],
        last_error_code=row["last_error_code"],
        published_at=row["published_at"],
        actor_id=row["actor_id"],
        user_id=row["user_id"],
        request_id=row["request_id"],
        trace_id=row["trace_id"],
        replay_count=(
            replay_count if replay_count is not None else int(row.get("replay_count", 0))
        ),
    )


def _replay_request(row: RowMapping) -> OutboxReplayRequest:
    return OutboxReplayRequest(
        replay_request_id=row["replay_request_id"],
        workspace_id=row["workspace_id"],
        event_id=row["event_id"],
        idempotency_key=row["idempotency_key"],
        reason_code=row["reason_code"],
        source_status=cast(Literal["published", "dead_letter"], row["source_status"]),
        source_attempt_count=row["source_attempt_count"],
        source_published_at=row["source_published_at"],
        source_error_code=row["source_error_code"],
        requested_by_actor_id=row["requested_by_actor_id"],
        requested_by_user_id=row["requested_by_user_id"],
        request_id=row["request_id"],
        trace_id=row["trace_id"],
        traceparent=row["traceparent"],
        requested_at=row["requested_at"],
    )
