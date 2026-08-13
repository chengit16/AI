from dataclasses import replace
from typing import Annotated, cast
from uuid import UUID

from fastapi import Header, Request

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.application.fields import FieldProjectionService
from ai_platform_api.modules.authorization.application.grants import RolePermissionService
from ai_platform_api.modules.authorization.application.policy import (
    ApiResource,
    PolicyDecisionPoint,
    PolicyRequest,
    ResourceReference,
    ResourceRegistry,
)
from ai_platform_api.modules.identity.application.authentication import AuthenticationService
from ai_platform_api.modules.identity.application.enterprise import EnterpriseWorkspaceService
from ai_platform_api.modules.identity.application.entitlements import EntitlementService
from ai_platform_api.modules.identity.application.errors import (
    AuthenticationRequiredError,
    WorkspaceRequiredError,
)
from ai_platform_api.modules.identity.application.organization import OrganizationService
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.application.roles import RoleService
from ai_platform_api.modules.workspace.application.resources import AuthorizationDeniedError

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def authentication_service(request: Request) -> AuthenticationService:
    service = getattr(request.app.state, "authentication_service", None)
    if not isinstance(service, AuthenticationService):
        raise RuntimeError("身份认证服务尚未完成装配")
    return service


def registration_service(request: Request) -> RegistrationService:
    service = getattr(request.app.state, "registration_service", None)
    if not isinstance(service, RegistrationService):
        raise RuntimeError("注册服务尚未完成装配")
    return service


def enterprise_workspace_service(request: Request) -> EnterpriseWorkspaceService:
    service = getattr(request.app.state, "enterprise_workspace_service", None)
    if not isinstance(service, EnterpriseWorkspaceService):
        raise RuntimeError("企业空间服务尚未完成装配")
    return service


def entitlement_service(request: Request) -> EntitlementService:
    service = getattr(request.app.state, "entitlement_service", None)
    if not isinstance(service, EntitlementService):
        raise RuntimeError("工作空间权益服务尚未完成装配")
    return service


def organization_service(request: Request) -> OrganizationService:
    service = getattr(request.app.state, "organization_service", None)
    if not isinstance(service, OrganizationService):
        raise RuntimeError("企业组织服务尚未完成装配")
    return service


def role_service(request: Request) -> RoleService:
    service = getattr(request.app.state, "role_service", None)
    if not isinstance(service, RoleService):
        raise RuntimeError("企业角色服务尚未完成装配")
    return service


def role_permission_service(request: Request) -> RolePermissionService:
    service = getattr(request.app.state, "role_permission_service", None)
    if not isinstance(service, RolePermissionService):
        raise RuntimeError("角色权限服务尚未完成装配")
    return service


def field_projection_service(request: Request) -> FieldProjectionService:
    service = getattr(request.app.state, "field_projection_service", None)
    if not isinstance(service, FieldProjectionService):
        raise RuntimeError("字段投影服务尚未完成装配")
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
        context = service.api_key_context(
            credential=credential,
            workspace_id=workspace_id,
            request_id=request_id,
            trace=trace,
        )
    else:
        context = service.browser_context(
            session_token=request.cookies.get("ai_platform_session"),
            csrf_token=csrf_token,
            require_csrf=request.method not in SAFE_METHODS,
            workspace_id=workspace_id,
            request_id=request_id,
            trace=trace,
        )
    return _authorize_registered_operation(request, context)


def _authorize_registered_operation(request: Request, context: RequestContext) -> RequestContext:
    operation_id = getattr(request.scope.get("route"), "operation_id", None)
    registry = getattr(request.app.state, "resource_registry", None)
    policy = getattr(request.app.state, "policy_decision_point", None)
    if not isinstance(operation_id, str) or not isinstance(registry, ResourceRegistry):
        raise AuthorizationDeniedError
    api_resource = next(
        (item for item in registry.api_resources if item.operation_id == operation_id),
        None,
    )
    if api_resource is None or api_resource.status != "active":
        raise AuthorizationDeniedError
    if api_resource.access_level != "authorized":
        return context
    if api_resource.permission_code is None or not hasattr(policy, "decide"):
        raise AuthorizationDeniedError
    permission = next(
        (item for item in registry.permissions if item.code == api_resource.permission_code),
        None,
    )
    if permission is None:
        raise AuthorizationDeniedError
    decision = cast(PolicyDecisionPoint, policy).decide(
        PolicyRequest(
            context=context,
            permission_code=api_resource.permission_code,
            resource=_resource_reference(request, context, api_resource, permission.resource_type),
        )
    )
    if not decision.allowed:
        raise AuthorizationDeniedError
    return replace(
        context,
        authorized_permission_code=decision.permission_code,
        authorized_workspace=decision.resource_scope.workspace,
        authorized_department_ids=decision.resource_scope.department_ids,
        authorized_account_ids=decision.resource_scope.account_ids,
        authorized_resource_ids=decision.resource_scope.resource_ids,
        authorized_field_mask=decision.field_mask,
    )


def _resource_reference(
    request: Request,
    context: RequestContext,
    api_resource: ApiResource,
    resource_type: str,
) -> ResourceReference:
    values = request.path_params
    resource_id = next(
        (
            value
            for key in (
                "account_id",
                "department_id",
                "position_id",
                "role_id",
                "binding_id",
                "invitation_id",
            )
            if (value := values.get(key)) is not None
        ),
        context.workspace_id,
    )
    trusted_id = resource_id if isinstance(resource_id, UUID) else UUID(str(resource_id))
    attributes: dict[str, object] = {
        key: value
        for key in ("account_id", "department_id", "position_id", "role_id")
        if (value := values.get(key)) is not None
    }
    attributes["risk_level"] = api_resource.risk_level
    return ResourceReference(
        resource_type=resource_type,
        resource_id=trusted_id,
        workspace_id=context.workspace_id,
        attributes=attributes,
    )
