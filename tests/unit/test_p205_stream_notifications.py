"""验证 P2-05 通知层只负责唤醒，失败不会改变 PostgreSQL 事实语义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import TracebackType
from uuid import UUID

import pytest
from ai_platform_api.config import Settings
from ai_platform_api.modules.streaming.application.service import TransactionalStreamService
from ai_platform_api.modules.streaming.domain.models import (
    StreamNotifier,
    StreamPolicy,
    StreamStore,
    StreamWakeupSubscription,
)

from tests.unit.test_p010_streaming import (
    CONVERSATION_ID,
    MESSAGE_ID,
    NOW,
    RUN_ID,
    TRACE_ID,
    TRACEPARENT,
    WORKSPACE_ID,
    FakeStreamStore,
)


@dataclass
class MemoryStreamUnitOfWork:
    """复用同一个内存 Store 模拟独立短事务，并记录显式提交次数。"""

    store: FakeStreamStore
    commits: int = 0

    @property
    def streams(self) -> StreamStore:
        return self.store

    def __enter__(self) -> MemoryStreamUnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1


@dataclass
class FakeSubscription(StreamWakeupSubscription):
    waits: list[float] = field(default_factory=list)
    closed: bool = False

    def wait(self, timeout_seconds: float) -> bool:
        self.waits.append(timeout_seconds)
        return True

    def close(self) -> None:
        self.closed = True


@dataclass
class RecordingNotifier(StreamNotifier):
    published: list[UUID] = field(default_factory=list)
    subscription: FakeSubscription = field(default_factory=FakeSubscription)
    closed: bool = False

    def publish(self, run_id: UUID) -> None:
        self.published.append(run_id)

    def subscribe(self, run_id: UUID) -> StreamWakeupSubscription:
        assert run_id == RUN_ID
        return self.subscription

    def close(self) -> None:
        self.closed = True


class FailingNotifier(StreamNotifier):
    """模拟 Valkey 不可用，确保瞬时通知故障不会污染业务返回值。"""

    def publish(self, run_id: UUID) -> None:
        del run_id
        raise ConnectionError("合成通知故障")

    def subscribe(self, run_id: UUID) -> StreamWakeupSubscription | None:
        del run_id
        raise ConnectionError("合成订阅故障")

    def close(self) -> None:
        return None


def test_committed_stream_changes_publish_only_run_wakeup() -> None:
    """创建、追加和终结都在提交后发布 Run 标识，不向通知接口传递事件正文。"""

    unit_of_work = MemoryStreamUnitOfWork(FakeStreamStore())
    notifier = RecordingNotifier()
    service = TransactionalStreamService(unit_of_work, StreamPolicy(), notifier)

    service.start_run(
        WORKSPACE_ID,
        CONVERSATION_ID,
        MESSAGE_ID,
        RUN_ID,
        now=NOW,
    )
    event = service.append(
        RUN_ID,
        "message.delta",
        TRACE_ID,
        TRACEPARENT,
        {"delta": "仅保存在事实库的合成正文"},
        now=NOW,
    )
    completed = service.finish(RUN_ID, "completed", {"text": "合成结果"}, now=NOW)
    subscription = service.subscribe(RUN_ID)
    service.close()

    assert event.sequence_no == 1
    assert completed.status == "completed"
    assert unit_of_work.commits == 3
    assert notifier.published == [RUN_ID, RUN_ID, RUN_ID]
    assert subscription is notifier.subscription
    assert notifier.closed is True


def test_notification_failure_preserves_committed_facts_and_replay() -> None:
    """通知发布和订阅同时失败时，调用方仍能从原 Run 回放且不会创建第二个 Run。"""

    store = FakeStreamStore()
    unit_of_work = MemoryStreamUnitOfWork(store)
    service = TransactionalStreamService(unit_of_work, StreamPolicy(), FailingNotifier())

    run = service.start_run(
        WORKSPACE_ID,
        CONVERSATION_ID,
        MESSAGE_ID,
        RUN_ID,
        now=NOW,
    )
    event = service.append(
        RUN_ID,
        "message.delta",
        TRACE_ID,
        TRACEPARENT,
        {"delta": "可恢复"},
        now=NOW,
    )
    replay = service.replay(WORKSPACE_ID, RUN_ID, None, now=NOW)

    assert service.subscribe(RUN_ID) is None
    assert run.run_id == RUN_ID
    assert [item.event_id for item in replay.events] == [event.event_id]
    assert store.start_calls == 1


@pytest.mark.parametrize("timeout_seconds", [0.01, 5.01])
def test_notification_connect_timeout_is_bounded(timeout_seconds: float) -> None:
    """拒绝过短或过长的连接超时，避免忙重试或通知故障长期阻塞事件提交。"""

    with pytest.raises(ValueError, match="SSE 通知连接超时"):
        Settings(
            environment="test",
            stream_notification_connect_timeout_seconds=timeout_seconds,
        )
