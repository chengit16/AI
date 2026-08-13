from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.config import Settings, get_settings
from ai_platform_api.modules.identity.api.dependencies import (
    authentication_service,
    registration_service,
    trusted_request_context,
)
from ai_platform_api.modules.identity.api.schemas import (
    AuthenticationContextResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    RegistrationRequest,
    RegistrationResponse,
)
from ai_platform_api.modules.identity.application.authentication import AuthenticationService
from ai_platform_api.modules.identity.application.errors import AuthenticationRequiredError
from ai_platform_api.modules.identity.application.registration import RegistrationService

router = APIRouter(prefix="/auth", tags=["身份认证"])


@router.post(
    "/register",
    response_model=RegistrationResponse,
    operation_id="registerPersonalAccount",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(409, 422, 500),
)
def register(
    body: RegistrationRequest,
    request: Request,
    response: Response,
    service: Annotated[RegistrationService, Depends(registration_service)],
) -> RegistrationResponse:
    state = request.scope.get("state", {})
    request_id = state.get("request_id")
    trace = state.get("trace_context")
    if not isinstance(request_id, UUID) or not isinstance(trace, TraceContext):
        raise RuntimeError("可信请求标识尚未建立")
    result = service.register(
        login_name=body.login_name,
        display_name=body.display_name,
        password=body.password.get_secret_value(),
        request_id=request_id,
        trace=trace,
    )
    response.headers["Cache-Control"] = "no-store"
    return RegistrationResponse(
        account_id=result.account_id,
        personal_workspace_id=result.personal_workspace_id,
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    operation_id="loginWithPassword",
    responses=error_responses(401, 422, 500),
)
def login(
    body: LoginRequest,
    response: Response,
    service: Annotated[AuthenticationService, Depends(authentication_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    session_token, csrf_token, account_id = service.login(
        body.login_name,
        body.password.get_secret_value(),
    )
    response.set_cookie(
        key="ai_platform_session",
        value=session_token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return LoginResponse(account_id=account_id, csrf_token=csrf_token)


@router.post(
    "/logout",
    response_model=LogoutResponse,
    operation_id="logoutCurrentSession",
    responses=error_responses(400, 401, 403, 422, 500),
)
def logout(
    request: Request,
    response: Response,
    _: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AuthenticationService, Depends(authentication_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LogoutResponse:
    session_token = request.cookies.get("ai_platform_session")
    if session_token is None:
        raise AuthenticationRequiredError
    service.logout(session_token)
    response.delete_cookie(
        key="ai_platform_session",
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return LogoutResponse(logged_out=True)


@router.get(
    "/context",
    response_model=AuthenticationContextResponse,
    operation_id="getAuthenticationContext",
    status_code=status.HTTP_200_OK,
    responses=error_responses(400, 401, 403, 422, 500),
)
def get_authentication_context(
    context: Annotated[RequestContext, Depends(trusted_request_context)],
) -> AuthenticationContextResponse:
    return AuthenticationContextResponse(
        request_id=context.request_id,
        trace_id=context.trace.trace_id,
        actor_id=context.actor_id,
        user_id=context.user_id,
        workspace_id=context.workspace_id,
        authentication_method=context.authentication_method,
        credential_scopes=(
            sorted(context.credential_scopes) if context.credential_scopes is not None else None
        ),
    )
