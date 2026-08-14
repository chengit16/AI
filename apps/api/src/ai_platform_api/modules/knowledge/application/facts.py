from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.identity.application.entitlement_errors import (
    EntitlementConflictError,
    EntitlementGovernanceDeniedError,
    QuotaExceededError,
)
from ai_platform_api.modules.identity.application.usage import UsageMutation, consume_usage
from ai_platform_api.modules.integration.domain.events import IntegrationEvent
from ai_platform_api.modules.knowledge.domain.models import (
    Document,
    DocumentSource,
    DocumentSourceKind,
    DocumentVersion,
    DocumentVisibility,
    InvalidDocumentVersionTransitionError,
    InvalidKnowledgeFactError,
    KnowledgeBase,
    KnowledgeUnitOfWork,
    KnowledgeWriteConflictError,
    VisibilityPolicy,
)

__all__ = [
    "Document",
    "DocumentSource",
    "DocumentVersion",
    "KnowledgeBase",
    "KnowledgeDeniedError",
    "KnowledgeFactService",
]


class KnowledgeDeniedError(PlatformError):
    error_code = "POLICY_DENIED"


class KnowledgeNotFoundError(PlatformError):
    error_code = "RESOURCE_NOT_FOUND"


class KnowledgeConflictError(PlatformError):
    error_code = "KNOWLEDGE_CONFLICT"


class KnowledgeValidationError(PlatformError):
    error_code = "VALIDATION_ERROR"


class KnowledgeQuotaExceededError(PlatformError):
    error_code = "QUOTA_EXCEEDED"


class KnowledgeFactService:
    """在一个事务内维护知识事实、审计和 Outbox，不处理对象内容或索引。"""

    def __init__(self, unit_of_work: KnowledgeUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def require_upload_target(
        self,
        context: RequestContext,
        *,
        knowledge_base_id: UUID,
        document_id: UUID | None = None,
    ) -> None:
        """在外部扫描与对象写入前，以短事务确认主体和目标仍可写。"""

        account_id = _account(context)
        with self._unit_of_work as unit_of_work:
            _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
            knowledge_base = unit_of_work.knowledge.get_knowledge_base(
                context.workspace_id,
                knowledge_base_id,
            )
            if knowledge_base is None or knowledge_base.status != "active":
                raise KnowledgeNotFoundError
            if document_id is None:
                return
            document = unit_of_work.knowledge.get_document(context.workspace_id, document_id)
            if (
                document is None
                or document.knowledge_base_id != knowledge_base_id
                or document.status != "active"
            ):
                raise KnowledgeNotFoundError

    def create_knowledge_base(
        self,
        context: RequestContext,
        *,
        name: str,
        description: str | None = None,
        default_visibility: DocumentVisibility = "private",
        department_ids: frozenset[UUID] = frozenset(),
        default_security_level: SecurityLevel = "INTERNAL",
    ) -> KnowledgeBase:
        account_id = _account(context)
        now = datetime.now(UTC)
        knowledge_base = KnowledgeBase(
            uuid4(),
            context.workspace_id,
            name.strip(),
            description.strip() if description is not None else None,
            default_visibility,
            department_ids,
            default_security_level,
            "active",
            account_id,
            now,
            now,
        )
        try:
            knowledge_base.assert_valid()
            with self._unit_of_work as unit_of_work:
                _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
                _require_departments(
                    unit_of_work.knowledge,
                    context.workspace_id,
                    department_ids,
                )
                usage = consume_usage(
                    unit_of_work.usage,
                    context=context,
                    workspace_id=context.workspace_id,
                    metric="knowledge_bases",
                    delta_value=1,
                    idempotency_key=f"knowledge-base:create:{knowledge_base.knowledge_base_id}",
                    occurred_at=now,
                )
                _record_usage(unit_of_work, usage)
                unit_of_work.knowledge.add_knowledge_base(knowledge_base)
                _record(
                    unit_of_work,
                    context,
                    knowledge_base.knowledge_base_id,
                    1,
                    "knowledge.base.created",
                    "knowledge.base.create",
                    "knowledge_base",
                    now,
                    {"visibility": default_visibility, "security_level": default_security_level},
                )
                unit_of_work.commit()
        except InvalidKnowledgeFactError as error:
            raise KnowledgeValidationError from error
        except QuotaExceededError as error:
            raise KnowledgeQuotaExceededError from error
        except EntitlementGovernanceDeniedError as error:
            raise KnowledgeDeniedError from error
        except EntitlementConflictError as error:
            raise KnowledgeConflictError from error
        except KnowledgeWriteConflictError as error:
            raise KnowledgeConflictError from error
        return knowledge_base

    def create_document(
        self,
        context: RequestContext,
        *,
        knowledge_base_id: UUID,
        title: str,
        source_kind: DocumentSourceKind,
        source_name: str,
        original_object_key: str | None = None,
        source_path: str | None = None,
        source_url: str | None = None,
        external_source_id: str | None = None,
        captured_at: datetime | None = None,
        visibility: DocumentVisibility | None = None,
        department_ids: frozenset[UUID] | None = None,
        security_level: SecurityLevel | None = None,
        permission_labels: frozenset[str] = frozenset(),
        upload_media_type: str | None = None,
        upload_size_bytes: int | None = None,
        upload_content_hash: str | None = None,
        upload_scan_status: str | None = None,
        upload_scanner_version: str | None = None,
        upload_scanned_at: datetime | None = None,
    ) -> tuple[Document, DocumentVersion, DocumentSource]:
        account_id = _account(context)
        now = datetime.now(UTC)
        _require_upload_security(
            source_kind,
            upload_media_type,
            upload_size_bytes,
            upload_content_hash,
            upload_scan_status,
            upload_scanner_version,
            upload_scanned_at,
        )
        try:
            with self._unit_of_work as unit_of_work:
                _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
                knowledge_base = unit_of_work.knowledge.get_knowledge_base(
                    context.workspace_id,
                    knowledge_base_id,
                    for_update=True,
                )
                if knowledge_base is None or knowledge_base.status != "active":
                    raise KnowledgeNotFoundError
                resolved_departments = (
                    department_ids if department_ids is not None else knowledge_base.department_ids
                )
                document = Document(
                    uuid4(),
                    context.workspace_id,
                    knowledge_base_id,
                    title.strip(),
                    visibility or knowledge_base.default_visibility,
                    resolved_departments,
                    security_level or knowledge_base.default_security_level,
                    permission_labels,
                    "active",
                    account_id,
                    now,
                    now,
                )
                version = DocumentVersion(
                    uuid4(),
                    context.workspace_id,
                    document.document_id,
                    1,
                    "draft",
                    None,
                    account_id,
                    now,
                )
                source = DocumentSource(
                    uuid4(),
                    context.workspace_id,
                    version.document_version_id,
                    source_kind,
                    source_name.strip(),
                    original_object_key,
                    source_path,
                    source_url,
                    external_source_id,
                    captured_at,
                    now,
                    upload_media_type,
                    upload_size_bytes,
                    upload_content_hash,
                    upload_scan_status,
                    upload_scanner_version,
                    upload_scanned_at,
                )
                document.assert_valid()
                version.assert_valid()
                source.assert_valid()
                _require_departments(
                    unit_of_work.knowledge,
                    context.workspace_id,
                    resolved_departments,
                )
                unit_of_work.knowledge.add_document(document)
                unit_of_work.knowledge.add_document_version(version, source)
                if upload_size_bytes is not None:
                    usage = consume_usage(
                        unit_of_work.usage,
                        context=context,
                        workspace_id=context.workspace_id,
                        metric="storage_bytes",
                        delta_value=upload_size_bytes,
                        idempotency_key=f"upload:{source.source_id}:storage",
                        occurred_at=now,
                    )
                    _record_usage(unit_of_work, usage)
                _record(
                    unit_of_work,
                    context,
                    document.document_id,
                    1,
                    "knowledge.document.created",
                    "knowledge.document.create",
                    "document",
                    now,
                    {
                        "knowledge_base_id": str(knowledge_base_id),
                        "document_version_id": str(version.document_version_id),
                        "source_kind": source_kind,
                    },
                )
                unit_of_work.commit()
        except InvalidKnowledgeFactError as error:
            raise KnowledgeValidationError from error
        except QuotaExceededError as error:
            raise KnowledgeQuotaExceededError from error
        except EntitlementGovernanceDeniedError as error:
            raise KnowledgeDeniedError from error
        except EntitlementConflictError as error:
            raise KnowledgeConflictError from error
        except KnowledgeWriteConflictError as error:
            raise KnowledgeConflictError from error
        return document, version, source

    def create_document_version(
        self,
        context: RequestContext,
        *,
        knowledge_base_id: UUID,
        document_id: UUID,
        source_kind: DocumentSourceKind,
        source_name: str,
        original_object_key: str | None = None,
        source_path: str | None = None,
        source_url: str | None = None,
        external_source_id: str | None = None,
        captured_at: datetime | None = None,
        upload_media_type: str | None = None,
        upload_size_bytes: int | None = None,
        upload_content_hash: str | None = None,
        upload_scan_status: str | None = None,
        upload_scanner_version: str | None = None,
        upload_scanned_at: datetime | None = None,
    ) -> tuple[DocumentVersion, DocumentSource]:
        account_id = _account(context)
        now = datetime.now(UTC)
        _require_upload_security(
            source_kind,
            upload_media_type,
            upload_size_bytes,
            upload_content_hash,
            upload_scan_status,
            upload_scanner_version,
            upload_scanned_at,
        )
        try:
            with self._unit_of_work as unit_of_work:
                _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
                document = unit_of_work.knowledge.get_document(
                    context.workspace_id,
                    document_id,
                    for_update=True,
                )
                if (
                    document is None
                    or document.knowledge_base_id != knowledge_base_id
                    or document.status != "active"
                ):
                    raise KnowledgeNotFoundError
                version = DocumentVersion(
                    uuid4(),
                    context.workspace_id,
                    document_id,
                    unit_of_work.knowledge.next_document_version_number(
                        context.workspace_id,
                        document_id,
                    ),
                    "draft",
                    None,
                    account_id,
                    now,
                )
                source = DocumentSource(
                    uuid4(),
                    context.workspace_id,
                    version.document_version_id,
                    source_kind,
                    source_name.strip(),
                    original_object_key,
                    source_path,
                    source_url,
                    external_source_id,
                    captured_at,
                    now,
                    upload_media_type,
                    upload_size_bytes,
                    upload_content_hash,
                    upload_scan_status,
                    upload_scanner_version,
                    upload_scanned_at,
                )
                version.assert_valid()
                source.assert_valid()
                unit_of_work.knowledge.add_document_version(version, source)
                if upload_size_bytes is not None:
                    usage = consume_usage(
                        unit_of_work.usage,
                        context=context,
                        workspace_id=context.workspace_id,
                        metric="storage_bytes",
                        delta_value=upload_size_bytes,
                        idempotency_key=f"upload:{source.source_id}:storage",
                        occurred_at=now,
                    )
                    _record_usage(unit_of_work, usage)
                _record(
                    unit_of_work,
                    context,
                    document_id,
                    version.version_number,
                    "knowledge.document.version.created",
                    "knowledge.document.version.create",
                    "document_version",
                    now,
                    {"document_version_id": str(version.document_version_id)},
                )
                unit_of_work.commit()
        except InvalidKnowledgeFactError as error:
            raise KnowledgeValidationError from error
        except QuotaExceededError as error:
            raise KnowledgeQuotaExceededError from error
        except EntitlementGovernanceDeniedError as error:
            raise KnowledgeDeniedError from error
        except EntitlementConflictError as error:
            raise KnowledgeConflictError from error
        except KnowledgeWriteConflictError as error:
            raise KnowledgeConflictError from error
        return version, source

    def mark_document_version_ready(
        self,
        context: RequestContext,
        *,
        knowledge_base_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
        content_hash: str,
    ) -> DocumentVersion:
        return self._transition_version(
            context,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            document_version_id=document_version_id,
            transition=lambda value, _: value.mark_ready(content_hash=content_hash),
            event_type="knowledge.document.version.ready",
            action="knowledge.document.version.mark_ready",
        )

    def publish_document_version(
        self,
        context: RequestContext,
        *,
        knowledge_base_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
    ) -> DocumentVersion:
        account_id = _account(context)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
                document = unit_of_work.knowledge.get_document(
                    context.workspace_id,
                    document_id,
                    for_update=True,
                )
                version = unit_of_work.knowledge.get_document_version(
                    context.workspace_id,
                    document_id,
                    document_version_id,
                    for_update=True,
                )
                if (
                    document is None
                    or document.knowledge_base_id != knowledge_base_id
                    or document.status != "active"
                    or version is None
                ):
                    raise KnowledgeNotFoundError
                published = version.publish(occurred_at=now)
                current = unit_of_work.knowledge.get_current_document_version(
                    context.workspace_id,
                    document_id,
                    for_update=True,
                )
                if current is not None:
                    unit_of_work.knowledge.save_document_version(current.supersede())
                unit_of_work.knowledge.save_document_version(published)
                unit_of_work.knowledge.set_current_document_version(
                    context.workspace_id,
                    document_id,
                    document_version_id,
                    published_at=now,
                )
                _record(
                    unit_of_work,
                    context,
                    document_id,
                    published.version_number,
                    "knowledge.document.version.published",
                    "knowledge.document.version.publish",
                    "document_version",
                    now,
                    {"document_version_id": str(document_version_id)},
                )
                unit_of_work.commit()
        except InvalidDocumentVersionTransitionError as error:
            raise KnowledgeConflictError from error
        except KnowledgeWriteConflictError as error:
            raise KnowledgeConflictError from error
        return published

    def delete_document(
        self,
        context: RequestContext,
        *,
        knowledge_base_id: UUID,
        document_id: UUID,
    ) -> Document:
        account_id = _account(context)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
                document = unit_of_work.knowledge.get_document(
                    context.workspace_id,
                    document_id,
                    for_update=True,
                )
                if document is None or document.knowledge_base_id != knowledge_base_id:
                    raise KnowledgeNotFoundError
                deleted = document.delete(occurred_at=now)
                unit_of_work.knowledge.save_document(deleted)
                _record(
                    unit_of_work,
                    context,
                    document_id,
                    deleted.version,
                    "knowledge.document.deleted",
                    "knowledge.document.delete",
                    "document",
                    now,
                    {},
                )
                unit_of_work.commit()
        except InvalidKnowledgeFactError as error:
            raise KnowledgeConflictError from error
        except KnowledgeWriteConflictError as error:
            raise KnowledgeConflictError from error
        return deleted

    def delete_knowledge_base(
        self,
        context: RequestContext,
        *,
        knowledge_base_id: UUID,
    ) -> KnowledgeBase:
        account_id = _account(context)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
                knowledge_base = unit_of_work.knowledge.get_knowledge_base(
                    context.workspace_id,
                    knowledge_base_id,
                    for_update=True,
                )
                if knowledge_base is None:
                    raise KnowledgeNotFoundError
                if unit_of_work.knowledge.has_active_documents(
                    context.workspace_id,
                    knowledge_base_id,
                ):
                    raise KnowledgeConflictError
                deleted = knowledge_base.delete(occurred_at=now)
                usage = consume_usage(
                    unit_of_work.usage,
                    context=context,
                    workspace_id=context.workspace_id,
                    metric="knowledge_bases",
                    delta_value=-1,
                    idempotency_key=(
                        f"knowledge-base:delete:{knowledge_base.knowledge_base_id}:"
                        f"v{deleted.version}"
                    ),
                    occurred_at=now,
                )
                _record_usage(unit_of_work, usage)
                unit_of_work.knowledge.save_knowledge_base(deleted)
                _record(
                    unit_of_work,
                    context,
                    knowledge_base_id,
                    deleted.version,
                    "knowledge.base.deleted",
                    "knowledge.base.delete",
                    "knowledge_base",
                    now,
                    {},
                )
                unit_of_work.commit()
        except InvalidKnowledgeFactError as error:
            raise KnowledgeConflictError from error
        except (EntitlementConflictError, QuotaExceededError) as error:
            raise KnowledgeConflictError from error
        except EntitlementGovernanceDeniedError as error:
            raise KnowledgeDeniedError from error
        except KnowledgeWriteConflictError as error:
            raise KnowledgeConflictError from error
        return deleted

    def _transition_version(
        self,
        context: RequestContext,
        *,
        knowledge_base_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
        transition: Callable[[DocumentVersion, datetime], DocumentVersion],
        event_type: str,
        action: str,
    ) -> DocumentVersion:
        account_id = _account(context)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
                document = unit_of_work.knowledge.get_document(
                    context.workspace_id,
                    document_id,
                    for_update=True,
                )
                version = unit_of_work.knowledge.get_document_version(
                    context.workspace_id,
                    document_id,
                    document_version_id,
                    for_update=True,
                )
                if (
                    document is None
                    or document.knowledge_base_id != knowledge_base_id
                    or document.status != "active"
                    or version is None
                ):
                    raise KnowledgeNotFoundError
                updated = transition(version, now)
                unit_of_work.knowledge.save_document_version(updated)
                _record(
                    unit_of_work,
                    context,
                    document_id,
                    updated.version_number,
                    event_type,
                    action,
                    "document_version",
                    now,
                    {"document_version_id": str(document_version_id)},
                )
                unit_of_work.commit()
        except InvalidDocumentVersionTransitionError as error:
            raise KnowledgeConflictError from error
        except KnowledgeWriteConflictError as error:
            raise KnowledgeConflictError from error
        return updated


def _account(context: RequestContext) -> UUID:
    if (
        context.user_id is None
        or context.user_id != context.actor_id
        or context.authentication_method != "browser_session"
    ):
        raise KnowledgeDeniedError
    return context.user_id


def _require_upload_security(
    source_kind: DocumentSourceKind,
    media_type: str | None,
    size_bytes: int | None,
    content_hash: str | None,
    scan_status: str | None,
    scanner_version: str | None,
    scanned_at: datetime | None,
) -> None:
    security_fact = (
        media_type,
        size_bytes,
        content_hash,
        scan_status,
        scanner_version,
        scanned_at,
    )
    if source_kind == "upload" and any(value is None for value in security_fact):
        raise KnowledgeValidationError
    if source_kind != "upload" and any(value is not None for value in security_fact):
        raise KnowledgeValidationError


def _require_owner(repository: object, workspace_id: UUID, account_id: UUID) -> None:
    if not hasattr(repository, "get_workspace_access"):
        raise KnowledgeDeniedError
    access = repository.get_workspace_access(workspace_id, account_id)
    if access != ("active", "owner"):
        raise KnowledgeDeniedError


def _require_departments(
    repository: object,
    workspace_id: UUID,
    department_ids: frozenset[UUID],
) -> None:
    VisibilityPolicy(
        "departments" if department_ids else "workspace", department_ids
    ).assert_valid()
    if department_ids and (
        not hasattr(repository, "departments_exist")
        or not repository.departments_exist(workspace_id, department_ids)
    ):
        raise KnowledgeValidationError


def _record(
    unit_of_work: KnowledgeUnitOfWork,
    context: RequestContext,
    aggregate_id: UUID,
    aggregate_version: int,
    event_type: str,
    action: str,
    resource_type: str,
    occurred_at: datetime,
    payload: dict[str, object],
) -> None:
    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=aggregate_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            # 审计只保存状态与版本标识，不复制标题、来源地址或对象键。
            attributes={"aggregate_version": aggregate_version},
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type=event_type,
            workspace_id=context.workspace_id,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=payload,
        )
    )


def _record_usage(unit_of_work: KnowledgeUnitOfWork, usage: UsageMutation) -> None:
    if not usage.created:
        return
    if usage.audit is None or usage.event is None:
        raise RuntimeError("用量变更事实不完整")
    unit_of_work.audit.add(usage.audit)
    unit_of_work.outbox.add(usage.event)
