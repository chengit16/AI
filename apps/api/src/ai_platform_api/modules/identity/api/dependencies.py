from typing import Annotated
from uuid import UUID

from fastapi import Header, Request

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.authentication import AuthenticationService
from ai_platform_api.modules.identity.application.errors import (
    AuthenticationRequiredError,
    WorkspaceRequiredError,
)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def authentication_service(request: Request) -> AuthenticationService:
    service = getattr(request.app.state, "authentication_service", None)
    if not isinstance(service, AuthenticationService):
        raise RuntimeError("身份认证服务尚未完成装配")
    return service


def trusted_request_context(
    request: Request,
    workspace_header: Annotated[str | None, Header(alias="X-Workspace-ID")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> RequestContext:
    try:
        workspace_id = UUID(workspace_header) if workspace_header is not None else None
    except ValueError as error:
        raise WorkspaceRequiredError from error
    if workspace_id is None:
        raise WorkspaceRequiredError

    state = request.scope.get("state", {})
    request_id = state.get("request_id")
    trace = state.get("trace_context")
    if not isinstance(request_id, UUID) or not isinstance(trace, TraceContext):
        raise RuntimeError("可信请求标识尚未建立")

    service = authentication_service(request)
    if authorization is not None:
        scheme, separator, credential = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not credential:
            raise AuthenticationRequiredError
        return service.api_key_context(
            credential=credential,
            workspace_id=workspace_id,
            request_id=request_id,
            trace=trace,
        )
    return service.browser_context(
        session_token=request.cookies.get("ai_platform_session"),
        csrf_token=csrf_token,
        require_csrf=request.method not in SAFE_METHODS,
        workspace_id=workspace_id,
        request_id=request_id,
        trace=trace,
    )
