"""把企业文档发布请求接入通用审批事务和知识发布指针。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID, uuid5

from sqlalchemy import CursorResult, insert, select, update
from sqlalchemy.orm import Session

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.domain.policy import (
    PolicyDecisionPoint,
    PolicyRequest,
    ResourceReference,
)
from ai_platform_api.modules.enterprise_knowledge.domain.models import (
    DocumentPublishFailureReason,
    DocumentPublishRequest,
)
from ai_platform_api.modules.enterprise_knowledge.infrastructure.sqlalchemy import (
    SqlAlchemyEnterpriseKnowledgeRepository,
)
from ai_platform_api.modules.knowledge.domain.models import (
    DocumentIndexNotReadyError,
    DocumentVersion,
    InvalidDocumentVersionTransitionError,
    KnowledgeRepository,
)
from ai_platform_api.modules.workflow.domain.approval_runtime import (
    ApprovalRuntimeState,
    ApprovalRuntimeStateError,
    ApprovalRuntimeTransition,
    ApprovalRuntimeValidationError,
    ApprovalSubjectEvent,
    ApprovalSubjectLifecycle,
    approval_subject_digest,
)
from ai_platform_api.modules.workflow.domain.approvals import ApprovalSubject
from ai_platform_api.persistence.tables import (
    document_publish_request_categories,
    document_publish_requests,
    document_versions,
    documents,
)

_PUBLISH_REQUEST_NAMESPACE = UUID("97b99c95-2782-465c-b19f-a8448f40804e")
_SUBJECT_FIELDS = frozenset(
    {
        "category_ids",
        "content_hash",
        "document_version_id",
        "governance_digest",
        "knowledge_base_id",
        "version_number",
    }
)


class SqlAlchemyDocumentPublishSubjectLifecycle(ApprovalSubjectLifecycle):
    """在审批事务内创建发布请求，并把终态同步到知识发布事实。"""

    def __init__(
        self,
        session: Session,
        knowledge_repository: KnowledgeRepository,
        policy: PolicyDecisionPoint,
        publish_version: Callable[..., DocumentVersion],
    ) -> None:
        self._session = session
        self._enterprise = SqlAlchemyEnterpriseKnowledgeRepository(session)
        self._knowledge = knowledge_repository
        self._policy = policy
        self._publish_version = publish_version

    def bind(
        self,
        state: ApprovalRuntimeState,
        subject: ApprovalSubject,
    ) -> ApprovalSubjectEvent | None:
        """验证低敏主题与当前治理事实，并原子创建唯一活动发布请求。"""

        # 1. 严格校验审批主题和冻结摘要，拒绝跨资源或篡改字段。
        if subject.resource_type != "document.publish":
            return None
        instance = state.instance
        document_id, document_version_id, knowledge_base_id, category_ids = _subject_ids(subject)
        if (
            subject.operation != "publish"
            or instance.resource_id != document_id
            or instance.subject_digest != approval_subject_digest(subject)
            or instance.requester_account_id != subject.requester_account_id
        ):
            raise ApprovalRuntimeValidationError
        # 2. 锁定文档、版本和活动分类，随后只写入不可变发布申请快照。
        candidate = self._enterprise.get_document_publish_candidate(
            subject.workspace_id,
            document_id,
            document_version_id,
            for_update=True,
        )
        if (
            candidate is None
            or candidate.knowledge_base_id != knowledge_base_id
            or not candidate.approval_required
            or not candidate.index_ready
            or candidate.version_number != subject.fields["version_number"]
            or candidate.content_hash != subject.fields["content_hash"]
            or candidate.governance_digest != subject.fields["governance_digest"]
            or tuple(item.category_id for item in candidate.categories) != category_ids
        ):
            raise ApprovalRuntimeStateError
        request = DocumentPublishRequest(
            publish_request_id=uuid5(
                _PUBLISH_REQUEST_NAMESPACE,
                f"{subject.workspace_id}:{instance.approval_instance_id}",
            ),
            workspace_id=subject.workspace_id,
            document_id=document_id,
            document_version_id=document_version_id,
            knowledge_base_id=knowledge_base_id,
            requester_account_id=subject.requester_account_id,
            approval_instance_id=instance.approval_instance_id,
            category_ids=category_ids,
            version_number=candidate.version_number,
            content_hash=candidate.content_hash,
            governance_digest=candidate.governance_digest,
            idempotency_key=instance.idempotency_key,
            status="pending",
            failure_reason_code=None,
            created_at=instance.created_at,
            updated_at=instance.created_at,
            completed_at=None,
            version=1,
        )
        request.assert_valid()
        self._session.execute(insert(document_publish_requests).values(**_request_values(request)))
        self._session.execute(
            insert(document_publish_request_categories),
            [
                {
                    "workspace_id": request.workspace_id,
                    "publish_request_id": request.publish_request_id,
                    "category_id": item.category_id,
                    "category_version": item.category_version,
                    "approval_required": item.approval_required,
                    "created_at": request.created_at,
                }
                for item in candidate.categories
            ],
        )
        return _event("enterprise.document.publish.requested", request)

    def apply_transition(
        self,
        previous: ApprovalRuntimeState,
        transition: ApprovalRuntimeTransition,
    ) -> ApprovalSubjectEvent | None:
        """审批终态保留批准事实；复核失败时只关闭发布请求，不切换指针。"""

        # 1. 仅处理本生命周期的审批主题，并读取仍处于待处理的业务申请。
        instance = transition.state.instance
        if previous.instance.resource_type != "document.publish" or instance.status == "pending":
            return None
        request = self._enterprise.get_publish_request_by_approval(
            instance.workspace_id,
            instance.approval_instance_id,
        )
        if request is None or request.status != "pending":
            raise ApprovalRuntimeStateError
        # 2. 驳回、撤回、超时和批准分别落入稳定业务终态。
        occurred_at = transition.action.occurred_at
        if instance.status == "rejected":
            if transition.action.action == "timeout_reject":
                completed = request.finish(status="expired", occurred_at=occurred_at)
            else:
                completed = request.finish(status="rejected", occurred_at=occurred_at)
        elif instance.status == "withdrawn":
            completed = request.finish(status="withdrawn", occurred_at=occurred_at)
        elif instance.status == "approved":
            failure = self._approval_failure(request, previous)
            if failure is None:
                try:
                    # 发布写入使用数据库保存点：即使索引在复核后异常消失，
                    # 也只回滚指针切换，外层仍可原子保留批准事实和失败原因。
                    with self._session.begin_nested():
                        self._publish_version(
                            self._knowledge,
                            workspace_id=request.workspace_id,
                            knowledge_base_id=request.knowledge_base_id,
                            document_id=request.document_id,
                            document_version_id=request.document_version_id,
                            occurred_at=occurred_at,
                            require_ready_index=True,
                        )
                except DocumentIndexNotReadyError:
                    failure = "index_not_ready"
                except InvalidDocumentVersionTransitionError:
                    failure = "version_changed"
            completed = request.finish(
                status="published" if failure is None else "publish_failed",
                occurred_at=occurred_at,
                failure_reason_code=failure,
            )
        else:
            raise ApprovalRuntimeStateError
        self._save_request(completed, expected_version=request.version)
        event_type = {
            "published": "enterprise.document.publish.published",
            "publish_failed": "enterprise.document.publish.failed",
            "rejected": "enterprise.document.publish.rejected",
            "withdrawn": "enterprise.document.publish.withdrawn",
            "expired": "enterprise.document.publish.expired",
        }[completed.status]
        return _event(event_type, completed)

    def _approval_failure(
        self,
        request: DocumentPublishRequest,
        previous: ApprovalRuntimeState,
    ) -> DocumentPublishFailureReason | None:
        """以申请人当前身份、版本和治理快照重新鉴权，所有异常路径失败关闭。"""

        # 1. 先复核申请人当前成员状态，停用主体必须失败关闭。
        if not self._enterprise.require_active_enterprise_member(
            request.workspace_id,
            request.requester_account_id,
            for_update=True,
        ):
            return "requester_inactive"
        # 2. 再复核版本、治理摘要和索引就绪状态，任何漂移都不切换指针。
        candidate = self._enterprise.get_document_publish_candidate(
            request.workspace_id,
            request.document_id,
            request.document_version_id,
            for_update=True,
        )
        if candidate is None:
            return self._missing_candidate_reason(request)
        if (
            candidate.knowledge_base_id != request.knowledge_base_id
            or candidate.version_number != request.version_number
            or candidate.content_hash != request.content_hash
        ):
            return "version_changed"
        if (
            candidate.governance_digest != request.governance_digest
            or tuple(item.category_id for item in candidate.categories) != request.category_ids
            or not candidate.approval_required
        ):
            return "governance_changed"
        if not candidate.index_ready:
            return "index_not_ready"
        requester_context = RequestContext.trusted(
            actor_id=request.requester_account_id,
            user_id=request.requester_account_id,
            workspace_id=request.workspace_id,
            # 审批实例保存的是完整 W3C traceparent；继续该链路时派生新 span，
            # 不能把 traceparent 误当成 TraceContext 的 span_id。
            trace=TraceContext.continue_from(previous.instance.traceparent),
            authentication_method="browser_session",
        )
        decision = self._policy.decide(
            PolicyRequest(
                requester_context,
                "knowledge.document.version.publish",
                ResourceReference(
                    "document_version",
                    request.document_version_id,
                    request.workspace_id,
                    {
                        "document_id": request.document_id,
                        "document_version_id": request.document_version_id,
                        "knowledge_base_id": request.knowledge_base_id,
                        "risk_level": "critical",
                    },
                ),
            )
        )
        if decision.allowed:
            return None
        return (
            "policy_unavailable"
            if decision.reason == "policy_unavailable"
            else "permission_revoked"
        )

    def _missing_candidate_reason(
        self, request: DocumentPublishRequest
    ) -> DocumentPublishFailureReason:
        document = self._session.execute(
            select(documents.c.status, documents.c.knowledge_base_id).where(
                documents.c.workspace_id == request.workspace_id,
                documents.c.document_id == request.document_id,
            )
        ).one_or_none()
        if (
            document is None
            or document.status != "active"
            or document.knowledge_base_id != request.knowledge_base_id
        ):
            return "document_inactive"
        version = self._session.execute(
            select(document_versions.c.status, document_versions.c.content_hash).where(
                document_versions.c.workspace_id == request.workspace_id,
                document_versions.c.document_id == request.document_id,
                document_versions.c.document_version_id == request.document_version_id,
            )
        ).one_or_none()
        if (
            version is None
            or version.status != "ready"
            or version.content_hash != request.content_hash
        ):
            return "version_changed"
        return "index_not_ready"

    def _save_request(self, request: DocumentPublishRequest, *, expected_version: int) -> None:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(document_publish_requests)
                .where(
                    document_publish_requests.c.workspace_id == request.workspace_id,
                    document_publish_requests.c.publish_request_id == request.publish_request_id,
                    document_publish_requests.c.version == expected_version,
                )
                .values(**_request_values(request, include_identity=False))
            ),
        )
        if result.rowcount != 1:
            raise ApprovalRuntimeStateError


def _subject_ids(subject: ApprovalSubject) -> tuple[UUID, UUID, UUID, tuple[UUID, ...]]:
    """严格解析发布主题，不允许正文、对象键或额外自由字段进入审批事实。"""

    if subject.resource_id is None or set(subject.fields) != _SUBJECT_FIELDS:
        raise ApprovalRuntimeValidationError
    try:
        categories = tuple(
            UUID(str(value)) for value in cast(list[object], subject.fields["category_ids"])
        )
        version_number = subject.fields["version_number"]
        if not isinstance(version_number, int) or isinstance(version_number, bool):
            raise ValueError
        if not categories or len(set(categories)) != len(categories):
            raise ValueError
        return (
            subject.resource_id,
            UUID(str(subject.fields["document_version_id"])),
            UUID(str(subject.fields["knowledge_base_id"])),
            tuple(sorted(categories, key=lambda item: item.int)),
        )
    except (TypeError, ValueError) as error:
        raise ApprovalRuntimeValidationError from error


def _request_values(
    request: DocumentPublishRequest,
    *,
    include_identity: bool = True,
) -> dict[str, object]:
    values: dict[str, object] = {
        "document_id": request.document_id,
        "document_version_id": request.document_version_id,
        "knowledge_base_id": request.knowledge_base_id,
        "requester_account_id": request.requester_account_id,
        "approval_instance_id": request.approval_instance_id,
        "version_number": request.version_number,
        "content_hash": request.content_hash,
        "governance_digest": request.governance_digest,
        "idempotency_key": request.idempotency_key,
        "status": request.status,
        "failure_reason_code": request.failure_reason_code,
        "created_at": request.created_at,
        "updated_at": request.updated_at,
        "completed_at": request.completed_at,
        "version": request.version,
    }
    if include_identity:
        values.update(
            publish_request_id=request.publish_request_id,
            workspace_id=request.workspace_id,
        )
    return values


def _event(event_type: str, request: DocumentPublishRequest) -> ApprovalSubjectEvent:
    attributes: dict[str, object] = {
        "document_id": str(request.document_id),
        "document_version_id": str(request.document_version_id),
        "publish_request_id": str(request.publish_request_id),
        "status": request.status,
        "version_number": request.version_number,
    }
    if request.failure_reason_code is not None:
        attributes["failure_reason_code"] = request.failure_reason_code
    return ApprovalSubjectEvent(
        event_type=event_type,
        resource_type="document_publish_request",
        resource_id=request.publish_request_id,
        aggregate_id=request.publish_request_id,
        aggregate_version=request.version,
        attributes=attributes,
    )
