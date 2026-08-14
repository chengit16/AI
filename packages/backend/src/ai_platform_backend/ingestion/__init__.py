"""API 与 Worker 共用的入库任务事实和持久化契约。"""

from ai_platform_backend.ingestion.domain import (
    IngestionFailureStage,
    IngestionJob,
    IngestionJobStatus,
    InvalidIngestionJobError,
)

__all__ = [
    "IngestionFailureStage",
    "IngestionJob",
    "IngestionJobStatus",
    "InvalidIngestionJobError",
]
