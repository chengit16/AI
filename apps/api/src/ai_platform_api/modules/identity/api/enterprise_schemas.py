"""定义企业空间和成员生命周期接口的请求与响应 Schema。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateEnterpriseWorkspaceRequest(BaseModel):
    """定义创建企业工作空间操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)


class InviteWorkspaceMemberRequest(BaseModel):
    """定义邀请工作空间成员操作的请求字段与协议校验边界。"""

    model_config = ConfigDict(extra="forbid")

    login_name: str = Field(min_length=3, max_length=255)


class WorkspaceSummaryResponse(BaseModel):
    """定义工作空间摘要操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    workspace_type: Literal["personal", "enterprise"]
    name: str
    status: Literal["active", "suspended", "archived"]
    membership_type: Literal["owner", "member"]
    membership_status: Literal["active", "disabled", "left"]


class WorkspaceListResponse(BaseModel):
    """定义工作空间列表操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    items: list[WorkspaceSummaryResponse]


class WorkspaceInvitationResponse(BaseModel):
    """定义工作空间邀请操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    invitation_id: UUID
    workspace_id: UUID
    status: Literal["pending", "accepted", "cancelled", "expired"]
    expires_at: datetime


class WorkspaceMembershipResponse(BaseModel):
    """定义工作空间成员身份操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    membership_type: Literal["owner", "member"]
    status: Literal["active", "disabled", "left"]


class WorkspaceMemberResponse(WorkspaceMembershipResponse):
    """定义工作空间成员操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    display_name: str


class WorkspaceMemberProjectionResponse(BaseModel):
    """定义工作空间成员投影操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    account_id: UUID | None = None
    display_name: str | None = None
    membership_type: Literal["owner", "member"] | None = None
    status: Literal["active", "disabled", "left"] | None = None


class WorkspaceMemberListResponse(BaseModel):
    """定义工作空间成员列表操作的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    items: list[WorkspaceMemberResponse | WorkspaceMemberProjectionResponse]


class EnterpriseConsoleTrendPointResponse(BaseModel):
    """定义企业文档增长趋势的低敏统计点。"""

    model_config = ConfigDict(extra="forbid")

    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    document_count: int = Field(ge=0)


class EnterpriseConsoleRecentDocumentResponse(BaseModel):
    """定义企业控制台最近内容摘要，不暴露正文和对象存储信息。"""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    knowledge_base_id: UUID
    knowledge_base_name: str
    title: str
    updated_at: datetime
    published_at: datetime | None
    status: Literal["published", "unpublished"]


class EnterpriseConsoleStatisticsResponse(BaseModel):
    """定义企业控制台统计快照的非负计数和容量字段。"""

    model_config = ConfigDict(extra="forbid")

    active_member_count: int = Field(ge=0)
    active_knowledge_base_count: int = Field(ge=0)
    active_document_count: int = Field(ge=0)
    published_document_count: int = Field(ge=0)
    processing_document_count: int = Field(ge=0)
    failed_document_count: int = Field(ge=0)
    storage_used_bytes: int = Field(ge=0)
    storage_limit_bytes: int = Field(ge=0)


class EnterpriseConsoleWorkspaceResponse(BaseModel):
    """定义企业控制台空间资料的稳定响应结构。"""

    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    workspace_type: Literal["enterprise"]
    name: str
    status: Literal["active", "suspended", "archived"]
    description: str | None
    logo_url: str | None


class EnterpriseConsoleResponse(BaseModel):
    """定义企业控制台聚合响应及其最终一致性时间窗口。"""

    model_config = ConfigDict(extra="forbid")

    workspace: EnterpriseConsoleWorkspaceResponse
    statistics: EnterpriseConsoleStatisticsResponse
    trend: list[EnterpriseConsoleTrendPointResponse]
    recent_documents: list[EnterpriseConsoleRecentDocumentResponse]
    generated_at: datetime
    time_window_start: datetime
    time_window_end: datetime
    consistency: Literal["eventually_consistent"]
