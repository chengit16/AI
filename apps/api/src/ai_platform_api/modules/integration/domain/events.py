from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class IntegrationEvent:
    event_id: UUID
    event_type: str
    workspace_id: UUID
    aggregate_id: UUID
    aggregate_version: int
    occurred_at: datetime
    trace_id: str
    traceparent: str
    payload: dict[str, object]
    schema_version: int = 1


class OutboxWriter(Protocol):
    def add(self, event: IntegrationEvent) -> None: ...
