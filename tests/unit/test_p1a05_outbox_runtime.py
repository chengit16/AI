from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

import pytest
from ai_platform_backend.integration.application import OutboxDispatcher
from ai_platform_backend.integration.domain import (
    ClaimedOutboxEvent,
    IntegrationEvent,
    OutboxClaimBatch,
)
from ai_platform_backend.integration.envelope import (
    HmacTaskEnvelopeSigner,
    InvalidTaskEnvelopeError,
    SigningKeyFile,
)

NOW = datetime(2026, 8, 13, 8, 0, tzinfo=UTC)
EVENT_ID = UUID("40000000-0000-4000-8000-000000000021")
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000021")
AGGREGATE_ID = UUID("30000000-0000-4000-8000-000000000021")
TASK_ID = UUID("50000000-0000-4000-8000-000000000021")
TRACEPARENT = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"


def synthetic_event() -> IntegrationEvent:
    return IntegrationEvent(
        event_id=EVENT_ID,
        event_type="workspace.resource.created",
        workspace_id=WORKSPACE_ID,
        aggregate_id=AGGREGATE_ID,
        aggregate_version=1,
        occurred_at=NOW,
        trace_id="0123456789abcdef0123456789abcdef",
        traceparent=TRACEPARENT,
        actor_id=UUID("10000000-0000-4000-8000-000000000021"),
        user_id=UUID("10000000-0000-4000-8000-000000000021"),
        request_id=UUID("60000000-0000-4000-8000-000000000021"),
        payload={"synthetic": True},
    )


class MemoryLeaseStore:
    def __init__(
        self,
        attempt_count: int = 1,
        failure_status: Literal["pending", "dead_letter", "lost_claim"] | None = None,
    ) -> None:
        self.attempt_count = attempt_count
        self.failure_status = failure_status
        self.failed: list[tuple[str, datetime]] = []
        self.published = 0

    def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
        max_attempts: int,
    ) -> OutboxClaimBatch:
        return OutboxClaimBatch(
            events=(
                ClaimedOutboxEvent(
                    event=synthetic_event(),
                    attempt_count=self.attempt_count,
                    claimed_by=worker_id,
                    claim_until=now + timedelta(seconds=lease_seconds),
                ),
            ),
            dead_lettered=0,
        )

    def mark_published(
        self,
        *,
        event_id: UUID,
        worker_id: str,
        published_at: datetime,
    ) -> bool:
        self.published += 1
        return True

    def mark_failed(
        self,
        *,
        event_id: UUID,
        worker_id: str,
        error_code: str,
        next_attempt_at: datetime,
        max_attempts: int,
    ) -> Literal["pending", "dead_letter", "lost_claim"]:
        self.failed.append((error_code, next_attempt_at))
        if self.failure_status is not None:
            return self.failure_status
        return "dead_letter" if self.attempt_count >= max_attempts else "pending"


class RecordingPublisher:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.events: list[IntegrationEvent] = []

    def publish(self, event: IntegrationEvent) -> None:
        self.events.append(event)
        if self.error is not None:
            raise self.error


def test_task_envelope_signature_rejects_tampered_event() -> None:
    signer = HmacTaskEnvelopeSigner(b"s" * 32)
    envelope = signer.issue(
        task_name="platform.integration.consume.v1",
        task_id=TASK_ID,
        issued_at=NOW,
        event=synthetic_event(),
    ).to_dict()
    event = envelope["event"]
    assert isinstance(event, dict)
    event["workspace_id"] = "20000000-0000-4000-8000-000000000099"

    with pytest.raises(InvalidTaskEnvelopeError):
        signer.verify(envelope)


def test_task_envelope_roundtrip_keeps_subject_and_trace() -> None:
    signer = HmacTaskEnvelopeSigner(b"s" * 32)
    issued = signer.issue(
        task_name="platform.integration.consume.v1",
        task_id=TASK_ID,
        issued_at=NOW,
        event=synthetic_event(),
    )

    verified = signer.verify(issued.to_dict())

    assert verified.task_id == TASK_ID
    assert verified.event.actor_id == synthetic_event().actor_id
    assert verified.event.traceparent == TRACEPARENT


def test_signing_key_file_rejects_broad_permissions_and_symlink(tmp_path: Path) -> None:
    key_path = tmp_path / "task-signing.key"
    key_path.write_bytes(b"s" * 32)
    key_path.chmod(0o644)

    with pytest.raises(PermissionError):
        SigningKeyFile(str(key_path)).load()

    key_path.chmod(0o600)
    assert SigningKeyFile(str(key_path)).load() == b"s" * 32
    link_path = tmp_path / "task-signing-link.key"
    link_path.symlink_to(key_path)
    with pytest.raises(PermissionError):
        SigningKeyFile(str(link_path)).load()


def test_dispatcher_marks_success_after_publish() -> None:
    store = MemoryLeaseStore()
    publisher = RecordingPublisher()
    dispatcher = OutboxDispatcher(store, publisher, worker_id="synthetic-worker", clock=lambda: NOW)

    result = dispatcher.dispatch_once()

    assert result.claimed == 1
    assert result.published == 1
    assert store.published == 1
    assert publisher.events == [synthetic_event()]


def test_dispatcher_uses_bounded_backoff_and_dead_letters() -> None:
    store = MemoryLeaseStore(attempt_count=5)
    publisher = RecordingPublisher(RuntimeError("synthetic broker failure"))
    dispatcher = OutboxDispatcher(
        store,
        publisher,
        worker_id="synthetic-worker",
        max_attempts=5,
        base_retry_seconds=3,
        clock=lambda: NOW,
    )

    result = dispatcher.dispatch_once()

    assert result.dead_lettered == 1
    assert result.retried == 0
    assert store.failed == [("PUBLISH_RUNTIMEERROR", NOW + timedelta(seconds=48))]


def test_dispatcher_does_not_count_lost_claim_as_retry() -> None:
    store = MemoryLeaseStore(attempt_count=50, failure_status="lost_claim")
    dispatcher = OutboxDispatcher(
        store,
        RecordingPublisher(RuntimeError("synthetic late publisher failure")),
        worker_id="synthetic-worker",
        max_attempts=5,
        base_retry_seconds=3,
        clock=lambda: NOW,
    )

    result = dispatcher.dispatch_once()

    assert result.claimed == 1
    assert result.retried == 0
    assert result.dead_lettered == 0
    # 即使租约多次过期，退避也不会超过配置的最后一个自动重试窗口。
    assert store.failed == [("PUBLISH_RUNTIMEERROR", NOW + timedelta(seconds=48))]
