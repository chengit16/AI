from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.api.dependencies import (
    role_service,
    trusted_request_context,
)
from ai_platform_api.modules.identity.api.role_schemas import (
    CreateRoleBindingRequest,
    CreateRoleRequest,
    EffectiveRoleResponse,
    EffectiveRoleSetResponse,
    EffectiveRoleSourceResponse,
    RoleBindingResponse,
    RoleListResponse,
    RoleResponse,
    RoleStatusRequest,
)
from ai_platform_api.modules.identity.application.roles import (
    EffectiveRoleSet,
    Role,
    RoleBinding,
    RoleService,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/roles", tags=["企业角色"])


def _role_response(role: Role) -> RoleResponse:
    return RoleResponse(
        role_id=role.role_id,
        role_key=role.role_key,
        name=role.name,
        status=role.status,
        system_managed=role.system_managed,
        version=role.version,
    )


def _binding_response(binding: RoleBinding) -> RoleBindingResponse:
    return RoleBindingResponse(
        binding_id=binding.binding_id,
        role_id=binding.role_id,
        scope_type=binding.scope_type,
        department_id=binding.department_id,
        membership_id=binding.membership_id,
        status=binding.status,
        version=binding.version,
    )


def _effective_response(role_set: EffectiveRoleSet) -> EffectiveRoleSetResponse:
    return EffectiveRoleSetResponse(
        account_id=role_set.account_id,
        membership_id=role_set.membership_id,
        role_version=role_set.role_version,
        roles=[
            EffectiveRoleResponse(
                role_id=role.role_id,
                role_key=role.role_key,
                name=role.name,
                sources=[
                    EffectiveRoleSourceResponse(
                        scope_type=source.scope_type,
                        scope_id=source.scope_id,
                    )
                    for source in role.sources
                ],
            )
            for role in role_set.roles
        ],
    )


@router.post(
    "",
    response_model=RoleResponse,
    operation_id="createEnterpriseRole",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def create_role(
    workspace_id: UUID,
    body: CreateRoleRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[RoleService, Depends(role_service)],
) -> RoleResponse:
    return _role_response(
        service.create(
            context,
            workspace_id=workspace_id,
            role_key=body.role_key,
            name=body.name,
        )
    )


@router.get(
    "",
    response_model=RoleListResponse,
    operation_id="listEnterpriseRoles",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_roles(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[RoleService, Depends(role_service)],
) -> RoleListResponse:
    return RoleListResponse(
        items=[
            _role_response(role) for role in service.list_roles(context, workspace_id=workspace_id)
        ]
    )


@router.post(
    "/{role_id}/status",
    response_model=RoleResponse,
    operation_id="setEnterpriseRoleStatus",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def set_role_status(
    workspace_id: UUID,
    role_id: UUID,
    body: RoleStatusRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[RoleService, Depends(role_service)],
) -> RoleResponse:
    return _role_response(
        service.set_status(
            context,
            workspace_id=workspace_id,
            role_id=role_id,
            active=body.active,
        )
    )


@router.post(
    "/bindings",
    response_model=RoleBindingResponse,
    operation_id="bindEnterpriseRole",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def bind_role(
    workspace_id: UUID,
    body: CreateRoleBindingRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[RoleService, Depends(role_service)],
) -> RoleBindingResponse:
    return _binding_response(
        service.bind(
            context,
            workspace_id=workspace_id,
            role_id=body.role_id,
            scope_type=body.scope_type,
            department_id=body.department_id,
            target_account_id=body.account_id,
        )
    )


@router.post(
    "/bindings/{binding_id}/revoke",
    response_model=RoleBindingResponse,
    operation_id="revokeEnterpriseRoleBinding",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def revoke_role_binding(
    workspace_id: UUID,
    binding_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[RoleService, Depends(role_service)],
) -> RoleBindingResponse:
    return _binding_response(
        service.revoke(
            context,
            workspace_id=workspace_id,
            binding_id=binding_id,
        )
    )


@router.get(
    "/effective/{account_id}",
    response_model=EffectiveRoleSetResponse,
    operation_id="getEffectiveEnterpriseRoles",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def get_effective_roles(
    workspace_id: UUID,
    account_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[RoleService, Depends(role_service)],
) -> EffectiveRoleSetResponse:
    return _effective_response(
        service.effective_roles(
            context,
            workspace_id=workspace_id,
            target_account_id=account_id,
        )
    )
