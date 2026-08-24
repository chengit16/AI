"""使用短事务清理回收站文档数据库事实并登记外部清理意图。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.indexing.persistence import (
    document_index_publications,
    index_versions,
    retrieval_chunks,
)
from ai_platform_backend.ingestion.persistence import (
    ingestion_job_attempts,
    ingestion_job_stages,
    ingestion_jobs,
)
from ai_platform_backend.integration.persistence import outbox_events
from ai_platform_backend.knowledge.object_keys import parsed_artifact_object_key
from ai_platform_backend.knowledge.persistence import (
    document_favorites,
    document_folder_bindings,
    document_publications,
    document_sources,
    document_tag_bindings,
    document_versions,
    documents,
)
from sqlalchemy import delete, exists, insert, select
from sqlalchemy.orm import Session

from ai_platform_worker.modules.knowledge.domain.trash_retention import TrashPurgeResult

SessionFactory = Callable[[], Session]
_EVENT_TYPE = "knowledge.document.trash_purge_requested"


class SqlAlchemyTrashRetentionStore:
    """按工作空间和截止时间领取并清理有限批次文档。"""

    def __init__(self, sessions: SessionFactory, *, worker_id: str) -> None:
        self._sessions = sessions
        self._worker_id = worker_id

    def purge_expired(
        self,
        *,
        workspace_id: UUID | None,
        cutoff: datetime,
        batch_size: int,
    ) -> TrashPurgeResult:
        """领取并清理一批到期文档，所有 SQL 均附带空间和状态边界。"""

        # 1. 在短事务内按空间、截止时间和主键稳定领取候选文档。
        with self._sessions() as session:
            statement = select(documents.c.document_id, documents.c.workspace_id).where(
                documents.c.status == "deleted",
                documents.c.deleted_at.is_not(None),
                documents.c.deleted_at <= cutoff,
                # 活动解析租约可能仍在事务外写对象，当前批次必须等待其完成或失租。
                ~exists().where(
                    ingestion_jobs.c.workspace_id == documents.c.workspace_id,
                    ingestion_jobs.c.document_id == documents.c.document_id,
                    ingestion_jobs.c.status == "running",
                ),
            )
            if workspace_id is not None:
                statement = statement.where(documents.c.workspace_id == workspace_id)
            # 先收窄工作空间，再排序和限批，避免全局批次先占满后才应用空间过滤。
            statement = (
                statement.order_by(
                    documents.c.workspace_id, documents.c.deleted_at, documents.c.document_id
                )
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            candidates = tuple(session.execute(statement))
            external_requested = 0
            # 2. 先删除数据库事实，再为对象存储清理写入可重放 Outbox 意图。
            for row in candidates:
                keys = self._purge_document(
                    session,
                    workspace_id=row.workspace_id,
                    document_id=row.document_id,
                )
                self._record_external_cleanup_intent(
                    session,
                    workspace_id=row.workspace_id,
                    document_id=row.document_id,
                    cutoff=cutoff,
                    object_keys=keys,
                )
                external_requested += 1
            session.commit()
            return TrashPurgeResult(
                scanned=len(candidates),
                purged=len(candidates),
                external_cleanup_requested=external_requested,
            )

    def _purge_document(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        document_id: UUID,
    ) -> tuple[str, ...]:
        """按外键依赖顺序删除数据库事实，并在删除前收集对象键。"""

        # 1. 先收集版本、入库任务、来源和索引产生的外部对象键。
        version_ids = select(document_versions.c.document_version_id).where(
            document_versions.c.workspace_id == workspace_id,
            document_versions.c.document_id == document_id,
        )
        ingestion_ids = select(ingestion_jobs.c.ingestion_job_id).where(
            ingestion_jobs.c.workspace_id == workspace_id,
            ingestion_jobs.c.document_id == document_id,
        )
        source_keys = session.execute(
            select(document_sources.c.original_object_key).where(
                document_sources.c.workspace_id == workspace_id,
                document_sources.c.document_version_id.in_(version_ids),
                document_sources.c.original_object_key.is_not(None),
            )
        ).scalars()
        ingestion_keys = session.execute(
            select(
                ingestion_jobs.c.document_version_id,
                ingestion_jobs.c.ingestion_job_id,
                ingestion_jobs.c.source_object_key,
                ingestion_jobs.c.artifact_object_key,
            ).where(
                ingestion_jobs.c.workspace_id == workspace_id,
                ingestion_jobs.c.document_id == document_id,
            )
        )
        index_keys = session.execute(
            select(index_versions.c.artifact_object_key).where(
                index_versions.c.workspace_id == workspace_id,
                index_versions.c.document_id == document_id,
            )
        ).scalars()
        object_keys = {key for key in source_keys if key}
        for row in ingestion_keys:
            object_keys.add(row.source_object_key)
            if row.artifact_object_key:
                object_keys.add(row.artifact_object_key)
            object_keys.add(
                parsed_artifact_object_key(
                    workspace_id,
                    row.document_version_id,
                    row.ingestion_job_id,
                )
            )
        object_keys.update(key for key in index_keys if key)

        # 2. 按派生索引、任务、关系、版本和文档的依赖顺序删除事实。
        delete_statements = (
            delete(retrieval_chunks).where(
                retrieval_chunks.c.workspace_id == workspace_id,
                retrieval_chunks.c.document_id == document_id,
            ),
            delete(document_index_publications).where(
                document_index_publications.c.workspace_id == workspace_id,
                document_index_publications.c.document_id == document_id,
            ),
            delete(index_versions).where(
                index_versions.c.workspace_id == workspace_id,
                index_versions.c.document_id == document_id,
            ),
            delete(ingestion_job_attempts).where(
                ingestion_job_attempts.c.workspace_id == workspace_id,
                ingestion_job_attempts.c.ingestion_job_id.in_(ingestion_ids),
            ),
            delete(ingestion_job_stages).where(
                ingestion_job_stages.c.workspace_id == workspace_id,
                ingestion_job_stages.c.ingestion_job_id.in_(ingestion_ids),
            ),
            delete(ingestion_jobs).where(
                ingestion_jobs.c.workspace_id == workspace_id,
                ingestion_jobs.c.document_id == document_id,
            ),
            delete(document_favorites).where(
                document_favorites.c.workspace_id == workspace_id,
                document_favorites.c.document_id == document_id,
            ),
            delete(document_tag_bindings).where(
                document_tag_bindings.c.workspace_id == workspace_id,
                document_tag_bindings.c.document_id == document_id,
            ),
            delete(document_folder_bindings).where(
                document_folder_bindings.c.workspace_id == workspace_id,
                document_folder_bindings.c.document_id == document_id,
            ),
            delete(document_publications).where(
                document_publications.c.workspace_id == workspace_id,
                document_publications.c.document_id == document_id,
            ),
            delete(document_sources).where(
                document_sources.c.workspace_id == workspace_id,
                document_sources.c.document_version_id.in_(version_ids),
            ),
            delete(document_versions).where(
                document_versions.c.workspace_id == workspace_id,
                document_versions.c.document_id == document_id,
            ),
            delete(documents).where(
                documents.c.workspace_id == workspace_id,
                documents.c.document_id == document_id,
            ),
        )
        for statement in delete_statements:
            session.execute(statement)
        # 3. 返回排序后的对象键，使 Outbox 载荷稳定且可幂等重放。
        return tuple(sorted(object_keys))

    def _record_external_cleanup_intent(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        document_id: UUID,
        cutoff: datetime,
        object_keys: tuple[str, ...],
    ) -> None:
        """登记待处理对象键；事件消费者完成后才能声明外部清理成功。"""

        now = datetime.now(UTC)
        trace_id = uuid4().hex
        session.execute(
            insert(outbox_events).values(
                event_id=uuid4(),
                event_type=_EVENT_TYPE,
                schema_version=1,
                workspace_id=workspace_id,
                aggregate_id=document_id,
                aggregate_version=1,
                occurred_at=now,
                trace_id=trace_id,
                traceparent=f"00-{trace_id}-{'0' * 16}-01",
                actor_id=None,
                user_id=None,
                request_id=None,
                payload={
                    "resource_type": "knowledge_document",
                    "resource_id": str(document_id),
                    "external_object_keys": list(object_keys),
                    "retention_cutoff": cutoff.isoformat(),
                    "database_facts_purged": True,
                    "external_cleanup_status": "pending",
                    "purge_trigger": "retention",
                    "requested_by": self._worker_id,
                },
                status="pending",
                attempt_count=0,
                available_at=now,
                claimed_by=None,
                claim_until=None,
                last_error_code=None,
                published_at=None,
            )
        )
