"""定义企业团队管理聚合和原子成员治理 HTTP Schema。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TeamManagementStatisticsResponse(BaseModel):
    """定义团队成员、邀请与组织结构计数。"""

    model_config = ConfigDict(extra="forbid")

    active_members: int = Field(ge=0)
    disabled_members: int = Field(ge=0)
    pending_invitations: int = Field(ge=0)
    departments: int = Field(ge=0)
    positions: int = Field(ge=0)


class TeamDepartmentResponse(BaseModel):
    """定义团队页可选部门及其层级和有效状态。"""

    model_config = ConfigDict(extra="forbid")

    department_id: UUID
    parent_department_id: UUID | None
    name: str
    status: Literal["active", "disabled"]
    effective_active: bool
    depth: int = Field(ge=0)
    version: int = Field(ge=1)


class TeamPositionResponse(BaseModel):
    """定义团队页可选岗位及其所属部门。"""

    model_config = ConfigDict(extra="forbid")

    position_id: UUID
    department_id: UUID
    name: str
    status: Literal["active", "disabled"]
    effective_active: bool
    version: int = Field(ge=1)


class TeamRoleResponse(BaseModel):
    """定义团队页允许直接分配的自定义角色。"""

    model_config = ConfigDict(extra="forbid")

    role_id: UUID
    role_key: str
    name: str


class TeamEffectiveRoleResponse(TeamRoleResponse):
    """定义成员有效角色及其授权来源类型。"""

    source_types: list[Literal["workspace", "department", "member"]]


class TeamMemberResponse(BaseModel):
    """定义成员生命周期、组织归属、角色和最后活动快照。"""

    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    display_name: str | None
    login_name: str | None
    membership_type: Literal["owner", "member"]
    status: Literal["active", "disabled", "left"]
    department_ids: list[UUID]
    primary_department_id: UUID | None
    position_ids: list[UUID]
    direct_role_ids: list[UUID]
    effective_roles: list[TeamEffectiveRoleResponse]
    joined_at: datetime
    updated_at: datetime
    last_active_at: datetime | None
    version: int = Field(ge=1)


class TeamInvitationResponse(BaseModel):
    """定义邀请目标、邀请人、有效状态与时间窗口。"""

    model_config = ConfigDict(extra="forbid")

    invitation_id: UUID
    invited_account_id: UUID
    invited_display_name: str | None
    invited_login_name: str | None
    invited_by_display_name: str | None
    status: Literal["pending", "accepted", "cancelled", "expired"]
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None


class TeamAuditResponse(BaseModel):
    """定义团队治理时间线的低敏审计摘要。"""

    model_config = ConfigDict(extra="forbid")

    audit_id: UUID
    actor_display_name: str | None
    action: str
    resource_type: str
    resource_id: UUID
    outcome: Literal["succeeded", "denied", "failed"]
    occurred_at: datetime


class TeamWorkspaceResponse(BaseModel):
    """定义团队页企业空间摘要。"""

    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    workspace_type: Literal["enterprise"]
    name: str
    status: Literal["active", "suspended", "archived"]


class TeamManagementResponse(BaseModel):
    """定义团队管理统一读模型，前端无需拼接多份漂移请求。"""

    model_config = ConfigDict(extra="forbid")

    workspace: TeamWorkspaceResponse
    statistics: TeamManagementStatisticsResponse
    members: list[TeamMemberResponse]
    invitations: list[TeamInvitationResponse]
    departments: list[TeamDepartmentResponse]
    positions: list[TeamPositionResponse]
    roles: list[TeamRoleResponse]
    recent_audits: list[TeamAuditResponse]
    generated_at: datetime


class UpdateTeamMemberRequest(BaseModel):
    """定义带乐观版本的成员组织和直接角色原子替换输入。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    department_ids: list[UUID] = Field(max_length=50)
    primary_department_id: UUID | None
    position_ids: list[UUID] = Field(max_length=50)
    direct_role_ids: list[UUID] = Field(max_length=50)
