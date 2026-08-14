"""定义审批策略、不可变版本和审批链预计算的 HTTP Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ai_platform_api.modules.workflow.application.approvals import (
    ApprovalApproverSource,
    ApprovalFieldCondition,
    ApprovalLevelDefinition,
    ApprovalPolicyDefinition,
    ApprovalRiskLevel,
    ApprovalSubject,
    SecurityLevel,
)


class ApprovalFieldConditionDocument(BaseModel):
    """表示一个受限字段路径和固定操作符条件，不接受脚本表达式。"""

    field_path: str = Field(min_length=1, max_length=512)
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "exists"]
    expected: object | None = None

    def to_domain(self) -> ApprovalFieldCondition:
        """转换为领域条件，跨字段规则仍由领域校验器统一判定。"""

        return ApprovalFieldCondition(self.field_path, self.operator, self.expected)


class ApprovalApproverSourceDocument(BaseModel):
    """表示指定账号、角色或负责人职位来源。"""

    source_type: Literal["accounts", "roles", "department_managers", "upper_managers"]
    reference_ids: list[UUID] = Field(min_length=1, max_length=50)
    levels_up: int | None = Field(default=None, ge=1, le=5)

    def to_domain(self) -> ApprovalApproverSource:
        """保留管理员选择的稳定 ID，负责人解析禁止依赖职位名称。"""

        return ApprovalApproverSource(
            self.source_type,
            tuple(self.reference_ids),
            self.levels_up,
        )


class ApprovalLevelDocument(BaseModel):
    """表示一个串行层级的同级通过模式、提醒和超时动作。"""

    sequence_no: int = Field(ge=1, le=5)
    mode: Literal["any", "all"]
    sources: list[ApprovalApproverSourceDocument] = Field(min_length=1, max_length=20)
    reminder_after_minutes: int = Field(default=1_440, ge=1, le=43_199)
    timeout_after_minutes: int = Field(default=4_320, ge=2, le=43_200)
    timeout_action: Literal["escalate", "transfer", "reject", "wait"] = "wait"
    fallback_sources: list[ApprovalApproverSourceDocument] = Field(
        default_factory=list,
        max_length=20,
    )

    def to_domain(self) -> ApprovalLevelDefinition:
        """转换层级并保留顺序，连续层级和候补要求由领域校验器判定。"""

        return ApprovalLevelDefinition(
            self.sequence_no,
            self.mode,
            tuple(source.to_domain() for source in self.sources),
            self.reminder_after_minutes,
            self.timeout_after_minutes,
            self.timeout_action,
            tuple(source.to_domain() for source in self.fallback_sources),
        )


class ApprovalPolicyDefinitionDocument(BaseModel):
    """表示资源、组织、密级、风险、字段和最多五级审批定义。"""

    resource_type: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=128)
    priority: int = Field(ge=0, le=10_000)
    department_ids: list[UUID] = Field(default_factory=list, max_length=100)
    security_levels: list[SecurityLevel] = Field(default_factory=list, max_length=4)
    risk_levels: list[ApprovalRiskLevel] = Field(default_factory=list, max_length=3)
    field_conditions: list[ApprovalFieldConditionDocument] = Field(
        default_factory=list,
        max_length=20,
    )
    levels: list[ApprovalLevelDocument] = Field(min_length=1, max_length=5)
    allow_self_approval: bool = False

    def to_domain(self) -> ApprovalPolicyDefinition:
        """转换为不可变领域定义，服务端随后执行完整语义校验。"""

        return ApprovalPolicyDefinition(
            self.resource_type,
            self.operation,
            self.priority,
            tuple(self.department_ids),
            tuple(self.security_levels),
            tuple(self.risk_levels),
            tuple(condition.to_domain() for condition in self.field_conditions),
            tuple(level.to_domain() for level in self.levels),
            self.allow_self_approval,
        )


class CreateApprovalPolicyRequest(BaseModel):
    """创建审批策略身份和首个不可变版本。"""

    name: str = Field(min_length=1, max_length=120)
    definition: ApprovalPolicyDefinitionDocument


class ReviseApprovalPolicyRequest(BaseModel):
    """按策略乐观锁版本新增不可变版本。"""

    expected_version: int = Field(ge=1)
    definition: ApprovalPolicyDefinitionDocument


class PreviewApprovalChainRequest(BaseModel):
    """提供一次审批链预计算所需的可信业务主题字段。"""

    resource_type: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=128)
    resource_id: UUID | None = None
    department_ids: list[UUID] = Field(default_factory=list, max_length=100)
    security_level: SecurityLevel
    risk_level: ApprovalRiskLevel
    fields: dict[str, object] = Field(default_factory=dict)

    def to_domain(
        self,
        *,
        workspace_id: UUID,
        requester_account_id: UUID,
    ) -> ApprovalSubject:
        """使用认证账号覆盖申请人身份，客户端不能替其他账号预计算审批链。"""

        return ApprovalSubject(
            workspace_id,
            requester_account_id,
            self.resource_type,
            self.operation,
            self.resource_id,
            tuple(self.department_ids),
            self.security_level,
            self.risk_level,
            self.fields,
        )


class ApprovalPolicyResponse(BaseModel):
    """返回审批策略稳定身份和当前版本指针。"""

    approval_policy_id: UUID
    workspace_id: UUID
    name: str
    status: Literal["active", "disabled"]
    current_version_id: UUID
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    version: int


class ApprovalPolicyVersionResponse(BaseModel):
    """返回只增不改的审批定义版本及规范摘要。"""

    approval_policy_version_id: UUID
    approval_policy_id: UUID
    workspace_id: UUID
    version_number: int
    definition: ApprovalPolicyDefinitionDocument
    definition_digest: str
    created_by_account_id: UUID
    created_at: datetime


class ApprovalPolicyDetailResponse(BaseModel):
    """聚合策略身份和当前不可变版本。"""

    policy: ApprovalPolicyResponse
    version: ApprovalPolicyVersionResponse


class ApprovalPolicyListResponse(BaseModel):
    """返回当前授权范围内的审批策略身份列表。"""

    items: list[ApprovalPolicyResponse]


class ResolvedApprovalLevelResponse(BaseModel):
    """返回一个层级的稳定审批人集合和冻结超时参数。"""

    sequence_no: int
    mode: Literal["any", "all"]
    approver_account_ids: list[UUID]
    reminder_after_minutes: int
    timeout_after_minutes: int
    timeout_action: Literal["escalate", "transfer", "reject", "wait"]
    fallback_approver_account_ids: list[UUID]


class ApprovalChainResponse(BaseModel):
    """返回供 P1F-04 冻结的策略版本、审批层级和稳定链摘要。"""

    workspace_id: UUID
    approval_policy_id: UUID | None
    approval_policy_version_id: UUID | None
    personal_owner_confirmation: bool
    levels: list[ResolvedApprovalLevelResponse]
    chain_digest: str
