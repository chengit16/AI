"""定义企业知识治理用例返回给协议层的稳定只读结果。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.enterprise_knowledge.domain.models import (
    EnterpriseCategoryView as DomainEnterpriseCategoryView,
)
from ai_platform_api.modules.enterprise_knowledge.domain.models import (
    EnterpriseKnowledgePortalSnapshot,
    ResolvedKnowledgeDomainScope,
    TeamKnowledgeDomain,
)

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
