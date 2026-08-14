"""编排流式 Run 创建、事件追加、结束和工作空间隔离回放。"""

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
        """创建流式运行及首个事件，序号从一开始且绑定唯一工作空间。"""

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
        """在同一运行内追加严格递增事件，终态后禁止继续写入。"""

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
        """写入终态事件和最终快照，使回放过期后仍可恢复最终结果。"""

        return self._store.finish_run(run_id, status, final_payload, now)

    def replay(
        self,
        workspace_id: UUID,
        run_id: UUID,
        last_event_id: UUID | None,
        *,
        now: datetime,
    ) -> StreamReplay:
        """校验工作空间、游标和保留期后回放事件，越界或过期时返回稳定错误。"""

        return self._store.replay(workspace_id, run_id, last_event_id, now, self._policy)
