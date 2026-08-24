"""提供知识生产页范围化查询和安全人工重试用例。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.ingestion.domain import (
    IngestionJob,
    InvalidIngestionJobError,
    ManualIngestionRetryNotAllowedError,
)
from ai_platform_backend.integration.domain import AuditRecord

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.integration.domain.events import IntegrationEvent
from ai_platform_api.modules.knowledge.application.facts import (
    KnowledgeConflictError,
    KnowledgeDeniedError,
    KnowledgeNotFoundError,
)
from ai_platform_api.modules.knowledge.domain.models import (
    KnowledgeBase,
    KnowledgeDocumentSummary,
    KnowledgeRepository,
    KnowledgeUnitOfWork,
    KnowledgeWriteConflictError,
)

__all__ = ["IngestionJob", "KnowledgeDocumentSummary", "KnowledgeManagementService"]


class KnowledgeManagementService:
    """提供知识生产页所需的范围化读模型与安全人工重试命令。"""

    def __init__(self, unit_of_work: KnowledgeUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

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
