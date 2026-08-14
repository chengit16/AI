"""实现严格序号、同会话并发约束和保留期回放的 PostgreSQL Store。"""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import RowMapping, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.streaming.domain.errors import (
    ConversationBusyError,
    StreamCursorNotFoundError,
    StreamEventExpiredError,
    StreamReplayLimitExceededError,
    StreamRunClosedError,
    StreamRunNotFoundError,
)
from ai_platform_api.modules.streaming.domain.models import (
    SseEventType,
    StreamEvent,
    StreamPolicy,
    StreamReplay,
    StreamRun,
    StreamStore,
)
from ai_platform_api.persistence.tables import stream_events, stream_runs


def stream_run_from_row(row: RowMapping) -> StreamRun:
    """处理流式事件运行从数据库行，在基础设施边界维持稳定领域对象映射。"""

    return StreamRun(
        run_id=row["run_id"],
        workspace_id=row["workspace_id"],
        conversation_id=row["conversation_id"],
        message_id=row["message_id"],
        status=row["status"],
        last_sequence_no=row["last_sequence_no"],
        expires_at=row["expires_at"],
        final_payload=row["final_payload"],
    )


def stream_event_from_row(row: RowMapping) -> StreamEvent:
    """处理流式事件事件从数据库行，在基础设施边界维持稳定领域对象映射。"""

    return StreamEvent(
        event_id=row["event_id"],
        event_type=row["event_type"],
        workspace_id=row["workspace_id"],
        conversation_id=row["conversation_id"],
        message_id=row["message_id"],
        run_id=row["run_id"],
        sequence_no=row["sequence_no"],
        occurred_at=row["occurred_at"],
        expires_at=row["expires_at"],
        trace_id=row["trace_id"],
        traceparent=row["traceparent"],
        payload=row["payload"],
    )


class SqlAlchemyStreamStore(StreamStore):
    """原子维护 Run、严格递增事件序号、保留期和按工作空间隔离的游标回放。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def start_run(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        run_id: UUID,
        now: datetime,
        policy: StreamPolicy,
    ) -> StreamRun:
        values = {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "status": "active",
            "last_sequence_no": 0,
            "created_at": now,
            "updated_at": now,
            "expires_at": now + timedelta(seconds=policy.retention_seconds),
            "final_payload": None,
        }
        try:
            # 保存点只回滚冲突插入，避免把调用方外层事务一并置为 failed。
            with self._session.begin_nested():
                row = (
                    self._session.execute(insert(stream_runs).values(values).returning(stream_runs))
                    .mappings()
                    .one()
                )
        except IntegrityError as error:
            raise ConversationBusyError from error
        return stream_run_from_row(row)

    def append_event(
        self,
        run_id: UUID,
        event_id: UUID,
        event_type: SseEventType,
        trace_id: str,
        traceparent: str,
        payload: dict[str, object],
        now: datetime,
    ) -> StreamEvent:
        run_row = (
            self._session.execute(
                select(stream_runs).where(stream_runs.c.run_id == run_id).with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if run_row is None:
            raise StreamRunNotFoundError
        run = stream_run_from_row(run_row)
        if run.status != "active":
            raise StreamRunClosedError
        existing_row = (
            self._session.execute(
                select(stream_events).where(
                    stream_events.c.event_id == event_id,
                    stream_events.c.run_id == run_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing_row is not None:
            return stream_event_from_row(existing_row)
        sequence_no = run.last_sequence_no + 1
        self._session.execute(
            update(stream_runs)
            .where(stream_runs.c.run_id == run_id)
            .values(last_sequence_no=sequence_no, updated_at=now)
        )
        row = (
            self._session.execute(
                insert(stream_events)
                .values(
                    event_id=event_id,
                    workspace_id=run.workspace_id,
                    conversation_id=run.conversation_id,
                    message_id=run.message_id,
                    run_id=run.run_id,
                    event_type=event_type,
                    sequence_no=sequence_no,
                    occurred_at=now,
                    expires_at=run.expires_at,
                    trace_id=trace_id,
                    traceparent=traceparent,
                    payload=payload,
                )
                .returning(stream_events)
            )
            .mappings()
            .one()
        )
        return stream_event_from_row(row)

    def finish_run(
        self,
        run_id: UUID,
        status: Literal["completed", "failed", "cancelled"],
        final_payload: dict[str, object] | None,
        now: datetime,
    ) -> StreamRun:
        row = (
            self._session.execute(
                update(stream_runs)
                .where(stream_runs.c.run_id == run_id, stream_runs.c.status == "active")
                .values(status=status, final_payload=final_payload, updated_at=now)
                .returning(stream_runs)
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            return stream_run_from_row(row)
        exists = self._session.execute(
            select(stream_runs.c.run_id).where(stream_runs.c.run_id == run_id)
        ).scalar_one_or_none()
        if exists is None:
            raise StreamRunNotFoundError
        raise StreamRunClosedError

    def replay(
        self,
        workspace_id: UUID,
        run_id: UUID,
        last_event_id: UUID | None,
        now: datetime,
        policy: StreamPolicy,
    ) -> StreamReplay:
        run_row = (
            self._session.execute(
                select(stream_runs).where(
                    stream_runs.c.workspace_id == workspace_id,
                    stream_runs.c.run_id == run_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if run_row is None:
            raise StreamRunNotFoundError
        run = stream_run_from_row(run_row)
        if run.expires_at <= now:
            raise StreamEventExpiredError

        start_sequence = 0
        if last_event_id is not None:
            cursor = (
                self._session.execute(
                    select(stream_events).where(
                        stream_events.c.workspace_id == workspace_id,
                        stream_events.c.run_id == run_id,
                        stream_events.c.event_id == last_event_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if cursor is None:
                raise StreamCursorNotFoundError
            if cursor["expires_at"] <= now:
                raise StreamEventExpiredError
            start_sequence = cursor["sequence_no"]

        rows = (
            self._session.execute(
                select(stream_events)
                .where(
                    stream_events.c.workspace_id == workspace_id,
                    stream_events.c.run_id == run_id,
                    stream_events.c.sequence_no > start_sequence,
                    stream_events.c.expires_at > now,
                )
                .order_by(stream_events.c.sequence_no)
                .limit(policy.replay_limit_events + 1)
            )
            .mappings()
            .all()
        )
        if len(rows) > policy.replay_limit_events:
            raise StreamReplayLimitExceededError
        events = tuple(stream_event_from_row(row) for row in rows)
        if sum(event.encoded_size() for event in events) > policy.replay_limit_bytes:
            raise StreamReplayLimitExceededError
        return StreamReplay(
            events=events,
            final_run=run,
            snapshot_required=not events and run.final_payload is not None,
        )
