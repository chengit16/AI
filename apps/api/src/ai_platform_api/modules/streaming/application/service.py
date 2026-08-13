from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from ai_platform_api.modules.streaming.domain.models import (
    SseEventType,
    StreamEvent,
    StreamPolicy,
    StreamReplay,
    StreamRun,
    StreamStore,
)


class StreamService:
    """应用层只编排可信工作空间、Run 生命周期和事件回放预算。"""

    def __init__(self, store: StreamStore, policy: StreamPolicy) -> None:
        self._store = store
        self._policy = policy

    def start_run(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        *,
        run_id: UUID | None = None,
        now: datetime,
    ) -> StreamRun:
        return self._store.start_run(
            workspace_id,
            conversation_id,
            message_id,
            run_id or uuid4(),
            now,
            self._policy,
        )

    def append(
        self,
        run_id: UUID,
        event_type: SseEventType,
        trace_id: str,
        traceparent: str,
        payload: dict[str, object],
        *,
        event_id: UUID | None = None,
        now: datetime,
    ) -> StreamEvent:
        return self._store.append_event(
            run_id,
            event_id or uuid4(),
            event_type,
            trace_id,
            traceparent,
            payload,
            now,
        )

    def finish(
        self,
        run_id: UUID,
        status: Literal["completed", "failed", "cancelled"],
        final_payload: dict[str, object] | None,
        *,
        now: datetime,
    ) -> StreamRun:
        return self._store.finish_run(run_id, status, final_payload, now)

    def replay(
        self,
        workspace_id: UUID,
        run_id: UUID,
        last_event_id: UUID | None,
        *,
        now: datetime,
    ) -> StreamReplay:
        return self._store.replay(workspace_id, run_id, last_event_id, now, self._policy)
