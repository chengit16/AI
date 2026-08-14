from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal
from uuid import UUID

IngestionJobStatus = Literal["queued", "running", "retry_wait", "succeeded", "failed"]
IngestionFailureStage = Literal["source", "parse", "ocr", "artifact", "worker"]

MANUALLY_RETRYABLE_ERROR_CODES = frozenset(
    {
        "INGESTION_PARSER_UNAVAILABLE",
        "INGESTION_SOURCE_UNAVAILABLE",
        "INGESTION_ARTIFACT_UNAVAILABLE",
        "INGESTION_WORKER_LEASE_EXPIRED",
        "INTERNAL_ERROR",
    }
)


class InvalidIngestionJobError(Exception):
    """入库任务状态、来源快照或结果字段不满足事实约束。"""


class ManualIngestionRetryNotAllowedError(Exception):
    """任务未终止，或失败原因需要修复内容并上传新版本。"""


@dataclass(frozen=True)
class IngestionJob:
    ingestion_job_id: UUID
    workspace_id: UUID
    knowledge_base_id: UUID
    document_id: UUID
    document_version_id: UUID
    source_id: UUID
    source_name: str
    source_object_key: str
    source_media_type: str
    source_content_hash: str
    status: IngestionJobStatus
    attempt_count: int
    max_attempts: int
    available_at: datetime
    requested_by_actor_id: UUID
    trace_id: str
    traceparent: str
    created_at: datetime
    updated_at: datetime
    claimed_by: str | None = None
    claim_until: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_stage: IngestionFailureStage | None = None
    error_code: str | None = None
    error_message: str | None = None
    artifact_object_key: str | None = None
    parsed_content_hash: str | None = None
    parser_name: str | None = None
    ocr_used: bool | None = None
    page_count: int | None = None
    block_count: int | None = None
    manual_retry_count: int = 0
    last_retried_by_actor_id: UUID | None = None
    last_retried_at: datetime | None = None

    def assert_valid(self) -> None:
        source_prefix = f"workspaces/{self.workspace_id}/uploads/"
        artifact_prefix = f"workspaces/{self.workspace_id}/parsed/"
        if not self.source_name.strip() or len(self.source_name) > 255:
            raise InvalidIngestionJobError
        if not self.source_object_key.startswith(source_prefix):
            raise InvalidIngestionJobError
        if not self.source_media_type.strip() or self.attempt_count < 0 or self.max_attempts < 1:
            raise InvalidIngestionJobError
        if self.attempt_count > self.max_attempts:
            raise InvalidIngestionJobError
        if not _is_sha256(self.source_content_hash):
            raise InvalidIngestionJobError
        if len(self.trace_id) != 32 or len(self.traceparent) != 55:
            raise InvalidIngestionJobError
        if self.status == "running":
            if self.claimed_by is None or self.claim_until is None or self.started_at is None:
                raise InvalidIngestionJobError
        elif self.claimed_by is not None or self.claim_until is not None:
            raise InvalidIngestionJobError
        if self.status == "succeeded":
            if (
                self.completed_at is None
                or self.artifact_object_key is None
                or not self.artifact_object_key.startswith(artifact_prefix)
                or not _is_sha256(self.parsed_content_hash)
                or not self.parser_name
                or self.ocr_used is None
                or self.page_count is None
                or self.page_count < 1
                or self.block_count is None
                or self.block_count < 1
            ):
                raise InvalidIngestionJobError
        elif any(
            value is not None
            for value in (
                self.artifact_object_key,
                self.parsed_content_hash,
                self.parser_name,
                self.ocr_used,
                self.page_count,
                self.block_count,
            )
        ):
            raise InvalidIngestionJobError
        if self.status in {"retry_wait", "failed"}:
            if self.failure_stage is None or not self.error_code or not self.error_message:
                raise InvalidIngestionJobError
        elif any(
            value is not None for value in (self.failure_stage, self.error_code, self.error_message)
        ):
            raise InvalidIngestionJobError
        if self.status not in {"succeeded", "failed"} and self.completed_at is not None:
            raise InvalidIngestionJobError
        if self.status == "failed" and self.completed_at is None:
            raise InvalidIngestionJobError
        retry_metadata_complete = (
            self.last_retried_by_actor_id is not None and self.last_retried_at is not None
        )
        if self.manual_retry_count < 0 or (self.manual_retry_count > 0) != retry_metadata_complete:
            raise InvalidIngestionJobError

    @property
    def can_retry_manually(self) -> bool:
        return self.status == "failed" and self.error_code in MANUALLY_RETRYABLE_ERROR_CODES

    def retry_manually(
        self,
        *,
        actor_id: UUID,
        trace_id: str,
        traceparent: str,
        occurred_at: datetime,
    ) -> IngestionJob:
        """人工重试开启新的有限尝试窗口，同时保留累计人工重试事实。"""

        if not self.can_retry_manually:
            raise ManualIngestionRetryNotAllowedError
        retried = replace(
            self,
            status="queued",
            attempt_count=0,
            available_at=occurred_at,
            claimed_by=None,
            claim_until=None,
            requested_by_actor_id=actor_id,
            trace_id=trace_id,
            traceparent=traceparent,
            started_at=None,
            completed_at=None,
            failure_stage=None,
            error_code=None,
            error_message=None,
            updated_at=occurred_at,
            manual_retry_count=self.manual_retry_count + 1,
            last_retried_by_actor_id=actor_id,
            last_retried_at=occurred_at,
        )
        retried.assert_valid()
        return retried


def _is_sha256(value: str | None) -> bool:
    return (
        value is not None
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
