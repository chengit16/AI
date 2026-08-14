"""编排入库任务租约认领、解析执行、有限重试和终态回写。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ai_platform_worker.modules.ingestion.application.ingest import ParseDocument
from ai_platform_worker.modules.ingestion.domain.documents import (
    IngestionLimits,
    ParsedDocument,
    ParseRequest,
)
from ai_platform_worker.modules.ingestion.domain.errors import (
    IngestionError,
    IngestionFailureStage,
)
from ai_platform_worker.modules.ingestion.domain.jobs import (
    ClaimedIngestionJob,
    IngestionJobStore,
    IngestionObjectStorage,
    IngestionStorageUnavailableError,
    ParsedArtifact,
)


@dataclass(frozen=True)
class IngestionBatchResult:
    """汇总一次入库领取批次的成功、重试、失败和跳过数量。"""

    claimed: int = 0
    succeeded: int = 0
    retried: int = 0
    failed: int = 0
    lost_claims: int = 0


class IngestionJobProcessor:
    """每次只在短事务中认领和落状态，解析、OCR 与对象访问均在事务外执行。"""

    def __init__(
        self,
        store: IngestionJobStore,
        storage: IngestionObjectStorage,
        parser: ParseDocument,
        limits: IngestionLimits,
        *,
        worker_id: str,
        lease_seconds: int,
        retry_base_seconds: int,
    ) -> None:
        self._store = store
        self._storage = storage
        self._parser = parser
        self._limits = limits
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._retry_base_seconds = retry_base_seconds

    def run_batch(self, *, limit: int, now: datetime | None = None) -> IngestionBatchResult:
        current = now or datetime.now(UTC)
        counts = {"claimed": 0, "succeeded": 0, "retried": 0, "failed": 0, "lost": 0}
        for _ in range(limit):
            job = self._store.claim_next(
                worker_id=self._worker_id,
                now=current,
                lease_seconds=self._lease_seconds,
            )
            if job is None:
                break
            counts["claimed"] += 1
            outcome = self._process(job, now=current)
            counts[outcome] += 1
        return IngestionBatchResult(
            claimed=counts["claimed"],
            succeeded=counts["succeeded"],
            retried=counts["retried"],
            failed=counts["failed"],
            lost_claims=counts["lost"],
        )

    def _process(
        self,
        job: ClaimedIngestionJob,
        *,
        now: datetime,
    ) -> str:
        try:
            # 1. 读取来源后复核上传摘要，再执行有界解析，来源变化属于不可重试失败。
            try:
                content = self._storage.read_source(job)
            except IngestionStorageUnavailableError as error:
                raise IngestionError(
                    "INGESTION_SOURCE_UNAVAILABLE",
                    "入库任务暂时无法读取来源对象",
                    retryable=True,
                    stage="source",
                ) from error
            if hashlib.sha256(content).hexdigest() != job.source_content_hash:
                raise IngestionError(
                    "INGESTION_SOURCE_CHANGED",
                    "来源对象摘要与上传安全事实不一致",
                    retryable=False,
                    stage="source",
                )
            document = self._parser.execute(
                ParseRequest(
                    file_name=job.source_name,
                    declared_media_type=job.source_media_type,
                    limits=self._limits,
                ),
                content,
            )
            # 2. 解析产物先写对象存储，再由数据库租约条件确认成功，避免暴露半完成任务。
            artifact = _artifact(job, document)
            try:
                self._storage.write_artifact(job, artifact)
            except IngestionStorageUnavailableError as error:
                raise IngestionError(
                    "INGESTION_ARTIFACT_UNAVAILABLE",
                    "入库任务暂时无法保存解析产物",
                    retryable=True,
                    stage="artifact",
                ) from error
            return (
                "succeeded"
                if self._store.mark_succeeded(job, artifact, completed_at=now)
                else "lost"
            )
        except IngestionError as error:
            return self._record_failure(
                job,
                stage=error.stage,
                error_code=error.code,
                error_message=str(error),
                retryable=error.retryable,
                now=now,
            )
        except Exception:
            # 未分类实现异常不得把堆栈或底层消息写入用户可见任务事实。
            return self._record_failure(
                job,
                stage="worker",
                error_code="INTERNAL_ERROR",
                error_message="入库 Worker 处理任务时发生内部错误",
                retryable=True,
                now=now,
            )

    def _record_failure(
        self,
        job: ClaimedIngestionJob,
        *,
        stage: IngestionFailureStage,
        error_code: str,
        error_message: str,
        retryable: bool,
        now: datetime,
    ) -> str:
        backoff = self._retry_base_seconds * 2 ** max(job.attempt_count - 1, 0)
        status = self._store.mark_failed(
            job,
            stage=stage,
            error_code=error_code,
            error_message=error_message[:1000],
            retryable=retryable,
            failed_at=now,
            next_attempt_at=now + timedelta(seconds=backoff),
        )
        return {"retry_wait": "retried", "failed": "failed", "lost_claim": "lost"}[status]


def _artifact(job: ClaimedIngestionJob, document: ParsedDocument) -> ParsedArtifact:
    body = {
        "schema_version": 1,
        "workspace_id": str(job.workspace_id),
        "knowledge_base_id": str(job.knowledge_base_id),
        "document_id": str(job.document_id),
        "document_version_id": str(job.document_version_id),
        "source_id": str(job.source_id),
        "media_type": document.media_type,
        "parser_name": document.parser_name,
        "page_count": document.page_count,
        "used_ocr": document.used_ocr,
        "metadata": dict(sorted(document.metadata.items())),
        "blocks": [
            {
                "block_type": block.block_type,
                "text": block.text,
                "source_position": {
                    "page_number": block.source_position.page_number,
                    "line_start": block.source_position.line_start,
                    "line_end": block.source_position.line_end,
                },
            }
            for block in document.blocks
        ],
    }
    payload = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return ParsedArtifact(
        object_key=(
            f"workspaces/{job.workspace_id}/parsed/"
            f"{job.document_version_id}/{job.ingestion_job_id}.json"
        ),
        payload=payload,
        content_hash=hashlib.sha256(payload).hexdigest(),
        document=document,
    )
