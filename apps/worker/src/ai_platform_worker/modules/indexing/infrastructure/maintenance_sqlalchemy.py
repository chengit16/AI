"""使用 PostgreSQL 巡检索引引用、修复活动面并排队可重建派生索引。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from ai_platform_backend.indexing.facts import document_publications, documents
from ai_platform_backend.indexing.maintenance import (
    IndexCleanupResult,
    IndexFindingCode,
    IndexInspectionFinding,
    IndexInspectionReport,
    IndexRebuildBatchResult,
    IndexRepairResult,
)
from ai_platform_backend.indexing.persistence import (
    document_index_publications,
    index_inspection_findings,
    index_maintenance_runs,
    index_versions,
    retrieval_chunks,
)
from ai_platform_backend.indexing.sqlalchemy import (
    activate_document_index_version,
    clear_document_index_activation,
)
from ai_platform_backend.ingestion.persistence import ingestion_jobs
from sqlalchemy import RowMapping, delete, func, insert, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

ACTIVE_BUILD_STATUSES = ("ready", "active", "retired")
PENDING_BUILD_STATUSES = (
    "queued",
    "embedding_running",
    "embedding_retry_wait",
    "index_queued",
    "index_running",
    "index_retry_wait",
)


@dataclass(frozen=True)
class _PublishedIndex:
    workspace_id: UUID
    knowledge_base_id: UUID
    document_id: UUID
    current_document_version_id: UUID
    publication_document_version_id: UUID | None
    index_version_id: UUID | None
    index_document_version_id: UUID | None
    index_status: str | None
    chunk_count: int | None
    ingestion_job_id: UUID | None
    source_id: UUID | None
    parsed_content_hash: str | None
    ingestion_status: str | None
    ingestion_source_id: UUID | None
    ingestion_parsed_content_hash: str | None


@dataclass(frozen=True)
class _ChunkStats:
    total_count: int
    active_count: int
    references_valid: bool


class SqlAlchemyIndexMaintenanceStore:
    """持久化巡检证据，并只从文档、入库和解析事实重建索引。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def inspect(
        self,
        *,
        now: datetime,
        workspace_id: UUID | None = None,
    ) -> IndexInspectionReport:
        """在同一数据库快照中生成不含正文的索引一致性报告。"""

        maintenance_run_id = uuid4()
        with self._session_factory() as session, session.begin():
            # 1. 业务发布事实决定必须存在的活动索引，派生指针不能反向决定发布状态。
            published = _load_published_indexes(session, workspace_id=workspace_id)
            chunk_stats = _load_chunk_stats(
                session,
                tuple(
                    row.index_version_id for row in published if row.index_version_id is not None
                ),
            )
            findings = _find_publication_issues(published, chunk_stats)
            # 2. 再从活动 Chunk 和活动索引反查孤儿，覆盖“多余活动引用”方向的差异。
            findings.extend(_find_orphan_active_chunks(session, workspace_id=workspace_id))
            findings.extend(_find_orphan_active_versions(session, workspace_id=workspace_id))
            normalized = _deduplicate_findings(findings)
            digest = _finding_digest(normalized)
            _persist_maintenance_run(
                session,
                maintenance_run_id=maintenance_run_id,
                run_kind="inspection",
                workspace_id=workspace_id,
                requested_by_actor_id=None,
                now=now,
                scanned_document_count=len(published),
                inconsistency_count=len(normalized),
                result_digest=digest,
            )
            finding_rows = [
                {
                    "finding_id": finding.finding_id,
                    "maintenance_run_id": maintenance_run_id,
                    "finding_code": finding.code,
                    "workspace_id": finding.workspace_id,
                    "document_id": finding.document_id,
                    "document_version_id": finding.document_version_id,
                    "index_version_id": finding.index_version_id,
                    "resolution": "unresolved",
                    "detected_at": now,
                }
                for finding in normalized
            ]
            # SQLAlchemy 会把空列表解释成单行默认插入；健康巡检必须跳过发现表写入。
            if finding_rows:
                session.execute(insert(index_inspection_findings), finding_rows)
        return IndexInspectionReport(
            maintenance_run_id=maintenance_run_id,
            started_at=now,
            completed_at=now,
            scanned_document_count=len(published),
            inconsistency_count=len(normalized),
            result_digest=digest,
            findings=tuple(normalized),
        )

    def repair(
        self,
        report: IndexInspectionReport,
        *,
        now: datetime,
        max_attempts: int,
        chunker_version: str,
        embedding_model_version: str,
        tokenizer_version: str,
    ) -> IndexRepairResult:
        """修复报告涉及的文档；完整候选原子切换，否则排队重建。"""

        repaired = 0
        queued = 0
        affected = {
            (finding.workspace_id, finding.document_id)
            for finding in report.findings
            if finding.resolution == "unresolved"
        }
        with self._session_factory() as session, session.begin():
            # 1. 每个文档重新锁定当前发布事实，过期报告不能恢复历史版本或跨空间索引。
            for workspace_id, document_id in sorted(affected, key=lambda item: str(item[1])):
                current_version_id = session.execute(
                    select(document_publications.c.current_document_version_id)
                    .where(
                        document_publications.c.workspace_id == workspace_id,
                        document_publications.c.document_id == document_id,
                    )
                    .with_for_update()
                ).scalar_one_or_none()
                if current_version_id is None:
                    # 文档发布已撤销时不允许保留孤儿活动面，也不创建无来源重建任务。
                    clear_document_index_activation(
                        session,
                        workspace_id=workspace_id,
                        document_id=document_id,
                        changed_at=now,
                    )
                    repaired += 1
                    resolution = "repaired"
                    _update_finding_resolution(
                        session,
                        maintenance_run_id=report.maintenance_run_id,
                        workspace_id=workspace_id,
                        document_id=document_id,
                        resolution=resolution,
                    )
                    continue
                current_version_id = cast(UUID, current_version_id)
                candidate = _find_complete_candidate(
                    session,
                    workspace_id=workspace_id,
                    document_id=document_id,
                    document_version_id=current_version_id,
                )
                # 2. 完整旧候选优先恢复服务；没有候选时停用异常活动面并幂等排队重建。
                if candidate is not None and activate_document_index_version(
                    session,
                    workspace_id=workspace_id,
                    document_id=document_id,
                    document_version_id=current_version_id,
                    index_version_id=candidate,
                    activated_at=now,
                ):
                    repaired += 1
                    resolution = "repaired"
                else:
                    clear_document_index_activation(
                        session,
                        workspace_id=workspace_id,
                        document_id=document_id,
                        changed_at=now,
                    )
                    was_queued = _enqueue_rebuild(
                        session,
                        workspace_id=workspace_id,
                        document_version_id=current_version_id,
                        now=now,
                        max_attempts=max_attempts,
                        chunker_version=chunker_version,
                        embedding_model_version=embedding_model_version,
                        tokenizer_version=tokenizer_version,
                        skip_when_pending=True,
                    )
                    queued += int(was_queued is not None)
                    resolution = (
                        "rebuild_queued"
                        if was_queued is not None
                        or _has_pending_build(
                            session,
                            workspace_id=workspace_id,
                            document_version_id=current_version_id,
                        )
                        else "unresolved"
                    )
                _update_finding_resolution(
                    session,
                    maintenance_run_id=report.maintenance_run_id,
                    workspace_id=workspace_id,
                    document_id=document_id,
                    resolution=resolution,
                )
            # 3. 原巡检运行保留发现摘要，只回填修复与排队计数供后续指标和运营查询。
            session.execute(
                update(index_maintenance_runs)
                .where(index_maintenance_runs.c.maintenance_run_id == report.maintenance_run_id)
                .values(repaired_count=repaired, rebuild_queued_count=queued)
            )
        return IndexRepairResult(report.maintenance_run_id, repaired, queued)

    def enqueue_full_rebuild(
        self,
        maintenance_run_id: UUID,
        *,
        requested_by_actor_id: UUID | None,
        workspace_id: UUID | None,
        now: datetime,
        max_attempts: int,
        chunker_version: str,
        embedding_model_version: str,
        tokenizer_version: str,
    ) -> IndexRebuildBatchResult:
        """为当前已发布文档幂等创建新构建，旧活动索引保持可见。"""

        with self._session_factory() as session, session.begin():
            existing = _read_rebuild_result(session, maintenance_run_id)
            if existing is not None:
                return existing
            # 1. 全量范围只来自活动文档的当前发布事实，不扫描旧版本或缓存索引。
            statement = (
                select(
                    document_publications.c.workspace_id,
                    document_publications.c.current_document_version_id,
                )
                .select_from(
                    document_publications.join(
                        documents,
                        (documents.c.workspace_id == document_publications.c.workspace_id)
                        & (documents.c.document_id == document_publications.c.document_id),
                    )
                )
                .where(documents.c.status == "active")
                .order_by(
                    document_publications.c.workspace_id,
                    document_publications.c.document_id,
                )
            )
            if workspace_id is not None:
                statement = statement.where(document_publications.c.workspace_id == workspace_id)
            sources = session.execute(statement).all()
            queued_ids: list[UUID] = []
            # 2. 每个文档在来源行锁下递增 build_no；已有在途构建时跳过，避免重复消耗资源。
            for source_workspace_id, document_version_id in sources:
                queued_id = _enqueue_rebuild(
                    session,
                    workspace_id=source_workspace_id,
                    document_version_id=document_version_id,
                    now=now,
                    max_attempts=max_attempts,
                    chunker_version=chunker_version,
                    embedding_model_version=embedding_model_version,
                    tokenizer_version=tokenizer_version,
                    skip_when_pending=True,
                )
                if queued_id is not None:
                    queued_ids.append(queued_id)
            digest = _identifier_digest(queued_ids)
            _persist_maintenance_run(
                session,
                maintenance_run_id=maintenance_run_id,
                run_kind="full_rebuild",
                workspace_id=workspace_id,
                requested_by_actor_id=requested_by_actor_id,
                now=now,
                scanned_document_count=len(sources),
                rebuild_queued_count=len(queued_ids),
                result_digest=digest,
            )
        return IndexRebuildBatchResult(
            maintenance_run_id,
            len(sources),
            len(queued_ids),
            digest,
        )

    def cleanup_unrecoverable_chunks(
        self,
        maintenance_run_id: UUID,
        *,
        requested_by_actor_id: UUID | None,
        workspace_id: UUID | None,
        now: datetime,
    ) -> IndexCleanupResult:
        """幂等删除失败或恢复预算耗尽构建中的不可见 Chunk。"""

        with self._session_factory() as session, session.begin():
            existing = _read_cleanup_result(session, maintenance_run_id)
            if existing is not None:
                return existing
            # 1. 可恢复死信必须保留 staged Chunk；只圈定永久失败或三次恢复耗尽的版本。
            unusable = select(index_versions.c.index_version_id).where(
                or_(
                    index_versions.c.status == "failed",
                    (index_versions.c.status == "dead_letter")
                    & (index_versions.c.manual_recovery_count >= 3),
                )
            )
            if workspace_id is not None:
                unusable = unusable.where(index_versions.c.workspace_id == workspace_id)
            # 2. 仅删除不可见 Chunk，并以实际返回的 Chunk ID 形成可重放的清理证据。
            cleaned_ids = [
                cast(UUID, chunk_id)
                for chunk_id in session.scalars(
                    delete(retrieval_chunks)
                    .where(
                        retrieval_chunks.c.index_version_id.in_(unusable),
                        retrieval_chunks.c.active.is_(False),
                    )
                    .returning(retrieval_chunks.c.chunk_id)
                )
            ]
            cleaned = len(cleaned_ids)
            digest = _identifier_digest(cleaned_ids)
            _persist_maintenance_run(
                session,
                maintenance_run_id=maintenance_run_id,
                run_kind="cleanup",
                workspace_id=workspace_id,
                requested_by_actor_id=requested_by_actor_id,
                now=now,
                cleaned_chunk_count=cleaned,
                result_digest=digest,
            )
        return IndexCleanupResult(maintenance_run_id, cleaned, digest)


def _load_published_indexes(
    session: Session,
    *,
    workspace_id: UUID | None,
) -> list[_PublishedIndex]:
    # 1. 以活动文档和当前文档发布为必需事实，左连接可能缺失或损坏的派生索引。
    statement = (
        select(
            documents.c.workspace_id,
            documents.c.knowledge_base_id,
            documents.c.document_id,
            document_publications.c.current_document_version_id,
            document_index_publications.c.document_version_id.label(
                "publication_document_version_id"
            ),
            document_index_publications.c.index_version_id,
            index_versions.c.document_version_id.label("index_document_version_id"),
            index_versions.c.status.label("index_status"),
            index_versions.c.chunk_count,
            index_versions.c.ingestion_job_id,
            index_versions.c.source_id,
            index_versions.c.parsed_content_hash,
            ingestion_jobs.c.status.label("ingestion_status"),
            ingestion_jobs.c.source_id.label("ingestion_source_id"),
            ingestion_jobs.c.parsed_content_hash.label("ingestion_parsed_content_hash"),
        )
        .select_from(
            documents.join(
                document_publications,
                (document_publications.c.workspace_id == documents.c.workspace_id)
                & (document_publications.c.document_id == documents.c.document_id),
            )
            .outerjoin(
                document_index_publications,
                (document_index_publications.c.workspace_id == documents.c.workspace_id)
                & (document_index_publications.c.document_id == documents.c.document_id),
            )
            .outerjoin(
                index_versions,
                index_versions.c.index_version_id == document_index_publications.c.index_version_id,
            )
            .outerjoin(
                ingestion_jobs,
                ingestion_jobs.c.ingestion_job_id == index_versions.c.ingestion_job_id,
            )
        )
        .where(documents.c.status == "active")
        .order_by(documents.c.workspace_id, documents.c.document_id)
    )
    # 2. 可选工作空间范围只缩小巡检集合，映射结果不携带正文和向量数据。
    if workspace_id is not None:
        statement = statement.where(documents.c.workspace_id == workspace_id)
    return [_published_index(row) for row in session.execute(statement).mappings()]


def _published_index(row: RowMapping) -> _PublishedIndex:
    return _PublishedIndex(
        workspace_id=cast(UUID, row["workspace_id"]),
        knowledge_base_id=cast(UUID, row["knowledge_base_id"]),
        document_id=cast(UUID, row["document_id"]),
        current_document_version_id=cast(UUID, row["current_document_version_id"]),
        publication_document_version_id=cast(UUID | None, row["publication_document_version_id"]),
        index_version_id=cast(UUID | None, row["index_version_id"]),
        index_document_version_id=cast(UUID | None, row["index_document_version_id"]),
        index_status=cast(str | None, row["index_status"]),
        chunk_count=cast(int | None, row["chunk_count"]),
        ingestion_job_id=cast(UUID | None, row["ingestion_job_id"]),
        source_id=cast(UUID | None, row["source_id"]),
        parsed_content_hash=cast(str | None, row["parsed_content_hash"]),
        ingestion_status=cast(str | None, row["ingestion_status"]),
        ingestion_source_id=cast(UUID | None, row["ingestion_source_id"]),
        ingestion_parsed_content_hash=cast(str | None, row["ingestion_parsed_content_hash"]),
    )


def _load_chunk_stats(
    session: Session,
    index_version_ids: tuple[UUID, ...],
) -> dict[UUID, _ChunkStats]:
    if not index_version_ids:
        return {}
    reference_valid = (
        (retrieval_chunks.c.workspace_id == index_versions.c.workspace_id)
        & (retrieval_chunks.c.knowledge_base_id == index_versions.c.knowledge_base_id)
        & (retrieval_chunks.c.document_id == index_versions.c.document_id)
        & (retrieval_chunks.c.document_version_id == index_versions.c.document_version_id)
        & (retrieval_chunks.c.ingestion_job_id == index_versions.c.ingestion_job_id)
        & (retrieval_chunks.c.source_id == index_versions.c.source_id)
        & (retrieval_chunks.c.parsed_content_hash == index_versions.c.parsed_content_hash)
    )
    rows = session.execute(
        select(
            retrieval_chunks.c.index_version_id,
            func.count().label("total_count"),
            func.count().filter(retrieval_chunks.c.active.is_(True)).label("active_count"),
            func.bool_and(reference_valid).label("references_valid"),
        )
        .select_from(
            retrieval_chunks.join(
                index_versions,
                index_versions.c.index_version_id == retrieval_chunks.c.index_version_id,
            )
        )
        .where(retrieval_chunks.c.index_version_id.in_(index_version_ids))
        .group_by(retrieval_chunks.c.index_version_id)
    ).mappings()
    return {
        cast(UUID, row["index_version_id"]): _ChunkStats(
            total_count=int(row["total_count"]),
            active_count=int(row["active_count"]),
            references_valid=bool(row["references_valid"]),
        )
        for row in rows
    }


def _find_publication_issues(
    published: list[_PublishedIndex],
    chunk_stats: dict[UUID, _ChunkStats],
) -> list[IndexInspectionFinding]:
    findings: list[IndexInspectionFinding] = []
    for row in published:
        if row.index_version_id is None:
            findings.append(_finding("INDEX_PUBLICATION_MISSING", row))
            continue
        if row.publication_document_version_id != row.current_document_version_id:
            findings.append(_finding("INDEX_PUBLICATION_VERSION_MISMATCH", row))
        if (
            row.index_document_version_id != row.current_document_version_id
            or row.index_status != "active"
        ):
            findings.append(_finding("INDEX_VERSION_STATE_MISMATCH", row))
        if (
            row.ingestion_status != "succeeded"
            or row.source_id != row.ingestion_source_id
            or row.parsed_content_hash != row.ingestion_parsed_content_hash
        ):
            findings.append(_finding("INDEX_SOURCE_FACT_MISMATCH", row))
        stats = chunk_stats.get(row.index_version_id, _ChunkStats(0, 0, False))
        if (
            row.chunk_count is None
            or stats.total_count != row.chunk_count
            or (stats.active_count != row.chunk_count)
        ):
            findings.append(_finding("INDEX_CHUNK_COUNT_MISMATCH", row))
        if not stats.references_valid:
            findings.append(_finding("INDEX_CHUNK_REFERENCE_MISMATCH", row))
    return findings


def _finding(code: IndexFindingCode, row: _PublishedIndex) -> IndexInspectionFinding:
    return IndexInspectionFinding(
        finding_id=uuid4(),
        code=code,
        workspace_id=row.workspace_id,
        document_id=row.document_id,
        document_version_id=row.current_document_version_id,
        index_version_id=row.index_version_id,
        resolution="unresolved",
    )


def _find_orphan_active_chunks(
    session: Session,
    *,
    workspace_id: UUID | None,
) -> list[IndexInspectionFinding]:
    # 1. 从活动 Chunk 反向连接发布指针，找出不属于当前活动索引的多余可见数据。
    statement = (
        select(
            retrieval_chunks.c.workspace_id,
            retrieval_chunks.c.document_id,
            retrieval_chunks.c.document_version_id,
            retrieval_chunks.c.index_version_id,
        )
        .select_from(
            retrieval_chunks.outerjoin(
                document_index_publications,
                (document_index_publications.c.workspace_id == retrieval_chunks.c.workspace_id)
                & (document_index_publications.c.document_id == retrieval_chunks.c.document_id)
                & (
                    document_index_publications.c.index_version_id
                    == retrieval_chunks.c.index_version_id
                ),
            )
        )
        .where(
            retrieval_chunks.c.active.is_(True),
            document_index_publications.c.index_version_id.is_(None),
        )
        .distinct()
    )
    # 2. 工作空间过滤在数据库内完成，每个索引版本只生成一项不含正文的发现。
    if workspace_id is not None:
        statement = statement.where(retrieval_chunks.c.workspace_id == workspace_id)
    return [
        IndexInspectionFinding(
            uuid4(),
            "INDEX_UNEXPECTED_ACTIVE_CHUNK",
            row.workspace_id,
            row.document_id,
            row.document_version_id,
            row.index_version_id,
            "unresolved",
        )
        for row in session.execute(statement)
    ]


def _find_orphan_active_versions(
    session: Session,
    *,
    workspace_id: UUID | None,
) -> list[IndexInspectionFinding]:
    statement = (
        select(
            index_versions.c.workspace_id,
            index_versions.c.document_id,
            index_versions.c.document_version_id,
            index_versions.c.index_version_id,
        )
        .select_from(
            index_versions.outerjoin(
                document_index_publications,
                (document_index_publications.c.workspace_id == index_versions.c.workspace_id)
                & (document_index_publications.c.document_id == index_versions.c.document_id)
                & (
                    document_index_publications.c.index_version_id
                    == index_versions.c.index_version_id
                ),
            )
        )
        .where(
            index_versions.c.status == "active",
            document_index_publications.c.index_version_id.is_(None),
        )
    )
    if workspace_id is not None:
        statement = statement.where(index_versions.c.workspace_id == workspace_id)
    return [
        IndexInspectionFinding(
            uuid4(),
            "INDEX_ORPHAN_ACTIVE_VERSION",
            row.workspace_id,
            row.document_id,
            row.document_version_id,
            row.index_version_id,
            "unresolved",
        )
        for row in session.execute(statement)
    ]


def _deduplicate_findings(
    findings: list[IndexInspectionFinding],
) -> list[IndexInspectionFinding]:
    unique: dict[tuple[object, ...], IndexInspectionFinding] = {}
    for finding in findings:
        key = (
            finding.code,
            finding.workspace_id,
            finding.document_id,
            finding.document_version_id,
            finding.index_version_id,
        )
        unique.setdefault(key, finding)
    return sorted(
        unique.values(),
        key=lambda item: (
            str(item.workspace_id),
            str(item.document_id),
            item.code,
            str(item.index_version_id or ""),
        ),
    )


def _find_complete_candidate(
    session: Session,
    *,
    workspace_id: UUID,
    document_id: UUID,
    document_version_id: UUID,
) -> UUID | None:
    # 1. 只锁定当前文档版本的可发布候选，并优先检查最新构建。
    candidates = session.execute(
        select(index_versions.c.index_version_id)
        .where(
            index_versions.c.workspace_id == workspace_id,
            index_versions.c.document_id == document_id,
            index_versions.c.document_version_id == document_version_id,
            index_versions.c.status.in_(ACTIVE_BUILD_STATUSES),
        )
        .order_by(index_versions.c.build_no.desc())
        .with_for_update()
    ).scalars()
    # 2. 每个候选同时校验 Chunk 数量、引用链和成功入库摘要，任一不符即跳过。
    for candidate in candidates:
        candidate_id = cast(UUID, candidate)
        stats = _load_chunk_stats(session, (candidate_id,)).get(candidate_id)
        expected = session.execute(
            select(
                index_versions.c.chunk_count,
                ingestion_jobs.c.status,
                index_versions.c.source_id == ingestion_jobs.c.source_id,
                index_versions.c.parsed_content_hash == ingestion_jobs.c.parsed_content_hash,
            )
            .select_from(
                index_versions.join(
                    ingestion_jobs,
                    ingestion_jobs.c.ingestion_job_id == index_versions.c.ingestion_job_id,
                )
            )
            .where(index_versions.c.index_version_id == candidate_id)
        ).one()
        if (
            stats is not None
            and expected.chunk_count is not None
            and stats.total_count == expected.chunk_count
            and stats.references_valid
            and expected.status == "succeeded"
            and bool(expected[2])
            and bool(expected[3])
        ):
            return candidate_id
    return None


def _enqueue_rebuild(
    session: Session,
    *,
    workspace_id: UUID,
    document_version_id: UUID,
    now: datetime,
    max_attempts: int,
    chunker_version: str,
    embedding_model_version: str,
    tokenizer_version: str,
    skip_when_pending: bool,
) -> UUID | None:
    # 1. 来源必须是成功入库和活动文档事实；锁定任务后再计算 build_no，避免并发重复编号。
    source = (
        session.execute(
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
            .order_by(ingestion_jobs.c.completed_at.desc())
            .limit(1)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if source is None or source["artifact_object_key"] is None:
        return None
    if skip_when_pending and _has_pending_build(
        session,
        workspace_id=workspace_id,
        document_version_id=document_version_id,
    ):
        return None
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
    # 2. 新构建只复制来源摘要、组件版本和权限快照，不读取旧 Chunk 或旧索引指针。
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
    # 3. 新版本仅进入 Embedding 队列；活动索引要到完整提交事务后才会切换。
    return index_version_id


def _has_pending_build(
    session: Session,
    *,
    workspace_id: UUID,
    document_version_id: UUID,
) -> bool:
    return bool(
        session.scalar(
            select(func.count())
            .select_from(index_versions)
            .where(
                index_versions.c.workspace_id == workspace_id,
                index_versions.c.document_version_id == document_version_id,
                index_versions.c.status.in_(PENDING_BUILD_STATUSES),
            )
        )
    )


def _update_finding_resolution(
    session: Session,
    *,
    maintenance_run_id: UUID,
    workspace_id: UUID,
    document_id: UUID,
    resolution: str,
) -> None:
    session.execute(
        update(index_inspection_findings)
        .where(
            index_inspection_findings.c.maintenance_run_id == maintenance_run_id,
            index_inspection_findings.c.workspace_id == workspace_id,
            index_inspection_findings.c.document_id == document_id,
        )
        .values(resolution=resolution)
    )


def _persist_maintenance_run(
    session: Session,
    *,
    maintenance_run_id: UUID,
    run_kind: str,
    workspace_id: UUID | None,
    requested_by_actor_id: UUID | None,
    now: datetime,
    scanned_document_count: int = 0,
    inconsistency_count: int = 0,
    repaired_count: int = 0,
    rebuild_queued_count: int = 0,
    cleaned_chunk_count: int = 0,
    result_digest: str,
) -> None:
    session.execute(
        insert(index_maintenance_runs).values(
            maintenance_run_id=maintenance_run_id,
            run_kind=run_kind,
            workspace_id=workspace_id,
            requested_by_actor_id=requested_by_actor_id,
            status="completed",
            started_at=now,
            completed_at=now,
            scanned_document_count=scanned_document_count,
            inconsistency_count=inconsistency_count,
            repaired_count=repaired_count,
            rebuild_queued_count=rebuild_queued_count,
            cleaned_chunk_count=cleaned_chunk_count,
            result_digest=result_digest,
        )
    )


def _read_rebuild_result(
    session: Session,
    maintenance_run_id: UUID,
) -> IndexRebuildBatchResult | None:
    row = session.execute(
        select(index_maintenance_runs).where(
            index_maintenance_runs.c.maintenance_run_id == maintenance_run_id,
            index_maintenance_runs.c.run_kind == "full_rebuild",
        )
    ).one_or_none()
    if row is None:
        return None
    return IndexRebuildBatchResult(
        maintenance_run_id,
        row.scanned_document_count,
        row.rebuild_queued_count,
        row.result_digest,
    )


def _read_cleanup_result(
    session: Session,
    maintenance_run_id: UUID,
) -> IndexCleanupResult | None:
    row = session.execute(
        select(index_maintenance_runs).where(
            index_maintenance_runs.c.maintenance_run_id == maintenance_run_id,
            index_maintenance_runs.c.run_kind == "cleanup",
        )
    ).one_or_none()
    if row is None:
        return None
    return IndexCleanupResult(
        maintenance_run_id,
        row.cleaned_chunk_count,
        row.result_digest,
    )


def _finding_digest(findings: list[IndexInspectionFinding]) -> str:
    payload = [
        {
            "code": finding.code,
            "workspace_id": str(finding.workspace_id),
            "document_id": str(finding.document_id),
            "document_version_id": str(finding.document_version_id or ""),
            "index_version_id": str(finding.index_version_id or ""),
        }
        for finding in findings
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _identifier_digest(values: list[UUID]) -> str:
    payload = [str(value) for value in sorted(values, key=str)]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
