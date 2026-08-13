"""由 scripts/generate_contract_types.py 自动生成。请勿手工修改。"""

from __future__ import annotations

import typing


class AuthenticationContextResponse(typing.TypedDict):
    actor_id: str
    authentication_method: str
    credential_scopes: list[str] | None
    request_id: str
    trace_id: str
    user_id: str | None
    workspace_id: str


class CreateEnterpriseWorkspaceRequest(typing.TypedDict):
    name: str


class ErrorResponse(typing.TypedDict):
    code: str
    message: str
    request_id: str
    retryable: bool
    trace_id: str


class HealthResponse(typing.TypedDict):
    checks: dict[str, typing.Literal["ok", "degraded"]]
    environment: str
    service: str
    status: typing.Literal["ok", "degraded"]
    version: str


class InviteWorkspaceMemberRequest(typing.TypedDict):
    login_name: str


class LoginRequest(typing.TypedDict):
    login_name: str
    password: str


class LoginResponse(typing.TypedDict):
    account_id: str
    csrf_token: str


class LogoutResponse(typing.TypedDict):
    logged_out: bool


class RegistrationRequest(typing.TypedDict):
    display_name: str
    login_name: str
    password: str


class RegistrationResponse(typing.TypedDict):
    account_id: str
    personal_workspace_id: str


class WorkspaceInvitationResponse(typing.TypedDict):
    expires_at: str
    invitation_id: str
    status: typing.Literal["pending", "accepted", "cancelled", "expired"]
    workspace_id: str


class WorkspaceListResponse(typing.TypedDict):
    items: list[WorkspaceSummaryResponse]


class WorkspaceMemberListResponse(typing.TypedDict):
    items: list[WorkspaceMemberResponse]


class WorkspaceMemberResponse(typing.TypedDict):
    account_id: str
    display_name: str
    membership_type: typing.Literal["owner", "member"]
    status: typing.Literal["active", "disabled", "left"]


class WorkspaceMembershipResponse(typing.TypedDict):
    account_id: str
    membership_type: typing.Literal["owner", "member"]
    status: typing.Literal["active", "disabled", "left"]


class WorkspaceSummaryResponse(typing.TypedDict):
    membership_status: typing.Literal["active", "disabled", "left"]
    membership_type: typing.Literal["owner", "member"]
    name: str
    status: typing.Literal["active", "suspended", "archived"]
    workspace_id: str
    workspace_type: typing.Literal["personal", "enterprise"]
