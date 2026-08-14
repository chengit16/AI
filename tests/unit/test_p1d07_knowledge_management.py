from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from ai_platform_backend.ingestion.domain import (
    IngestionJob,
    InvalidIngestionJobError,
    ManualIngestionRetryNotAllowedError,
)

NOW = datetime(2026, 8, 14, 15, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000307")
ACTOR_ID = UUID("10000000-0000-4000-8000-000000000307")


def failed_job(error_code: str = "INGESTION_PARSER_UNAVAILABLE") -> IngestionJob:
    return IngestionJob(
        ingestion_job_id=UUID("50000000-0000-4000-8000-000000000307"),
        workspace_id=WORKSPACE_ID,
        knowledge_base_id=UUID("30000000-0000-4000-8000-000000000307"),
        document_id=UUID("40000000-0000-4000-8000-000000000307"),
        document_version_id=UUID("41000000-0000-4000-8000-000000000307"),
        source_id=UUID("42000000-0000-4000-8000-000000000307"),
        source_name="synthetic.txt",
        source_object_key=f"workspaces/{WORKSPACE_ID}/uploads/synthetic.txt",
        source_media_type="text/plain",
        source_content_hash="a" * 64,
        status="failed",
        attempt_count=3,
        max_attempts=3,
        available_at=NOW,
        requested_by_actor_id=ACTOR_ID,
        trace_id="1" * 32,
        traceparent=f"00-{'1' * 32}-{'2' * 16}-01",
        created_at=NOW,
        updated_at=NOW,
        completed_at=NOW,
        failure_stage="parse",
        error_code=error_code,
        error_message="全合成失败事实",
    )


def test_transient_failure_opens_new_bounded_attempt_window() -> None:
    current = failed_job()
    current.assert_valid()

    retried = current.retry_manually(
        actor_id=ACTOR_ID,
        trace_id="3" * 32,
        traceparent=f"00-{'3' * 32}-{'4' * 16}-01",
        occurred_at=NOW,
    )

    assert retried.status == "queued"
    assert retried.attempt_count == 0
    assert retried.max_attempts == 3
    assert retried.manual_retry_count == 1
    assert retried.last_retried_by_actor_id == ACTOR_ID
    assert retried.completed_at is None
    assert retried.failure_stage is retried.error_code is retried.error_message is None
    assert retried.can_retry_manually is False


@pytest.mark.parametrize(
    "error_code",
    [
        "INGESTION_PARSE_FAILED",
        "INGESTION_EMPTY_CONTENT",
        "INGESTION_SOURCE_CHANGED",
        "INGESTION_UNSUPPORTED_FORMAT",
    ],
)
def test_permanent_content_failure_requires_new_document_version(error_code: str) -> None:
    current = failed_job(error_code)
    current.assert_valid()

    with pytest.raises(ManualIngestionRetryNotAllowedError):
        current.retry_manually(
            actor_id=ACTOR_ID,
            trace_id="3" * 32,
            traceparent=f"00-{'3' * 32}-{'4' * 16}-01",
            occurred_at=NOW,
        )


def test_manual_retry_counter_requires_complete_audit_metadata() -> None:
    invalid = replace(failed_job(), manual_retry_count=1)

    with pytest.raises(InvalidIngestionJobError):
        invalid.assert_valid()
