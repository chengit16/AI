from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.api.schemas import (
    ReplaceRoleMenuVisibilityRequest,
    ReplaceRolePermissionsRequest,
    ReplaceWorkspaceMenuConfigurationRequest,
    RoleMenuVisibilityEntry,
    RoleMenuVisibilityResponse,
    RolePermissionEntry,
    RolePermissionListResponse,
    WorkspaceMenuConfigurationResponse,
    WorkspaceMenuOverrideEntry,
)
from ai_platform_api.modules.authorization.application.grants import (
    RolePermissionGrant,
    RolePermissionService,
)
from ai_platform_api.modules.authorization.application.menus import (
    MenuConfigurationService,
    WorkspaceMenuOverride,
)
from ai_platform_api.modules.identity.api.dependencies import (
    menu_configuration_service,
    role_permission_service,
    trusted_request_context,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/roles", tags=["角色权限"])
menu_router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["菜单权限"])


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


@menu_router.get(
    "/menus",
    response_model=WorkspaceMenuConfigurationResponse,
    operation_id="getWorkspaceMenuConfiguration",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def get_workspace_menu_configuration(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[MenuConfigurationService, Depends(menu_configuration_service)],
) -> WorkspaceMenuConfigurationResponse:
    configuration = service.get_workspace(context, workspace_id=workspace_id)
    return WorkspaceMenuConfigurationResponse(
        workspace_id=configuration.workspace_id,
        menu_version=configuration.menu_version,
        items=[_menu_override(item) for item in configuration.overrides],
    )


@menu_router.put(
    "/menus",
    response_model=WorkspaceMenuConfigurationResponse,
    operation_id="replaceWorkspaceMenuConfiguration",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def replace_workspace_menu_configuration(
    workspace_id: UUID,
    body: ReplaceWorkspaceMenuConfigurationRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[MenuConfigurationService, Depends(menu_configuration_service)],
) -> WorkspaceMenuConfigurationResponse:
    configuration = service.replace_workspace(
        context,
        workspace_id=workspace_id,
        entries=tuple(
            (
                item.menu_id,
                item.parent_menu_id,
                item.name,
                item.icon_key,
                item.sort_order,
                item.visible,
            )
            for item in body.items
        ),
    )
    return WorkspaceMenuConfigurationResponse(
        workspace_id=configuration.workspace_id,
        menu_version=configuration.menu_version,
        items=[_menu_override(item) for item in configuration.overrides],
    )


@menu_router.get(
    "/roles/{role_id}/menus",
    response_model=RoleMenuVisibilityResponse,
    operation_id="getRoleMenuVisibility",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def get_role_menu_visibility(
    workspace_id: UUID,
    role_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[MenuConfigurationService, Depends(menu_configuration_service)],
) -> RoleMenuVisibilityResponse:
    entries = service.get_role(context, workspace_id=workspace_id, role_id=role_id)
    return RoleMenuVisibilityResponse(
        role_id=role_id,
        items=[
            RoleMenuVisibilityEntry(menu_id=item.menu_id, visible=item.visible) for item in entries
        ],
    )


@menu_router.put(
    "/roles/{role_id}/menus",
    response_model=RoleMenuVisibilityResponse,
    operation_id="replaceRoleMenuVisibility",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def replace_role_menu_visibility(
    workspace_id: UUID,
    role_id: UUID,
    body: ReplaceRoleMenuVisibilityRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[MenuConfigurationService, Depends(menu_configuration_service)],
) -> RoleMenuVisibilityResponse:
    entries = service.replace_role(
        context,
        workspace_id=workspace_id,
        role_id=role_id,
        entries=tuple((item.menu_id, item.visible) for item in body.items),
    )
    return RoleMenuVisibilityResponse(
        role_id=role_id,
        items=[
            RoleMenuVisibilityEntry(menu_id=item.menu_id, visible=item.visible) for item in entries
        ],
    )


def _menu_override(item: WorkspaceMenuOverride) -> WorkspaceMenuOverrideEntry:
    return WorkspaceMenuOverrideEntry(
        menu_id=item.menu_id,
        parent_menu_id=item.parent_menu_id,
        name=item.name,
        icon_key=item.icon_key,
        sort_order=item.sort_order,
        visible=item.visible,
        version=item.version,
    )
