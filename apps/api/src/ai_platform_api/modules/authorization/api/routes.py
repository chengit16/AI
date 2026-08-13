from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.api.schemas import (
    ReplaceRolePermissionsRequest,
    RolePermissionEntry,
    RolePermissionListResponse,
)
from ai_platform_api.modules.authorization.application.grants import (
    RolePermissionGrant,
    RolePermissionService,
)
from ai_platform_api.modules.identity.api.dependencies import (
    role_permission_service,
    trusted_request_context,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/roles", tags=["角色权限"])


@router.get(
    "/{role_id}/permissions",
    response_model=RolePermissionListResponse,
    operation_id="listEnterpriseRolePermissions",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def list_role_permissions(
    workspace_id: UUID,
    role_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[RolePermissionService, Depends(role_permission_service)],
) -> RolePermissionListResponse:
    return RolePermissionListResponse(
        items=[
            _entry(grant)
            for grant in service.list(
                context,
                workspace_id=workspace_id,
                role_id=role_id,
            )
        ]
    )


@router.put(
    "/{role_id}/permissions",
    response_model=RolePermissionListResponse,
    operation_id="replaceEnterpriseRolePermissions",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def replace_role_permissions(
    workspace_id: UUID,
    role_id: UUID,
    body: ReplaceRolePermissionsRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[RolePermissionService, Depends(role_permission_service)],
) -> RolePermissionListResponse:
    grants = service.replace(
        context,
        workspace_id=workspace_id,
        role_id=role_id,
        entries=tuple(
            (
                item.permission_code,
                item.scope_type,
                frozenset(item.department_ids),
                frozenset(item.resource_ids),
                item.maximum_security_level,
                frozenset(item.field_mask),
            )
            for item in body.items
        ),
    )
    return RolePermissionListResponse(items=[_entry(grant) for grant in grants])


def _entry(grant: RolePermissionGrant) -> RolePermissionEntry:
    return RolePermissionEntry(
        permission_code=grant.permission_code,
        scope_type=grant.scope_type,
        department_ids=sorted(grant.department_ids, key=lambda value: value.int),
        resource_ids=sorted(grant.resource_ids, key=lambda value: value.int),
        maximum_security_level=grant.maximum_security_level,
        field_mask=sorted(grant.field_mask),
    )
