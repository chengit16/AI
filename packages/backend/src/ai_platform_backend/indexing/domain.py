"""定义 API 与 Worker 共用的索引版本、构建块和 Adapter 端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

IndexFailureStage = Literal["artifact", "chunk", "embedding", "index", "worker"]
IndexWorkerLane = Literal["embedding", "indexing"]
IndexFailureResult = Literal["retry_wait", "dead_letter", "lost_claim"]
IndexVisibility = Literal["private", "workspace", "departments"]
IndexSecurityLevel = Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]


@dataclass(frozen=True)
class ClaimedIndexVersion:
    """记录 Worker 已领取的索引版本、租约和构建所需文档事实。"""

    index_version_id: UUID
    job_attempt_id: UUID
    workspace_id: UUID
    knowledge_base_id: UUID
    document_id: UUID
    document_version_id: UUID
    ingestion_job_id: UUID
    source_id: UUID
    artifact_object_key: str
    source_content_hash: str
    parsed_content_hash: str
    attempt_count: int
    max_attempts: int
    claimed_by: str
    processing_lane: IndexWorkerLane
    chunker_version: str
    embedding_model_version: str
    tokenizer_version: str
    department_ids: tuple[UUID, ...]
    visibility: IndexVisibility
    security_level: IndexSecurityLevel
    permission_labels: tuple[str, ...]


@dataclass(frozen=True)
class BuiltIndexChunk:
    """保存索引构建后的规范文本、向量、权限元数据和来源位置。"""

    index_version_id: UUID
    chunk_id: UUID
    workspace_id: UUID
    knowledge_base_id: UUID
    document_id: UUID
    document_version_id: UUID
    ingestion_job_id: UUID
    source_id: UUID
    sequence_no: int
    content: str
    content_hash: str
    embedding: tuple[float, ...]
    keyword_text: str
    department_ids: tuple[UUID, ...]
    visibility: IndexVisibility
    security_level: IndexSecurityLevel
    permission_labels: tuple[str, ...]
    source_position: dict[str, int | None]
    parsed_content_hash: str
    parser_name: str
    ocr_used: bool


class IndexVersionStore(Protocol):
    """以租约领取待构建索引版本，并原子写回成功、重试或失败状态。"""

    def ensure_queued(
        self,
        *,
        now: datetime,
        max_attempts: int,
        chunker_version: str,
        embedding_model_version: str,
        tokenizer_version: str,
    ) -> int: ...

    def claim_embedding_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimedIndexVersion | None: ...

    def mark_embedding_succeeded(
        self,
        version: ClaimedIndexVersion,
        chunks: tuple[BuiltIndexChunk, ...],
        *,
        completed_at: datetime,
    ) -> bool: ...

    def claim_indexing_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimedIndexVersion | None: ...

    def mark_indexing_succeeded(
        self,
        version: ClaimedIndexVersion,
        *,
        completed_at: datetime,
    ) -> bool: ...

    def mark_failed(
        self,
        version: ClaimedIndexVersion,
        *,
        stage: IndexFailureStage,
        error_code: str,
        error_message: str,
        retryable: bool,
        failed_at: datetime,
        next_attempt_at: datetime,
    ) -> IndexFailureResult: ...


class IndexArtifactStorage(Protocol):
    """读取解析产物并原子替换某文档版本的全部索引分块。"""

    def read_artifact(self, version: ClaimedIndexVersion) -> bytes: ...


class EmbeddingAdapter(Protocol):
    """批量生成固定维度向量，输入顺序与输出顺序必须严格一致。"""

    model_version: str
    dimension: int

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...
