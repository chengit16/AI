"""验证 P2-02 入库取消、超时恢复和人工恢复上限。"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from ai_platform_backend.ingestion.domain import (
    MAX_MANUAL_RECOVERIES,
    IngestionJob,
    IngestionJobStatus,
    InvalidIngestionJobError,
    ManualIngestionRetryNotAllowedError,
)

NOW = datetime(2026, 8, 15, 13, 30, tzinfo=UTC)
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000202")
ACTOR_ID = UUID("10000000-0000-4000-8000-000000000202")


def queued_job() -> IngestionJob:
    return IngestionJob(
        ingestion_job_id=UUID("50000000-0000-4000-8000-000000000202"),
        workspace_id=WORKSPACE_ID,
        knowledge_base_id=UUID("30000000-0000-4000-8000-000000000202"),
        document_id=UUID("40000000-0000-4000-8000-000000000202"),
        document_version_id=UUID("41000000-0000-4000-8000-000000000202"),
        source_id=UUID("42000000-0000-4000-8000-000000000202"),
        source_name="synthetic-p202.txt",
        source_object_key=f"workspaces/{WORKSPACE_ID}/uploads/synthetic-p202.txt",
        source_media_type="text/plain",
        source_content_hash="a" * 64,
        status="queued",
        attempt_count=0,
        max_attempts=3,
        available_at=NOW,
        requested_by_actor_id=ACTOR_ID,
        trace_id="1" * 32,
        traceparent=f"00-{'1' * 32}-{'2' * 16}-01",
        created_at=NOW,
        updated_at=NOW,
    )


def timed_out_job(*, manual_retry_count: int = 0) -> IngestionJob:
    retried_by = ACTOR_ID if manual_retry_count else None
    retried_at = NOW if manual_retry_count else None
    return replace(
        queued_job(),
        status="timed_out",
        attempt_count=3,
        completed_at=NOW,
        failure_stage="worker",
        error_code="INGESTION_WORKER_LEASE_EXPIRED",
        error_message="合成租约耗尽",
        manual_retry_count=manual_retry_count,
        last_retried_by_actor_id=retried_by,
        last_retried_at=retried_at,
    )


def test_cancel_creates_stable_terminal_state_and_clears_execution_fields() -> None:
    running = replace(
        queued_job(),
        status="running",
        attempt_count=1,
        claimed_by="synthetic-worker",
        claim_until=NOW,
        active_attempt_id=UUID("51000000-0000-4000-8000-000000000202"),
        started_at=NOW,
    )
    running.assert_valid()

    cancelled = running.cancel(actor_id=ACTOR_ID, occurred_at=NOW)

    assert cancelled.status == "cancelled"
    assert cancelled.can_cancel is False
    assert cancelled.claimed_by is None
    assert cancelled.claim_until is None
    assert cancelled.cancelled_by_actor_id == ACTOR_ID
    assert cancelled.cancelled_at == cancelled.completed_at == NOW
    cancelled.assert_valid()


@pytest.mark.parametrize(
    ("cancelled_by_actor_id", "cancelled_at"),
    [(ACTOR_ID, None), (None, NOW)],
)
def test_partial_cancellation_metadata_is_rejected(
    cancelled_by_actor_id: UUID | None,
    cancelled_at: datetime | None,
) -> None:
    invalid = replace(
        queued_job(),
        cancelled_by_actor_id=cancelled_by_actor_id,
        cancelled_at=cancelled_at,
    )

    with pytest.raises(InvalidIngestionJobError):
        invalid.assert_valid()


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "timed_out"])
def test_terminal_job_cannot_be_cancelled(status: IngestionJobStatus) -> None:
    if status == "succeeded":
        terminal = replace(
            queued_job(),
            status="succeeded",
            completed_at=NOW,
            artifact_object_key=f"workspaces/{WORKSPACE_ID}/parsed/result.json",
            parsed_content_hash="b" * 64,
            parser_name="synthetic-parser",
            ocr_used=False,
            page_count=1,
            block_count=1,
        )
    elif status == "cancelled":
        terminal = queued_job().cancel(actor_id=ACTOR_ID, occurred_at=NOW)
    else:
        terminal = replace(timed_out_job(), status=status)
    terminal.assert_valid()

    with pytest.raises(InvalidIngestionJobError):
        terminal.cancel(actor_id=ACTOR_ID, occurred_at=NOW)


def test_timed_out_job_opens_new_generation_without_reusing_attempt_count() -> None:
    current = timed_out_job()
    current.assert_valid()

    recovered = current.retry_manually(
        actor_id=ACTOR_ID,
        trace_id="3" * 32,
        traceparent=f"00-{'3' * 32}-{'4' * 16}-01",
        occurred_at=NOW,
    )

    assert recovered.status == "queued"
    assert recovered.attempt_count == 0
    assert recovered.manual_retry_count == 1
    assert recovered.completed_at is None
    assert recovered.can_retry_manually is False


def test_manual_recovery_limit_is_finite() -> None:
    exhausted = timed_out_job(manual_retry_count=MAX_MANUAL_RECOVERIES)
    exhausted.assert_valid()

    assert exhausted.can_retry_manually is False
    with pytest.raises(ManualIngestionRetryNotAllowedError):
        exhausted.retry_manually(
            actor_id=ACTOR_ID,
            trace_id="3" * 32,
            traceparent=f"00-{'3' * 32}-{'4' * 16}-01",
            occurred_at=NOW,
        )
