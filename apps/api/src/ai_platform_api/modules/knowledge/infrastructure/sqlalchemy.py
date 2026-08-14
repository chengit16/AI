from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime
from types import TracebackType
from typing import Any, cast
from uuid import UUID

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import CursorResult, Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.identity.domain.entitlements import UsageRepository
from ai_platform_api.modules.knowledge.domain.models import (
    Document,
    DocumentSource,
    DocumentSourceKind,
    DocumentVersion,
    DocumentVersionStatus,
    DocumentVisibility,
    KnowledgeBase,
    KnowledgeWriteConflictError,
)
from ai_platform_api.persistence.tables import (
    department_closure,
    document_publications,
    document_sources,
    document_versions,
    documents,
    knowledge_bases,
    workspace_memberships,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyKnowledgeRepository:
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
    def usage(self) -> UsageRepository:
        return self._current()[2]

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        return self._current()[3]

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        return self._current()[4]

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
