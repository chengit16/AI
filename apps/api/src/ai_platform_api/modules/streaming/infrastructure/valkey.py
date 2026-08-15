"""使用 Valkey Pub/Sub 唤醒跨实例 SSE 读取，事件事实仍只保存在 PostgreSQL。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import cast
from uuid import UUID

from redis import Redis
from redis.client import PubSub
from redis.exceptions import RedisError

from ai_platform_api.modules.streaming.domain.models import (
    StreamNotifier,
    StreamWakeupSubscription,
)

_CHANNEL_PREFIX = "ai-platform:sse-wakeup:v1"


class ValkeyStreamWakeupSubscription(StreamWakeupSubscription):
    """持有单条 SSE 连接的订阅；收到的载荷只表示数据库中可能已有新事实。"""

    def __init__(self, pubsub: PubSub) -> None:
        self._pubsub = pubsub
        self._available = True

    def wait(self, timeout_seconds: float) -> bool:
        """等待一次唤醒；通知丢失或连接失败时交由上层超时轮询 PostgreSQL。"""

        if not self._available:
            return False
        try:
            message = self._pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=max(0.0, timeout_seconds),
            )
            return message is not None and message.get("type") == "message"
        except RedisError:
            # 已建立的 SSE 连接不能因 Valkey 短暂中断而结束，后续统一走数据库兜底。
            self._available = False
            self.close()
            return False

    def close(self) -> None:
        self._available = False
        with suppress(RedisError):
            self._pubsub.close()


class ValkeyStreamNotifier(StreamNotifier):
    """只发布无正文提示，不把 Valkey 当作事件队列、游标存储或恢复事实库。"""

    def __init__(self, url: str, *, connect_timeout_seconds: float = 0.5) -> None:
        self._client = Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=connect_timeout_seconds,
            health_check_interval=30,
        )

    def publish(self, run_id: UUID) -> None:
        """发布固定载荷；消费者必须回查 PostgreSQL 才能取得事件和严格序号。"""

        try:
            self._client.publish(self.channel(run_id), "1")
        except RedisError:
            # 最佳努力通知不得改变已经提交的 PostgreSQL 事实。
            return

    def subscribe(self, run_id: UUID) -> StreamWakeupSubscription | None:
        """为单个 Run 建立隔离频道；订阅失败时显式返回空值启用轮询兜底。"""

        # redis-py 6.4 的 Pub/Sub 方法尚未暴露完整类型，边界处收窄为官方对象签名。
        pubsub_factory = cast(Callable[..., PubSub], self._client.pubsub)
        pubsub = pubsub_factory(ignore_subscribe_messages=True)
        try:
            subscribe = cast(Callable[[str], object], pubsub.subscribe)
            subscribe(self.channel(run_id))
            return ValkeyStreamWakeupSubscription(pubsub)
        except RedisError:
            pubsub.close()
            return None

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def channel(run_id: UUID) -> str:
        """频道只包含不可枚举的 Run 标识，不携带工作空间、问题或回答正文。"""

        return f"{_CHANNEL_PREFIX}:{run_id}"
