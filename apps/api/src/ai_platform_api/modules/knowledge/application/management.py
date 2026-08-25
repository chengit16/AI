"""提供知识生产页范围化查询和安全人工重试用例。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.ingestion.domain import (
    IngestionJob,
    InvalidIngestionJobError,
    ManualIngestionRetryNotAllowedError,
)
from ai_platform_backend.integration.domain import AuditRecord

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.integration.domain.events import IntegrationEvent
from ai_platform_api.modules.knowledge.application.facts import (
    KnowledgeConflictError,
    KnowledgeDeniedError,
    KnowledgeNotFoundError,
)
from ai_platform_api.modules.knowledge.domain.models import (
    KnowledgeBase,
    KnowledgeDocumentDetail,
    KnowledgeDocumentDownload,
    KnowledgeDocumentSummary,
    KnowledgeRepository,
    KnowledgeUnitOfWork,
    KnowledgeWriteConflictError,
)
from ai_platform_api.modules.knowledge.domain.uploads import (
    ObjectStorage,
    ObjectStorageUnavailableError,
    WorkspaceObject,
)

__all__ = [
    "IngestionJob",
    "KnowledgeDocumentDetail",
    "KnowledgeDocumentDownloadFile",
    "KnowledgeDocumentSummary",
    "KnowledgeManagementService",
]


class DocumentStorageUnavailableError(PlatformError):
    """表示授权下载期间对象存储不可用或返回了不完整对象。"""

    error_code = "OBJECT_STORAGE_UNAVAILABLE"


@dataclass(frozen=True)
class KnowledgeDocumentDownloadFile:
    """返回路由所需文件名、媒体类型和已验证原文件内容。"""

    file_name: str
    media_type: str
    content: bytes


class KnowledgeManagementService:
    """提供知识生产页所需的范围化读模型与安全人工重试命令。"""

    def __init__(self, unit_of_work: KnowledgeUnitOfWork, storage: ObjectStorage) -> None:
        self._unit_of_work = unit_of_work
        self._storage = storage

    def list_knowledge_bases(
        self,
        context: RequestContext,
        *,
        limit: int,
    ) -> tuple[KnowledgeBase, ...]:
        """按策略决策和字段投影列出当前账号可见的知识库。"""

        account_id = _browser_account(context)
        _require_limit(limit)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work.knowledge, context.workspace_id, account_id)
            return unit_of_work.knowledge.list_knowledge_bases(
                context.workspace_id,
                limit=limit,
                authorized_workspace=context.authorized_workspace,
                department_ids=context.authorized_department_ids,
                resource_ids=context.authorized_resource_ids,
            )

    def list_documents(
        self,
        context: RequestContext,
        *,
        knowledge_base_id: UUID,
        limit: int,
    ) -> tuple[KnowledgeDocumentSummary, ...]:
        """按空间、部门、账号和资源授权交集列出文档摘要。"""

        account_id = _browser_account(context)
        _require_limit(limit)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work.knowledge, context.workspace_id, account_id)
            _require_active_knowledge_base(
                unit_of_work.knowledge,
                context.workspace_id,
                knowledge_base_id,
            )
            return unit_of_work.knowledge.list_document_summaries(
                context.workspace_id,
                knowledge_base_id,
                viewer_account_id=account_id,
                limit=limit,
                authorized_workspace=context.authorized_workspace,
                department_ids=context.authorized_department_ids,
                account_ids=context.authorized_account_ids,
                resource_ids=context.authorized_resource_ids,
            )

    def list_ingestion_jobs(
        self,
        context: RequestContext,
        *,
        knowledge_base_id: UUID,
        limit: int,
    ) -> tuple[IngestionJob, ...]:
        """只返回当前账号有权查看文档对应的入库任务。"""

        account_id = _browser_account(context)
        _require_limit(limit)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work.knowledge, context.workspace_id, account_id)
            _require_active_knowledge_base(
                unit_of_work.knowledge,
                context.workspace_id,
                knowledge_base_id,
            )
            return unit_of_work.knowledge.list_ingestion_jobs(
                context.workspace_id,
                knowledge_base_id,
                limit=limit,
                authorized_workspace=context.authorized_workspace,
                department_ids=context.authorized_department_ids,
                account_ids=context.authorized_account_ids,
                resource_ids=context.authorized_resource_ids,
            )

    def get_document_detail(
        self,
        context: RequestContext,
        *,
        knowledge_base_id: UUID,
        document_id: UUID,
    ) -> KnowledgeDocumentDetail:
        """返回单篇活动文档的版本、解析和索引聚合，越权与不存在统一为不可见。"""

        account_id = _browser_account(context)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work.knowledge, context.workspace_id, account_id)
            _require_active_knowledge_base(
                unit_of_work.knowledge,
                context.workspace_id,
                knowledge_base_id,
            )
            detail = unit_of_work.knowledge.get_document_detail(
                context.workspace_id,
                knowledge_base_id,
                document_id,
                viewer_account_id=account_id,
                authorized_workspace=context.authorized_workspace,
                department_ids=context.authorized_department_ids,
                account_ids=context.authorized_account_ids,
                resource_ids=context.authorized_resource_ids,
            )
            if detail is None:
                raise KnowledgeNotFoundError
            return detail

    def download_document_version(
        self,
        context: RequestContext,
        *,
        knowledge_base_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
    ) -> KnowledgeDocumentDownloadFile:
        """读取原文件并在响应前复核关系，避免数据库事务跨越外部对象读取。"""

        account_id = _browser_account(context)
        descriptor = self._get_download_descriptor(
            context,
            account_id=account_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            document_version_id=document_version_id,
        )
        try:
            content = self._storage.get(
                WorkspaceObject(descriptor.workspace_id, descriptor.object_key)
            )
        except ObjectStorageUnavailableError as error:
            raise DocumentStorageUnavailableError from error
        if len(content) != descriptor.size_bytes:
            raise DocumentStorageUnavailableError

        # 外部读取期间成员、文档或来源可能改变；返回字节前必须重查且要求描述完全一致。
        verified = self._get_download_descriptor(
            context,
            account_id=account_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            document_version_id=document_version_id,
            expected_descriptor=descriptor,
            record_audit=True,
        )
        return KnowledgeDocumentDownloadFile(
            file_name=verified.file_name,
            media_type=verified.media_type,
            content=content,
        )

    def _get_download_descriptor(
        self,
        context: RequestContext,
        *,
        account_id: UUID,
        knowledge_base_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
        expected_descriptor: KnowledgeDocumentDownload | None = None,
        record_audit: bool = False,
    ) -> KnowledgeDocumentDownload:
        """在一个短事务内重新建立下载所需的完整可信关系。"""

        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work.knowledge, context.workspace_id, account_id)
            _require_active_knowledge_base(
                unit_of_work.knowledge,
                context.workspace_id,
                knowledge_base_id,
            )
            descriptor = unit_of_work.knowledge.get_document_download(
                context.workspace_id,
                knowledge_base_id,
                document_id,
                document_version_id,
                authorized_workspace=context.authorized_workspace,
                department_ids=context.authorized_department_ids,
                account_ids=context.authorized_account_ids,
                resource_ids=context.authorized_resource_ids,
            )
            if descriptor is None:
                raise KnowledgeNotFoundError
            if expected_descriptor is not None and descriptor != expected_descriptor:
                raise KnowledgeNotFoundError
            if record_audit:
                _record_download(unit_of_work, context, descriptor, datetime.now(UTC))
                unit_of_work.commit()
            return descriptor

    def retry_ingestion_job(
        self,
        context: RequestContext,
        *,
        ingestion_job_id: UUID,
    ) -> IngestionJob:
        """校验任务归属和可重试状态后创建新租约，禁止复用失败执行。"""

        # 1. 锁定任务并重新验证空间成员、知识库和文档仍处于活动状态。
        account_id = _browser_account(context)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                _require_active_member(unit_of_work.knowledge, context.workspace_id, account_id)
                current = unit_of_work.knowledge.get_ingestion_job(
                    context.workspace_id,
                    ingestion_job_id,
                    for_update=True,
                )
                if current is None:
                    raise KnowledgeNotFoundError
                _require_active_knowledge_base(
                    unit_of_work.knowledge,
                    context.workspace_id,
                    current.knowledge_base_id,
                )
                _require_active_document(
                    unit_of_work.knowledge,
                    context.workspace_id,
                    current.knowledge_base_id,
                    current.document_id,
                )
                # 2. 领域状态机生成新的排队状态和追踪上下文，已运行或已成功任务不能重试。
                retried = current.retry_manually(
                    actor_id=context.actor_id,
                    trace_id=context.trace.trace_id,
                    traceparent=context.trace.traceparent,
                    occurred_at=now,
                )
                # 3. 任务、审计和 Outbox 同事务提交，Worker 只领取完整的新版本。
                unit_of_work.knowledge.save_ingestion_job(retried)
                _record_retry(unit_of_work, context, retried, now)
                unit_of_work.commit()
                return retried
        except ManualIngestionRetryNotAllowedError as error:
            raise KnowledgeConflictError from error
        except KnowledgeWriteConflictError as error:
            raise KnowledgeConflictError from error

    def cancel_ingestion_job(
        self,
        context: RequestContext,
        *,
        ingestion_job_id: UUID,
    ) -> IngestionJob:
        """取消尚未终止的入库任务，并原子关闭可能存在的活动 Attempt。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                # 1. 锁定任务并复核当前成员、知识库和文档，跨空间请求继续返回不可见。
                _require_active_member(unit_of_work.knowledge, context.workspace_id, account_id)
                current = unit_of_work.knowledge.get_ingestion_job(
                    context.workspace_id,
                    ingestion_job_id,
                    for_update=True,
                )
                if current is None:
                    raise KnowledgeNotFoundError
                _require_active_knowledge_base(
                    unit_of_work.knowledge,
                    context.workspace_id,
                    current.knowledge_base_id,
                )
                _require_active_document(
                    unit_of_work.knowledge,
                    context.workspace_id,
                    current.knowledge_base_id,
                    current.document_id,
                )
                # 2. 同事务转换任务/阶段/Attempt，Worker 的迟到成功或失败回写只能得到失租结果。
                cancelled = current.cancel(actor_id=context.actor_id, occurred_at=now)
                unit_of_work.knowledge.save_cancelled_ingestion_job(
                    cancelled,
                    previous_status=current.status,
                )
                _record_cancellation(unit_of_work, context, current, now)
                unit_of_work.commit()
                return cancelled
        except (InvalidIngestionJobError, KnowledgeWriteConflictError) as error:
            raise KnowledgeConflictError from error


def _browser_account(context: RequestContext) -> UUID:
    if (
        context.user_id is None
        or context.user_id != context.actor_id
        or context.authentication_method != "browser_session"
    ):
        raise KnowledgeDeniedError
    return context.user_id


def _require_active_member(
    repository: KnowledgeRepository,
    workspace_id: UUID,
    account_id: UUID,
) -> None:
    access = repository.get_workspace_access(workspace_id, account_id)
    if access is None or access[0] != "active":
        raise KnowledgeDeniedError


def _require_active_knowledge_base(
    repository: KnowledgeRepository,
    workspace_id: UUID,
    knowledge_base_id: UUID,
) -> None:
    knowledge_base = repository.get_knowledge_base(workspace_id, knowledge_base_id)
    if knowledge_base is None or knowledge_base.status != "active":
        raise KnowledgeNotFoundError


def _require_active_document(
    repository: KnowledgeRepository,
    workspace_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
) -> None:
    document = repository.get_document(workspace_id, document_id)
    if (
        document is None
        or document.knowledge_base_id != knowledge_base_id
        or document.status != "active"
    ):
        raise KnowledgeNotFoundError


def _require_limit(limit: int) -> None:
    if not 1 <= limit <= 100:
        raise ValueError("知识管理列表上限必须在 1 到 100 之间")


def _record_retry(
    unit_of_work: KnowledgeUnitOfWork,
    context: RequestContext,
    job: IngestionJob,
    occurred_at: datetime,
) -> None:
    # 重试事实只记录稳定标识和次数，不复制对象键、文件内容或历史错误明细。
    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action="knowledge.ingestion.retry",
            resource_type="ingestion_job",
            resource_id=job.ingestion_job_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes={"manual_retry_count": job.manual_retry_count},
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type="knowledge.ingestion.retry_requested",
            workspace_id=context.workspace_id,
            aggregate_id=job.ingestion_job_id,
            aggregate_version=job.manual_retry_count,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload={
                "ingestion_job_id": str(job.ingestion_job_id),
                "document_version_id": str(job.document_version_id),
                "manual_retry_count": job.manual_retry_count,
            },
        )
    )


def _record_cancellation(
    unit_of_work: KnowledgeUnitOfWork,
    context: RequestContext,
    job: IngestionJob,
    occurred_at: datetime,
) -> None:
    """记录最小取消证据，不复制对象键、来源内容或历史错误消息。"""

    attributes: dict[str, object] = {
        "previous_status": job.status,
        "attempt_count": job.attempt_count,
    }
    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action="knowledge.ingestion.cancel",
            resource_type="ingestion_job",
            resource_id=job.ingestion_job_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes=attributes,
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type="knowledge.ingestion.cancelled",
            workspace_id=context.workspace_id,
            aggregate_id=job.ingestion_job_id,
            aggregate_version=job.manual_retry_count + job.attempt_count + 1,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload={
                "ingestion_job_id": str(job.ingestion_job_id),
                "document_version_id": str(job.document_version_id),
                "previous_status": job.status,
            },
        )
    )


def _record_download(
    unit_of_work: KnowledgeUnitOfWork,
    context: RequestContext,
    descriptor: KnowledgeDocumentDownload,
    occurred_at: datetime,
) -> None:
    """记录下载成功事实；审计只保存业务标识和文件大小，不复制对象键。"""

    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action="knowledge.document.download",
            resource_type="document_version",
            resource_id=descriptor.document_version_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes={
                "document_id": str(descriptor.document_id),
                "source_id": str(descriptor.source_id),
                "size_bytes": descriptor.size_bytes,
            },
        )
    )
