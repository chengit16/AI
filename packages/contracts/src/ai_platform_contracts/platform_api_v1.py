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
