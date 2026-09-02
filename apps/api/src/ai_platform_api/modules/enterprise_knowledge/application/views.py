"""定义企业知识治理用例返回给协议层的稳定只读结果。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.enterprise_knowledge.domain.models import (
    DocumentPublishRequest,
    EnterpriseKnowledgePortalSnapshot,
    ResolvedKnowledgeDomainScope,
    TeamKnowledgeDomain,
)
from ai_platform_api.modules.enterprise_knowledge.domain.models import (
    EnterpriseCategoryView as DomainEnterpriseCategoryView,
)
from ai_platform_api.modules.workflow.domain.approval_runtime import ApprovalRuntimeState

CategoryVisibilityView = Literal["public", "departments", "private"]
GovernanceStatusView = Literal["active", "archived"]
RagPolicyModeView = Literal["balanced", "precision", "recall"]
ScopeEmptyReasonView = Literal[
    "none",
    "domain_inactive",
    "member_inactive",
    "actor_outside_declared_scope",
    "no_declared_knowledge_bases",
    "pdp_scope_empty",
]


@dataclass(frozen=True)
class EnterpriseCategoryResultView:
    """向协议层暴露分类策略与经过权限裁剪的文档绑定。"""

    category_id: UUID
    parent_category_id: UUID | None
    name: str
    description: str | None
    visibility: CategoryVisibilityView
    department_ids: tuple[UUID, ...]
    document_ids: tuple[UUID, ...]
    approval_required: bool
    status: GovernanceStatusView
    created_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class RagPolicyView:
    """向协议层暴露知识域当前生效的不可变 RAG 策略。"""

    policy_version: int
    mode: RagPolicyModeView
    top_k: int
    minimum_score: float


@dataclass(frozen=True)
class TeamKnowledgeDomainView:
    """向协议层暴露知识域治理范围和生命周期。"""

    domain_id: UUID
    name: str
    description: str | None
    member_ids: tuple[UUID, ...]
    department_ids: tuple[UUID, ...]
    knowledge_base_ids: tuple[UUID, ...]
    rag_policy: RagPolicyView
    status: GovernanceStatusView
    created_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class EnterpriseKnowledgeStatisticsView:
    """向协议层暴露企业知识门户治理计数。"""

    active_categories: int
    active_domains: int
    classified_documents: int
    governed_knowledge_bases: int


@dataclass(frozen=True)
class EnterpriseDocumentOptionView:
    """向协议层暴露经过密级裁剪的文档选项。"""

    document_id: UUID
    knowledge_base_id: UUID
    title: str
    security_level: SecurityLevel


@dataclass(frozen=True)
class EnterpriseKnowledgeBaseOptionView:
    """向协议层暴露可绑定的活动知识库选项。"""

    knowledge_base_id: UUID
    name: str
    default_security_level: SecurityLevel


@dataclass(frozen=True)
class EnterpriseDepartmentOptionView:
    """向协议层暴露可用于治理范围的活动部门。"""

    department_id: UUID
    name: str


@dataclass(frozen=True)
class EnterpriseMemberOptionView:
    """向协议层暴露经过字段遮罩的活动成员选项。"""

    membership_id: UUID
    account_id: UUID
    display_name: str | None


@dataclass(frozen=True)
class EnterpriseKnowledgePortalView:
    """聚合协议层渲染企业知识门户所需的稳定结果。"""

    workspace_id: UUID
    workspace_name: str
    statistics: EnterpriseKnowledgeStatisticsView
    categories: tuple[EnterpriseCategoryResultView, ...]
    domains: tuple[TeamKnowledgeDomainView, ...]
    documents: tuple[EnterpriseDocumentOptionView, ...]
    knowledge_bases: tuple[EnterpriseKnowledgeBaseOptionView, ...]
    departments: tuple[EnterpriseDepartmentOptionView, ...]
    members: tuple[EnterpriseMemberOptionView, ...]
    generated_at: datetime


@dataclass(frozen=True)
class ResolvedKnowledgeDomainScopeView:
    """向协议层解释声明范围、PDP 授权和最终有效交集。"""

    domain_id: UUID
    policy_version: int
    actor_in_declared_scope: bool
    declared_knowledge_base_ids: tuple[UUID, ...]
    authorized_knowledge_base_ids: tuple[UUID, ...]
    effective_knowledge_base_ids: tuple[UUID, ...]
    empty_reason: ScopeEmptyReasonView


@dataclass(frozen=True)
class DocumentPublishApprovalLevelView:
    """向发布门户暴露冻结审批层级与当前审批人，不返回策略条件原文。"""

    sequence_no: int
    mode: Literal["any", "all"]
    status: Literal["waiting", "active", "approved", "rejected", "withdrawn"]
    approver_account_ids: tuple[UUID, ...]
    fallback_activated: bool
    reminder_at: datetime | None
    timeout_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class DocumentPublishApprovalView:
    """向发布门户暴露通用审批运行快照，动作仍由审批运行时执行。"""

    approval_instance_id: UUID
    status: Literal["pending", "approved", "rejected", "withdrawn"]
    current_sequence_no: int
    personal_owner_confirmation: bool
    levels: tuple[DocumentPublishApprovalLevelView, ...]


@dataclass(frozen=True)
class DocumentPublishRequestView:
    """向协议层暴露发布请求、版本、分类和审批链的低敏台账事实。"""

    publish_request_id: UUID
    document_id: UUID
    document_version_id: UUID
    knowledge_base_id: UUID
    requester_account_id: UUID
    category_ids: tuple[UUID, ...]
    version_number: int
    status: Literal[
        "pending",
        "published",
        "rejected",
        "withdrawn",
        "expired",
        "publish_failed",
    ]
    failure_reason_code: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    version: int
    approval: DocumentPublishApprovalView


def enterprise_category_result_view(
    result: DomainEnterpriseCategoryView,
) -> EnterpriseCategoryResultView:
    """在 Application 边缘冻结分类结果，避免协议层依赖领域实体。"""

    category = result.category
    return EnterpriseCategoryResultView(
        category_id=category.category_id,
        parent_category_id=category.parent_category_id,
        name=category.name,
        description=category.description,
        visibility=category.visibility,
        department_ids=category.department_ids,
        document_ids=result.document_ids,
        approval_required=category.approval_required,
        status=category.status,
        created_at=category.created_at,
        updated_at=category.updated_at,
        version=category.version,
    )


def team_knowledge_domain_view(domain: TeamKnowledgeDomain) -> TeamKnowledgeDomainView:
    """裁剪知识域内部创建者和空间字段，只返回协议需要的事实。"""

    return TeamKnowledgeDomainView(
        domain_id=domain.domain_id,
        name=domain.name,
        description=domain.description,
        member_ids=domain.member_ids,
        department_ids=domain.department_ids,
        knowledge_base_ids=domain.knowledge_base_ids,
        rag_policy=RagPolicyView(**vars(domain.rag_policy)),
        status=domain.status,
        created_at=domain.created_at,
        updated_at=domain.updated_at,
        version=domain.version,
    )


def enterprise_knowledge_portal_view(
    snapshot: EnterpriseKnowledgePortalSnapshot,
) -> EnterpriseKnowledgePortalView:
    """在 Application 边缘冻结单一事务口径的企业知识门户快照。"""

    return EnterpriseKnowledgePortalView(
        workspace_id=snapshot.workspace_id,
        workspace_name=snapshot.workspace_name,
        statistics=EnterpriseKnowledgeStatisticsView(**vars(snapshot.statistics)),
        categories=tuple(enterprise_category_result_view(item) for item in snapshot.categories),
        domains=tuple(team_knowledge_domain_view(item) for item in snapshot.domains),
        documents=tuple(EnterpriseDocumentOptionView(**vars(item)) for item in snapshot.documents),
        knowledge_bases=tuple(
            EnterpriseKnowledgeBaseOptionView(**vars(item)) for item in snapshot.knowledge_bases
        ),
        departments=tuple(
            EnterpriseDepartmentOptionView(**vars(item)) for item in snapshot.departments
        ),
        members=tuple(EnterpriseMemberOptionView(**vars(item)) for item in snapshot.members),
        generated_at=snapshot.generated_at,
    )


def resolved_knowledge_domain_scope_view(
    scope: ResolvedKnowledgeDomainScope,
) -> ResolvedKnowledgeDomainScopeView:
    """冻结失败关闭后的范围解释结果，协议层不能再次扩大范围。"""

    return ResolvedKnowledgeDomainScopeView(**vars(scope))


def document_publish_request_view(
    request: DocumentPublishRequest,
    approval: ApprovalRuntimeState,
) -> DocumentPublishRequestView:
    """组合业务请求与通用审批快照，保持两个模块各自拥有自己的事实。"""

    assignments_by_level: dict[UUID, list[UUID]] = {}
    for assignment in approval.assignments:
        assignments_by_level.setdefault(assignment.approval_level_id, []).append(
            assignment.approver_account_id
        )
    levels = tuple(
        DocumentPublishApprovalLevelView(
            sequence_no=level.sequence_no,
            mode=level.mode,
            status=level.status,
            approver_account_ids=tuple(
                sorted(
                    assignments_by_level.get(level.approval_level_id, ()), key=lambda item: item.int
                )
            ),
            fallback_activated=level.fallback_activated,
            reminder_at=level.reminder_at,
            timeout_at=level.timeout_at,
            completed_at=level.completed_at,
        )
        for level in approval.levels
    )
    return DocumentPublishRequestView(
        publish_request_id=request.publish_request_id,
        document_id=request.document_id,
        document_version_id=request.document_version_id,
        knowledge_base_id=request.knowledge_base_id,
        requester_account_id=request.requester_account_id,
        category_ids=request.category_ids,
        version_number=request.version_number,
        status=request.status,
        failure_reason_code=request.failure_reason_code,
        created_at=request.created_at,
        updated_at=request.updated_at,
        completed_at=request.completed_at,
        version=request.version,
        approval=DocumentPublishApprovalView(
            approval_instance_id=approval.instance.approval_instance_id,
            status=approval.instance.status,
            current_sequence_no=approval.instance.current_sequence_no,
            personal_owner_confirmation=approval.instance.personal_owner_confirmation,
            levels=levels,
        ),
    )
