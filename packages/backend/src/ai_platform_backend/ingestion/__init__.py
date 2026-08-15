"""API 与 Worker 共用的入库任务事实和持久化契约。"""

from ai_platform_backend.ingestion.domain import (
    IngestionAttemptStatus,
    IngestionAttemptTrigger,
    IngestionFailureStage,
    IngestionJob,
    IngestionJobAttempt,
    IngestionJobStage,
    IngestionJobStatus,
    IngestionStageKey,
    InvalidIngestionJobError,
)

__all__ = [
    "IngestionAttemptStatus",
    "IngestionAttemptTrigger",
    "IngestionFailureStage",
    "IngestionJob",
    "IngestionJobAttempt",
    "IngestionJobStage",
    "IngestionJobStatus",
    "IngestionStageKey",
    "InvalidIngestionJobError",
]
