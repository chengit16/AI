from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
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
    actor_id: UUID | None = None
    user_id: UUID | None = None
    request_id: UUID | None = None
    schema_version: int = 1


@dataclass(frozen=True)
class AuditRecord:
    audit_id: UUID
    workspace_id: UUID
    actor_id: UUID
    user_id: UUID | None
    action: str
    resource_type: str
    resource_id: UUID
    outcome: str
    occurred_at: datetime
    request_id: UUID
    trace_id: str
    traceparent: str
    attributes: dict[str, object]


@dataclass(frozen=True)
class ClaimedOutboxEvent:
    event: IntegrationEvent
    attempt_count: int
    claimed_by: str
    claim_until: datetime


@dataclass(frozen=True)
class OutboxClaimBatch:
    events: tuple[ClaimedOutboxEvent, ...]
    dead_lettered: int


class OutboxWriter(Protocol):
    def add(self, event: IntegrationEvent) -> None: ...


class AuditWriter(Protocol):
    def add(self, record: AuditRecord) -> None: ...


class TaskPublisher(Protocol):
    def publish(self, event: IntegrationEvent) -> None: ...


class OutboxLeaseStore(Protocol):
    def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
        max_attempts: int,
    ) -> OutboxClaimBatch: ...

    def mark_published(
        self,
        *,
        event_id: UUID,
        worker_id: str,
        published_at: datetime,
    ) -> bool: ...

    def mark_failed(
        self,
        *,
        event_id: UUID,
        worker_id: str,
        error_code: str,
        next_attempt_at: datetime,
        max_attempts: int,
    ) -> Literal["pending", "dead_letter", "lost_claim"]: ...
