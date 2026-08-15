"""定义共享入库任务状态机、租约、有限重试和 Store 端口。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

IngestionJobStatus = Literal[
    "queued",
    "running",
    "retry_wait",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
]
IngestionFailureStage = Literal["source", "parse", "ocr", "artifact", "worker"]
IngestionLane = Literal["parsing", "ocr"]
IngestionStageKey = Literal["parsing", "ocr"]
IngestionAttemptStatus = Literal[
    "running",
    "succeeded",
    "retry_wait",
    "failed",
    "cancelled",
    "timed_out",
]
IngestionAttemptTrigger = Literal[
    "automatic",
    "automatic_retry",
    "lease_recovery",
    "manual_recovery",
    "legacy_backfill",
]

MAX_MANUAL_RECOVERIES = 3

OCR_SOURCE_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"})

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


def ingestion_lane_for_source(source_name: str) -> IngestionLane:
    """按不可变来源名称冻结处理 Lane，避免重试时漂移到另一类 Worker。"""

    return "ocr" if Path(source_name).suffix.lower() in OCR_SOURCE_EXTENSIONS else "parsing"


@dataclass(frozen=True)
class IngestionJob:
    """记录文档解析任务的租约、尝试次数、追踪上下文和终态。"""

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
    active_attempt_id: UUID | None = None
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
    cancelled_by_actor_id: UUID | None = None
    cancelled_at: datetime | None = None

    def assert_valid(self) -> None:
        # 1. 校验来源对象、摘要、Trace 和租约状态，所有定位都必须绑定当前工作空间。
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
            if (
                self.claimed_by is None
                or self.claim_until is None
                or self.active_attempt_id is None
                or self.started_at is None
            ):
                raise InvalidIngestionJobError
        elif any(
            value is not None
            for value in (self.claimed_by, self.claim_until, self.active_attempt_id)
        ):
            raise InvalidIngestionJobError
        # 2. 成功、失败和人工重试元数据必须与状态完整对应，不能保留半完成产物事实。
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
        if self.status in {"retry_wait", "failed", "timed_out"}:
            if self.failure_stage is None or not self.error_code or not self.error_message:
                raise InvalidIngestionJobError
        elif any(
            value is not None for value in (self.failure_stage, self.error_code, self.error_message)
        ):
            raise InvalidIngestionJobError
        # 3. 终态时间、取消证据和人工恢复次数必须成组出现，拒绝可误判的部分元数据。
        terminal_statuses = {"succeeded", "failed", "cancelled", "timed_out"}
        if self.status not in terminal_statuses and self.completed_at is not None:
            raise InvalidIngestionJobError
        if self.status in terminal_statuses and self.completed_at is None:
            raise InvalidIngestionJobError
        cancellation_metadata = (self.cancelled_by_actor_id, self.cancelled_at)
        if self.status == "cancelled" and (
            not all(value is not None for value in cancellation_metadata)
            or self.cancelled_at != self.completed_at
        ):
            raise InvalidIngestionJobError
        if self.status != "cancelled" and any(value is not None for value in cancellation_metadata):
            raise InvalidIngestionJobError
        retry_metadata_complete = (
            self.last_retried_by_actor_id is not None and self.last_retried_at is not None
        )
        if (
            not 0 <= self.manual_retry_count <= MAX_MANUAL_RECOVERIES
            or (self.manual_retry_count > 0) != retry_metadata_complete
        ):
            raise InvalidIngestionJobError

    @property
    def can_retry_manually(self) -> bool:
        return (
            self.status in {"failed", "timed_out"}
            and self.error_code in MANUALLY_RETRYABLE_ERROR_CODES
            and self.manual_retry_count < MAX_MANUAL_RECOVERIES
        )

    @property
    def can_cancel(self) -> bool:
        """只有尚未形成业务终态的任务允许取消。"""

        return self.status in {"queued", "running", "retry_wait"}

    def cancel(self, *, actor_id: UUID, occurred_at: datetime) -> IngestionJob:
        """把可执行任务转换为稳定取消终态，迟到 Worker 将因租约失效无法回写。"""

        if not self.can_cancel:
            raise InvalidIngestionJobError
        cancelled = replace(
            self,
            status="cancelled",
            claimed_by=None,
            claim_until=None,
            active_attempt_id=None,
            completed_at=occurred_at,
            failure_stage=None,
            error_code=None,
            error_message=None,
            updated_at=occurred_at,
            cancelled_by_actor_id=actor_id,
            cancelled_at=occurred_at,
        )
        cancelled.assert_valid()
        return cancelled

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
            active_attempt_id=None,
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
            cancelled_by_actor_id=None,
            cancelled_at=None,
        )
        retried.assert_valid()
        return retried


@dataclass(frozen=True)
class IngestionJobStage:
    """聚合入库阶段的当前运营状态，历史执行细节由 Attempt 保存。"""

    job_stage_id: UUID
    ingestion_job_id: UUID
    workspace_id: UUID
    stage_key: IngestionStageKey
    sequence_no: int
    status: IngestionJobStatus
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_stage: IngestionFailureStage | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class IngestionJobAttempt:
    """记录一次租约执行的触发来源、终态、错误和 Trace，终态后不可修改。"""

    job_attempt_id: UUID
    job_stage_id: UUID
    ingestion_job_id: UUID
    workspace_id: UUID
    generation: int
    attempt_no: int
    trigger: IngestionAttemptTrigger
    status: IngestionAttemptStatus
    worker_id: str
    initiated_by_actor_id: UUID
    trace_id: str
    traceparent: str
    lease_started_at: datetime
    lease_expires_at: datetime
    started_at: datetime
    created_at: datetime
    completed_at: datetime | None = None
    failure_stage: IngestionFailureStage | None = None
    error_code: str | None = None
    error_message: str | None = None


def _is_sha256(value: str | None) -> bool:
    return (
        value is not None
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
