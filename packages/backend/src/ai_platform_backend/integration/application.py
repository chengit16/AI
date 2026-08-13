from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ai_platform_backend.integration.domain import OutboxLeaseStore, TaskPublisher


@dataclass(frozen=True)
class DispatchResult:
    claimed: int
    published: int
    retried: int
    dead_lettered: int


class OutboxDispatcher:
    """以短租约认领 PostgreSQL 事实；发布与确认之间失败允许重复投递。"""

    def __init__(
        self,
        store: OutboxLeaseStore,
        publisher: TaskPublisher,
        *,
        worker_id: str,
        batch_size: int = 50,
        lease_seconds: int = 60,
        max_attempts: int = 5,
        base_retry_seconds: int = 5,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if batch_size < 1 or lease_seconds < 1 or max_attempts < 1 or base_retry_seconds < 1:
            raise ValueError("Outbox 调度参数必须为正整数")
        self._store = store
        self._publisher = publisher
        self._worker_id = worker_id
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._base_retry_seconds = base_retry_seconds
        self._clock = clock

    def dispatch_once(self) -> DispatchResult:
        now = self._clock()
        batch = self._store.claim_due(
            worker_id=self._worker_id,
            now=now,
            limit=self._batch_size,
            lease_seconds=self._lease_seconds,
            max_attempts=self._max_attempts,
        )
        published = retried = 0
        dead_lettered = batch.dead_lettered
        for lease in batch.events:
            try:
                self._publisher.publish(lease.event)
            except Exception as error:
                # 外部发布错误文本可能含 Broker 细节，只持久化稳定类型码和退避时间。
                error_code = f"PUBLISH_{type(error).__name__.upper()}"[:128]
                # 租约恢复也会增加尝试次数；限制指数可避免连续进程退出后产生越界时间。
                exponent = min(lease.attempt_count - 1, self._max_attempts - 1)
                delay = self._base_retry_seconds * (2**exponent)
                status = self._store.mark_failed(
                    event_id=lease.event.event_id,
                    worker_id=self._worker_id,
                    error_code=error_code,
                    next_attempt_at=now + timedelta(seconds=delay),
                    max_attempts=self._max_attempts,
                )
                if status == "dead_letter":
                    dead_lettered += 1
                elif status == "pending":
                    retried += 1
                continue
            if self._store.mark_published(
                event_id=lease.event.event_id,
                worker_id=self._worker_id,
                published_at=self._clock(),
            ):
                published += 1
        return DispatchResult(
            claimed=len(batch.events),
            published=published,
            retried=retried,
            dead_lettered=dead_lettered,
        )
