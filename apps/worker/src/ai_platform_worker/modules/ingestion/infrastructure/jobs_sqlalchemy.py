"""实现入库任务租约、状态机和有限重试的 PostgreSQL Store。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal, cast
from uuid import uuid4

from ai_platform_backend.ingestion.persistence import (
    ingestion_job_attempts,
    ingestion_job_stages,
    ingestion_jobs,
)
from sqlalchemy import CursorResult, Row, and_, insert, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from ai_platform_worker.modules.ingestion.domain.errors import IngestionFailureStage
from ai_platform_worker.modules.ingestion.domain.jobs import (
    ClaimedIngestionJob,
    IngestionWorkerLane,
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
        lane: IngestionWorkerLane = "parsing",
    ) -> ClaimedIngestionJob | None:
        claim_until = now + timedelta(seconds=lease_seconds)
        with self._session_factory() as session, session.begin():
            # 1. 进程退出没有异常回调，先关闭过期 Attempt，再决定重试或稳定超时终态。
            _expire_stale_leases(session, now, lane=lane)
            # 2. 使用 SKIP LOCKED 选择最早可执行任务，多 Worker 不能同时认领同一行。
            row = _select_claimable_job(session, now, lane=lane)
            if row is None:
                return None
            # 3. 任务、阶段与新 Attempt 在同一事务推进，返回值冻结本次租约身份。
            return _claim_job(
                session,
                row,
                worker_id=worker_id,
                now=now,
                claim_until=claim_until,
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
            # 1. 先按 Attempt 与租约截止时间条件更新任务，任何迟到结果都不能覆盖稳定终态。
            result = cast(
                CursorResult[object],
                session.execute(
                    update(ingestion_jobs)
                    .where(
                        ingestion_jobs.c.ingestion_job_id == job.ingestion_job_id,
                        ingestion_jobs.c.status == "running",
                        ingestion_jobs.c.claimed_by == job.claimed_by,
                        ingestion_jobs.c.active_attempt_id == job.job_attempt_id,
                        ingestion_jobs.c.claim_until > completed_at,
                    )
                    .values(
                        status="succeeded",
                        claimed_by=None,
                        claim_until=None,
                        active_attempt_id=None,
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
            if result.rowcount != 1:
                return False
            # 2. 只有租约仍有效时才同步关闭 Attempt 和阶段，三类事实随事务一起提交。
            _finish_attempt(
                session,
                job,
                status="succeeded",
                completed_at=completed_at,
            )
            session.execute(
                update(ingestion_job_stages)
                .where(ingestion_job_stages.c.job_stage_id == job.job_stage_id)
                .values(
                    status="succeeded",
                    completed_at=completed_at,
                    failure_stage=None,
                    error_code=None,
                    error_message=None,
                    updated_at=completed_at,
                )
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
            # 1. 锁定仍有效的当前租约，租约过期、取消或被回收均统一返回失租结果。
            row = session.execute(
                select(ingestion_jobs.c.attempt_count, ingestion_jobs.c.max_attempts)
                .where(
                    ingestion_jobs.c.ingestion_job_id == job.ingestion_job_id,
                    ingestion_jobs.c.status == "running",
                    ingestion_jobs.c.claimed_by == job.claimed_by,
                    ingestion_jobs.c.active_attempt_id == job.job_attempt_id,
                    ingestion_jobs.c.claim_until > failed_at,
                )
                .with_for_update()
            ).one_or_none()
            if row is None:
                return "lost_claim"
            # 2. 根据错误可重放性和尝试上限一次性关闭任务、Attempt 与阶段事实。
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
                    active_attempt_id=None,
                    failure_stage=stage,
                    error_code=error_code,
                    error_message=error_message,
                    completed_at=failed_at if terminal else None,
                    updated_at=failed_at,
                )
            )
            _finish_attempt(
                session,
                job,
                status=status,
                completed_at=failed_at,
                stage=stage,
                error_code=error_code,
                error_message=error_message,
            )
            session.execute(
                update(ingestion_job_stages)
                .where(ingestion_job_stages.c.job_stage_id == job.job_stage_id)
                .values(
                    status=status,
                    completed_at=failed_at if terminal else None,
                    failure_stage=stage,
                    error_code=error_code,
                    error_message=error_message,
                    updated_at=failed_at,
                )
            )
        return status


def _expire_stale_leases(
    session: Session,
    now: datetime,
    *,
    lane: IngestionWorkerLane,
) -> None:
    """关闭最多一批过期租约，保留旧 Attempt 后再开放恢复认领。"""

    # 1. 有界锁定最早过期的任务，SKIP LOCKED 避免多个 Worker 互相阻塞回收扫描。
    rows = session.execute(
        select(ingestion_jobs)
        .where(
            ingestion_jobs.c.status == "running",
            ingestion_jobs.c.claim_until <= now,
            ingestion_jobs.c.processing_lane == lane,
        )
        .order_by(ingestion_jobs.c.claim_until, ingestion_jobs.c.created_at)
        .limit(100)
        .with_for_update(skip_locked=True)
    ).all()
    # 2. 每条租约先冻结旧 Attempt，再把任务和阶段推进到等待重试或稳定超时终态。
    for row in rows:
        terminal = row.attempt_count >= row.max_attempts
        status = "timed_out" if terminal else "retry_wait"
        message = "入库 Worker 在最大尝试次数内未完成任务"
        attempt_result = cast(
            CursorResult[object],
            session.execute(
                update(ingestion_job_attempts)
                .where(
                    ingestion_job_attempts.c.job_attempt_id == row.active_attempt_id,
                    ingestion_job_attempts.c.status == "running",
                )
                .values(
                    status="timed_out",
                    completed_at=now,
                    failure_stage="worker",
                    error_code="INGESTION_WORKER_LEASE_EXPIRED",
                    error_message=message,
                )
            ),
        )
        if attempt_result.rowcount != 1:
            raise RuntimeError("过期入库租约缺少唯一活动 Attempt")
        values = {
            "status": status,
            "available_at": now,
            "claimed_by": None,
            "claim_until": None,
            "active_attempt_id": None,
            "failure_stage": "worker",
            "error_code": "INGESTION_WORKER_LEASE_EXPIRED",
            "error_message": message,
            "completed_at": now if terminal else None,
            "updated_at": now,
        }
        session.execute(
            update(ingestion_jobs)
            .where(ingestion_jobs.c.ingestion_job_id == row.ingestion_job_id)
            .values(**values)
        )
        session.execute(
            update(ingestion_job_stages)
            .where(ingestion_job_stages.c.job_stage_id == row.ingestion_job_id)
            .values(
                status=status,
                completed_at=now if terminal else None,
                failure_stage="worker",
                error_code="INGESTION_WORKER_LEASE_EXPIRED",
                error_message=message,
                updated_at=now,
            )
        )


def _select_claimable_job(
    session: Session,
    now: datetime,
    *,
    lane: IngestionWorkerLane,
) -> Row[Any] | None:
    return session.execute(
        select(ingestion_jobs)
        .where(
            and_(
                or_(
                    (ingestion_jobs.c.status == "queued") & (ingestion_jobs.c.available_at <= now),
                    (ingestion_jobs.c.status == "retry_wait")
                    & (ingestion_jobs.c.available_at <= now),
                ),
                ingestion_jobs.c.attempt_count < ingestion_jobs.c.max_attempts,
                ingestion_jobs.c.processing_lane == lane,
            )
        )
        .order_by(ingestion_jobs.c.available_at, ingestion_jobs.c.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).one_or_none()


def _claim_job(
    session: Session,
    row: Row[Any],
    *,
    worker_id: str,
    now: datetime,
    claim_until: datetime,
) -> ClaimedIngestionJob:
    # 1. 生成本次不可复用的 Attempt 身份，并按当前 generation 计算尝试序号与触发来源。
    attempt_count = cast(int, row.attempt_count) + 1
    attempt_id = uuid4()
    trigger = _attempt_trigger(row)
    initiated_by = row.last_retried_by_actor_id or row.requested_by_actor_id
    # 2. 任务和阶段先进入运行态；事务回滚时不会留下缺少 Attempt 的半完成租约。
    session.execute(
        update(ingestion_jobs)
        .where(ingestion_jobs.c.ingestion_job_id == row.ingestion_job_id)
        .values(
            status="running",
            attempt_count=attempt_count,
            claimed_by=worker_id,
            claim_until=claim_until,
            active_attempt_id=attempt_id,
            started_at=row.started_at or now,
            completed_at=None,
            failure_stage=None,
            error_code=None,
            error_message=None,
            updated_at=now,
        )
    )
    # 3. 同步阶段并追加运行 Attempt，返回的冻结租约要求后续写回携带该 Attempt ID。
    session.execute(
        update(ingestion_job_stages)
        .where(ingestion_job_stages.c.job_stage_id == row.ingestion_job_id)
        .values(
            status="running",
            attempt_count=ingestion_job_stages.c.attempt_count + 1,
            started_at=ingestion_job_stages.c.started_at if row.started_at else now,
            completed_at=None,
            failure_stage=None,
            error_code=None,
            error_message=None,
            updated_at=now,
        )
    )
    session.execute(
        insert(ingestion_job_attempts).values(
            job_attempt_id=attempt_id,
            workspace_id=row.workspace_id,
            job_stage_id=row.ingestion_job_id,
            ingestion_job_id=row.ingestion_job_id,
            generation=row.manual_retry_count,
            attempt_no=attempt_count,
            trigger=trigger,
            status="running",
            worker_id=worker_id,
            initiated_by_actor_id=initiated_by,
            trace_id=row.trace_id,
            traceparent=row.traceparent,
            lease_started_at=now,
            lease_expires_at=claim_until,
            started_at=now,
            completed_at=None,
            failure_stage=None,
            error_code=None,
            error_message=None,
            created_at=now,
        )
    )
    return ClaimedIngestionJob(
        ingestion_job_id=row.ingestion_job_id,
        job_stage_id=row.ingestion_job_id,
        job_attempt_id=attempt_id,
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
        generation=row.manual_retry_count,
        max_attempts=row.max_attempts,
        claimed_by=worker_id,
        trace_id=row.trace_id,
        traceparent=row.traceparent,
    )


def _attempt_trigger(row: Row[Any]) -> str:
    if row.error_code == "INGESTION_WORKER_LEASE_EXPIRED":
        return "lease_recovery"
    if row.manual_retry_count > 0 and row.attempt_count == 0:
        return "manual_recovery"
    if row.status == "retry_wait":
        return "automatic_retry"
    return "automatic"


def _finish_attempt(
    session: Session,
    job: ClaimedIngestionJob,
    *,
    status: str,
    completed_at: datetime,
    stage: IngestionFailureStage | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    result = cast(
        CursorResult[object],
        session.execute(
            update(ingestion_job_attempts)
            .where(
                ingestion_job_attempts.c.job_attempt_id == job.job_attempt_id,
                ingestion_job_attempts.c.status == "running",
                ingestion_job_attempts.c.worker_id == job.claimed_by,
            )
            .values(
                status=status,
                completed_at=completed_at,
                failure_stage=stage,
                error_code=error_code,
                error_message=error_message,
            )
        ),
    )
    if result.rowcount != 1:
        raise RuntimeError("入库任务终态缺少唯一活动 Attempt")
