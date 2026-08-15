"""编排索引版本认领、证据构建、Embedding、原子切换和失败回写。"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from ai_platform_backend.indexing.domain import (
    BuiltIndexChunk,
    ClaimedIndexVersion,
    EmbeddingAdapter,
    IndexArtifactStorage,
    IndexVersionStore,
)
from ai_platform_backend.indexing.tokenization import keyword_document

from ai_platform_worker.modules.indexing.domain.errors import (
    EmbeddingUnavailableError,
    IndexBuildError,
    IndexProcessOutcome,
    IndexStorageUnavailableError,
)
from ai_platform_worker.modules.ingestion.application.chunking import StructuralChunker
from ai_platform_worker.modules.ingestion.domain.documents import (
    Chunk,
    DocumentIdentity,
    IngestionLimits,
    ParsedBlock,
    ParsedDocument,
    SourcePosition,
)


@dataclass(frozen=True)
class IndexBatchResult:
    """汇总一次索引领取批次的成功、重试、失败和跳过数量。"""

    enqueued: int = 0
    claimed: int = 0
    succeeded: int = 0
    retried: int = 0
    failed: int = 0
    lost_claims: int = 0


class IndexEmbeddingProcessor:
    """生成并持久化不可见 Chunk，索引发布权保留给独立 Indexing Lane。"""

    def __init__(
        self,
        store: IndexVersionStore,
        storage: IndexArtifactStorage,
        chunker: StructuralChunker,
        embedding: EmbeddingAdapter,
        limits: IngestionLimits,
        *,
        worker_id: str,
        lease_seconds: int,
        retry_base_seconds: int,
        max_attempts: int,
        chunker_version: str,
        tokenizer_version: str,
    ) -> None:
        self._store = store
        self._storage = storage
        self._chunker = chunker
        self._embedding = embedding
        self._limits = limits
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._retry_base_seconds = retry_base_seconds
        self._max_attempts = max_attempts
        self._chunker_version = chunker_version
        self._tokenizer_version = tokenizer_version

    def run_batch(self, *, limit: int, now: datetime | None = None) -> IndexBatchResult:
        current = now or datetime.now(UTC)
        enqueued = self._store.ensure_queued(
            now=current,
            max_attempts=self._max_attempts,
            chunker_version=self._chunker_version,
            embedding_model_version=self._embedding.model_version,
            tokenizer_version=self._tokenizer_version,
        )
        counts = {"claimed": 0, "succeeded": 0, "retried": 0, "failed": 0, "lost": 0}
        for _ in range(limit):
            version = self._store.claim_embedding_next(
                worker_id=self._worker_id,
                now=current,
                lease_seconds=self._lease_seconds,
            )
            if version is None:
                break
            counts["claimed"] += 1
            counts[self._process(version, now=current)] += 1
        return IndexBatchResult(
            enqueued=enqueued,
            claimed=counts["claimed"],
            succeeded=counts["succeeded"],
            retried=counts["retried"],
            failed=counts["failed"],
            lost_claims=counts["lost"],
        )

    def _process(
        self,
        version: ClaimedIndexVersion,
        *,
        now: datetime,
    ) -> IndexProcessOutcome:
        try:
            # 1. 读取并校验解析产物，再按冻结组件版本生成具有权限元数据的确定性 Chunk。
            artifact = self._read_artifact(version)
            document = _decode_artifact(version, artifact)
            chunks = self._chunker.chunk(
                document,
                DocumentIdentity(
                    workspace_id=version.workspace_id,
                    knowledge_base_id=version.knowledge_base_id,
                    document_id=version.document_id,
                    document_version_id=version.document_version_id,
                    index_version_id=version.index_version_id,
                    department_ids=version.department_ids,
                    visibility=version.visibility,
                    security_level=version.security_level,
                    permission_labels=version.permission_labels,
                ),
                self._limits,
            )
            if not chunks:
                raise IndexBuildError(
                    "INDEX_CHUNK_EMPTY",
                    "解析产物没有生成可索引 Chunk",
                    retryable=False,
                    stage="chunk",
                )
            # 2. Embedding 完成后只持久化不可见 Chunk；丢失租约不得覆盖新 Worker。
            built = self._embed(version, chunks)
            return (
                "succeeded"
                if self._store.mark_embedding_succeeded(version, built, completed_at=now)
                else "lost"
            )
        except IndexBuildError as error:
            return self._record_failure(
                version,
                stage=error.stage,
                error_code=error.code,
                error_message=str(error),
                retryable=error.retryable,
                now=now,
            )
        except Exception:
            # 未分类实现异常不得把堆栈、对象键或模型底层消息写入任务事实。
            return self._record_failure(
                version,
                stage="worker",
                error_code="INTERNAL_ERROR",
                error_message="索引 Worker 处理任务时发生内部错误",
                retryable=True,
                now=now,
            )

    def _read_artifact(self, version: ClaimedIndexVersion) -> bytes:
        try:
            payload = self._storage.read_artifact(version)
        except IndexStorageUnavailableError as error:
            raise IndexBuildError(
                "INDEX_ARTIFACT_UNAVAILABLE",
                "索引构建暂时无法读取解析产物",
                retryable=True,
                stage="artifact",
            ) from error
        if hashlib.sha256(payload).hexdigest() != version.parsed_content_hash:
            raise IndexBuildError(
                "INDEX_ARTIFACT_CHANGED",
                "解析产物摘要与入库事实不一致",
                retryable=False,
                stage="artifact",
            )
        return payload

    def _embed(
        self,
        version: ClaimedIndexVersion,
        chunks: tuple[Chunk, ...],
    ) -> tuple[BuiltIndexChunk, ...]:
        # 1. Adapter 版本和物理维度必须匹配索引契约，调用失败按可重试错误收敛。
        if (
            self._embedding.model_version != version.embedding_model_version
            or self._embedding.dimension != 1024
        ):
            raise IndexBuildError(
                "INDEX_EMBEDDING_INVALID",
                "Embedding Adapter 与索引版本契约不兼容",
                retryable=False,
                stage="embedding",
            )
        try:
            embeddings = self._embedding.embed(tuple(chunk.content for chunk in chunks))
        except EmbeddingUnavailableError as error:
            raise IndexBuildError(
                "INDEX_EMBEDDING_UNAVAILABLE",
                "Embedding Adapter 暂时不可用",
                retryable=True,
                stage="embedding",
            ) from error
        # 2. 返回数量、维度和数值全部验证后，才组装可持久化索引事实。
        if len(embeddings) != len(chunks) or any(
            len(vector) != self._embedding.dimension
            or any(not math.isfinite(value) for value in vector)
            for vector in embeddings
        ):
            raise IndexBuildError(
                "INDEX_EMBEDDING_INVALID",
                "Embedding Adapter 返回的数量、维度或数值无效",
                retryable=False,
                stage="embedding",
            )
        return tuple(
            BuiltIndexChunk(
                index_version_id=version.index_version_id,
                chunk_id=chunk.chunk_id,
                workspace_id=chunk.workspace_id,
                knowledge_base_id=chunk.knowledge_base_id,
                document_id=chunk.document_id,
                document_version_id=chunk.document_version_id,
                ingestion_job_id=version.ingestion_job_id,
                source_id=version.source_id,
                sequence_no=chunk.sequence_no,
                content=chunk.content,
                content_hash=chunk.content_hash,
                embedding=embedding,
                keyword_text=keyword_document(chunk.content),
                department_ids=chunk.department_ids,
                visibility=version.visibility,
                security_level=version.security_level,
                permission_labels=version.permission_labels,
                source_position={
                    "page_number": chunk.source_position.page_number,
                    "line_start": chunk.source_position.line_start,
                    "line_end": chunk.source_position.line_end,
                    "block_start": chunk.source_position.block_start,
                    "block_end": chunk.source_position.block_end,
                },
                parsed_content_hash=version.parsed_content_hash,
                parser_name=chunk.parser_name,
                ocr_used=chunk.ocr_used,
            )
            for chunk, embedding in zip(chunks, embeddings, strict=True)
        )

    def _record_failure(
        self,
        version: ClaimedIndexVersion,
        *,
        stage: str,
        error_code: str,
        error_message: str,
        retryable: bool,
        now: datetime,
    ) -> IndexProcessOutcome:
        from ai_platform_backend.indexing.domain import IndexFailureStage

        backoff = self._retry_base_seconds * 2 ** max(version.attempt_count - 1, 0)
        status = self._store.mark_failed(
            version,
            stage=cast(IndexFailureStage, stage),
            error_code=error_code,
            error_message=error_message[:1000],
            retryable=retryable,
            failed_at=now,
            next_attempt_at=now + timedelta(seconds=backoff),
        )
        outcomes: dict[str, IndexProcessOutcome] = {
            "retry_wait": "retried",
            "dead_letter": "failed",
            "lost_claim": "lost",
        }
        return outcomes[status]


class IndexCommitProcessor:
    """只提交已持久化的 Chunk 并切换索引指针，不执行解析或 Embedding。"""

    def __init__(
        self,
        store: IndexVersionStore,
        *,
        worker_id: str,
        lease_seconds: int,
        retry_base_seconds: int,
    ) -> None:
        self._store = store
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._retry_base_seconds = retry_base_seconds

    def run_batch(self, *, limit: int, now: datetime | None = None) -> IndexBatchResult:
        current = now or datetime.now(UTC)
        counts = {"claimed": 0, "succeeded": 0, "retried": 0, "failed": 0, "lost": 0}
        for _ in range(limit):
            version = self._store.claim_indexing_next(
                worker_id=self._worker_id,
                now=current,
                lease_seconds=self._lease_seconds,
            )
            if version is None:
                break
            counts["claimed"] += 1
            counts[self._commit(version, now=current)] += 1
        return IndexBatchResult(
            claimed=counts["claimed"],
            succeeded=counts["succeeded"],
            retried=counts["retried"],
            failed=counts["failed"],
            lost_claims=counts["lost"],
        )

    def _commit(self, version: ClaimedIndexVersion, *, now: datetime) -> IndexProcessOutcome:
        try:
            return (
                "succeeded"
                if self._store.mark_indexing_succeeded(version, completed_at=now)
                else "lost"
            )
        except Exception:
            # 底层事务异常只能形成稳定错误码；Broker 重投仍受租约身份和原子切换保护。
            backoff = self._retry_base_seconds * 2 ** max(version.attempt_count - 1, 0)
            status = self._store.mark_failed(
                version,
                stage="index",
                error_code="INDEX_COMMIT_FAILED",
                error_message="索引 Worker 提交索引时发生内部错误",
                retryable=True,
                failed_at=now,
                next_attempt_at=now + timedelta(seconds=backoff),
            )
            outcomes: dict[str, IndexProcessOutcome] = {
                "retry_wait": "retried",
                "dead_letter": "failed",
                "lost_claim": "lost",
            }
            return outcomes[status]


def _decode_artifact(version: ClaimedIndexVersion, payload: bytes) -> ParsedDocument:
    try:
        # 1. Schema 版本和五项任务身份必须与已认领索引版本完全一致。
        value = json.loads(payload)
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError
        for field, expected in (
            ("workspace_id", version.workspace_id),
            ("knowledge_base_id", version.knowledge_base_id),
            ("document_id", version.document_id),
            ("document_version_id", version.document_version_id),
            ("source_id", version.source_id),
        ):
            if value.get(field) != str(expected):
                raise ValueError
        blocks_value = value["blocks"]
        if not isinstance(blocks_value, list) or not blocks_value:
            raise ValueError
        # 2. 逐块恢复类型、正文和来源位置，不接受解析器输出的额外对象形态。
        blocks: list[ParsedBlock] = []
        for item in blocks_value:
            if not isinstance(item, dict) or item.get("block_type") not in {
                "heading",
                "paragraph",
                "table",
                "ocr",
            }:
                raise ValueError
            position = item.get("source_position")
            if not isinstance(position, dict) or not isinstance(item.get("text"), str):
                raise ValueError
            blocks.append(
                ParsedBlock(
                    block_type=item["block_type"],
                    text=item["text"],
                    source_position=SourcePosition(
                        page_number=_optional_int(position.get("page_number")),
                        line_start=_optional_int(position.get("line_start")),
                        line_end=_optional_int(position.get("line_end")),
                    ),
                )
            )
        # 3. 最后校验解析器元数据和 OCR 事实，再构造不可变 ParsedDocument。
        parser_name = value["parser_name"]
        media_type = value["media_type"]
        page_count = value["page_count"]
        used_ocr = value["used_ocr"]
        if (
            not isinstance(parser_name, str)
            or not parser_name
            or not isinstance(media_type, str)
            or not media_type
            or not isinstance(page_count, int)
            or page_count < 1
            or not isinstance(used_ocr, bool)
        ):
            raise ValueError
        metadata_value = value.get("metadata", {})
        if not isinstance(metadata_value, dict) or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in metadata_value.items()
        ):
            raise ValueError
        return ParsedDocument(
            media_type=media_type,
            parser_name=parser_name,
            page_count=page_count,
            used_ocr=used_ocr,
            blocks=tuple(blocks),
            metadata=cast(dict[str, str], metadata_value),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise IndexBuildError(
            "INDEX_ARTIFACT_INVALID",
            "解析产物不符合固定 Schema 或任务身份",
            retryable=False,
            stage="artifact",
        ) from error


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError
    return value
