"""定义 SSE Run、持久化事件、回放结果和存储端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

SseEventType = Literal[
    "message.delta",
    "message.citation",
    "tool.status",
    "approval.required",
    "message.completed",
    "message.failed",
    "stream.heartbeat",
    "message.snapshot",
]
RunStatus = Literal["active", "completed", "failed", "cancelled"]


@dataclass(frozen=True)
class StreamPolicy:
    """阶段 0 固定的回放预算；事件保留期默认 24 小时。"""

    retention_seconds: int = 24 * 60 * 60
    replay_limit_events: int = 5_000
    replay_limit_bytes: int = 10 * 1024 * 1024

    def __post_init__(self) -> None:
        if min(self.retention_seconds, self.replay_limit_events, self.replay_limit_bytes) < 1:
            raise ValueError("SSE 保留期和回放上限必须为正整数")


@dataclass(frozen=True)
class StreamRun:
    """记录一次流式生成所属空间、会话、消息、终态和事件保留时间。"""

    run_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    message_id: UUID
    status: RunStatus
    last_sequence_no: int
    expires_at: datetime
    final_payload: dict[str, object] | None


@dataclass(frozen=True)
class StreamEvent:
    """保存 Run 内严格递增序号、稳定事件标识、追踪上下文和载荷。"""

    event_id: UUID
    event_type: SseEventType
    workspace_id: UUID
    conversation_id: UUID
    message_id: UUID
    run_id: UUID
    sequence_no: int
    occurred_at: datetime
    expires_at: datetime
    trace_id: str
    traceparent: str
    payload: dict[str, object]

    def encoded_size(self) -> int:
        # 采用稳定 ASCII 估算回放预算，避免不同 JSON 序列化器造成大幅差异。
        import json

        return len(
            json.dumps(
                {
                    "event_id": str(self.event_id),
                    "event_type": self.event_type,
                    "sequence_no": self.sequence_no,
                    "payload": self.payload,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )


@dataclass(frozen=True)
class StreamReplay:
    """返回游标后的事件、Run 状态及是否需要用最终快照恢复。"""

    events: tuple[StreamEvent, ...]
    final_run: StreamRun
    snapshot_required: bool


class StreamStore(Protocol):
    """原子维护 Run、事件序号、终态快照和按工作空间隔离的游标回放。"""

    def start_run(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        run_id: UUID,
        now: datetime,
        policy: StreamPolicy,
    ) -> StreamRun: ...

    def append_event(
        self,
        run_id: UUID,
        event_id: UUID,
        event_type: SseEventType,
        trace_id: str,
        traceparent: str,
        payload: dict[str, object],
        now: datetime,
    ) -> StreamEvent: ...

    def finish_run(
        self,
        run_id: UUID,
        status: Literal["completed", "failed", "cancelled"],
        final_payload: dict[str, object] | None,
        now: datetime,
    ) -> StreamRun: ...

    def replay(
        self,
        workspace_id: UUID,
        run_id: UUID,
        last_event_id: UUID | None,
        now: datetime,
        policy: StreamPolicy,
    ) -> StreamReplay: ...


class StreamUnitOfWork(Protocol):
    """为单次流事件操作提供短事务，禁止 SSE 长连接长期持有 Session。"""

    @property
    def streams(self) -> StreamStore: ...

    def __enter__(self) -> StreamUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class StreamWakeupSubscription(Protocol):
    """等待某个 Run 的无正文唤醒信号；超时或通知层故障均返回未命中。"""

    def wait(self, timeout_seconds: float) -> bool: ...

    def close(self) -> None: ...


class StreamNotifier(Protocol):
    """提供跨实例瞬时唤醒，不保存事件、游标或任何业务正文。"""

    def publish(self, run_id: UUID) -> None: ...

    def subscribe(self, run_id: UUID) -> StreamWakeupSubscription | None: ...

    def close(self) -> None: ...
