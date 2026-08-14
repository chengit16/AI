"""向 Worker 模块公开共享的入库任务领域对象和持久化端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_worker.modules.ingestion.domain.documents import ParsedDocument
from ai_platform_worker.modules.ingestion.domain.errors import IngestionFailureStage


class IngestionStorageUnavailableError(Exception):
    """来源对象或解析产物存储当前不可用。"""


@dataclass(frozen=True)
class ClaimedIngestionJob:
    """保存由当前 Worker 租约保护的入库任务及领取标识。"""

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
    attempt_count: int
    max_attempts: int
    claimed_by: str
    trace_id: str
    traceparent: str


@dataclass(frozen=True)
class ParsedArtifact:
    """记录解析产物对象键、内容摘要、解析器版本和分块数量。"""

    object_key: str
    payload: bytes
    content_hash: str
    document: ParsedDocument


class IngestionJobStore(Protocol):
    """按租约领取入库任务，并在持有租约时写回完成、重试或失败状态。"""

    def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimedIngestionJob | None: ...

    def mark_succeeded(
        self,
        job: ClaimedIngestionJob,
        artifact: ParsedArtifact,
        *,
        completed_at: datetime,
    ) -> bool: ...

    def mark_failed(
        self,
        job: ClaimedIngestionJob,
        *,
        stage: IngestionFailureStage,
        error_code: str,
        error_message: str,
        retryable: bool,
        failed_at: datetime,
        next_attempt_at: datetime,
    ) -> Literal["retry_wait", "failed", "lost_claim"]: ...


class IngestionObjectStorage(Protocol):
    """读取来源对象并写入内容寻址解析产物，摘要不匹配时拒绝处理。"""

    def read_source(self, job: ClaimedIngestionJob) -> bytes: ...

    def write_artifact(self, job: ClaimedIngestionJob, artifact: ParsedArtifact) -> None: ...
