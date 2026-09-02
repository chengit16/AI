"""定义企业分类、团队知识域与范围投影 HTTP Schema。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EnterpriseKnowledgeStatisticsResponse(BaseModel):
    """定义企业知识门户顶部治理计数。"""

    model_config = ConfigDict(extra="forbid")

    active_categories: int = Field(ge=0)
    active_domains: int = Field(ge=0)
    classified_documents: int = Field(ge=0)
    governed_knowledge_bases: int = Field(ge=0)


class EnterpriseCategoryResponse(BaseModel):
    """定义企业分类、独立可见策略和显式文档关系。"""

    model_config = ConfigDict(extra="forbid")

    category_id: UUID
    parent_category_id: UUID | None
    name: str
    description: str | None
    visibility: Literal["public", "departments", "private"]
    department_ids: list[UUID]
    document_ids: list[UUID]
    # 兼容既有企业分类响应：历史服务端未返回该字段时，客户端按关闭审批处理。
    approval_required: bool = False
    status: Literal["active", "archived"]
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class RagPolicyResponse(BaseModel):
    """定义知识域当前生效的不可变 RAG 策略版本。"""

    model_config = ConfigDict(extra="forbid")

    policy_version: int = Field(ge=1)
    mode: Literal["balanced", "precision", "recall"]
    top_k: int = Field(ge=1, le=50)
    minimum_score: float = Field(ge=0, le=1)


class TeamKnowledgeDomainResponse(BaseModel):
    """定义团队知识域范围、知识库集合与治理状态。"""

    model_config = ConfigDict(extra="forbid")

    domain_id: UUID
    name: str
    description: str | None
    member_ids: list[UUID]
    department_ids: list[UUID]
    knowledge_base_ids: list[UUID]
    rag_policy: RagPolicyResponse
    status: Literal["active", "archived"]
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class EnterpriseDocumentOptionResponse(BaseModel):
    """定义分类绑定可选的活动文档。"""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    knowledge_base_id: UUID
    title: str
    security_level: str


class EnterpriseKnowledgeBaseOptionResponse(BaseModel):
    """定义知识域绑定可选的活动知识库。"""

    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: UUID
    name: str
    default_security_level: str


class EnterpriseDepartmentOptionResponse(BaseModel):
    """定义治理范围可选的活动部门。"""

    model_config = ConfigDict(extra="forbid")

    department_id: UUID
    name: str


class EnterpriseMemberOptionResponse(BaseModel):
    """定义知识域范围可选的活动成员。"""

    model_config = ConfigDict(extra="forbid")

    membership_id: UUID
    account_id: UUID
    display_name: str | None


class EnterpriseKnowledgePortalResponse(BaseModel):
    """定义企业知识门户统一快照，前端无需拼接多份漂移请求。"""

    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    workspace_name: str
    statistics: EnterpriseKnowledgeStatisticsResponse
    categories: list[EnterpriseCategoryResponse]
    domains: list[TeamKnowledgeDomainResponse]
    documents: list[EnterpriseDocumentOptionResponse]
    knowledge_bases: list[EnterpriseKnowledgeBaseOptionResponse]
    departments: list[EnterpriseDepartmentOptionResponse]
    members: list[EnterpriseMemberOptionResponse]
    generated_at: datetime


class CreateEnterpriseCategoryRequest(BaseModel):
    """定义分类创建及初始文档绑定输入。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    parent_category_id: UUID | None = None
    visibility: Literal["public", "departments", "private"]
    department_ids: list[UUID] = Field(default_factory=list, max_length=100)
    document_ids: list[UUID] = Field(default_factory=list, max_length=500)
    approval_required: bool = False


class UpdateEnterpriseCategoryRequest(BaseModel):
    """定义带乐观版本的分类元数据更新输入。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    parent_category_id: UUID | None = None
    visibility: Literal["public", "departments", "private"]
    department_ids: list[UUID] = Field(default_factory=list, max_length=100)
    approval_required: bool = False


class ReplaceCategoryDocumentsRequest(BaseModel):
    """定义分类文档关系整体替换输入。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    document_ids: list[UUID] = Field(max_length=500)


class VersionedArchiveRequest(BaseModel):
    """定义分类和知识域归档所需的乐观版本。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class CreateTeamKnowledgeDomainRequest(BaseModel):
    """定义知识域、初始范围与 RAG 策略输入。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    member_ids: list[UUID] = Field(default_factory=list, max_length=500)
    department_ids: list[UUID] = Field(default_factory=list, max_length=100)
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=100)
    rag_mode: Literal["balanced", "precision", "recall"] = "balanced"
    top_k: int = Field(default=8, ge=1, le=50)
    minimum_score: float = Field(default=0.25, ge=0, le=1)


class UpdateTeamKnowledgeDomainRequest(BaseModel):
    """定义知识域元数据和 RAG 策略更新输入。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    rag_mode: Literal["balanced", "precision", "recall"]
    top_k: int = Field(ge=1, le=50)
    minimum_score: float = Field(ge=0, le=1)


class ReplaceKnowledgeDomainScopeRequest(BaseModel):
    """定义成员、部门与知识库范围的原子替换输入。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    member_ids: list[UUID] = Field(max_length=500)
    department_ids: list[UUID] = Field(max_length=100)
    knowledge_base_ids: list[UUID] = Field(max_length=100)


class ResolvedKnowledgeDomainScopeResponse(BaseModel):
    """解释知识域声明范围与当前授权的交集结果。"""

    model_config = ConfigDict(extra="forbid")

    domain_id: UUID
    policy_version: int = Field(ge=1)
    actor_in_declared_scope: bool
    declared_knowledge_base_ids: list[UUID]
    authorized_knowledge_base_ids: list[UUID]
    effective_knowledge_base_ids: list[UUID]
    empty_reason: Literal[
        "none",
        "domain_inactive",
        "member_inactive",
        "actor_outside_declared_scope",
        "no_declared_knowledge_bases",
        "pdp_scope_empty",
    ]


class CreateDocumentPublishRequest(BaseModel):
    """定义文档版本发布申请的幂等输入。"""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class DocumentPublishApprovalLevelResponse(BaseModel):
    """定义发布审批层级、审批人和时间游标。"""

    model_config = ConfigDict(extra="forbid")

    sequence_no: int = Field(ge=1, le=5)
    mode: Literal["any", "all"]
    status: Literal["waiting", "active", "approved", "rejected", "withdrawn"]
    approver_account_ids: list[UUID]
    fallback_activated: bool
    reminder_at: datetime | None
    timeout_at: datetime | None
    completed_at: datetime | None


class DocumentPublishApprovalResponse(BaseModel):
    """定义发布请求绑定的通用审批运行快照。"""

    model_config = ConfigDict(extra="forbid")

    approval_instance_id: UUID
    status: Literal["pending", "approved", "rejected", "withdrawn"]
    current_sequence_no: int = Field(ge=1, le=5)
    personal_owner_confirmation: bool
    levels: list[DocumentPublishApprovalLevelResponse]


class DocumentPublishRequestResponse(BaseModel):
    """定义发布版本、分类快照、业务终态和审批链台账。"""

    model_config = ConfigDict(extra="forbid")

    publish_request_id: UUID
    document_id: UUID
    document_version_id: UUID
    knowledge_base_id: UUID
    requester_account_id: UUID
    category_ids: list[UUID]
    version_number: int = Field(ge=1)
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
    version: int = Field(ge=1)
    approval: DocumentPublishApprovalResponse
