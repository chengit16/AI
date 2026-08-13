from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.api.schemas import (
    CurrentMenuReleaseResponse,
    MenuReleaseDecisionRequest,
    MenuReleaseListResponse,
    MenuReleaseResponse,
    MenuReleaseSnapshotApiBindingEntry,
    MenuReleaseSnapshotMenuEntry,
    MenuReleaseSnapshotResponse,
    MenuReleaseSnapshotRoleMenuEntry,
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
from ai_platform_api.modules.authorization.application.menu_releases import (
    MenuRelease,
    MenuReleaseService,
)
from ai_platform_api.modules.authorization.application.menus import (
    MenuConfigurationService,
    WorkspaceMenuOverride,
)
from ai_platform_api.modules.identity.api.dependencies import (
    menu_configuration_service,
    menu_release_service,
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


@menu_router.post(
    "/menu-releases",
    response_model=MenuReleaseResponse,
    operation_id="createWorkspaceMenuRelease",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def create_workspace_menu_release(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[MenuReleaseService, Depends(menu_release_service)],
) -> MenuReleaseResponse:
    return _menu_release(service.create_draft(context, workspace_id=workspace_id))


@menu_router.post(
    "/menu-releases/{release_id}/validate",
    response_model=MenuReleaseResponse,
    operation_id="validateWorkspaceMenuRelease",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def validate_workspace_menu_release(
    workspace_id: UUID,
    release_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[MenuReleaseService, Depends(menu_release_service)],
) -> MenuReleaseResponse:
    return _menu_release(
        service.validate(context, workspace_id=workspace_id, release_id=release_id)
    )


@menu_router.post(
    "/menu-releases/{release_id}/decision",
    response_model=MenuReleaseResponse,
    operation_id="decideWorkspaceMenuRelease",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def decide_workspace_menu_release(
    workspace_id: UUID,
    release_id: UUID,
    body: MenuReleaseDecisionRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[MenuReleaseService, Depends(menu_release_service)],
) -> MenuReleaseResponse:
    return _menu_release(
        service.decide(
            context,
            workspace_id=workspace_id,
            release_id=release_id,
            approved=body.approved,
            reason=body.reason,
        )
    )


@menu_router.post(
    "/menu-releases/{release_id}/publish",
    response_model=MenuReleaseResponse,
    operation_id="publishWorkspaceMenuRelease",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def publish_workspace_menu_release(
    workspace_id: UUID,
    release_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[MenuReleaseService, Depends(menu_release_service)],
) -> MenuReleaseResponse:
    return _menu_release(service.publish(context, workspace_id=workspace_id, release_id=release_id))


@menu_router.post(
    "/menu-releases/{release_id}/rollback",
    response_model=MenuReleaseResponse,
    operation_id="rollbackWorkspaceMenuRelease",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def rollback_workspace_menu_release(
    workspace_id: UUID,
    release_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[MenuReleaseService, Depends(menu_release_service)],
) -> MenuReleaseResponse:
    return _menu_release(
        service.rollback(context, workspace_id=workspace_id, source_release_id=release_id)
    )


@menu_router.get(
    "/menu-releases",
    response_model=MenuReleaseListResponse,
    operation_id="listWorkspaceMenuReleases",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def list_workspace_menu_releases(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[MenuReleaseService, Depends(menu_release_service)],
) -> MenuReleaseListResponse:
    return MenuReleaseListResponse(
        items=[_menu_release(item) for item in service.list(context, workspace_id=workspace_id)]
    )


@menu_router.get(
    "/menu-releases/current",
    response_model=CurrentMenuReleaseResponse,
    operation_id="getCurrentWorkspaceMenuRelease",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def get_current_workspace_menu_release(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[MenuReleaseService, Depends(menu_release_service)],
) -> CurrentMenuReleaseResponse:
    release = service.get_current(context, workspace_id=workspace_id)
    return CurrentMenuReleaseResponse(
        item=_menu_release(release) if release is not None else None,
        snapshot=_menu_snapshot(release) if release is not None else None,
    )


def _menu_release(release: MenuRelease) -> MenuReleaseResponse:
    snapshot = release.snapshot
    return MenuReleaseResponse(
        release_id=release.release_id,
        workspace_id=release.workspace_id,
        release_number=release.release_number,
        release_kind=release.release_kind,
        source_release_id=release.source_release_id,
        status=release.status,
        snapshot_digest=release.snapshot_digest,
        snapshot_schema_version=snapshot.schema_version,
        registry_version=snapshot.registry_version,
        menu_version=snapshot.menu_version,
        menu_count=len(snapshot.menus),
        role_menu_count=len(snapshot.role_menus),
        menu_api_binding_count=len(snapshot.menu_api_bindings),
        validation_errors=list(release.validation_errors),
        rejection_reason=release.rejection_reason,
        created_by_account_id=release.created_by_account_id,
        decided_by_account_id=release.decided_by_account_id,
        created_at=release.created_at,
        validated_at=release.validated_at,
        decided_at=release.decided_at,
        published_at=release.published_at,
        version=release.version,
    )


def _menu_snapshot(release: MenuRelease) -> MenuReleaseSnapshotResponse:
    snapshot = release.snapshot
    return MenuReleaseSnapshotResponse(
        schema_version=snapshot.schema_version,
        registry_version=snapshot.registry_version,
        workspace_id=snapshot.workspace_id,
        menu_version=snapshot.menu_version,
        menus=[
            MenuReleaseSnapshotMenuEntry(
                menu_id=item.menu_id,
                menu_key=item.menu_key,
                parent_menu_id=item.parent_menu_id,
                name=item.name,
                menu_type=item.menu_type,
                page_resource_id=item.page_resource_id,
                permission_code=item.permission_code,
                icon_key=item.icon_key,
                sort_order=item.sort_order,
                source=item.source,
                status=item.status,
                visible=item.visible,
            )
            for item in snapshot.menus
        ],
        role_menus=[
            MenuReleaseSnapshotRoleMenuEntry(
                role_id=item.role_id,
                menu_id=item.menu_id,
                visible=item.visible,
            )
            for item in snapshot.role_menus
        ],
        menu_api_bindings=[
            MenuReleaseSnapshotApiBindingEntry(
                menu_id=menu_id,
                api_resource_id=api_resource_id,
                action_type=action_type,
            )
            for menu_id, api_resource_id, action_type in snapshot.menu_api_bindings
        ],
    )
