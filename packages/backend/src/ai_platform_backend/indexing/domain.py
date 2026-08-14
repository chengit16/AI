from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

IndexFailureStage = Literal["artifact", "chunk", "embedding", "index", "worker"]
IndexFailureResult = Literal["retry_wait", "failed", "lost_claim"]
IndexVisibility = Literal["private", "workspace", "departments"]
IndexSecurityLevel = Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]


@dataclass(frozen=True)
class ClaimedIndexVersion:
    index_version_id: UUID
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
    chunker_version: str
    embedding_model_version: str
    tokenizer_version: str
    department_ids: tuple[UUID, ...]
    visibility: IndexVisibility
    security_level: IndexSecurityLevel
    permission_labels: tuple[str, ...]


@dataclass(frozen=True)
class BuiltIndexChunk:
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
    def ensure_queued(
        self,
        *,
        now: datetime,
        max_attempts: int,
        chunker_version: str,
        embedding_model_version: str,
        tokenizer_version: str,
    ) -> int: ...

    def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimedIndexVersion | None: ...

    def mark_succeeded(
        self,
        version: ClaimedIndexVersion,
        chunks: tuple[BuiltIndexChunk, ...],
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
    def read_artifact(self, version: ClaimedIndexVersion) -> bytes: ...


class EmbeddingAdapter(Protocol):
    model_version: str
    dimension: int

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...
