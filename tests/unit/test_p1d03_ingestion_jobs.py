from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from ai_platform_worker.modules.ingestion.application.ingest import ParseDocument
from ai_platform_worker.modules.ingestion.application.jobs import IngestionJobProcessor
from ai_platform_worker.modules.ingestion.domain.documents import (
    DocumentParser,
    IngestionLimits,
    ParsedBlock,
    ParsedDocument,
    SourcePosition,
)
from ai_platform_worker.modules.ingestion.domain.errors import (
    IngestionError,
    IngestionFailureStage,
)
from ai_platform_worker.modules.ingestion.domain.jobs import (
    ClaimedIngestionJob,
    IngestionStorageUnavailableError,
    ParsedArtifact,
)

NOW = datetime(2026, 8, 14, 10, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000303")
CONTENT = "合成入库内容".encode()


class StaticParser:
    def parse(self, **_: object) -> ParsedDocument:
        return ParsedDocument(
            media_type="text/plain",
            parser_name="synthetic-parser-v1",
            page_count=1,
            used_ocr=False,
            blocks=(ParsedBlock("paragraph", "合成入库内容", SourcePosition(line_start=1)),),
            metadata={"synthetic": "true"},
        )


class FailingOcrParser:
    def parse(self, **_: object) -> ParsedDocument:
        raise IngestionError(
            "INGESTION_PARSER_UNAVAILABLE",
            "合成 OCR 服务不可用",
            retryable=True,
            stage="ocr",
        )


@dataclass
class StaticRouter:
    parser: DocumentParser

    def parser_for(self, _: str) -> DocumentParser:
        return self.parser


@dataclass
class FakeStore:
    claimed: ClaimedIngestionJob | None
    completed: ParsedArtifact | None = None
    failure: tuple[IngestionFailureStage, str, str, bool, datetime] | None = None

    def claim_next(self, **_: object) -> ClaimedIngestionJob | None:
        claimed, self.claimed = self.claimed, None
        return claimed

    def mark_succeeded(
        self,
        _: ClaimedIngestionJob,
        artifact: ParsedArtifact,
        *,
        completed_at: datetime,
    ) -> bool:
        assert completed_at == NOW
        self.completed = artifact
        return True

    def mark_failed(
        self,
        _: ClaimedIngestionJob,
        *,
        stage: IngestionFailureStage,
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
    content: bytes = CONTENT
    fail_read: bool = False
    fail_write: bool = False
    artifacts: list[ParsedArtifact] = field(default_factory=list)

    def read_source(self, _: ClaimedIngestionJob) -> bytes:
        if self.fail_read:
            raise IngestionStorageUnavailableError
        return self.content

    def write_artifact(self, _: ClaimedIngestionJob, artifact: ParsedArtifact) -> None:
        if self.fail_write:
            raise IngestionStorageUnavailableError
        self.artifacts.append(artifact)


def job(*, source_hash: str | None = None, attempt_count: int = 1) -> ClaimedIngestionJob:
    return ClaimedIngestionJob(
        ingestion_job_id=UUID("50000000-0000-4000-8000-000000000303"),
        workspace_id=WORKSPACE_ID,
        knowledge_base_id=UUID("30000000-0000-4000-8000-000000000303"),
        document_id=UUID("40000000-0000-4000-8000-000000000303"),
        document_version_id=UUID("41000000-0000-4000-8000-000000000303"),
        source_id=UUID("42000000-0000-4000-8000-000000000303"),
        source_name="synthetic.txt",
        source_object_key=f"workspaces/{WORKSPACE_ID}/uploads/synthetic.txt",
        source_media_type="text/plain",
        source_content_hash=source_hash or hashlib.sha256(CONTENT).hexdigest(),
        attempt_count=attempt_count,
        max_attempts=3,
        claimed_by="synthetic-worker",
        trace_id="1" * 32,
        traceparent=f"00-{'1' * 32}-{'2' * 16}-01",
    )


def processor(
    store: FakeStore,
    storage: FakeStorage,
    parser: DocumentParser | None = None,
) -> IngestionJobProcessor:
    return IngestionJobProcessor(
        store,
        storage,
        ParseDocument(StaticRouter(parser or StaticParser())),
        IngestionLimits(1024, 10, 64, 8),
        worker_id="synthetic-worker",
        lease_seconds=120,
        retry_base_seconds=5,
    )


def test_success_writes_deterministic_private_artifact() -> None:
    store = FakeStore(job())
    storage = FakeStorage()

    result = processor(store, storage).run_batch(limit=1, now=NOW)

    assert (result.claimed, result.succeeded) == (1, 1)
    assert store.completed == storage.artifacts[0]
    artifact = storage.artifacts[0]
    assert artifact.object_key.startswith(f"workspaces/{WORKSPACE_ID}/parsed/")
    assert artifact.content_hash == hashlib.sha256(artifact.payload).hexdigest()
    payload = json.loads(artifact.payload)
    assert payload["schema_version"] == 1
    assert payload["blocks"][0]["text"] == "合成入库内容"
    assert "source_object_key" not in payload


def test_source_hash_mismatch_is_terminal_before_parser_or_artifact() -> None:
    store = FakeStore(job(source_hash="f" * 64))
    storage = FakeStorage()

    result = processor(store, storage).run_batch(limit=1, now=NOW)

    assert result.failed == 1
    assert not storage.artifacts
    assert store.failure is not None
    assert store.failure[:4] == (
        "source",
        "INGESTION_SOURCE_CHANGED",
        "来源对象摘要与上传安全事实不一致",
        False,
    )


def test_retryable_ocr_failure_uses_bounded_exponential_backoff() -> None:
    store = FakeStore(job(attempt_count=2))

    result = processor(store, FakeStorage(), FailingOcrParser()).run_batch(limit=1, now=NOW)

    assert result.retried == 1
    assert store.failure is not None
    assert store.failure[:4] == (
        "ocr",
        "INGESTION_PARSER_UNAVAILABLE",
        "合成 OCR 服务不可用",
        True,
    )
    assert (store.failure[4] - NOW).total_seconds() == 10


def test_storage_failure_does_not_expose_low_level_exception() -> None:
    store = FakeStore(job())

    result = processor(store, FakeStorage(fail_read=True)).run_batch(limit=1, now=NOW)

    assert result.retried == 1
    assert store.failure is not None
    assert store.failure[:4] == (
        "source",
        "INGESTION_SOURCE_UNAVAILABLE",
        "入库任务暂时无法读取来源对象",
        True,
    )
