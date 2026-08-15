"""编排流式 Run 创建、事件追加、结束和工作空间隔离回放。"""

from contextlib import suppress
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from ai_platform_api.modules.streaming.domain.errors import StreamRunNotFoundError
from ai_platform_api.modules.streaming.domain.models import (
    SseEventType,
    StreamEvent,
    StreamNotifier,
    StreamPolicy,
    StreamReplay,
    StreamRun,
    StreamStore,
    StreamUnitOfWork,
    StreamWakeupSubscription,
)

__all__ = [
    "StreamEvent",
    "StreamReplay",
    "StreamRunNotFoundError",
    "StreamService",
    "TransactionalStreamService",
]


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


class TransactionalStreamService:
    """以独立短事务持久化或回放事件，供正式 HTTP SSE 与运行编排复用。"""

    def __init__(
        self,
        unit_of_work: StreamUnitOfWork,
        policy: StreamPolicy,
        notifier: StreamNotifier | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy = policy
        self._notifier = notifier

    def start_run(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        run_id: UUID,
        *,
        now: datetime,
    ) -> StreamRun:
        """在短事务中创建与 AssistantRun 共用标识的流恢复事实。"""

        with self._unit_of_work as unit_of_work:
            run = unit_of_work.streams.start_run(
                workspace_id,
                conversation_id,
                message_id,
                run_id,
                now,
                self._policy,
            )
            unit_of_work.commit()
        self._notify(run_id)
        return run

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
        """在短事务中幂等追加事件，数据库锁负责分配严格递增序号。"""

        with self._unit_of_work as unit_of_work:
            event = unit_of_work.streams.append_event(
                run_id,
                event_id or uuid4(),
                event_type,
                trace_id,
                traceparent,
                payload,
                now,
            )
            unit_of_work.commit()
        self._notify(run_id)
        return event

    def finish(
        self,
        run_id: UUID,
        status: Literal["completed", "failed", "cancelled"],
        final_payload: dict[str, object] | None,
        *,
        now: datetime,
    ) -> StreamRun:
        """在短事务中保存流终态和最终快照，供事件已确认后的恢复使用。"""

        with self._unit_of_work as unit_of_work:
            run = unit_of_work.streams.finish_run(run_id, status, final_payload, now)
            unit_of_work.commit()
        self._notify(run_id)
        return run

    def replay(
        self,
        workspace_id: UUID,
        run_id: UUID,
        last_event_id: UUID | None,
        *,
        now: datetime,
    ) -> StreamReplay:
        """用一次只读短事务回放游标后的事件，返回前即释放数据库 Session。"""

        with self._unit_of_work as unit_of_work:
            return unit_of_work.streams.replay(
                workspace_id,
                run_id,
                last_event_id,
                now,
                self._policy,
            )

    def subscribe(self, run_id: UUID) -> StreamWakeupSubscription | None:
        """订阅瞬时唤醒；不可用时返回空值，由调用方继续按 PostgreSQL 轮询恢复。"""

        if self._notifier is None:
            return None
        try:
            return self._notifier.subscribe(run_id)
        except Exception:
            # 通知层不是事实库，连接失败不能阻断已授权客户端从 PostgreSQL 恢复。
            return None

    def close(self) -> None:
        """释放进程级通知连接；数据库资源仍由应用容器单独管理。"""

        if self._notifier is not None:
            self._notifier.close()

    def _notify(self, run_id: UUID) -> None:
        """在数据库提交后尽力唤醒其他实例，失败时保留轮询兜底语义。"""

        if self._notifier is None:
            return
        # 事实已经提交，通知失败不得把成功写入伪装成业务失败或触发重复生成。
        with suppress(Exception):
            self._notifier.publish(run_id)
