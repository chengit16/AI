from types import TracebackType
from typing import Protocol

from ai_platform_api.modules.integration.domain.events import IntegrationEvent


class ConsumerUnitOfWork(Protocol):
    def __enter__(self) -> "ConsumerUnitOfWork": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def claim(self, consumer_name: str, event: IntegrationEvent) -> bool: ...

    def apply_projection(self, event: IntegrationEvent) -> None: ...

    def commit(self) -> None: ...


class IdempotentProjectionConsumer:
    """Claim 和业务投影在同一事务中完成，重复投递不会重复产生副作用。"""

    def __init__(self, consumer_name: str, unit_of_work: ConsumerUnitOfWork) -> None:
        self._consumer_name = consumer_name
        self._unit_of_work = unit_of_work

    def handle(self, event: IntegrationEvent) -> bool:
        with self._unit_of_work as unit_of_work:
            if not unit_of_work.claim(self._consumer_name, event):
                return False
            unit_of_work.apply_projection(event)
            unit_of_work.commit()
            return True
