from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateEnterpriseWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)


class InviteWorkspaceMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_name: str = Field(min_length=3, max_length=255)


class WorkspaceSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    workspace_type: Literal["personal", "enterprise"]
    name: str
    status: Literal["active", "suspended", "archived"]
    membership_type: Literal["owner", "member"]
    membership_status: Literal["active", "disabled", "left"]


class WorkspaceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WorkspaceSummaryResponse]


class WorkspaceInvitationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invitation_id: UUID
    workspace_id: UUID
    status: Literal["pending", "accepted", "cancelled", "expired"]
    expires_at: datetime


class WorkspaceMembershipResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    membership_type: Literal["owner", "member"]
    status: Literal["active", "disabled", "left"]


class WorkspaceMemberResponse(WorkspaceMembershipResponse):
    display_name: str


class WorkspaceMemberListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WorkspaceMemberResponse]
