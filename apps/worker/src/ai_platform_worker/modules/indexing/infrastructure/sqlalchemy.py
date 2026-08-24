"""实现索引版本租约认领、切换、重试和失败记录的 PostgreSQL Store。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from ai_platform_backend.indexing.domain import (
    BuiltIndexChunk,
    ClaimedIndexVersion,
    IndexFailureResult,
    IndexFailureStage,
    IndexSecurityLevel,
    IndexVisibility,
    IndexWorkerLane,
)
from ai_platform_backend.indexing.facts import (
    document_publications,
    document_versions,
    documents,
)
from ai_platform_backend.indexing.persistence import index_versions, retrieval_chunks
from ai_platform_backend.indexing.sqlalchemy import (
    published_document_version_id,
    switch_active_document_index,
)
from ai_platform_backend.ingestion.persistence import ingestion_jobs
from sqlalchemy import Row, exists, func, insert, literal, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session, sessionmaker

SessionFactory = sessionmaker[Session]


class SqlAlchemyIndexVersionStore:
    """索引版本以 PostgreSQL 为事实源，外部计算不持有数据库事务。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def ensure_queued(
        self,
        *,
        now: datetime,
        max_attempts: int,
        chunker_version: str,
        embedding_model_version: str,
        tokenizer_version: str,
    ) -> int:
        # 1. 固定目标列集合，确保从成功入库任务复制的身份和权限元数据不会错位。
        columns = (
            "index_version_id",
            "workspace_id",
            "knowledge_base_id",
            "document_id",
            "document_version_id",
            "ingestion_job_id",
            "source_id",
            "build_no",
            "artifact_object_key",
            "source_content_hash",
            "parsed_content_hash",
            "chunker_version",
            "embedding_model_version",
            "tokenizer_version",
            "department_ids",
            "visibility",
            "security_level",
            "permission_labels",
            "status",
            "processing_lane",
            "attempt_count",
            "embedding_attempt_count",
            "indexing_attempt_count",
            "max_attempts",
            "available_at",
            "created_at",
            "updated_at",
            "manual_recovery_count",
        )
        # 2. 只选择成功入库且文档仍有效、尚未创建首个构建版本的来源事实。
        source = (
            select(
                ingestion_jobs.c.ingestion_job_id,
                ingestion_jobs.c.workspace_id,
                ingestion_jobs.c.knowledge_base_id,
                ingestion_jobs.c.document_id,
                ingestion_jobs.c.document_version_id,
                ingestion_jobs.c.ingestion_job_id,
                ingestion_jobs.c.source_id,
                literal(1, type_=index_versions.c.build_no.type),
                ingestion_jobs.c.artifact_object_key,
                ingestion_jobs.c.source_content_hash,
                ingestion_jobs.c.parsed_content_hash,
                literal(chunker_version, type_=index_versions.c.chunker_version.type),
                literal(
                    embedding_model_version,
                    type_=index_versions.c.embedding_model_version.type,
                ),
                literal(tokenizer_version, type_=index_versions.c.tokenizer_version.type),
                documents.c.department_ids,
                documents.c.visibility,
                documents.c.security_level,
                documents.c.permission_labels,
                literal("queued", type_=index_versions.c.status.type),
                literal("embedding", type_=index_versions.c.processing_lane.type),
                literal(0, type_=index_versions.c.attempt_count.type),
                literal(0, type_=index_versions.c.embedding_attempt_count.type),
                literal(0, type_=index_versions.c.indexing_attempt_count.type),
                literal(max_attempts, type_=index_versions.c.max_attempts.type),
                literal(now, type_=index_versions.c.available_at.type),
                literal(now, type_=index_versions.c.created_at.type),
                literal(now, type_=index_versions.c.updated_at.type),
                literal(0, type_=index_versions.c.manual_recovery_count.type),
            )
            .select_from(
                ingestion_jobs.join(
                    documents,
                    (documents.c.workspace_id == ingestion_jobs.c.workspace_id)
                    & (documents.c.document_id == ingestion_jobs.c.document_id),
                )
            )
            .where(
                ingestion_jobs.c.status == "succeeded",
                # 尚未回写解析产物键的任务不能进入索引队列，避免向非空 Artifact 字段写入空值。
                # 回收链仍可独立清理这类任务，确保解析和清理的最终一致性不互相阻断。
                ingestion_jobs.c.artifact_object_key.is_not(None),
                documents.c.status == "active",
                ~exists().where(
                    index_versions.c.ingestion_job_id == ingestion_jobs.c.ingestion_job_id,
                    index_versions.c.build_no == 1,
                ),
            )
        )
        # 3. 以入库任务和 build_no 幂等插入，重复调度只返回本次真正创建的数量。
        statement = postgresql_insert(index_versions).from_select(columns, source)
        with self._session_factory() as session, session.begin():
            queued_ids = session.scalars(
                statement.on_conflict_do_nothing(
                    index_elements=[
                        index_versions.c.ingestion_job_id,
                        index_versions.c.build_no,
                    ]
                ).returning(index_versions.c.index_version_id)
            ).all()
        return len(queued_ids)

    def enqueue_rebuild(
        self,
        *,
        workspace_id: UUID,
        document_version_id: UUID,
        now: datetime,
        max_attempts: int,
        chunker_version: str,
        embedding_model_version: str,
        tokenizer_version: str,
    ) -> UUID:
        with self._session_factory() as session, session.begin():
            # 1. 锁定成功入库任务和当前文档权限快照，防止重建期间来源事实变化。
            source = (
                session.execute(
                    # 显式选择并标记列，避免两张事实表的同名列在映射中产生歧义。
                    select(
                        ingestion_jobs.c.ingestion_job_id.label("ingestion_job_id"),
                        ingestion_jobs.c.workspace_id.label("workspace_id"),
                        ingestion_jobs.c.knowledge_base_id.label("knowledge_base_id"),
                        ingestion_jobs.c.document_id.label("document_id"),
                        ingestion_jobs.c.document_version_id.label("document_version_id"),
                        ingestion_jobs.c.source_id.label("source_id"),
                        ingestion_jobs.c.artifact_object_key.label("artifact_object_key"),
                        ingestion_jobs.c.source_content_hash.label("source_content_hash"),
                        ingestion_jobs.c.parsed_content_hash.label("parsed_content_hash"),
                        documents.c.department_ids.label("department_ids"),
                        documents.c.visibility.label("visibility"),
                        documents.c.security_level.label("security_level"),
                        documents.c.permission_labels.label("permission_labels"),
                    )
                    .select_from(
                        ingestion_jobs.join(
                            documents,
                            (documents.c.workspace_id == ingestion_jobs.c.workspace_id)
                            & (documents.c.document_id == ingestion_jobs.c.document_id),
                        )
                    )
                    .where(
                        ingestion_jobs.c.workspace_id == workspace_id,
                        ingestion_jobs.c.document_version_id == document_version_id,
                        ingestion_jobs.c.status == "succeeded",
                        documents.c.status == "active",
                    )
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            # 2. 在锁内计算下一 build_no，使同一入库任务的重建版本严格递增。
            build_no = (
                int(
                    session.scalar(
                        select(func.coalesce(func.max(index_versions.c.build_no), 0)).where(
                            index_versions.c.ingestion_job_id == source["ingestion_job_id"]
                        )
                    )
                    or 0
                )
                + 1
            )
            # 3. 新版本冻结组件版本、来源摘要和权限元数据，创建后保持 queued 不可见。
            index_version_id = uuid4()
            session.execute(
                insert(index_versions).values(
                    index_version_id=index_version_id,
                    workspace_id=source["workspace_id"],
                    knowledge_base_id=source["knowledge_base_id"],
                    document_id=source["document_id"],
                    document_version_id=source["document_version_id"],
                    ingestion_job_id=source["ingestion_job_id"],
                    source_id=source["source_id"],
                    build_no=build_no,
                    artifact_object_key=source["artifact_object_key"],
                    source_content_hash=source["source_content_hash"],
                    parsed_content_hash=source["parsed_content_hash"],
                    chunker_version=chunker_version,
                    embedding_model_version=embedding_model_version,
                    tokenizer_version=tokenizer_version,
                    department_ids=source["department_ids"],
                    visibility=source["visibility"],
                    security_level=source["security_level"],
                    permission_labels=source["permission_labels"],
                    status="queued",
                    processing_lane="embedding",
                    attempt_count=0,
                    embedding_attempt_count=0,
                    indexing_attempt_count=0,
                    max_attempts=max_attempts,
                    available_at=now,
                    created_at=now,
                    updated_at=now,
                    manual_recovery_count=0,
                )
            )
        return index_version_id

    def claim_embedding_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimedIndexVersion | None:
        """只领取 Artifact、Chunk 与 Embedding 阶段，索引写入由另一 Lane 完成。"""

        return self._claim_next(
            lane="embedding",
            worker_id=worker_id,
            now=now,
            lease_seconds=lease_seconds,
        )

    def claim_indexing_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimedIndexVersion | None:
        """只领取已有持久化 Chunk 的索引发布阶段，不执行 Embedding。"""

        return self._claim_next(
            lane="indexing",
            worker_id=worker_id,
            now=now,
            lease_seconds=lease_seconds,
        )

    def _claim_next(
        self,
        *,
        lane: IndexWorkerLane,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimedIndexVersion | None:
        claim_until = now + timedelta(seconds=lease_seconds)
        with self._session_factory() as session, session.begin():
            # 1. 崩溃没有回调，先把本 Lane 的过期租约收敛到重试或死信。
            _expire_stale_index_leases(session, lane=lane, now=now)
            # 2. SKIP LOCKED 与持久化 Lane 共同保证不同资源池不会领取同一任务。
            row = _select_claimable_index(session, lane=lane, now=now)
            if row is None:
                return None
            return _claim_index(
                session,
                row,
                lane=lane,
                worker_id=worker_id,
                now=now,
                claim_until=claim_until,
            )

    def mark_embedding_succeeded(
        self,
        version: ClaimedIndexVersion,
        chunks: tuple[BuiltIndexChunk, ...],
        *,
        completed_at: datetime,
    ) -> bool:
        with self._session_factory() as session, session.begin():
            if not _owns_index_lease(
                session,
                version,
                lane="embedding",
                completed_at=completed_at,
            ):
                return False
            # Chunk 先以不可见形式持久化，Embedding Worker 不拥有发布写入权。
            _upsert_staged_chunks(session, chunks, created_at=completed_at)
            session.execute(
                update(index_versions)
                .where(index_versions.c.index_version_id == version.index_version_id)
                .values(
                    status="index_queued",
                    processing_lane="indexing",
                    attempt_count=0,
                    available_at=completed_at,
                    claimed_by=None,
                    claim_until=None,
                    active_attempt_id=None,
                    staged_chunk_count=len(chunks),
                    failure_stage=None,
                    error_code=None,
                    error_message=None,
                    updated_at=completed_at,
                )
            )
        return True

    def mark_indexing_succeeded(
        self,
        version: ClaimedIndexVersion,
        *,
        completed_at: datetime,
    ) -> bool:
        with self._session_factory() as session, session.begin():
            # 1. 保持“发布指针 → 索引版本”锁顺序，避免并发发布和重放互相覆盖。
            session.execute(
                select(document_publications.c.current_document_version_id)
                .where(
                    document_publications.c.workspace_id == version.workspace_id,
                    document_publications.c.document_id == version.document_id,
                )
                .with_for_update()
            ).one_or_none()
            row = _owned_index_row(
                session,
                version,
                lane="indexing",
                completed_at=completed_at,
            )
            if row is None or not row.staged_chunk_count:
                return False
            # 2. 文档、版本与当前索引指针在一个事务中切换，重复投递不会重复发布。
            session.execute(
                update(document_versions)
                .where(
                    document_versions.c.workspace_id == version.workspace_id,
                    document_versions.c.document_version_id == version.document_version_id,
                    document_versions.c.status == "draft",
                )
                .values(
                    status="ready",
                    content_hash=version.source_content_hash,
                    record_version=document_versions.c.record_version + 1,
                )
            )
            session.execute(
                update(index_versions)
                .where(index_versions.c.index_version_id == version.index_version_id)
                .values(
                    status="ready",
                    claimed_by=None,
                    claim_until=None,
                    active_attempt_id=None,
                    completed_at=completed_at,
                    chunk_count=row.staged_chunk_count,
                    updated_at=completed_at,
                )
            )
            if (
                published_document_version_id(
                    session,
                    workspace_id=version.workspace_id,
                    document_id=version.document_id,
                )
                == version.document_version_id
            ):
                switch_active_document_index(
                    session,
                    workspace_id=version.workspace_id,
                    document_id=version.document_id,
                    document_version_id=version.document_version_id,
                    activated_at=completed_at,
                )
        return True

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
    ) -> IndexFailureResult:
        with self._session_factory() as session, session.begin():
            row = _owned_index_row(
                session,
                version,
                lane=version.processing_lane,
                completed_at=failed_at,
            )
            if row is None:
                return "lost_claim"
            terminal = not retryable or row.attempt_count >= row.max_attempts
            status = (
                "dead_letter" if terminal else _lane_status(version.processing_lane, "retry_wait")
            )
            session.execute(
                update(index_versions)
                .where(index_versions.c.index_version_id == version.index_version_id)
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
                    dead_lettered_at=failed_at if terminal else None,
                    updated_at=failed_at,
                )
            )
        return "dead_letter" if terminal else "retry_wait"

    def recover_dead_letter(
        self,
        index_version_id: UUID,
        *,
        actor_id: UUID,
        recovered_at: datetime,
    ) -> bool:
        """最多三次恢复死信，并从失败 Lane 的持久化边界重新开始。"""

        # 1. 锁定稳定死信并复核恢复次数，重复或越界命令不会修改任务。
        with self._session_factory() as session, session.begin():
            row = session.execute(
                select(index_versions)
                .where(
                    index_versions.c.index_version_id == index_version_id,
                    index_versions.c.status == "dead_letter",
                )
                .with_for_update()
            ).one_or_none()
            if row is None or row.manual_recovery_count >= 3:
                return False
            # 2. 从失败 Lane 的持久化交接点恢复，只重置该 Lane 的有限尝试窗口。
            lane = cast(IndexWorkerLane, row.processing_lane)
            values: dict[str, object | None] = {
                "status": "queued" if lane == "embedding" else "index_queued",
                "attempt_count": 0,
                "available_at": recovered_at,
                "completed_at": None,
                "failure_stage": None,
                "error_code": None,
                "error_message": None,
                "dead_lettered_at": None,
                "manual_recovery_count": row.manual_recovery_count + 1,
                "last_recovered_by_actor_id": actor_id,
                "last_recovered_at": recovered_at,
                "updated_at": recovered_at,
            }
            if lane == "embedding":
                values["embedding_attempt_count"] = 0
            else:
                values["indexing_attempt_count"] = 0
            session.execute(
                update(index_versions)
                .where(index_versions.c.index_version_id == index_version_id)
                .values(**values)
            )
        return True


def _lane_status(lane: IndexWorkerLane, state: str) -> str:
    """把稳定 Lane 和局部状态组合为数据库状态，避免跨 Lane 状态误写。"""

    prefix = "index" if lane == "indexing" else lane
    return f"{prefix}_{state}"


def _expire_stale_index_leases(
    session: Session,
    *,
    lane: IndexWorkerLane,
    now: datetime,
) -> None:
    running_status = _lane_status(lane, "running")
    message = f"{lane} Worker 在最大尝试次数内未完成任务"
    common = (
        index_versions.c.processing_lane == lane,
        index_versions.c.status == running_status,
        index_versions.c.claim_until <= now,
    )
    # 1. 耗尽的租约进入稳定死信，并保留索引阶段已持久化的不可见 Chunk。
    session.execute(
        update(index_versions)
        .where(*common, index_versions.c.attempt_count >= index_versions.c.max_attempts)
        .values(
            status="dead_letter",
            claimed_by=None,
            claim_until=None,
            active_attempt_id=None,
            failure_stage="worker",
            error_code="INDEX_WORKER_LEASE_EXPIRED",
            error_message=message,
            completed_at=now,
            dead_lettered_at=now,
            updated_at=now,
        )
    )
    # 2. 尚有预算的租约回到本 Lane 等待态，其他 Lane 的任务不会被本次扫描触碰。
    session.execute(
        update(index_versions)
        .where(*common, index_versions.c.attempt_count < index_versions.c.max_attempts)
        .values(
            status=_lane_status(lane, "retry_wait"),
            available_at=now,
            claimed_by=None,
            claim_until=None,
            active_attempt_id=None,
            failure_stage="worker",
            error_code="INDEX_WORKER_LEASE_EXPIRED",
            error_message=message,
            updated_at=now,
        )
    )


def _select_claimable_index(
    session: Session,
    *,
    lane: IndexWorkerLane,
    now: datetime,
) -> Row[Any] | None:
    queued_status = "queued" if lane == "embedding" else "index_queued"
    retry_status = _lane_status(lane, "retry_wait")
    return session.execute(
        select(index_versions)
        .where(
            index_versions.c.processing_lane == lane,
            index_versions.c.status.in_((queued_status, retry_status)),
            index_versions.c.available_at <= now,
            index_versions.c.attempt_count < index_versions.c.max_attempts,
        )
        .order_by(index_versions.c.available_at, index_versions.c.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).one_or_none()


def _claim_index(
    session: Session,
    row: Row[Any],
    *,
    lane: IndexWorkerLane,
    worker_id: str,
    now: datetime,
    claim_until: datetime,
) -> ClaimedIndexVersion:
    attempt_count = cast(int, row.attempt_count) + 1
    attempt_id = uuid4()
    lane_attempt_column = (
        index_versions.c.embedding_attempt_count
        if lane == "embedding"
        else index_versions.c.indexing_attempt_count
    )
    session.execute(
        update(index_versions)
        .where(index_versions.c.index_version_id == row.index_version_id)
        .values(
            status=_lane_status(lane, "running"),
            processing_lane=lane,
            attempt_count=attempt_count,
            claimed_by=worker_id,
            claim_until=claim_until,
            active_attempt_id=attempt_id,
            started_at=row.started_at or now,
            failure_stage=None,
            error_code=None,
            error_message=None,
            updated_at=now,
            **{lane_attempt_column.key: lane_attempt_column + 1},
        )
    )
    return ClaimedIndexVersion(
        index_version_id=row.index_version_id,
        job_attempt_id=attempt_id,
        workspace_id=row.workspace_id,
        knowledge_base_id=row.knowledge_base_id,
        document_id=row.document_id,
        document_version_id=row.document_version_id,
        ingestion_job_id=row.ingestion_job_id,
        source_id=row.source_id,
        artifact_object_key=row.artifact_object_key,
        source_content_hash=row.source_content_hash,
        parsed_content_hash=row.parsed_content_hash,
        attempt_count=attempt_count,
        max_attempts=row.max_attempts,
        claimed_by=worker_id,
        processing_lane=lane,
        chunker_version=row.chunker_version,
        embedding_model_version=row.embedding_model_version,
        tokenizer_version=row.tokenizer_version,
        department_ids=tuple(row.department_ids),
        visibility=cast(IndexVisibility, row.visibility),
        security_level=cast(IndexSecurityLevel, row.security_level),
        permission_labels=tuple(row.permission_labels),
    )


def _owned_index_row(
    session: Session,
    version: ClaimedIndexVersion,
    *,
    lane: IndexWorkerLane,
    completed_at: datetime,
) -> Row[Any] | None:
    return session.execute(
        select(index_versions)
        .where(
            index_versions.c.index_version_id == version.index_version_id,
            index_versions.c.status == _lane_status(lane, "running"),
            index_versions.c.processing_lane == lane,
            index_versions.c.claimed_by == version.claimed_by,
            index_versions.c.active_attempt_id == version.job_attempt_id,
            index_versions.c.claim_until > completed_at,
        )
        .with_for_update()
    ).one_or_none()


def _owns_index_lease(
    session: Session,
    version: ClaimedIndexVersion,
    *,
    lane: IndexWorkerLane,
    completed_at: datetime,
) -> bool:
    return (
        _owned_index_row(
            session,
            version,
            lane=lane,
            completed_at=completed_at,
        )
        is not None
    )


def _upsert_staged_chunks(
    session: Session,
    chunks: tuple[BuiltIndexChunk, ...],
    *,
    created_at: datetime,
) -> None:
    statement = postgresql_insert(retrieval_chunks).values(
        [
            {
                "index_version_id": chunk.index_version_id,
                "chunk_id": chunk.chunk_id,
                "workspace_id": chunk.workspace_id,
                "knowledge_base_id": chunk.knowledge_base_id,
                "document_id": chunk.document_id,
                "document_version_id": chunk.document_version_id,
                "ingestion_job_id": chunk.ingestion_job_id,
                "source_id": chunk.source_id,
                "sequence_no": chunk.sequence_no,
                "content": chunk.content,
                "content_hash": chunk.content_hash,
                "embedding": list(chunk.embedding),
                "keyword_text": chunk.keyword_text,
                "department_ids": list(chunk.department_ids),
                "visibility": chunk.visibility,
                "security_level": chunk.security_level,
                "permission_labels": list(chunk.permission_labels),
                "source_position": chunk.source_position,
                "parsed_content_hash": chunk.parsed_content_hash,
                "parser_name": chunk.parser_name,
                "ocr_used": chunk.ocr_used,
                "created_at": created_at,
                "active": False,
            }
            for chunk in chunks
        ]
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[retrieval_chunks.c.index_version_id, retrieval_chunks.c.chunk_id],
            set_={
                "content": statement.excluded.content,
                "content_hash": statement.excluded.content_hash,
                "embedding": statement.excluded.embedding,
                "keyword_text": statement.excluded.keyword_text,
                "department_ids": statement.excluded.department_ids,
                "visibility": statement.excluded.visibility,
                "security_level": statement.excluded.security_level,
                "permission_labels": statement.excluded.permission_labels,
                "source_position": statement.excluded.source_position,
                "parsed_content_hash": statement.excluded.parsed_content_hash,
                "parser_name": statement.excluded.parser_name,
                "ocr_used": statement.excluded.ocr_used,
                "active": False,
            },
        )
    )
