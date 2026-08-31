"""映射团队聚合、邀请撤销和成员原子治理 HTTP 协议。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.api.dependencies import (
    team_management_service,
    trusted_request_context,
)
from ai_platform_api.modules.identity.api.enterprise_schemas import (
    WorkspaceInvitationResponse,
    WorkspaceMembershipResponse,
)
from ai_platform_api.modules.identity.api.team_management_schemas import (
    TeamAuditResponse,
    TeamDepartmentResponse,
    TeamEffectiveRoleResponse,
    TeamInvitationResponse,
    TeamManagementResponse,
    TeamManagementStatisticsResponse,
    TeamMemberResponse,
    TeamPositionResponse,
    TeamRoleResponse,
    TeamWorkspaceResponse,
    UpdateTeamMemberRequest,
)
from ai_platform_api.modules.identity.application.team_management import (
    TeamManagementService,
)
from ai_platform_api.modules.identity.application.team_management_views import (
    InvitationLifecycleView,
    MembershipLifecycleView,
    TeamManagementView,
)

router = APIRouter(prefix="/workspaces", tags=["团队管理"])


@router.get(
    "/{workspace_id}/team-management",
    response_model=TeamManagementResponse,
    operation_id="getEnterpriseTeamManagement",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def get_team_management(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[TeamManagementService, Depends(team_management_service)],
    audit_limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TeamManagementResponse:
    """读取企业团队聚合；权限、字段遮罩和空间隔离由后端统一执行。"""

    return _snapshot_response(
        service.get_snapshot(context, workspace_id=workspace_id, audit_limit=audit_limit)
    )


@router.post(
    "/{workspace_id}/invitations/{invitation_id}/cancel",
    response_model=WorkspaceInvitationResponse,
    operation_id="cancelEnterpriseWorkspaceInvitation",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def cancel_invitation(
    workspace_id: UUID,
    invitation_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[TeamManagementService, Depends(team_management_service)],
) -> WorkspaceInvitationResponse:
    """撤销仍有效的待处理邀请，已接受、过期或跨空间目标失败关闭。"""

    return _invitation_lifecycle_response(
        service.cancel_invitation(context, workspace_id=workspace_id, invitation_id=invitation_id)
    )


@router.put(
    "/{workspace_id}/team-management/members/{account_id}",
    response_model=WorkspaceMembershipResponse,
    operation_id="updateEnterpriseTeamMember",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def update_team_member(
    workspace_id: UUID,
    account_id: UUID,
    body: UpdateTeamMemberRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[TeamManagementService, Depends(team_management_service)],
) -> WorkspaceMembershipResponse:
    """原子替换成员组织和直接角色，成员版本冲突不会覆盖新事实。"""

    membership = service.update_member(
        context,
        workspace_id=workspace_id,
        target_account_id=account_id,
        expected_version=body.expected_version,
        department_ids=tuple(body.department_ids),
        primary_department_id=body.primary_department_id,
        position_ids=tuple(body.position_ids),
        direct_role_ids=tuple(body.direct_role_ids),
    )
    return _membership_response(membership)


@router.post(
    "/{workspace_id}/team-management/members/{account_id}/activate",
    response_model=WorkspaceMembershipResponse,
    operation_id="activateEnterpriseTeamMember",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def activate_team_member(
    workspace_id: UUID,
    account_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[TeamManagementService, Depends(team_management_service)],
) -> WorkspaceMembershipResponse:
    """恢复被停用成员，保留的组织和直接角色配置重新生效。"""

    return _membership_response(
        service.activate_member(context, workspace_id=workspace_id, target_account_id=account_id)
    )


@router.post(
    "/{workspace_id}/team-management/members/{account_id}/remove",
    response_model=WorkspaceMembershipResponse,
    operation_id="removeEnterpriseTeamMember",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def remove_team_member(
    workspace_id: UUID,
    account_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[TeamManagementService, Depends(team_management_service)],
) -> WorkspaceMembershipResponse:
    """移除普通成员并清理组织与自定义直接角色，所有者不可移除。"""

    return _membership_response(
        service.remove_member(context, workspace_id=workspace_id, target_account_id=account_id)
    )


def _snapshot_response(snapshot: TeamManagementView) -> TeamManagementResponse:
    """把团队领域快照映射为显式 HTTP 类型，不泄露内部表结构。"""

    return TeamManagementResponse(
        workspace=TeamWorkspaceResponse(
            workspace_id=snapshot.workspace.workspace_id,
            workspace_type="enterprise",
            name=snapshot.workspace.name,
            status=snapshot.workspace.status,
        ),
        statistics=TeamManagementStatisticsResponse(**vars(snapshot.statistics)),
        members=[
            TeamMemberResponse(
                account_id=item.account_id,
                display_name=item.display_name,
                login_name=item.login_name,
                membership_type=item.membership_type,
                status=item.status,
                department_ids=list(item.department_ids),
                primary_department_id=item.primary_department_id,
                position_ids=list(item.position_ids),
                direct_role_ids=list(item.direct_role_ids),
                effective_roles=[
                    TeamEffectiveRoleResponse(
                        role_id=role.role_id,
                        role_key=role.role_key,
                        name=role.name,
                        source_types=list(role.source_types),
                    )
                    for role in item.effective_roles
                ],
                joined_at=item.joined_at,
                updated_at=item.updated_at,
                last_active_at=item.last_active_at,
                version=item.version,
            )
            for item in snapshot.members
        ],
        invitations=[TeamInvitationResponse(**vars(item)) for item in snapshot.invitations],
        departments=[TeamDepartmentResponse(**vars(item)) for item in snapshot.departments],
        positions=[TeamPositionResponse(**vars(item)) for item in snapshot.positions],
        roles=[TeamRoleResponse(**vars(item)) for item in snapshot.roles],
        recent_audits=[TeamAuditResponse(**vars(item)) for item in snapshot.recent_audits],
        generated_at=snapshot.generated_at,
    )


def _membership_response(membership: MembershipLifecycleView) -> WorkspaceMembershipResponse:
    return WorkspaceMembershipResponse(
        account_id=membership.account_id,
        membership_type=membership.membership_type,
        status=membership.status,
    )


def _invitation_lifecycle_response(
    invitation: InvitationLifecycleView,
) -> WorkspaceInvitationResponse:
    return WorkspaceInvitationResponse(
        invitation_id=invitation.invitation_id,
        workspace_id=invitation.workspace_id,
        status=invitation.status,
        expires_at=invitation.expires_at,
    )
