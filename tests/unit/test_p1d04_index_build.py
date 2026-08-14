from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from ai_platform_backend.indexing.domain import (
    BuiltIndexChunk,
    ClaimedIndexVersion,
    IndexFailureStage,
)
from ai_platform_backend.indexing.tokenization import TOKENIZER_VERSION
from ai_platform_worker.modules.indexing.application.build import IndexBuildProcessor
from ai_platform_worker.modules.indexing.domain.errors import IndexStorageUnavailableError
from ai_platform_worker.modules.indexing.infrastructure.embeddings import (
    DeterministicHashEmbeddingAdapter,
)
from ai_platform_worker.modules.ingestion.application.chunking import StructuralChunker
from ai_platform_worker.modules.ingestion.domain.documents import IngestionLimits

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000304")
KNOWLEDGE_BASE_ID = UUID("30000000-0000-4000-8000-000000000304")
DOCUMENT_ID = UUID("40000000-0000-4000-8000-000000000304")
DOCUMENT_VERSION_ID = UUID("41000000-0000-4000-8000-000000000304")
SOURCE_ID = UUID("42000000-0000-4000-8000-000000000304")
INGESTION_JOB_ID = UUID("50000000-0000-4000-8000-000000000304")


def artifact(**overrides: object) -> bytes:
    value: dict[str, object] = {
        "schema_version": 1,
        "workspace_id": str(WORKSPACE_ID),
        "knowledge_base_id": str(KNOWLEDGE_BASE_ID),
        "document_id": str(DOCUMENT_ID),
        "document_version_id": str(DOCUMENT_VERSION_ID),
        "source_id": str(SOURCE_ID),
        "media_type": "text/markdown",
        "parser_name": "markdown-structural-v1",
        "page_count": 1,
        "used_ocr": False,
        "metadata": {"synthetic": "true"},
        "blocks": [
            {
                "block_type": "heading",
                "text": "合成差旅制度",
                "source_position": {"page_number": 1, "line_start": 1, "line_end": 1},
            },
            {
                "block_type": "paragraph",
                "text": "差旅报销必须在三十天内提交。",
                "source_position": {"page_number": 1, "line_start": 2, "line_end": 2},
            },
        ],
    }
    value.update(overrides)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


@dataclass
class FakeStore:
    claimed: ClaimedIndexVersion | None
    completed: tuple[BuiltIndexChunk, ...] | None = None
    failure: tuple[IndexFailureStage, str, str, bool, datetime] | None = None
    enqueued: int = 1

    def ensure_queued(self, **_: object) -> int:
        return self.enqueued

    def claim_next(self, **_: object) -> ClaimedIndexVersion | None:
        value, self.claimed = self.claimed, None
        return value

    def mark_succeeded(
        self,
        _: ClaimedIndexVersion,
        chunks: tuple[BuiltIndexChunk, ...],
        *,
        completed_at: datetime,
    ) -> bool:
        assert completed_at == NOW
        self.completed = chunks
        return True

    def mark_failed(
        self,
        _: ClaimedIndexVersion,
        *,
        stage: IndexFailureStage,
        error_code: str,
        error_message: str,
        retryable: bool,
        failed_at: datetime,
        next_attempt_at: datetime,
    ) -> Literal["retry_wait", "failed", "lost_claim"]:
        assert failed_at == NOW
        self.failure = (stage, error_code, error_message, retryable, next_attempt_at)
        return "retry_wait" if retryable else "failed"


@dataclass
class FakeStorage:
    payload: bytes
    unavailable: bool = False
    reads: list[UUID] = field(default_factory=list)

    def read_artifact(self, version: ClaimedIndexVersion) -> bytes:
        self.reads.append(version.index_version_id)
        if self.unavailable:
            raise IndexStorageUnavailableError
        return self.payload


class InvalidEmbeddingAdapter:
    model_version = "deterministic-hash-1024-v1"
    dimension = 1024

    def embed(self, _: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return ((0.0,) * 12,)


def version(payload: bytes, *, attempt_count: int = 1) -> ClaimedIndexVersion:
    return ClaimedIndexVersion(
        index_version_id=INGESTION_JOB_ID,
        workspace_id=WORKSPACE_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        document_id=DOCUMENT_ID,
        document_version_id=DOCUMENT_VERSION_ID,
        ingestion_job_id=INGESTION_JOB_ID,
        source_id=SOURCE_ID,
        artifact_object_key=(
            f"workspaces/{WORKSPACE_ID}/parsed/{DOCUMENT_VERSION_ID}/{INGESTION_JOB_ID}.json"
        ),
        source_content_hash="1" * 64,
        parsed_content_hash=hashlib.sha256(payload).hexdigest(),
        attempt_count=attempt_count,
        max_attempts=3,
        claimed_by="synthetic-index-worker",
        chunker_version="structural-char-v1",
        embedding_model_version="deterministic-hash-1024-v1",
        tokenizer_version=TOKENIZER_VERSION,
        department_ids=(UUID("60000000-0000-4000-8000-000000000304"),),
        visibility="departments",
        security_level="CONFIDENTIAL",
        permission_labels=("finance", "synthetic"),
    )


def processor(
    store: FakeStore,
    storage: FakeStorage,
    embedding: object | None = None,
) -> IndexBuildProcessor:
    from typing import cast

    from ai_platform_backend.indexing.domain import EmbeddingAdapter

    return IndexBuildProcessor(
        store,
        storage,
        StructuralChunker(),
        cast(EmbeddingAdapter, embedding or DeterministicHashEmbeddingAdapter()),
        IngestionLimits(1024 * 1024, 10, 64, 8),
        worker_id="synthetic-index-worker",
        lease_seconds=120,
        retry_base_seconds=5,
        max_attempts=3,
        chunker_version="structural-char-v1",
        tokenizer_version=TOKENIZER_VERSION,
    )


def test_build_propagates_permission_and_traceability_metadata() -> None:
    payload = artifact()
    store = FakeStore(version(payload))

    result = processor(store, FakeStorage(payload)).run_batch(limit=1, now=NOW)

    assert (result.enqueued, result.claimed, result.succeeded) == (1, 1, 1)
    assert store.completed is not None
    assert len(store.completed) == 1
    chunk = store.completed[0]
    assert chunk.permission_labels == ("finance", "synthetic")
    assert chunk.department_ids == version(payload).department_ids
    assert chunk.ingestion_job_id == INGESTION_JOB_ID
    assert chunk.source_id == SOURCE_ID
    assert chunk.parsed_content_hash == hashlib.sha256(payload).hexdigest()
    assert "差旅" in chunk.keyword_text
    assert len(chunk.embedding) == 1024
    assert math.sqrt(sum(value * value for value in chunk.embedding)) == 1.0


def test_artifact_hash_mismatch_is_terminal_before_chunking() -> None:
    payload = artifact()
    changed = artifact(parser_name="changed-parser")
    store = FakeStore(version(payload))

    result = processor(store, FakeStorage(changed)).run_batch(limit=1, now=NOW)

    assert result.failed == 1
    assert store.completed is None
    assert store.failure is not None
    assert store.failure[:4] == (
        "artifact",
        "INDEX_ARTIFACT_CHANGED",
        "解析产物摘要与入库事实不一致",
        False,
    )


def test_artifact_identity_mismatch_is_terminal() -> None:
    payload = artifact(workspace_id=str(UUID(int=999)))
    store = FakeStore(version(payload))

    result = processor(store, FakeStorage(payload)).run_batch(limit=1, now=NOW)

    assert result.failed == 1
    assert store.failure is not None
    assert store.failure[0:2] == ("artifact", "INDEX_ARTIFACT_INVALID")


def test_invalid_embedding_contract_fails_closed() -> None:
    payload = artifact()
    store = FakeStore(version(payload))

    result = processor(store, FakeStorage(payload), InvalidEmbeddingAdapter()).run_batch(
        limit=1,
        now=NOW,
    )

    assert result.failed == 1
    assert store.failure is not None
    assert store.failure[0:2] == ("embedding", "INDEX_EMBEDDING_INVALID")


def test_storage_unavailable_uses_bounded_retry() -> None:
    payload = artifact()
    store = FakeStore(version(payload, attempt_count=2))

    result = processor(store, FakeStorage(payload, unavailable=True)).run_batch(limit=1, now=NOW)

    assert result.retried == 1
    assert store.failure is not None
    assert store.failure[:4] == (
        "artifact",
        "INDEX_ARTIFACT_UNAVAILABLE",
        "索引构建暂时无法读取解析产物",
        True,
    )
    assert (store.failure[4] - NOW).total_seconds() == 10


def test_local_embedding_is_deterministic_and_versioned() -> None:
    adapter = DeterministicHashEmbeddingAdapter()

    first, second, different = adapter.embed(("差旅报销", "差旅报销", "仓库盘点"))

    assert adapter.model_version == "deterministic-hash-1024-v1"
    assert first == second
    assert first != different
