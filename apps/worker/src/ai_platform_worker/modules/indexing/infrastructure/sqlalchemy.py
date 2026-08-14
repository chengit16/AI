from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

from ai_platform_backend.indexing.domain import (
    BuiltIndexChunk,
    ClaimedIndexVersion,
    IndexFailureResult,
    IndexFailureStage,
    IndexSecurityLevel,
    IndexVisibility,
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
from sqlalchemy import exists, func, insert, literal, or_, select, update
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
            "attempt_count",
            "max_attempts",
            "available_at",
            "created_at",
            "updated_at",
        )
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
                literal(0, type_=index_versions.c.attempt_count.type),
                literal(max_attempts, type_=index_versions.c.max_attempts.type),
                literal(now, type_=index_versions.c.available_at.type),
                literal(now, type_=index_versions.c.created_at.type),
                literal(now, type_=index_versions.c.updated_at.type),
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
                documents.c.status == "active",
                ~exists().where(
                    index_versions.c.ingestion_job_id == ingestion_jobs.c.ingestion_job_id,
                    index_versions.c.build_no == 1,
                ),
            )
        )
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
                    attempt_count=0,
                    max_attempts=max_attempts,
                    available_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
        return index_version_id

    def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimedIndexVersion | None:
        claim_until = now + timedelta(seconds=lease_seconds)
        with self._session_factory() as session, session.begin():
            # 最后一次执行崩溃时没有异常回调，过期租约必须在下次扫描收敛。
            session.execute(
                update(index_versions)
                .where(
                    index_versions.c.status == "running",
                    index_versions.c.claim_until <= now,
                    index_versions.c.attempt_count >= index_versions.c.max_attempts,
                )
                .values(
                    status="failed",
                    claimed_by=None,
                    claim_until=None,
                    failure_stage="worker",
                    error_code="INDEX_WORKER_LEASE_EXPIRED",
                    error_message="索引 Worker 在最大尝试次数内未完成任务",
                    completed_at=now,
                    updated_at=now,
                )
            )
            row = session.execute(
                select(index_versions)
                .where(
                    or_(
                        (index_versions.c.status == "queued")
                        & (index_versions.c.available_at <= now),
                        (index_versions.c.status == "retry_wait")
                        & (index_versions.c.available_at <= now),
                        (index_versions.c.status == "running")
                        & (index_versions.c.claim_until <= now),
                    ),
                    index_versions.c.attempt_count < index_versions.c.max_attempts,
                )
                .order_by(index_versions.c.available_at, index_versions.c.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).one_or_none()
            if row is None:
                return None
            attempt_count = cast(int, row.attempt_count) + 1
            session.execute(
                update(index_versions)
                .where(index_versions.c.index_version_id == row.index_version_id)
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
            return ClaimedIndexVersion(
                index_version_id=row.index_version_id,
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
                chunker_version=row.chunker_version,
                embedding_model_version=row.embedding_model_version,
                tokenizer_version=row.tokenizer_version,
                department_ids=tuple(row.department_ids),
                visibility=cast(IndexVisibility, row.visibility),
                security_level=cast(IndexSecurityLevel, row.security_level),
                permission_labels=tuple(row.permission_labels),
            )

    def mark_succeeded(
        self,
        version: ClaimedIndexVersion,
        chunks: tuple[BuiltIndexChunk, ...],
        *,
        completed_at: datetime,
    ) -> bool:
        with self._session_factory() as session, session.begin():
            # 与发布事务保持“发布指针 → 索引版本”的锁顺序，避免并发发布形成死锁。
            session.execute(
                select(document_publications.c.current_document_version_id)
                .where(
                    document_publications.c.workspace_id == version.workspace_id,
                    document_publications.c.document_id == version.document_id,
                )
                .with_for_update()
            ).one_or_none()
            owned = session.execute(
                select(index_versions.c.index_version_id)
                .where(
                    index_versions.c.index_version_id == version.index_version_id,
                    index_versions.c.status == "running",
                    index_versions.c.claimed_by == version.claimed_by,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if owned is None:
                return False
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
                        "created_at": completed_at,
                        "active": False,
                    }
                    for chunk in chunks
                ]
            )
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        retrieval_chunks.c.index_version_id,
                        retrieval_chunks.c.chunk_id,
                    ],
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
                    completed_at=completed_at,
                    chunk_count=len(chunks),
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
            row = session.execute(
                select(index_versions.c.attempt_count, index_versions.c.max_attempts)
                .where(
                    index_versions.c.index_version_id == version.index_version_id,
                    index_versions.c.status == "running",
                    index_versions.c.claimed_by == version.claimed_by,
                )
                .with_for_update()
            ).one_or_none()
            if row is None:
                return "lost_claim"
            terminal = not retryable or row.attempt_count >= row.max_attempts
            status: Literal["retry_wait", "failed"] = "failed" if terminal else "retry_wait"
            session.execute(
                update(index_versions)
                .where(index_versions.c.index_version_id == version.index_version_id)
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
