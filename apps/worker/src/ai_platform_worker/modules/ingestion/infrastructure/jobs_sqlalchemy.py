"""实现入库任务租约、状态机和有限重试的 PostgreSQL Store。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, cast

from ai_platform_backend.ingestion.persistence import ingestion_jobs
from sqlalchemy import CursorResult, and_, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from ai_platform_worker.modules.ingestion.domain.errors import IngestionFailureStage
from ai_platform_worker.modules.ingestion.domain.jobs import (
    ClaimedIngestionJob,
    ParsedArtifact,
)

SessionFactory = sessionmaker[Session]


class SqlAlchemyIngestionJobStore:
    """用 PostgreSQL 行锁和租约提供至少一次执行，数据库始终是任务状态事实源。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimedIngestionJob | None:
        claim_until = now + timedelta(seconds=lease_seconds)
        with self._session_factory() as session, session.begin():
            # 1. 最后一次执行中进程退出没有异常回调，过期且耗尽次数的租约必须先终止。
            session.execute(
                update(ingestion_jobs)
                .where(
                    ingestion_jobs.c.status == "running",
                    ingestion_jobs.c.claim_until <= now,
                    ingestion_jobs.c.attempt_count >= ingestion_jobs.c.max_attempts,
                )
                .values(
                    status="failed",
                    claimed_by=None,
                    claim_until=None,
                    failure_stage="worker",
                    error_code="INGESTION_WORKER_LEASE_EXPIRED",
                    error_message="入库 Worker 在最大尝试次数内未完成任务",
                    completed_at=now,
                    updated_at=now,
                )
            )
            # 2. 使用 SKIP LOCKED 选择最早可执行任务，多 Worker 不能同时认领同一行。
            row = session.execute(
                select(ingestion_jobs)
                .where(
                    and_(
                        or_(
                            (ingestion_jobs.c.status == "queued")
                            & (ingestion_jobs.c.available_at <= now),
                            (ingestion_jobs.c.status == "retry_wait")
                            & (ingestion_jobs.c.available_at <= now),
                            (ingestion_jobs.c.status == "running")
                            & (ingestion_jobs.c.claim_until <= now),
                        ),
                        ingestion_jobs.c.attempt_count < ingestion_jobs.c.max_attempts,
                    )
                )
                .order_by(ingestion_jobs.c.available_at, ingestion_jobs.c.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).one_or_none()
            if row is None:
                return None
            # 3. 尝试次数和新租约在同一事务中推进，再返回冻结的来源与 Trace 事实。
            attempt_count = cast(int, row.attempt_count) + 1
            session.execute(
                update(ingestion_jobs)
                .where(ingestion_jobs.c.ingestion_job_id == row.ingestion_job_id)
                .values(
                    status="running",
                    attempt_count=attempt_count,
                    claimed_by=worker_id,
                    claim_until=claim_until,
                    started_at=row.started_at or now,
                    failure_stage=None,
                    error_code=None,
                    error_message=None,
                    updated_at=now,
                )
            )
            return ClaimedIngestionJob(
                ingestion_job_id=row.ingestion_job_id,
                workspace_id=row.workspace_id,
                knowledge_base_id=row.knowledge_base_id,
                document_id=row.document_id,
                document_version_id=row.document_version_id,
                source_id=row.source_id,
                source_name=row.source_name,
                source_object_key=row.source_object_key,
                source_media_type=row.source_media_type,
                source_content_hash=row.source_content_hash,
                attempt_count=attempt_count,
                max_attempts=row.max_attempts,
                claimed_by=worker_id,
                trace_id=row.trace_id,
                traceparent=row.traceparent,
            )

    def mark_succeeded(
        self,
        job: ClaimedIngestionJob,
        artifact: ParsedArtifact,
        *,
        completed_at: datetime,
    ) -> bool:
        document = artifact.document
        with self._session_factory() as session, session.begin():
            result = cast(
                CursorResult[object],
                session.execute(
                    update(ingestion_jobs)
                    .where(
                        ingestion_jobs.c.ingestion_job_id == job.ingestion_job_id,
                        ingestion_jobs.c.status == "running",
                        ingestion_jobs.c.claimed_by == job.claimed_by,
                    )
                    .values(
                        status="succeeded",
                        claimed_by=None,
                        claim_until=None,
                        completed_at=completed_at,
                        artifact_object_key=artifact.object_key,
                        parsed_content_hash=artifact.content_hash,
                        parser_name=document.parser_name,
                        ocr_used=document.used_ocr,
                        page_count=document.page_count,
                        block_count=len(document.blocks),
                        updated_at=completed_at,
                    )
                ),
            )
        return result.rowcount == 1

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
    ) -> Literal["retry_wait", "failed", "lost_claim"]:
        with self._session_factory() as session, session.begin():
            row = session.execute(
                select(ingestion_jobs.c.attempt_count, ingestion_jobs.c.max_attempts)
                .where(
                    ingestion_jobs.c.ingestion_job_id == job.ingestion_job_id,
                    ingestion_jobs.c.status == "running",
                    ingestion_jobs.c.claimed_by == job.claimed_by,
                )
                .with_for_update()
            ).one_or_none()
            if row is None:
                return "lost_claim"
            terminal = not retryable or row.attempt_count >= row.max_attempts
            status: Literal["retry_wait", "failed"] = "failed" if terminal else "retry_wait"
            session.execute(
                update(ingestion_jobs)
                .where(ingestion_jobs.c.ingestion_job_id == job.ingestion_job_id)
                .values(
                    status=status,
                    available_at=next_attempt_at,
                    claimed_by=None,
                    claim_until=None,
                    failure_stage=stage,
                    error_code=error_code,
                    error_message=error_message,
                    completed_at=failed_at if terminal else None,
                    updated_at=failed_at,
                )
            )
        return status
