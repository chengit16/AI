from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import IntegrationEvent


class ConsumerUnitOfWork(Protocol):
    def __enter__(self) -> ConsumerUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def claim(
        self,
        consumer_name: str,
        event: IntegrationEvent,
        *,
        task_id: UUID,
        trace_id: str,
        traceparent: str,
        processed_at: datetime,
    ) -> bool: ...

    def apply_projection(self, event: IntegrationEvent, *, traceparent: str) -> None: ...

    def commit(self) -> None: ...


class IdempotentProjectionConsumer:
    """消费回执和投影在同一事务提交，重复任务不能重复产生业务副作用。"""

    def __init__(self, consumer_name: str, unit_of_work: ConsumerUnitOfWork) -> None:
        self._consumer_name = consumer_name
        self._unit_of_work = unit_of_work

    def handle(
        self,
        event: IntegrationEvent,
        *,
        task_id: UUID,
        trace_id: str,
        traceparent: str,
        processed_at: datetime,
    ) -> bool:
        with self._unit_of_work as unit_of_work:
            if not unit_of_work.claim(
                self._consumer_name,
                event,
                task_id=task_id,
                trace_id=trace_id,
                traceparent=traceparent,
                processed_at=processed_at,
            ):
                return False
            unit_of_work.apply_projection(event, traceparent=traceparent)
            unit_of_work.commit()
            return True
