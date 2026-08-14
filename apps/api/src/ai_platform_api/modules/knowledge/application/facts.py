"""编排知识库、文档和不可变版本事实的创建与发布事务。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.ingestion.domain import IngestionJob
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
    """表示知识拒绝错误，由协议层映射为稳定错误码。"""

    error_code = "POLICY_DENIED"


class KnowledgeNotFoundError(PlatformError):
    """表示知识未找到错误，由协议层映射为稳定错误码。"""

    error_code = "RESOURCE_NOT_FOUND"


class KnowledgeConflictError(PlatformError):
    """表示知识冲突错误，由协议层映射为稳定错误码。"""

    error_code = "KNOWLEDGE_CONFLICT"


class KnowledgeValidationError(PlatformError):
    """表示知识校验错误，由协议层映射为稳定错误码。"""

    error_code = "VALIDATION_ERROR"


class KnowledgeQuotaExceededError(PlatformError):
    """表示知识额度超限错误，由协议层映射为稳定错误码。"""

    error_code = "QUOTA_EXCEEDED"


class KnowledgeFactService:
    """在一个事务内维护知识事实、审计和 Outbox，不处理对象内容或索引。"""

    def __init__(
        self,
        unit_of_work: KnowledgeUnitOfWork,
        *,
        ingestion_max_attempts: int = 3,
    ) -> None:
        if ingestion_max_attempts < 1:
            raise ValueError("入库任务最大尝试次数必须为正数")
        self._unit_of_work = unit_of_work
        self._ingestion_max_attempts = ingestion_max_attempts

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
        """校验知识权限、可见范围和额度后创建知识库事实。"""

        # 1. 先构造并校验完整领域对象，名称和可见范围错误不进入事务。
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
                # 2. 在写知识库前原子占用套餐额度，幂等键绑定新聚合标识。
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
                # 3. 用量、知识库、审计和 Outbox 同事务提交。
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
        """在知识库默认策略上创建文档，并固定字段级安全与可见范围。"""

        # 1. 上传来源必须携带完整安全事实，其他来源不能伪造上传扫描结果。
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
                # 2. 锁定知识库后解析继承策略，并把结果冻结到文档而非运行时动态继承。
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
                # 3. 文档、首版、来源和入库任务构成一个不可拆分的知识事实。
                unit_of_work.knowledge.add_document(document)
                unit_of_work.knowledge.add_document_version(version, source)
                self._add_ingestion_job(unit_of_work, context, document, version, source, now)
                # 4. 上传来源同时占用存储额度，并与知识事实、审计和 Outbox 原子提交。
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
        """创建不可变文档版本及来源事实，不直接改变当前发布指针。"""

        # 1. 校验上传安全元数据后锁定活动文档，版本号在事务内递增。
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
                # 2. 新版本和来源一经创建不可变，但尚不影响当前发布版本和在线索引。
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
                # 3. 版本、来源、入库任务和存储用量同事务提交。
                unit_of_work.knowledge.add_document_version(version, source)
                self._add_ingestion_job(unit_of_work, context, document, version, source, now)
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

    def _add_ingestion_job(
        self,
        unit_of_work: KnowledgeUnitOfWork,
        context: RequestContext,
        document: Document,
        version: DocumentVersion,
        source: DocumentSource,
        now: datetime,
    ) -> None:
        # 1. 只有上传来源需要异步解析；手工和后置连接器来源由各自生产链路负责。
        if source.source_kind != "upload":
            return
        if (
            source.original_object_key is None
            or source.media_type is None
            or source.content_hash is None
        ):
            raise KnowledgeValidationError
        # 2. 任务固定来源摘要、最大尝试次数和原始追踪上下文，Worker 不重新猜测这些事实。
        job = IngestionJob(
            ingestion_job_id=uuid4(),
            workspace_id=context.workspace_id,
            knowledge_base_id=document.knowledge_base_id,
            document_id=document.document_id,
            document_version_id=version.document_version_id,
            source_id=source.source_id,
            source_name=source.source_name,
            source_object_key=source.original_object_key,
            source_media_type=source.media_type,
            source_content_hash=source.content_hash,
            status="queued",
            attempt_count=0,
            max_attempts=self._ingestion_max_attempts,
            available_at=now,
            requested_by_actor_id=context.actor_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            created_at=now,
            updated_at=now,
        )
        job.assert_valid()
        unit_of_work.knowledge.add_ingestion_job(job)

    def mark_document_version_ready(
        self,
        context: RequestContext,
        *,
        knowledge_base_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
        content_hash: str,
    ) -> DocumentVersion:
        """仅允许草稿在内容摘要确定后进入就绪状态。"""

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
        """发布就绪版本、替代旧版本并原子切换当前版本和索引指针。"""

        # 1. 锁定文档、目标版本和当前发布版本，确保状态转换基于同一数据库快照。
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
                # 2. 先生成目标发布态并把旧发布版本标记为已替代。
                published = version.publish(occurred_at=now)
                current = unit_of_work.knowledge.get_current_document_version(
                    context.workspace_id,
                    document_id,
                    for_update=True,
                )
                if current is not None:
                    unit_of_work.knowledge.save_document_version(current.supersede())
                unit_of_work.knowledge.save_document_version(published)
                # 3. 当前版本指针和在线索引指针必须原子切换，避免回答引用错误内容。
                unit_of_work.knowledge.set_current_document_version(
                    context.workspace_id,
                    document_id,
                    document_version_id,
                    published_at=now,
                )
                unit_of_work.knowledge.switch_document_index(
                    context.workspace_id,
                    document_id,
                    document_version_id,
                    activated_at=now,
                )
                # 4. 发布事实与指针切换同事务提交，消费者不会提前观察到未生效版本。
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
        """逻辑删除文档并停用其索引，保留历史版本用于审计。"""

        # 1. 锁定活动文档并通过领域状态机生成逻辑删除版本。
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
                # 2. 文档删除与全部在线索引停用必须在同一事务完成。
                unit_of_work.knowledge.deactivate_document_indexes(
                    context.workspace_id,
                    document_id,
                    deactivated_at=now,
                )
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
        """仅允许删除没有活动文档的知识库，避免形成孤立文档。"""

        # 1. 锁定知识库并再次查询活动文档，关闭检查后并发新增的竞态。
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
                # 2. 逻辑删除同时释放套餐额度，幂等键绑定删除后的聚合版本。
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
                # 3. 知识库、额度、审计和 Outbox 同事务提交。
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
        # 1. 锁定文档与目标版本，并校验二者仍属于同一活动知识聚合。
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
                # 2. 调用方提供的领域转换只处理状态，新版本、审计和事件统一在此提交。
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
