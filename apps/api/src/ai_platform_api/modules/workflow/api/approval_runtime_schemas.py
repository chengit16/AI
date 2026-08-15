"""定义审批实例、层级、指派和动作命令的 HTTP Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ai_platform_api.modules.workflow.application.approval_runtime import (
    ApprovalRiskLevel,
    ApprovalRuntimeState,
    ApprovalSubject,
    SecurityLevel,
)


class StartApprovalInstanceRequest(BaseModel):
    """提供独立审批实例所需的可信主题和客户端幂等键。"""

    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
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
        """使用认证账号覆盖申请人身份，客户端不能代表其他成员发起审批。"""

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


class ApprovalActionRequest(BaseModel):
    """提供通过、驳回或撤回动作的稳定幂等键和原因码。"""

    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    reason_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_.-]{0,127}$",
    )


class TransferApprovalRequest(ApprovalActionRequest):
    """提供转交目标；活动成员与禁止申请人自审规则仍由服务端复核。"""

    target_account_id: UUID


class ApprovalAssignmentResponse(BaseModel):
    """返回层级内审批人的当前责任状态，不包含自由文本意见。"""

    approval_assignment_id: UUID
    approval_level_id: UUID
    approver_account_id: UUID
    status: Literal[
        "waiting",
        "pending",
        "approved",
        "rejected",
        "transferred",
        "cancelled",
    ]
    transferred_to_account_id: UUID | None
    created_at: datetime
    decided_at: datetime | None
    version: int


class ApprovalLevelResponse(BaseModel):
    """返回冻结层级模式、提醒超时游标和候补激活状态。"""

    approval_level_id: UUID
    sequence_no: int
    mode: Literal["any", "all"]
    status: Literal["waiting", "active", "approved", "rejected", "withdrawn"]
    reminder_after_minutes: int
    timeout_after_minutes: int
    timeout_action: Literal["escalate", "transfer", "reject", "wait"]
    fallback_approver_account_ids: list[UUID]
    fallback_activated: bool
    reminder_at: datetime | None
    reminded_at: datetime | None
    timeout_at: datetime | None
    activated_at: datetime | None
    completed_at: datetime | None
    version: int


class ApprovalInstanceResponse(BaseModel):
    """返回审批运行聚合，条件字段原文、Trace 和内部请求摘要不出接口。"""

    approval_instance_id: UUID
    workspace_id: UUID
    approval_policy_id: UUID | None
    approval_policy_version_id: UUID | None
    requester_account_id: UUID
    resource_type: str
    operation: str
    resource_id: UUID | None
    subject_digest: str
    chain_digest: str
    personal_owner_confirmation: bool
    status: Literal["pending", "approved", "rejected", "withdrawn"]
    current_sequence_no: int
    workflow_run_id: UUID | None
    workflow_step_id: UUID | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    version: int
    levels: list[ApprovalLevelResponse]
    assignments: list[ApprovalAssignmentResponse]

    @classmethod
    def from_domain(cls, state: ApprovalRuntimeState) -> ApprovalInstanceResponse:
        """将内部聚合投影为不泄漏条件字段与动作原因正文的稳定响应。"""

        instance = state.instance
        return cls(
            approval_instance_id=instance.approval_instance_id,
            workspace_id=instance.workspace_id,
            approval_policy_id=instance.approval_policy_id,
            approval_policy_version_id=instance.approval_policy_version_id,
            requester_account_id=instance.requester_account_id,
            resource_type=instance.resource_type,
            operation=instance.operation,
            resource_id=instance.resource_id,
            subject_digest=instance.subject_digest,
            chain_digest=instance.chain_digest,
            personal_owner_confirmation=instance.personal_owner_confirmation,
            status=instance.status,
            current_sequence_no=instance.current_sequence_no,
            workflow_run_id=instance.workflow_run_id,
            workflow_step_id=instance.workflow_step_id,
            created_at=instance.created_at,
            updated_at=instance.updated_at,
            completed_at=instance.completed_at,
            version=instance.version,
            levels=[
                ApprovalLevelResponse(
                    approval_level_id=level.approval_level_id,
                    sequence_no=level.sequence_no,
                    mode=level.mode,
                    status=level.status,
                    reminder_after_minutes=level.reminder_after_minutes,
                    timeout_after_minutes=level.timeout_after_minutes,
                    timeout_action=level.timeout_action,
                    fallback_approver_account_ids=list(level.fallback_approver_account_ids),
                    fallback_activated=level.fallback_activated,
                    reminder_at=level.reminder_at,
                    reminded_at=level.reminded_at,
                    timeout_at=level.timeout_at,
                    activated_at=level.activated_at,
                    completed_at=level.completed_at,
                    version=level.version,
                )
                for level in state.levels
            ],
            assignments=[
                ApprovalAssignmentResponse(
                    approval_assignment_id=assignment.approval_assignment_id,
                    approval_level_id=assignment.approval_level_id,
                    approver_account_id=assignment.approver_account_id,
                    status=assignment.status,
                    transferred_to_account_id=assignment.transferred_to_account_id,
                    created_at=assignment.created_at,
                    decided_at=assignment.decided_at,
                    version=assignment.version,
                )
                for assignment in state.assignments
            ],
        )


class ApprovalCommandResponse(BaseModel):
    """返回人工或到期命令后的聚合，以及是否命中幂等回放。"""

    instance: ApprovalInstanceResponse
    replayed: bool


class ApprovalInstanceListResponse(BaseModel):
    """返回申请人或历史审批人可见的审批实例。"""

    items: list[ApprovalInstanceResponse]


class DueApprovalResponse(BaseModel):
    """返回本次短批处理实际推进的到期审批实例。"""

    items: list[ApprovalCommandResponse]
