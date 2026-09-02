"""实现知识事实、上传和入库任务的 PostgreSQL Repository 与 UoW。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime
from types import TracebackType
from typing import Any, cast
from uuid import UUID

from ai_platform_backend.indexing.persistence import (
    document_index_publications,
    index_versions,
    retrieval_chunks,
)
from ai_platform_backend.indexing.sqlalchemy import (
    deactivate_document_indexes,
    switch_active_document_index,
)
from ai_platform_backend.ingestion.domain import (
    IngestionJob,
    IngestionJobStatus,
    ingestion_lane_for_source,
)
from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from ai_platform_backend.knowledge.object_keys import parsed_artifact_object_key
from sqlalchemy import (
    and_,
    case,
    delete,
    false,
    func,
    insert,
    literal,
    null,
    or_,
    select,
    text,
    true,
    update,
)
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import CursorResult, Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.identity.domain.entitlements import UsageRepository
from ai_platform_api.modules.knowledge.domain.models import (
    Document,
    DocumentIndexStatus,
    DocumentIndexSummary,
    DocumentSource,
    DocumentSourceKind,
    DocumentVersion,
    DocumentVersionStatus,
    DocumentVisibility,
    KnowledgeBase,
    KnowledgeDocumentDetail,
    KnowledgeDocumentDownload,
    KnowledgeDocumentSummary,
    KnowledgeDocumentVersionDetail,
    KnowledgeSearchFilter,
    KnowledgeSearchItem,
    KnowledgeSearchPage,
    KnowledgeWriteConflictError,
    PersonalKnowledgeWorkbench,
    PersonalWorkbenchDocument,
    PersonalWorkbenchStatistics,
)
from ai_platform_api.modules.knowledge.domain.organization import (
    DEFAULT_FOLDER_NAME,
    InvalidOrganizationError,
    KnowledgeFolder,
    KnowledgeOrganizationRepository,
    KnowledgeTag,
    OrganizationWriteConflictError,
    default_folder_id,
)
from ai_platform_api.persistence.tables import (
    department_closure,
    document_accesses,
    document_favorites,
    document_folder_bindings,
    document_publications,
    document_sources,
    document_tag_bindings,
    document_versions,
    documents,
    enterprise_categories,
    enterprise_category_documents,
    ingestion_job_attempts,
    ingestion_job_stages,
    ingestion_jobs,
    knowledge_bases,
    knowledge_folders,
    knowledge_tags,
    workspace_memberships,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyKnowledgeRepository:
    """在授权条件下维护知识库、文档版本、来源、入库任务和索引指针。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_workspace_access(self, workspace_id: UUID, account_id: UUID) -> tuple[str, str] | None:
        row = self._session.execute(
            select(workspace_memberships.c.status, workspace_memberships.c.membership_type).where(
                workspace_memberships.c.workspace_id == workspace_id,
                workspace_memberships.c.account_id == account_id,
            )
        ).one_or_none()
        return tuple(row) if row is not None else None

    # 目录、标签和收藏与文档事实共用此 Repository，所有方法都把工作空间条件写入 SQL。
    def list_folders(
        self, workspace_id: UUID, *, include_deleted: bool = False
    ) -> tuple[KnowledgeFolder, ...]:
        statement = select(knowledge_folders).where(
            knowledge_folders.c.workspace_id == workspace_id
        )
        if not include_deleted:
            statement = statement.where(knowledge_folders.c.status == "active")
        rows = self._session.execute(
            statement.order_by(
                knowledge_folders.c.parent_folder_id,
                func.lower(knowledge_folders.c.name),
                knowledge_folders.c.folder_id,
            )
        )
        return tuple(_knowledge_folder(row) for row in rows)

    def get_folder(
        self, workspace_id: UUID, folder_id: UUID, *, for_update: bool = False
    ) -> KnowledgeFolder | None:
        statement = select(knowledge_folders).where(
            knowledge_folders.c.workspace_id == workspace_id,
            knowledge_folders.c.folder_id == folder_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _knowledge_folder(row) if row is not None else None

    def get_default_folder(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> KnowledgeFolder | None:
        """读取空间唯一默认根目录，并沿用工作空间条件做资源隔离。"""

        statement = select(knowledge_folders).where(
            knowledge_folders.c.workspace_id == workspace_id,
            knowledge_folders.c.is_default.is_(true()),
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _knowledge_folder(row) if row is not None else None

    def add_folder(self, folder: KnowledgeFolder) -> None:
        self._session.execute(insert(knowledge_folders).values(**_knowledge_folder_values(folder)))

    def ensure_default_folder(
        self, workspace_id: UUID, created_by_account_id: UUID, *, occurred_at: datetime
    ) -> KnowledgeFolder:
        """幂等创建默认根目录，兼容迁移前已存在的工作空间。"""

        existing = self.get_default_folder(workspace_id)
        if existing is not None:
            try:
                existing.assert_valid()
            except InvalidOrganizationError as error:
                raise OrganizationWriteConflictError from error
            return existing
        folder = KnowledgeFolder(
            folder_id=default_folder_id(workspace_id),
            workspace_id=workspace_id,
            name=DEFAULT_FOLDER_NAME,
            parent_folder_id=None,
            created_by_account_id=created_by_account_id,
            created_at=occurred_at,
            updated_at=occurred_at,
            is_default=True,
        )
        folder.assert_valid()
        self._session.execute(
            postgres_insert(knowledge_folders)
            .values(**_knowledge_folder_values(folder))
            .on_conflict_do_nothing(index_elements=[knowledge_folders.c.folder_id])
        )
        persisted = self.get_default_folder(workspace_id)
        if persisted is None:
            raise OrganizationWriteConflictError
        return persisted

    def save_folder(self, folder: KnowledgeFolder) -> None:
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(knowledge_folders)
                .where(
                    knowledge_folders.c.workspace_id == folder.workspace_id,
                    knowledge_folders.c.folder_id == folder.folder_id,
                    knowledge_folders.c.version == folder.version - 1,
                )
                .values(**_knowledge_folder_values(folder))
            ),
        )
        if result.rowcount != 1:
            raise OrganizationWriteConflictError

    def folder_name_exists(
        self,
        workspace_id: UUID,
        parent_folder_id: UUID | None,
        name: str,
        *,
        exclude_folder_id: UUID | None = None,
    ) -> bool:
        conditions = [
            knowledge_folders.c.workspace_id == workspace_id,
            knowledge_folders.c.status == "active",
            func.lower(knowledge_folders.c.name) == name.strip().lower(),
        ]
        if parent_folder_id is None:
            conditions.append(knowledge_folders.c.parent_folder_id.is_(None))
        else:
            conditions.append(knowledge_folders.c.parent_folder_id == parent_folder_id)
        if exclude_folder_id is not None:
            conditions.append(knowledge_folders.c.folder_id != exclude_folder_id)
        return bool(
            self._session.scalar(
                select(func.count()).select_from(knowledge_folders).where(*conditions)
            )
        )

    def folder_has_children(self, workspace_id: UUID, folder_id: UUID) -> bool:
        return bool(
            self._session.scalar(
                select(func.count())
                .select_from(knowledge_folders)
                .where(
                    knowledge_folders.c.workspace_id == workspace_id,
                    knowledge_folders.c.parent_folder_id == folder_id,
                    knowledge_folders.c.status.in_(("active", "deleted")),
                )
            )
        )

    def folder_has_documents(self, workspace_id: UUID, folder_id: UUID) -> bool:
        return bool(
            self._session.scalar(
                select(func.count())
                .select_from(document_folder_bindings.join(documents))
                .where(
                    document_folder_bindings.c.workspace_id == workspace_id,
                    document_folder_bindings.c.folder_id == folder_id,
                    documents.c.workspace_id == workspace_id,
                    documents.c.status.in_(("active", "deleted")),
                )
            )
        )

    def list_tags(
        self, workspace_id: UUID, *, include_deleted: bool = False
    ) -> tuple[KnowledgeTag, ...]:
        statement = select(knowledge_tags).where(knowledge_tags.c.workspace_id == workspace_id)
        if not include_deleted:
            statement = statement.where(knowledge_tags.c.status == "active")
        rows = self._session.execute(
            statement.order_by(func.lower(knowledge_tags.c.name), knowledge_tags.c.tag_id)
        )
        return tuple(_knowledge_tag(row) for row in rows)

    def get_tag(
        self, workspace_id: UUID, tag_id: UUID, *, for_update: bool = False
    ) -> KnowledgeTag | None:
        statement = select(knowledge_tags).where(
            knowledge_tags.c.workspace_id == workspace_id,
            knowledge_tags.c.tag_id == tag_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _knowledge_tag(row) if row is not None else None

    def add_tag(self, tag: KnowledgeTag) -> None:
        self._session.execute(insert(knowledge_tags).values(**_knowledge_tag_values(tag)))

    def save_tag(self, tag: KnowledgeTag) -> None:
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(knowledge_tags)
                .where(
                    knowledge_tags.c.workspace_id == tag.workspace_id,
                    knowledge_tags.c.tag_id == tag.tag_id,
                    knowledge_tags.c.version == tag.version - 1,
                )
                .values(**_knowledge_tag_values(tag))
            ),
        )
        if result.rowcount != 1:
            raise OrganizationWriteConflictError

    def tag_name_exists(
        self, workspace_id: UUID, name: str, *, exclude_tag_id: UUID | None = None
    ) -> bool:
        conditions = [
            knowledge_tags.c.workspace_id == workspace_id,
            knowledge_tags.c.status == "active",
            func.lower(knowledge_tags.c.name) == name.strip().lower(),
        ]
        if exclude_tag_id is not None:
            conditions.append(knowledge_tags.c.tag_id != exclude_tag_id)
        return bool(
            self._session.scalar(
                select(func.count()).select_from(knowledge_tags).where(*conditions)
            )
        )

    def bind_document_folder(
        self,
        workspace_id: UUID,
        document_id: UUID,
        folder_id: UUID,
        *,
        occurred_at: datetime,
    ) -> None:
        """以替换语义写入主目录，避免一个文档出现多个活动归属。"""

        self._session.execute(
            delete(document_folder_bindings).where(
                document_folder_bindings.c.workspace_id == workspace_id,
                document_folder_bindings.c.document_id == document_id,
            )
        )
        self._session.execute(
            insert(document_folder_bindings).values(
                workspace_id=workspace_id,
                document_id=document_id,
                folder_id=folder_id,
                created_at=occurred_at,
            )
        )

    def unbind_document_folder(
        self, workspace_id: UUID, document_id: UUID, folder_id: UUID
    ) -> None:
        self._session.execute(
            delete(document_folder_bindings).where(
                document_folder_bindings.c.workspace_id == workspace_id,
                document_folder_bindings.c.document_id == document_id,
                document_folder_bindings.c.folder_id == folder_id,
            )
        )

    def list_document_folders(
        self, workspace_id: UUID, document_id: UUID
    ) -> tuple[KnowledgeFolder, ...]:
        rows = self._session.execute(
            select(knowledge_folders)
            .join(
                document_folder_bindings,
                (document_folder_bindings.c.workspace_id == knowledge_folders.c.workspace_id)
                & (document_folder_bindings.c.folder_id == knowledge_folders.c.folder_id),
            )
            .where(
                document_folder_bindings.c.workspace_id == workspace_id,
                document_folder_bindings.c.document_id == document_id,
                knowledge_folders.c.status == "active",
            )
            .order_by(func.lower(knowledge_folders.c.name))
        )
        return tuple(_knowledge_folder(row) for row in rows)

    def get_document_folder(self, workspace_id: UUID, document_id: UUID) -> KnowledgeFolder | None:
        """读取文档主目录，包含已删除目录以支持恢复回落判断。"""

        row = self._session.execute(
            select(knowledge_folders)
            .join(
                document_folder_bindings,
                (document_folder_bindings.c.workspace_id == knowledge_folders.c.workspace_id)
                & (document_folder_bindings.c.folder_id == knowledge_folders.c.folder_id),
            )
            .where(
                document_folder_bindings.c.workspace_id == workspace_id,
                document_folder_bindings.c.document_id == document_id,
            )
        ).one_or_none()
        return _knowledge_folder(row) if row is not None else None

    def bind_document_tag(self, workspace_id: UUID, document_id: UUID, tag_id: UUID) -> None:
        existing = self._session.scalar(
            select(func.count())
            .select_from(document_tag_bindings)
            .where(
                document_tag_bindings.c.workspace_id == workspace_id,
                document_tag_bindings.c.document_id == document_id,
                document_tag_bindings.c.tag_id == tag_id,
            )
        )
        if not existing:
            from datetime import UTC, datetime

            self._session.execute(
                insert(document_tag_bindings).values(
                    workspace_id=workspace_id,
                    document_id=document_id,
                    tag_id=tag_id,
                    created_at=datetime.now(UTC),
                )
            )

    def unbind_document_tag(self, workspace_id: UUID, document_id: UUID, tag_id: UUID) -> None:
        self._session.execute(
            delete(document_tag_bindings).where(
                document_tag_bindings.c.workspace_id == workspace_id,
                document_tag_bindings.c.document_id == document_id,
                document_tag_bindings.c.tag_id == tag_id,
            )
        )

    def unbind_tag_documents(self, workspace_id: UUID, tag_id: UUID) -> None:
        """删除标签时清空本空间绑定，避免恢复标签后旧关系隐式复活。"""

        self._session.execute(
            delete(document_tag_bindings).where(
                document_tag_bindings.c.workspace_id == workspace_id,
                document_tag_bindings.c.tag_id == tag_id,
            )
        )

    def list_document_tags(self, workspace_id: UUID, document_id: UUID) -> tuple[KnowledgeTag, ...]:
        rows = self._session.execute(
            select(knowledge_tags)
            .join(
                document_tag_bindings,
                (document_tag_bindings.c.workspace_id == knowledge_tags.c.workspace_id)
                & (document_tag_bindings.c.tag_id == knowledge_tags.c.tag_id),
            )
            .where(
                document_tag_bindings.c.workspace_id == workspace_id,
                document_tag_bindings.c.document_id == document_id,
                knowledge_tags.c.status == "active",
            )
            .order_by(func.lower(knowledge_tags.c.name))
        )
        return tuple(_knowledge_tag(row) for row in rows)

    def add_favorite(
        self, workspace_id: UUID, account_id: UUID, document_id: UUID, *, occurred_at: datetime
    ) -> None:
        if self._session.scalar(
            select(func.count())
            .select_from(document_favorites)
            .where(
                document_favorites.c.workspace_id == workspace_id,
                document_favorites.c.account_id == account_id,
                document_favorites.c.document_id == document_id,
            )
        ):
            return
        self._session.execute(
            insert(document_favorites).values(
                workspace_id=workspace_id,
                account_id=account_id,
                document_id=document_id,
                created_at=occurred_at,
            )
        )

    def remove_favorite(self, workspace_id: UUID, account_id: UUID, document_id: UUID) -> None:
        self._session.execute(
            delete(document_favorites).where(
                document_favorites.c.workspace_id == workspace_id,
                document_favorites.c.account_id == account_id,
                document_favorites.c.document_id == document_id,
            )
        )

    def list_favorite_document_ids(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        limit: int,
        authorized_workspace: bool,
        department_ids: frozenset[UUID],
        account_ids: frozenset[UUID],
        resource_ids: frozenset[UUID],
    ) -> tuple[UUID, ...]:
        rows = self._session.execute(
            select(document_favorites.c.document_id)
            .join(
                documents,
                (documents.c.workspace_id == document_favorites.c.workspace_id)
                & (documents.c.document_id == document_favorites.c.document_id),
            )
            .where(
                document_favorites.c.workspace_id == workspace_id,
                document_favorites.c.account_id == account_id,
                documents.c.status == "active",
                _document_scope(
                    authorized_workspace=authorized_workspace,
                    department_ids=department_ids,
                    account_ids=account_ids,
                    resource_ids=resource_ids,
                ),
            )
            .order_by(document_favorites.c.created_at.desc())
            .limit(limit)
        )
        return tuple(row.document_id for row in rows)

    def list_trash_documents(self, workspace_id: UUID, *, limit: int) -> tuple[Document, ...]:
        rows = self._session.execute(
            select(documents)
            .where(
                documents.c.workspace_id == workspace_id,
                documents.c.status == "deleted",
            )
            .order_by(documents.c.deleted_at.desc(), documents.c.document_id)
            .limit(limit)
        )
        return tuple(_document(row) for row in rows)

    def permanently_delete_folder(self, workspace_id: UUID, folder_id: UUID) -> None:
        self._session.execute(
            delete(document_folder_bindings).where(
                document_folder_bindings.c.workspace_id == workspace_id,
                document_folder_bindings.c.folder_id == folder_id,
            )
        )
        self._session.execute(
            delete(knowledge_folders).where(
                knowledge_folders.c.workspace_id == workspace_id,
                knowledge_folders.c.folder_id == folder_id,
            )
        )

    def has_running_ingestion_jobs(self, workspace_id: UUID, document_id: UUID) -> bool:
        """判断文档是否仍有可能在事务外写入解析产物的活动租约。"""

        return bool(
            self._session.scalar(
                select(func.count())
                .select_from(ingestion_jobs)
                .where(
                    ingestion_jobs.c.workspace_id == workspace_id,
                    ingestion_jobs.c.document_id == document_id,
                    ingestion_jobs.c.status == "running",
                )
            )
        )

    def permanently_delete_document(self, workspace_id: UUID, document_id: UUID) -> tuple[str, ...]:
        """收集外部对象键后按依赖顺序清理文档数据库事实。"""

        # 1. 删除前收集原文件、解析产物和索引产物对象键；事件必须与数据库删除同事务形成。
        version_ids = select(document_versions.c.document_version_id).where(
            document_versions.c.workspace_id == workspace_id,
            document_versions.c.document_id == document_id,
        )
        ingestion_ids = select(ingestion_jobs.c.ingestion_job_id).where(
            ingestion_jobs.c.workspace_id == workspace_id,
            ingestion_jobs.c.document_id == document_id,
        )
        source_keys = tuple(
            self._session.execute(
                select(document_sources.c.original_object_key).where(
                    document_sources.c.workspace_id == workspace_id,
                    document_sources.c.document_version_id.in_(version_ids),
                    document_sources.c.original_object_key.is_not(None),
                )
            ).scalars()
        )
        ingestion_keys = tuple(
            self._session.execute(
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
        )
        index_keys = tuple(
            self._session.execute(
                select(index_versions.c.artifact_object_key).where(
                    index_versions.c.workspace_id == workspace_id,
                    index_versions.c.document_id == document_id,
                )
            ).scalars()
        )
        object_keys = {key for key in source_keys if key}
        for row in ingestion_keys:
            object_keys.add(row.source_object_key)
            if row.artifact_object_key:
                object_keys.add(row.artifact_object_key)
            # 产物键由任务身份确定；即使 Worker 尚未回写数据库，也必须进入清理意图。
            object_keys.add(
                parsed_artifact_object_key(
                    workspace_id,
                    row.document_version_id,
                    row.ingestion_job_id,
                )
            )
        object_keys.update(key for key in index_keys if key)

        # 2. Attempt 历史默认不可变；仅在当前已授权清理事务内开启既有删除旁路。
        self._session.execute(text("SET LOCAL ai_platform.lifecycle_purge = 'on'"))
        # 3. 按索引、任务、组织关系、版本和文档的外键依赖顺序删除派生事实。
        self._session.execute(
            delete(retrieval_chunks).where(
                retrieval_chunks.c.document_id == document_id,
                retrieval_chunks.c.workspace_id == workspace_id,
            )
        )
        self._session.execute(
            delete(document_index_publications).where(
                document_index_publications.c.workspace_id == workspace_id,
                document_index_publications.c.document_id == document_id,
            )
        )
        self._session.execute(
            delete(index_versions).where(
                index_versions.c.workspace_id == workspace_id,
                index_versions.c.document_id == document_id,
            )
        )
        self._session.execute(
            delete(ingestion_job_attempts).where(
                ingestion_job_attempts.c.workspace_id == workspace_id,
                ingestion_job_attempts.c.ingestion_job_id.in_(ingestion_ids),
            )
        )
        self._session.execute(
            delete(ingestion_job_stages).where(
                ingestion_job_stages.c.workspace_id == workspace_id,
                ingestion_job_stages.c.ingestion_job_id.in_(ingestion_ids),
            )
        )
        self._session.execute(
            delete(ingestion_jobs).where(
                ingestion_jobs.c.workspace_id == workspace_id,
                ingestion_jobs.c.document_id == document_id,
            )
        )
        self._session.execute(
            delete(document_favorites).where(
                document_favorites.c.workspace_id == workspace_id,
                document_favorites.c.document_id == document_id,
            )
        )
        self._session.execute(
            delete(document_tag_bindings).where(
                document_tag_bindings.c.workspace_id == workspace_id,
                document_tag_bindings.c.document_id == document_id,
            )
        )
        self._session.execute(
            delete(document_folder_bindings).where(
                document_folder_bindings.c.workspace_id == workspace_id,
                document_folder_bindings.c.document_id == document_id,
            )
        )
        self._session.execute(
            delete(document_publications).where(
                document_publications.c.workspace_id == workspace_id,
                document_publications.c.document_id == document_id,
            )
        )
        self._session.execute(
            delete(document_sources).where(
                document_sources.c.workspace_id == workspace_id,
                document_sources.c.document_version_id.in_(version_ids),
            )
        )
        # 4. 最后删除来源、版本和文档主事实，保留外部清理由 Outbox 接管。
        self._session.execute(
            delete(document_versions).where(
                document_versions.c.workspace_id == workspace_id,
                document_versions.c.document_id == document_id,
            )
        )
        self._session.execute(
            delete(documents).where(
                documents.c.workspace_id == workspace_id, documents.c.document_id == document_id
            )
        )
        # 5. 稳定排序让 Outbox 载荷可比较、可重放，实际对象删除由 Worker 接管。
        return tuple(sorted(object_keys))

    def departments_exist(self, workspace_id: UUID, department_ids: frozenset[UUID]) -> bool:
        if not department_ids:
            return True
        count = self._session.scalar(
            select(func.count(func.distinct(department_closure.c.descendant_department_id))).where(
                department_closure.c.workspace_id == workspace_id,
                department_closure.c.descendant_department_id.in_(department_ids),
            )
        )
        return int(count or 0) == len(department_ids)

    def add_knowledge_base(self, knowledge_base: KnowledgeBase) -> None:
        self._session.execute(
            insert(knowledge_bases).values(**_knowledge_base_values(knowledge_base))
        )

    def list_knowledge_bases(
        self,
        workspace_id: UUID,
        *,
        limit: int,
        authorized_workspace: bool,
        department_ids: frozenset[UUID],
        resource_ids: frozenset[UUID],
    ) -> tuple[KnowledgeBase, ...]:
        scope: ColumnElement[bool] = (
            true()
            if authorized_workspace
            else or_(
                knowledge_bases.c.knowledge_base_id.in_(resource_ids),
                knowledge_bases.c.department_ids.overlap(list(department_ids)),
            )
            if department_ids or resource_ids
            else false()
        )
        rows = self._session.execute(
            select(knowledge_bases)
            .where(
                knowledge_bases.c.workspace_id == workspace_id,
                knowledge_bases.c.status == "active",
                scope,
            )
            .order_by(knowledge_bases.c.updated_at.desc(), knowledge_bases.c.knowledge_base_id)
            .limit(limit)
        )
        return tuple(_knowledge_base(row) for row in rows)

    def get_knowledge_base(
        self,
        workspace_id: UUID,
        knowledge_base_id: UUID,
        *,
        for_update: bool = False,
    ) -> KnowledgeBase | None:
        statement = select(knowledge_bases).where(
            knowledge_bases.c.workspace_id == workspace_id,
            knowledge_bases.c.knowledge_base_id == knowledge_base_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _knowledge_base(row) if row is not None else None

    def save_knowledge_base(self, knowledge_base: KnowledgeBase) -> None:
        previous_version = knowledge_base.version - 1
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(knowledge_bases)
                .where(
                    knowledge_bases.c.workspace_id == knowledge_base.workspace_id,
                    knowledge_bases.c.knowledge_base_id == knowledge_base.knowledge_base_id,
                    knowledge_bases.c.version == previous_version,
                )
                .values(**_knowledge_base_values(knowledge_base))
            ),
        )
        if result.rowcount != 1:
            raise KnowledgeWriteConflictError

    def has_active_documents(self, workspace_id: UUID, knowledge_base_id: UUID) -> bool:
        return bool(
            self._session.scalar(
                select(func.count())
                .select_from(documents)
                .where(
                    documents.c.workspace_id == workspace_id,
                    documents.c.knowledge_base_id == knowledge_base_id,
                    documents.c.status == "active",
                )
            )
        )

    def add_document(self, document: Document) -> None:
        self._session.execute(insert(documents).values(**_document_values(document)))
        default_folder = self.ensure_default_folder(
            document.workspace_id,
            document.created_by_account_id,
            occurred_at=document.created_at,
        )
        self.bind_document_folder(
            document.workspace_id,
            document.document_id,
            default_folder.folder_id,
            occurred_at=document.created_at,
        )

    def list_document_summaries(
        self,
        workspace_id: UUID,
        knowledge_base_id: UUID,
        *,
        viewer_account_id: UUID,
        limit: int,
        authorized_workspace: bool,
        department_ids: frozenset[UUID],
        account_ids: frozenset[UUID],
        resource_ids: frozenset[UUID],
    ) -> tuple[KnowledgeDocumentSummary, ...]:
        # 1. 先构造服务端授权条件，目录与标签筛选不能替代文档资源范围。
        scope = _document_scope(
            authorized_workspace=authorized_workspace,
            department_ids=department_ids,
            account_ids=account_ids,
            resource_ids=resource_ids,
        )
        latest_versions = (
            select(
                document_versions.c.workspace_id,
                document_versions.c.document_id,
                func.max(document_versions.c.version_number).label("latest_version_number"),
            )
            .where(document_versions.c.workspace_id == workspace_id)
            .group_by(document_versions.c.workspace_id, document_versions.c.document_id)
            .subquery()
        )
        # 2. 组织关系通过空间化相关子查询投影，避免多标签 JOIN 放大文档列表行数。
        folder_id = (
            select(document_folder_bindings.c.folder_id)
            .where(
                document_folder_bindings.c.workspace_id == documents.c.workspace_id,
                document_folder_bindings.c.document_id == documents.c.document_id,
            )
            .scalar_subquery()
        )
        tag_ids = (
            select(func.array_agg(document_tag_bindings.c.tag_id))
            .select_from(
                document_tag_bindings.join(
                    knowledge_tags,
                    (knowledge_tags.c.workspace_id == document_tag_bindings.c.workspace_id)
                    & (knowledge_tags.c.tag_id == document_tag_bindings.c.tag_id),
                )
            )
            .where(
                document_tag_bindings.c.workspace_id == documents.c.workspace_id,
                document_tag_bindings.c.document_id == documents.c.document_id,
                knowledge_tags.c.status == "active",
            )
            .scalar_subquery()
        )
        favorite_count = (
            select(func.count())
            .select_from(document_favorites)
            .where(
                document_favorites.c.workspace_id == documents.c.workspace_id,
                document_favorites.c.document_id == documents.c.document_id,
                document_favorites.c.account_id == viewer_account_id,
            )
            .scalar_subquery()
        )
        # 3. 最终查询同时应用工作空间、知识库、活动状态和资源授权，再生成页面摘要。
        rows = self._session.execute(
            select(
                documents,
                document_versions.c.document_version_id.label("latest_document_version_id"),
                document_versions.c.version_number.label("latest_version_number"),
                document_versions.c.status.label("latest_version_status"),
                document_versions.c.content_hash.label("latest_content_hash"),
                document_versions.c.created_by_account_id.label("latest_created_by_account_id"),
                document_versions.c.created_at.label("latest_created_at"),
                document_versions.c.published_at.label("latest_published_at"),
                document_versions.c.record_version.label("latest_record_version"),
                document_sources.c.source_id.label("latest_source_id"),
                document_sources.c.source_kind.label("latest_source_kind"),
                document_sources.c.source_name.label("latest_source_name"),
                document_publications.c.current_document_version_id,
                func.coalesce(folder_id, default_folder_id(workspace_id)).label("folder_id"),
                tag_ids.label("tag_ids"),
                (favorite_count > 0).label("is_favorite"),
            )
            .join(
                latest_versions,
                (latest_versions.c.workspace_id == documents.c.workspace_id)
                & (latest_versions.c.document_id == documents.c.document_id),
            )
            .join(
                document_versions,
                (document_versions.c.workspace_id == latest_versions.c.workspace_id)
                & (document_versions.c.document_id == latest_versions.c.document_id)
                & (document_versions.c.version_number == latest_versions.c.latest_version_number),
            )
            .join(
                document_sources,
                (document_sources.c.workspace_id == document_versions.c.workspace_id)
                & (
                    document_sources.c.document_version_id
                    == document_versions.c.document_version_id
                ),
            )
            .outerjoin(
                document_publications,
                (document_publications.c.workspace_id == documents.c.workspace_id)
                & (document_publications.c.document_id == documents.c.document_id),
            )
            .where(
                documents.c.workspace_id == workspace_id,
                documents.c.knowledge_base_id == knowledge_base_id,
                documents.c.status == "active",
                scope,
            )
            .order_by(documents.c.updated_at.desc(), documents.c.document_id)
            .limit(limit)
        )
        return tuple(_document_summary(row) for row in rows)

    def get_document(
        self,
        workspace_id: UUID,
        document_id: UUID,
        *,
        for_update: bool = False,
    ) -> Document | None:
        statement = select(documents).where(
            documents.c.workspace_id == workspace_id,
            documents.c.document_id == document_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _document(row) if row is not None else None

    def document_requires_publish_approval(self, workspace_id: UUID, document_id: UUID) -> bool:
        """只要命中一个活动审批分类，直接发布入口就必须失败关闭。"""

        return (
            self._session.execute(
                select(enterprise_categories.c.category_id)
                .select_from(
                    enterprise_categories.join(
                        enterprise_category_documents,
                        (
                            enterprise_category_documents.c.workspace_id
                            == enterprise_categories.c.workspace_id
                        )
                        & (
                            enterprise_category_documents.c.category_id
                            == enterprise_categories.c.category_id
                        ),
                    )
                )
                .where(
                    enterprise_category_documents.c.workspace_id == workspace_id,
                    enterprise_category_documents.c.document_id == document_id,
                    enterprise_categories.c.status == "active",
                    enterprise_categories.c.approval_required.is_(True),
                )
                .limit(1)
            ).scalar_one_or_none()
            is not None
        )

    def get_document_detail(
        self,
        workspace_id: UUID,
        knowledge_base_id: UUID,
        document_id: UUID,
        *,
        viewer_account_id: UUID,
        authorized_workspace: bool,
        department_ids: frozenset[UUID],
        account_ids: frozenset[UUID],
        resource_ids: frozenset[UUID],
    ) -> KnowledgeDocumentDetail | None:
        """按资源范围聚合版本、解析和索引事实，所有子查询都固定在同一工作空间。"""

        # 1. 先建立文档资源授权条件和当前账号的组织投影，不允许目录或标签扩大资源范围。
        scope = _document_scope(
            authorized_workspace=authorized_workspace,
            department_ids=department_ids,
            account_ids=account_ids,
            resource_ids=resource_ids,
        )
        folder_id = (
            select(document_folder_bindings.c.folder_id)
            .where(
                document_folder_bindings.c.workspace_id == documents.c.workspace_id,
                document_folder_bindings.c.document_id == documents.c.document_id,
            )
            .scalar_subquery()
        )
        tag_ids = (
            select(func.array_agg(document_tag_bindings.c.tag_id))
            .select_from(
                document_tag_bindings.join(
                    knowledge_tags,
                    (knowledge_tags.c.workspace_id == document_tag_bindings.c.workspace_id)
                    & (knowledge_tags.c.tag_id == document_tag_bindings.c.tag_id),
                )
            )
            .where(
                document_tag_bindings.c.workspace_id == documents.c.workspace_id,
                document_tag_bindings.c.document_id == documents.c.document_id,
                knowledge_tags.c.status == "active",
            )
            .scalar_subquery()
        )
        favorite_count = (
            select(func.count())
            .select_from(document_favorites)
            .where(
                document_favorites.c.workspace_id == documents.c.workspace_id,
                document_favorites.c.document_id == documents.c.document_id,
                document_favorites.c.account_id == viewer_account_id,
            )
            .scalar_subquery()
        )
        # 2. 主查询同时约束工作空间、知识库、活动状态和资源 ID，不可见与不存在返回同一结果。
        document_row = self._session.execute(
            select(
                documents,
                document_publications.c.current_document_version_id,
                func.coalesce(folder_id, default_folder_id(workspace_id)).label("folder_id"),
                tag_ids.label("tag_ids"),
                (favorite_count > 0).label("is_favorite"),
            )
            .outerjoin(
                document_publications,
                (document_publications.c.workspace_id == documents.c.workspace_id)
                & (document_publications.c.document_id == documents.c.document_id),
            )
            .where(
                documents.c.workspace_id == workspace_id,
                documents.c.knowledge_base_id == knowledge_base_id,
                documents.c.document_id == document_id,
                documents.c.status == "active",
                scope,
            )
        ).one_or_none()
        if document_row is None:
            return None

        # 3. 只有主文档通过授权后才读取其版本、解析和索引子事实，且每组查询重复空间条件。
        version_rows = tuple(
            self._session.execute(
                select(document_versions, document_sources)
                .join(
                    document_sources,
                    (document_sources.c.workspace_id == document_versions.c.workspace_id)
                    & (
                        document_sources.c.document_version_id
                        == document_versions.c.document_version_id
                    ),
                )
                .where(
                    document_versions.c.workspace_id == workspace_id,
                    document_versions.c.document_id == document_id,
                )
                .order_by(document_versions.c.version_number.desc())
            )
        )
        job_by_version = {
            row.document_version_id: _ingestion_job(row)
            for row in self._session.execute(
                select(ingestion_jobs).where(
                    ingestion_jobs.c.workspace_id == workspace_id,
                    ingestion_jobs.c.document_id == document_id,
                )
            )
        }
        index_by_version: dict[UUID, DocumentIndexSummary] = {}
        for row in self._session.execute(
            select(index_versions)
            .where(
                index_versions.c.workspace_id == workspace_id,
                index_versions.c.document_id == document_id,
            )
            .order_by(
                index_versions.c.document_version_id,
                index_versions.c.build_no.desc(),
                index_versions.c.updated_at.desc(),
            )
        ):
            index_by_version.setdefault(row.document_version_id, _document_index_summary(row))
        # 4. 每个版本只选择最新构建批次，并在领域结果中排除解析和索引对象键。
        return KnowledgeDocumentDetail(
            document=_document(document_row),
            current_document_version_id=document_row.current_document_version_id,
            folder_id=document_row.folder_id,
            tag_ids=tuple(document_row.tag_ids or ()),
            is_favorite=document_row.is_favorite,
            versions=tuple(
                KnowledgeDocumentVersionDetail(
                    version=_version(row),
                    source=_source(row),
                    ingestion_job=job_by_version.get(row.document_version_id),
                    index=index_by_version.get(row.document_version_id),
                )
                for row in version_rows
            ),
        )

    def get_document_download(
        self,
        workspace_id: UUID,
        knowledge_base_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
        *,
        authorized_workspace: bool,
        department_ids: frozenset[UUID],
        account_ids: frozenset[UUID],
        resource_ids: frozenset[UUID],
    ) -> KnowledgeDocumentDownload | None:
        """只为有原始上传对象的活动文档返回服务端下载定位描述。"""

        # 1. 下载描述查询把文档资源范围和版本、来源归属放在同一 SQL 条件中。
        row = self._session.execute(
            select(
                document_sources.c.source_id,
                document_sources.c.source_name,
                document_sources.c.media_type,
                document_sources.c.size_bytes,
                document_sources.c.original_object_key,
            )
            .select_from(documents)
            .join(
                document_versions,
                (document_versions.c.workspace_id == documents.c.workspace_id)
                & (document_versions.c.document_id == documents.c.document_id),
            )
            .join(
                document_sources,
                (document_sources.c.workspace_id == document_versions.c.workspace_id)
                & (
                    document_sources.c.document_version_id
                    == document_versions.c.document_version_id
                ),
            )
            .where(
                documents.c.workspace_id == workspace_id,
                documents.c.knowledge_base_id == knowledge_base_id,
                documents.c.document_id == document_id,
                documents.c.status == "active",
                document_versions.c.document_version_id == document_version_id,
                document_sources.c.source_kind == "upload",
                document_sources.c.media_type.is_not(None),
                document_sources.c.size_bytes.is_not(None),
                document_sources.c.original_object_key.is_not(None),
                _document_scope(
                    authorized_workspace=authorized_workspace,
                    department_ids=department_ids,
                    account_ids=account_ids,
                    resource_ids=resource_ids,
                ),
            )
        ).one_or_none()
        # 2. 非上传来源、元数据不完整、跨空间或越权版本统一表现为不可下载。
        if row is None:
            return None
        return KnowledgeDocumentDownload(
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            document_version_id=document_version_id,
            source_id=row.source_id,
            file_name=row.source_name,
            media_type=cast(str, row.media_type),
            size_bytes=cast(int, row.size_bytes),
            object_key=cast(str, row.original_object_key),
        )

    def get_personal_workbench(
        self,
        workspace_id: UUID,
        *,
        viewer_account_id: UUID,
        recent_limit: int,
        favorite_limit: int,
        authorized_workspace: bool,
        department_ids: frozenset[UUID],
        account_ids: frozenset[UUID],
        resource_ids: frozenset[UUID],
        maximum_security_level: SecurityLevel,
    ) -> PersonalKnowledgeWorkbench:
        """从同一授权文档集合聚合统计、最近活动和收藏。"""

        # 长函数保留原因: 统计和两类文档列表必须复用完全相同的授权表达式，集中编排可防止口径漂移。
        # 1. 先冻结授权、密级、收藏、访问和当前索引表达式，后续聚合只从该集合派生。
        scope = _document_scope(
            authorized_workspace=authorized_workspace,
            department_ids=department_ids,
            account_ids=account_ids,
            resource_ids=resource_ids,
        )
        security_scope = _document_security_scope(maximum_security_level)
        favorite_count = (
            select(func.count())
            .select_from(document_favorites)
            .where(
                document_favorites.c.workspace_id == documents.c.workspace_id,
                document_favorites.c.document_id == documents.c.document_id,
                document_favorites.c.account_id == viewer_account_id,
            )
            .scalar_subquery()
        )
        last_accessed_at = (
            select(document_accesses.c.last_accessed_at)
            .where(
                document_accesses.c.workspace_id == documents.c.workspace_id,
                document_accesses.c.document_id == documents.c.document_id,
                document_accesses.c.account_id == viewer_account_id,
            )
            .scalar_subquery()
        )
        indexed = and_(
            document_index_publications.c.index_version_id.is_not(None),
            index_versions.c.status == "active",
        )
        common_from = (
            documents.join(
                knowledge_bases,
                (knowledge_bases.c.workspace_id == documents.c.workspace_id)
                & (knowledge_bases.c.knowledge_base_id == documents.c.knowledge_base_id),
            )
            .outerjoin(
                document_publications,
                (document_publications.c.workspace_id == documents.c.workspace_id)
                & (document_publications.c.document_id == documents.c.document_id),
            )
            .outerjoin(
                document_index_publications,
                (document_index_publications.c.workspace_id == document_publications.c.workspace_id)
                & (document_index_publications.c.document_id == document_publications.c.document_id)
                & (
                    document_index_publications.c.document_version_id
                    == document_publications.c.current_document_version_id
                ),
            )
            .outerjoin(
                index_versions,
                (index_versions.c.workspace_id == document_index_publications.c.workspace_id)
                & (
                    index_versions.c.index_version_id
                    == document_index_publications.c.index_version_id
                ),
            )
        )
        common_where = (
            documents.c.workspace_id == workspace_id,
            documents.c.status == "active",
            knowledge_bases.c.status == "active",
            scope,
            security_scope,
        )

        # 2. 工作空间级授权可以看到空知识库；受限授权只能从实际获权文档反推知识库。
        if authorized_workspace:
            knowledge_base_count = int(
                self._session.scalar(
                    select(func.count())
                    .select_from(knowledge_bases)
                    .where(
                        knowledge_bases.c.workspace_id == workspace_id,
                        knowledge_bases.c.status == "active",
                    )
                )
                or 0
            )
        else:
            knowledge_base_count = int(
                self._session.scalar(
                    select(func.count(func.distinct(documents.c.knowledge_base_id)))
                    .select_from(
                        documents.join(
                            knowledge_bases,
                            (knowledge_bases.c.workspace_id == documents.c.workspace_id)
                            & (
                                knowledge_bases.c.knowledge_base_id == documents.c.knowledge_base_id
                            ),
                        )
                    )
                    .where(*common_where)
                )
                or 0
            )

        statistics_row = self._session.execute(
            select(
                func.count(func.distinct(documents.c.document_id)).label("document_count"),
                func.count(func.distinct(document_publications.c.document_id)).label(
                    "published_document_count"
                ),
                func.count(
                    func.distinct(case((favorite_count > 0, documents.c.document_id)))
                ).label("favorite_document_count"),
                func.count(func.distinct(case((indexed, documents.c.document_id)))).label(
                    "indexed_document_count"
                ),
            )
            .select_from(common_from)
            .where(*common_where)
        ).one()
        published_count = int(statistics_row.published_document_count or 0)
        indexed_count = int(statistics_row.indexed_document_count or 0)
        statistics = PersonalWorkbenchStatistics(
            knowledge_base_count=knowledge_base_count,
            document_count=int(statistics_row.document_count or 0),
            published_document_count=published_count,
            favorite_document_count=int(statistics_row.favorite_document_count or 0),
            indexed_document_count=indexed_count,
            pending_index_document_count=max(0, published_count - indexed_count),
        )
        # 3. 最近访问和收藏使用同一低敏投影，排序差异不改变资源可见集合。
        projection = select(
            documents.c.document_id,
            documents.c.knowledge_base_id,
            knowledge_bases.c.name.label("knowledge_base_name"),
            documents.c.title,
            documents.c.updated_at,
            document_publications.c.published_at,
            last_accessed_at.label("last_accessed_at"),
            (favorite_count > 0).label("is_favorite"),
            indexed.label("is_indexed"),
        ).select_from(common_from)
        recent_rows = self._session.execute(
            projection.where(*common_where)
            .order_by(
                last_accessed_at.desc().nullslast(),
                documents.c.updated_at.desc(),
                documents.c.document_id,
            )
            .limit(recent_limit)
        )
        favorite_rows = self._session.execute(
            projection.where(*common_where, favorite_count > 0)
            .order_by(documents.c.updated_at.desc(), documents.c.document_id)
            .limit(favorite_limit)
        )
        return PersonalKnowledgeWorkbench(
            statistics=statistics,
            recent_documents=tuple(_workbench_document(row) for row in recent_rows),
            favorite_documents=tuple(_workbench_document(row) for row in favorite_rows),
        )

    def search_published_documents(
        self,
        workspace_id: UUID,
        *,
        viewer_account_id: UUID,
        query: str,
        knowledge_base_id: UUID | None,
        match_type: KnowledgeSearchFilter,
        favorite_only: bool,
        include_content: bool,
        limit: int,
        offset: int,
        authorized_workspace: bool,
        department_ids: frozenset[UUID],
        account_ids: frozenset[UUID],
        resource_ids: frozenset[UUID],
        maximum_security_level: SecurityLevel,
    ) -> KnowledgeSearchPage:
        """搜索当前发布版本，正文命中只读取与发布指针对齐的活动 Chunk。"""

        # 长函数保留原因: 标题与正文分支共用候选授权、发布指针和分页口径，
        # 集中编排可审计“不读正文”边界。
        # 1. 先建立获权且已发布的候选集合，并独立统计当前版本未建立活动索引的数量。
        normalized_query = query.casefold()
        scope = _document_scope(
            authorized_workspace=authorized_workspace,
            department_ids=department_ids,
            account_ids=account_ids,
            resource_ids=resource_ids,
        )
        favorite_count = (
            select(func.count())
            .select_from(document_favorites)
            .where(
                document_favorites.c.workspace_id == documents.c.workspace_id,
                document_favorites.c.document_id == documents.c.document_id,
                document_favorites.c.account_id == viewer_account_id,
            )
            .scalar_subquery()
        )
        candidate_where: list[ColumnElement[bool]] = [
            documents.c.workspace_id == workspace_id,
            documents.c.status == "active",
            knowledge_bases.c.status == "active",
            scope,
            _document_security_scope(maximum_security_level),
        ]
        if knowledge_base_id is not None:
            candidate_where.append(documents.c.knowledge_base_id == knowledge_base_id)
        if favorite_only:
            candidate_where.append(favorite_count > 0)

        publication_from = documents.join(
            knowledge_bases,
            (knowledge_bases.c.workspace_id == documents.c.workspace_id)
            & (knowledge_bases.c.knowledge_base_id == documents.c.knowledge_base_id),
        ).join(
            document_publications,
            (document_publications.c.workspace_id == documents.c.workspace_id)
            & (document_publications.c.document_id == documents.c.document_id),
        )
        unavailable_index_count = int(
            self._session.scalar(
                select(func.count())
                .select_from(
                    publication_from.outerjoin(
                        document_index_publications,
                        (document_index_publications.c.workspace_id == documents.c.workspace_id)
                        & (document_index_publications.c.document_id == documents.c.document_id)
                        & (
                            document_index_publications.c.document_version_id
                            == document_publications.c.current_document_version_id
                        ),
                    ).outerjoin(
                        index_versions,
                        (
                            index_versions.c.workspace_id
                            == document_index_publications.c.workspace_id
                        )
                        & (
                            index_versions.c.index_version_id
                            == document_index_publications.c.index_version_id
                        )
                        & (index_versions.c.status == "active"),
                    )
                )
                .where(*candidate_where, index_versions.c.index_version_id.is_(None))
            )
            or 0
        )
        if match_type == "content" and not include_content:
            return KnowledgeSearchPage((), 0, unavailable_index_count, False)

        title_match = func.strpos(func.lower(documents.c.title), normalized_query) > 0
        # 2. 字段遮罩关闭正文或用户仅搜索标题时，SQL 本身不得引用 Chunk 表，避免“未返回但已读取”。
        if match_type == "title" or not include_content:
            title_statement = (
                select(
                    documents.c.document_id,
                    documents.c.knowledge_base_id,
                    knowledge_bases.c.name.label("knowledge_base_name"),
                    documents.c.title,
                    documents.c.updated_at,
                    document_publications.c.published_at,
                    (favorite_count > 0).label("is_favorite"),
                    literal("title").label("matched_by"),
                    null().label("excerpt"),
                    null().label("chunk_id"),
                    null().label("sequence_no"),
                )
                .select_from(publication_from)
                .where(*candidate_where, title_match)
            )
            total = int(
                self._session.scalar(select(func.count()).select_from(title_statement.subquery()))
                or 0
            )
            rows = self._session.execute(
                title_statement.order_by(
                    documents.c.updated_at.desc(),
                    documents.c.document_id,
                )
                .limit(limit)
                .offset(offset)
            )
            return KnowledgeSearchPage(
                items=tuple(_search_item(row) for row in rows),
                total=total,
                unavailable_index_document_count=unavailable_index_count,
                content_search_available=include_content,
            )

        # 3. 正文路径只连接当前发布版本对应的活动索引，并为每篇文档截取首个稳定命中。
        matching_chunk = (
            select(
                retrieval_chunks.c.chunk_id,
                retrieval_chunks.c.sequence_no,
                func.substr(
                    retrieval_chunks.c.content,
                    func.greatest(
                        func.strpos(func.lower(retrieval_chunks.c.content), normalized_query) - 60,
                        1,
                    ),
                    220,
                ).label("excerpt"),
            )
            .where(
                retrieval_chunks.c.workspace_id == documents.c.workspace_id,
                retrieval_chunks.c.document_id == documents.c.document_id,
                retrieval_chunks.c.document_version_id
                == document_publications.c.current_document_version_id,
                retrieval_chunks.c.index_version_id == index_versions.c.index_version_id,
                retrieval_chunks.c.active.is_(True),
                func.strpos(func.lower(retrieval_chunks.c.content), normalized_query) > 0,
            )
            .order_by(retrieval_chunks.c.sequence_no, retrieval_chunks.c.chunk_id)
            .limit(1)
            .lateral("matching_chunk")
        )
        searchable_from = (
            publication_from.outerjoin(
                document_index_publications,
                (document_index_publications.c.workspace_id == documents.c.workspace_id)
                & (document_index_publications.c.document_id == documents.c.document_id)
                & (
                    document_index_publications.c.document_version_id
                    == document_publications.c.current_document_version_id
                ),
            )
            .outerjoin(
                index_versions,
                (index_versions.c.workspace_id == document_index_publications.c.workspace_id)
                & (
                    index_versions.c.index_version_id
                    == document_index_publications.c.index_version_id
                )
                & (index_versions.c.status == "active"),
            )
            .outerjoin(matching_chunk, true())
        )
        content_match = matching_chunk.c.chunk_id.is_not(None)
        search_match = {
            "all": or_(title_match, content_match),
            "title": title_match,
            "content": content_match,
        }[match_type]
        matched_by = case(
            (and_(title_match, content_match), "title_and_content"),
            (title_match, "title"),
            else_="content",
        ).label("matched_by")
        search_statement = (
            select(
                documents.c.document_id,
                documents.c.knowledge_base_id,
                knowledge_bases.c.name.label("knowledge_base_name"),
                documents.c.title,
                documents.c.updated_at,
                document_publications.c.published_at,
                (favorite_count > 0).label("is_favorite"),
                matched_by,
                matching_chunk.c.excerpt,
                matching_chunk.c.chunk_id,
                matching_chunk.c.sequence_no,
            )
            .select_from(searchable_from)
            .where(*candidate_where, search_match)
        )
        # 4. 总数与分页复用同一命中语句，标题命中优先且次序可重复。
        total = int(
            self._session.scalar(select(func.count()).select_from(search_statement.subquery())) or 0
        )
        rows = self._session.execute(
            search_statement.order_by(
                case((title_match, 0), else_=1),
                documents.c.updated_at.desc(),
                documents.c.document_id,
            )
            .limit(limit)
            .offset(offset)
        )
        return KnowledgeSearchPage(
            items=tuple(_search_item(row) for row in rows),
            total=total,
            unavailable_index_document_count=unavailable_index_count,
            content_search_available=include_content,
        )

    def record_document_access(
        self,
        workspace_id: UUID,
        document_id: UUID,
        *,
        viewer_account_id: UUID,
        accessed_at: datetime,
        authorized_workspace: bool,
        department_ids: frozenset[UUID],
        account_ids: frozenset[UUID],
        resource_ids: frozenset[UUID],
        maximum_security_level: SecurityLevel,
    ) -> bool:
        """复核活动文档授权后幂等推进最近访问时间。"""

        # 1. 写入前使用工作空间、活动状态、策略资源范围和密级共同复核文档可见性。
        visible_document_id = self._session.scalar(
            select(documents.c.document_id)
            .select_from(
                documents.join(
                    knowledge_bases,
                    (knowledge_bases.c.workspace_id == documents.c.workspace_id)
                    & (knowledge_bases.c.knowledge_base_id == documents.c.knowledge_base_id),
                )
            )
            .where(
                documents.c.workspace_id == workspace_id,
                documents.c.document_id == document_id,
                documents.c.status == "active",
                knowledge_bases.c.status == "active",
                _document_scope(
                    authorized_workspace=authorized_workspace,
                    department_ids=department_ids,
                    account_ids=account_ids,
                    resource_ids=resource_ids,
                ),
                _document_security_scope(maximum_security_level),
            )
        )
        if visible_document_id is None:
            return False
        # 2. 同一账号重复打开文档只推进服务端时间，不允许较旧请求覆盖较新访问事实。
        statement = postgres_insert(document_accesses).values(
            workspace_id=workspace_id,
            account_id=viewer_account_id,
            document_id=document_id,
            last_accessed_at=accessed_at,
        )
        self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    document_accesses.c.workspace_id,
                    document_accesses.c.account_id,
                    document_accesses.c.document_id,
                ],
                set_={
                    "last_accessed_at": func.greatest(
                        document_accesses.c.last_accessed_at,
                        statement.excluded.last_accessed_at,
                    )
                },
            )
        )
        return True

    def save_document(self, document: Document) -> None:
        previous_version = document.version - 1
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(documents)
                .where(
                    documents.c.workspace_id == document.workspace_id,
                    documents.c.document_id == document.document_id,
                    documents.c.version == previous_version,
                )
                .values(**_document_values(document))
            ),
        )
        if result.rowcount != 1:
            raise KnowledgeWriteConflictError

    def next_document_version_number(self, workspace_id: UUID, document_id: UUID) -> int:
        current = self._session.scalar(
            select(document_versions.c.version_number)
            .where(
                document_versions.c.workspace_id == workspace_id,
                document_versions.c.document_id == document_id,
            )
            .order_by(document_versions.c.version_number.desc())
            .limit(1)
            .with_for_update()
        )
        return int(current or 0) + 1

    def add_document_version(self, version: DocumentVersion, source: DocumentSource) -> None:
        self._session.execute(insert(document_versions).values(**_version_values(version)))
        self._session.execute(insert(document_sources).values(**_source_values(source)))

    def add_ingestion_job(self, ingestion_job: IngestionJob) -> None:
        self._session.execute(insert(ingestion_jobs).values(**_ingestion_job_values(ingestion_job)))
        self._session.execute(
            insert(ingestion_job_stages).values(**_ingestion_stage_values(ingestion_job))
        )

    def list_ingestion_jobs(
        self,
        workspace_id: UUID,
        knowledge_base_id: UUID,
        *,
        limit: int,
        authorized_workspace: bool,
        department_ids: frozenset[UUID],
        account_ids: frozenset[UUID],
        resource_ids: frozenset[UUID],
    ) -> tuple[IngestionJob, ...]:
        scope = _document_scope(
            authorized_workspace=authorized_workspace,
            department_ids=department_ids,
            account_ids=account_ids,
            resource_ids=resource_ids,
        )
        rows = self._session.execute(
            select(ingestion_jobs)
            .join(
                documents,
                (documents.c.workspace_id == ingestion_jobs.c.workspace_id)
                & (documents.c.document_id == ingestion_jobs.c.document_id),
            )
            .where(
                ingestion_jobs.c.workspace_id == workspace_id,
                ingestion_jobs.c.knowledge_base_id == knowledge_base_id,
                documents.c.status == "active",
                scope,
            )
            .order_by(ingestion_jobs.c.updated_at.desc(), ingestion_jobs.c.ingestion_job_id)
            .limit(limit)
        )
        return tuple(_ingestion_job(row) for row in rows)

    def get_ingestion_job(
        self,
        workspace_id: UUID,
        ingestion_job_id: UUID,
        *,
        for_update: bool = False,
    ) -> IngestionJob | None:
        statement = select(ingestion_jobs).where(
            ingestion_jobs.c.workspace_id == workspace_id,
            ingestion_jobs.c.ingestion_job_id == ingestion_job_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _ingestion_job(row) if row is not None else None

    def save_ingestion_job(self, ingestion_job: IngestionJob) -> None:
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(ingestion_jobs)
                .where(
                    ingestion_jobs.c.workspace_id == ingestion_job.workspace_id,
                    ingestion_jobs.c.ingestion_job_id == ingestion_job.ingestion_job_id,
                    ingestion_jobs.c.status.in_(("failed", "timed_out")),
                    ingestion_jobs.c.manual_retry_count == ingestion_job.manual_retry_count - 1,
                )
                .values(**_ingestion_job_values(ingestion_job))
            ),
        )
        if result.rowcount != 1:
            raise KnowledgeWriteConflictError
        self._session.execute(
            update(ingestion_job_stages)
            .where(
                ingestion_job_stages.c.workspace_id == ingestion_job.workspace_id,
                ingestion_job_stages.c.ingestion_job_id == ingestion_job.ingestion_job_id,
                ingestion_job_stages.c.status.in_(("failed", "timed_out")),
            )
            .values(
                status="queued",
                completed_at=None,
                failure_stage=None,
                error_code=None,
                error_message=None,
                updated_at=ingestion_job.updated_at,
            )
        )

    def save_cancelled_ingestion_job(
        self,
        ingestion_job: IngestionJob,
        *,
        previous_status: IngestionJobStatus,
    ) -> None:
        """原子取消任务与活动 Attempt，使迟到 Worker 失去终态写入资格。"""

        # 1. 任务状态必须仍与锁定时一致，否则拒绝覆盖并发产生的新终态。
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(ingestion_jobs)
                .where(
                    ingestion_jobs.c.workspace_id == ingestion_job.workspace_id,
                    ingestion_jobs.c.ingestion_job_id == ingestion_job.ingestion_job_id,
                    ingestion_jobs.c.status == previous_status,
                )
                .values(**_ingestion_job_values(ingestion_job))
            ),
        )
        if result.rowcount != 1 or ingestion_job.cancelled_at is None:
            raise KnowledgeWriteConflictError
        # 2. 运行中任务必须恰好关闭一个 Attempt，随后阶段与任务共同形成取消终态。
        attempt_result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(ingestion_job_attempts)
                .where(
                    ingestion_job_attempts.c.workspace_id == ingestion_job.workspace_id,
                    ingestion_job_attempts.c.ingestion_job_id == ingestion_job.ingestion_job_id,
                    ingestion_job_attempts.c.status == "running",
                )
                .values(status="cancelled", completed_at=ingestion_job.cancelled_at)
            ),
        )
        expected_attempts = 1 if previous_status == "running" else 0
        if attempt_result.rowcount != expected_attempts:
            raise KnowledgeWriteConflictError
        self._session.execute(
            update(ingestion_job_stages)
            .where(
                ingestion_job_stages.c.workspace_id == ingestion_job.workspace_id,
                ingestion_job_stages.c.ingestion_job_id == ingestion_job.ingestion_job_id,
            )
            .values(
                status="cancelled",
                completed_at=ingestion_job.cancelled_at,
                failure_stage=None,
                error_code=None,
                error_message=None,
                updated_at=ingestion_job.updated_at,
            )
        )

    def get_document_version(
        self,
        workspace_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
        *,
        for_update: bool = False,
    ) -> DocumentVersion | None:
        statement = select(document_versions).where(
            document_versions.c.workspace_id == workspace_id,
            document_versions.c.document_id == document_id,
            document_versions.c.document_version_id == document_version_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _version(row) if row is not None else None

    def save_document_version(self, version: DocumentVersion) -> None:
        previous_version = version.record_version - 1
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(document_versions)
                .where(
                    document_versions.c.workspace_id == version.workspace_id,
                    document_versions.c.document_version_id == version.document_version_id,
                    document_versions.c.record_version == previous_version,
                )
                .values(**_version_values(version))
            ),
        )
        if result.rowcount != 1:
            raise KnowledgeWriteConflictError

    def get_current_document_version(
        self,
        workspace_id: UUID,
        document_id: UUID,
        *,
        for_update: bool = False,
    ) -> DocumentVersion | None:
        statement = (
            select(document_versions)
            .join(
                document_publications,
                (document_publications.c.workspace_id == document_versions.c.workspace_id)
                & (
                    document_publications.c.current_document_version_id
                    == document_versions.c.document_version_id
                ),
            )
            .where(
                document_publications.c.workspace_id == workspace_id,
                document_publications.c.document_id == document_id,
            )
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _version(row) if row is not None else None

    def set_current_document_version(
        self,
        workspace_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
        *,
        published_at: datetime,
    ) -> None:
        existing = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(document_publications)
                .where(
                    document_publications.c.workspace_id == workspace_id,
                    document_publications.c.document_id == document_id,
                )
                .values(
                    current_document_version_id=document_version_id,
                    published_at=published_at,
                )
            ),
        )
        if existing.rowcount == 0:
            self._session.execute(
                insert(document_publications).values(
                    workspace_id=workspace_id,
                    document_id=document_id,
                    current_document_version_id=document_version_id,
                    published_at=published_at,
                )
            )

    def switch_document_index(
        self,
        workspace_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
        *,
        activated_at: datetime,
    ) -> UUID | None:
        return switch_active_document_index(
            self._session,
            workspace_id=workspace_id,
            document_id=document_id,
            document_version_id=document_version_id,
            activated_at=activated_at,
        )

    def deactivate_document_indexes(
        self,
        workspace_id: UUID,
        document_id: UUID,
        *,
        deactivated_at: datetime,
    ) -> None:
        deactivate_document_indexes(
            self._session,
            workspace_id=workspace_id,
            document_id=document_id,
            deactivated_at=deactivated_at,
        )


class SqlAlchemyKnowledgeUnitOfWork:
    """知识事实、审计和 Outbox 共用一个 PostgreSQL 事务。"""

    def __init__(
        self,
        session_factory: SessionFactory,
        usage_repository_factory: Callable[[Session], UsageRepository],
    ) -> None:
        self._session_factory = session_factory
        self._usage_repository_factory = usage_repository_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyKnowledgeRepository,
                SqlAlchemyKnowledgeRepository,
                UsageRepository,
                SqlAlchemyAuditWriter,
                SqlAlchemyOutboxWriter,
            ]
            | None
        ] = ContextVar("knowledge_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyKnowledgeUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Knowledge Unit of Work 不允许在同一上下文重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyKnowledgeRepository(session),
                SqlAlchemyKnowledgeRepository(session),
                self._usage_repository_factory(session),
                SqlAlchemyAuditWriter(session),
                SqlAlchemyOutboxWriter(session),
            )
        )
        return self

    def _current(
        self,
    ) -> tuple[
        Session,
        SqlAlchemyKnowledgeRepository,
        SqlAlchemyKnowledgeRepository,
        UsageRepository,
        SqlAlchemyAuditWriter,
        SqlAlchemyOutboxWriter,
    ]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Knowledge Unit of Work 尚未进入事务范围")
        return state

    @property
    def knowledge(self) -> SqlAlchemyKnowledgeRepository:
        return self._current()[1]

    @property
    def organization(self) -> KnowledgeOrganizationRepository:
        return self._current()[2]

    @property
    def usage(self) -> UsageRepository:
        return self._current()[3]

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        return self._current()[4]

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        return self._current()[5]

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        state = self._state.get()
        if state is not None:
            session = state[0]
            if exc_type is not None:
                session.rollback()
            session.close()
            self._state.set(None)
        # SQLAlchemy Core 的 INSERT 可能在 commit 前触发约束异常，必须在事务出口统一
        # 转换，否则重复名称、跨空间外键等可预期冲突会泄漏为平台内部错误。
        if exc_type is not None and issubclass(exc_type, IntegrityError):
            raise KnowledgeWriteConflictError from exc_value

    def commit(self) -> None:
        session = self._current()[0]
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise KnowledgeWriteConflictError from error


def _knowledge_base_values(value: KnowledgeBase) -> dict[str, object]:
    return {
        "knowledge_base_id": value.knowledge_base_id,
        "workspace_id": value.workspace_id,
        "name": value.name,
        "description": value.description,
        "default_visibility": value.default_visibility,
        "department_ids": sorted(value.department_ids, key=lambda item: item.int),
        "default_security_level": value.default_security_level,
        "status": value.status,
        "created_by_account_id": value.created_by_account_id,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
        "deleted_at": value.deleted_at,
        "version": value.version,
    }


def _knowledge_folder_values(value: KnowledgeFolder) -> dict[str, object]:
    return {
        "folder_id": value.folder_id,
        "workspace_id": value.workspace_id,
        "name": value.name,
        "parent_folder_id": value.parent_folder_id,
        "created_by_account_id": value.created_by_account_id,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
        "status": value.status,
        "deleted_at": value.deleted_at,
        "version": value.version,
        "is_default": value.is_default,
    }


def _knowledge_folder(value: Row[Any]) -> KnowledgeFolder:
    return KnowledgeFolder(
        value.folder_id,
        value.workspace_id,
        value.name,
        value.parent_folder_id,
        value.created_by_account_id,
        value.created_at,
        value.updated_at,
        value.status,
        value.deleted_at,
        value.version,
        value.is_default,
    )


def _knowledge_tag_values(value: KnowledgeTag) -> dict[str, object]:
    return {
        "tag_id": value.tag_id,
        "workspace_id": value.workspace_id,
        "name": value.name,
        "color": value.color,
        "created_by_account_id": value.created_by_account_id,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
        "status": value.status,
        "deleted_at": value.deleted_at,
        "version": value.version,
    }


def _knowledge_tag(value: Row[Any]) -> KnowledgeTag:
    return KnowledgeTag(
        value.tag_id,
        value.workspace_id,
        value.name,
        value.color,
        value.created_by_account_id,
        value.created_at,
        value.updated_at,
        value.status,
        value.deleted_at,
        value.version,
    )


def _document_values(value: Document) -> dict[str, object]:
    return {
        "document_id": value.document_id,
        "workspace_id": value.workspace_id,
        "knowledge_base_id": value.knowledge_base_id,
        "title": value.title,
        "visibility": value.visibility,
        "department_ids": sorted(value.department_ids, key=lambda item: item.int),
        "security_level": value.security_level,
        "permission_labels": sorted(value.permission_labels),
        "status": value.status,
        "created_by_account_id": value.created_by_account_id,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
        "deleted_at": value.deleted_at,
        "version": value.version,
    }


def _version_values(value: DocumentVersion) -> dict[str, object]:
    return {
        "document_version_id": value.document_version_id,
        "workspace_id": value.workspace_id,
        "document_id": value.document_id,
        "version_number": value.version_number,
        "status": value.status,
        "content_hash": value.content_hash,
        "created_by_account_id": value.created_by_account_id,
        "created_at": value.created_at,
        "published_at": value.published_at,
        "record_version": value.record_version,
    }


def _ingestion_job_values(value: IngestionJob) -> dict[str, object]:
    return {
        "ingestion_job_id": value.ingestion_job_id,
        "workspace_id": value.workspace_id,
        "knowledge_base_id": value.knowledge_base_id,
        "document_id": value.document_id,
        "document_version_id": value.document_version_id,
        "source_id": value.source_id,
        "source_name": value.source_name,
        "source_object_key": value.source_object_key,
        "source_media_type": value.source_media_type,
        "source_content_hash": value.source_content_hash,
        "processing_lane": ingestion_lane_for_source(value.source_name),
        "status": value.status,
        "attempt_count": value.attempt_count,
        "max_attempts": value.max_attempts,
        "available_at": value.available_at,
        "claimed_by": value.claimed_by,
        "claim_until": value.claim_until,
        "active_attempt_id": value.active_attempt_id,
        "requested_by_actor_id": value.requested_by_actor_id,
        "trace_id": value.trace_id,
        "traceparent": value.traceparent,
        "started_at": value.started_at,
        "completed_at": value.completed_at,
        "failure_stage": value.failure_stage,
        "error_code": value.error_code,
        "error_message": value.error_message,
        "artifact_object_key": value.artifact_object_key,
        "parsed_content_hash": value.parsed_content_hash,
        "parser_name": value.parser_name,
        "ocr_used": value.ocr_used,
        "page_count": value.page_count,
        "block_count": value.block_count,
        "manual_retry_count": value.manual_retry_count,
        "last_retried_by_actor_id": value.last_retried_by_actor_id,
        "last_retried_at": value.last_retried_at,
        "cancelled_by_actor_id": value.cancelled_by_actor_id,
        "cancelled_at": value.cancelled_at,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }


def _ingestion_stage_values(value: IngestionJob) -> dict[str, object]:
    """按来源类型冻结解析或 OCR 阶段，后续恢复仍使用同一资源 Lane。"""

    return {
        "job_stage_id": value.ingestion_job_id,
        "workspace_id": value.workspace_id,
        "ingestion_job_id": value.ingestion_job_id,
        "stage_key": ingestion_lane_for_source(value.source_name),
        "sequence_no": 1,
        "status": value.status,
        "attempt_count": 0,
        "started_at": value.started_at,
        "completed_at": value.completed_at,
        "failure_stage": value.failure_stage,
        "error_code": value.error_code,
        "error_message": value.error_message,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }


def _source_values(value: DocumentSource) -> dict[str, object]:
    return {
        "source_id": value.source_id,
        "workspace_id": value.workspace_id,
        "document_version_id": value.document_version_id,
        "source_kind": value.source_kind,
        "source_name": value.source_name,
        "original_object_key": value.original_object_key,
        "source_path": value.source_path,
        "source_url": value.source_url,
        "external_source_id": value.external_source_id,
        "captured_at": value.captured_at,
        "created_at": value.created_at,
        "media_type": value.media_type,
        "size_bytes": value.size_bytes,
        "content_hash": value.content_hash,
        "scan_status": value.scan_status,
        "scanner_version": value.scanner_version,
        "scanned_at": value.scanned_at,
    }


def _knowledge_base(value: Row[Any]) -> KnowledgeBase:
    return KnowledgeBase(
        value.knowledge_base_id,
        value.workspace_id,
        value.name,
        value.description,
        cast(DocumentVisibility, value.default_visibility),
        frozenset(value.department_ids),
        cast(SecurityLevel, value.default_security_level),
        value.status,
        value.created_by_account_id,
        value.created_at,
        value.updated_at,
        value.deleted_at,
        value.version,
    )


def _document(value: Row[Any]) -> Document:
    return Document(
        value.document_id,
        value.workspace_id,
        value.knowledge_base_id,
        value.title,
        cast(DocumentVisibility, value.visibility),
        frozenset(value.department_ids),
        cast(SecurityLevel, value.security_level),
        frozenset(value.permission_labels),
        value.status,
        value.created_by_account_id,
        value.created_at,
        value.updated_at,
        value.deleted_at,
        value.version,
    )


def _version(value: Row[Any]) -> DocumentVersion:
    return DocumentVersion(
        value.document_version_id,
        value.workspace_id,
        value.document_id,
        value.version_number,
        cast(DocumentVersionStatus, value.status),
        value.content_hash,
        value.created_by_account_id,
        value.created_at,
        value.published_at,
        value.record_version,
    )


def _source(value: Row[Any]) -> DocumentSource:
    return DocumentSource(
        value.source_id,
        value.workspace_id,
        value.document_version_id,
        cast(DocumentSourceKind, value.source_kind),
        value.source_name,
        value.original_object_key,
        value.source_path,
        value.source_url,
        value.external_source_id,
        value.captured_at,
        value.created_at,
        value.media_type,
        value.size_bytes,
        value.content_hash,
        value.scan_status,
        value.scanner_version,
        value.scanned_at,
    )


def _document_summary(value: Row[Any]) -> KnowledgeDocumentSummary:
    return KnowledgeDocumentSummary(
        document=_document(value),
        latest_version=DocumentVersion(
            value.latest_document_version_id,
            value.workspace_id,
            value.document_id,
            value.latest_version_number,
            cast(DocumentVersionStatus, value.latest_version_status),
            value.latest_content_hash,
            value.latest_created_by_account_id,
            value.latest_created_at,
            value.latest_published_at,
            value.latest_record_version,
        ),
        source_id=value.latest_source_id,
        source_kind=cast(DocumentSourceKind, value.latest_source_kind),
        source_name=value.latest_source_name,
        current_document_version_id=value.current_document_version_id,
        folder_id=value.folder_id,
        tag_ids=tuple(value.tag_ids or ()),
        is_favorite=value.is_favorite,
    )


def _workbench_document(value: Row[Any]) -> PersonalWorkbenchDocument:
    """把统一聚合行转换为工作台低敏摘要。"""

    return PersonalWorkbenchDocument(
        document_id=value.document_id,
        knowledge_base_id=value.knowledge_base_id,
        knowledge_base_name=value.knowledge_base_name,
        title=value.title,
        updated_at=value.updated_at,
        published_at=value.published_at,
        last_accessed_at=value.last_accessed_at,
        is_favorite=value.is_favorite,
        is_indexed=value.is_indexed,
    )


def _search_item(value: Row[Any]) -> KnowledgeSearchItem:
    """把搜索结果行转换为不包含完整正文的引用摘要。"""

    return KnowledgeSearchItem(
        document_id=value.document_id,
        knowledge_base_id=value.knowledge_base_id,
        knowledge_base_name=value.knowledge_base_name,
        title=value.title,
        updated_at=value.updated_at,
        published_at=value.published_at,
        is_favorite=value.is_favorite,
        matched_by=value.matched_by,
        excerpt=value.excerpt,
        chunk_id=value.chunk_id,
        sequence_no=value.sequence_no,
    )


def _ingestion_job(value: Row[Any]) -> IngestionJob:
    return IngestionJob(
        ingestion_job_id=value.ingestion_job_id,
        workspace_id=value.workspace_id,
        knowledge_base_id=value.knowledge_base_id,
        document_id=value.document_id,
        document_version_id=value.document_version_id,
        source_id=value.source_id,
        source_name=value.source_name,
        source_object_key=value.source_object_key,
        source_media_type=value.source_media_type,
        source_content_hash=value.source_content_hash,
        status=value.status,
        attempt_count=value.attempt_count,
        max_attempts=value.max_attempts,
        available_at=value.available_at,
        requested_by_actor_id=value.requested_by_actor_id,
        trace_id=value.trace_id,
        traceparent=value.traceparent,
        created_at=value.created_at,
        updated_at=value.updated_at,
        claimed_by=value.claimed_by,
        claim_until=value.claim_until,
        active_attempt_id=value.active_attempt_id,
        started_at=value.started_at,
        completed_at=value.completed_at,
        failure_stage=value.failure_stage,
        error_code=value.error_code,
        error_message=value.error_message,
        artifact_object_key=value.artifact_object_key,
        parsed_content_hash=value.parsed_content_hash,
        parser_name=value.parser_name,
        ocr_used=value.ocr_used,
        page_count=value.page_count,
        block_count=value.block_count,
        manual_retry_count=value.manual_retry_count,
        last_retried_by_actor_id=value.last_retried_by_actor_id,
        last_retried_at=value.last_retried_at,
        cancelled_by_actor_id=value.cancelled_by_actor_id,
        cancelled_at=value.cancelled_at,
    )


def _document_index_summary(value: Row[Any]) -> DocumentIndexSummary:
    """把索引表限制为详情页所需状态，排除对象键、模型版本和权限副本。"""

    return DocumentIndexSummary(
        index_version_id=value.index_version_id,
        build_no=value.build_no,
        status=cast(DocumentIndexStatus, value.status),
        chunk_count=value.chunk_count,
        staged_chunk_count=value.staged_chunk_count,
        failure_stage=value.failure_stage,
        error_code=value.error_code,
        error_message=value.error_message,
        completed_at=value.completed_at,
        activated_at=value.activated_at,
        updated_at=value.updated_at,
    )


def _document_scope(
    *,
    authorized_workspace: bool,
    department_ids: frozenset[UUID],
    account_ids: frozenset[UUID],
    resource_ids: frozenset[UUID],
) -> ColumnElement[bool]:
    if authorized_workspace:
        return true()
    conditions = []
    if department_ids:
        conditions.append(documents.c.department_ids.overlap(list(department_ids)))
    if account_ids:
        conditions.append(documents.c.created_by_account_id.in_(account_ids))
    if resource_ids:
        conditions.append(documents.c.document_id.in_(resource_ids))
    return or_(*conditions) if conditions else false()


def _document_security_scope(maximum_security_level: SecurityLevel) -> ColumnElement[bool]:
    """把密级上限转换为显式允许集合，未知值不能扩大查询。"""

    allowed_levels: dict[SecurityLevel, tuple[SecurityLevel, ...]] = {
        "PUBLIC": ("PUBLIC",),
        "INTERNAL": ("PUBLIC", "INTERNAL"),
        "CONFIDENTIAL": ("PUBLIC", "INTERNAL", "CONFIDENTIAL"),
        "RESTRICTED": ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"),
    }
    return documents.c.security_level.in_(allowed_levels[maximum_security_level])
