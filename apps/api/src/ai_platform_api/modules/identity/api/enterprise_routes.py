from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.api.dependencies import (
    enterprise_workspace_service,
    trusted_request_context,
)
from ai_platform_api.modules.identity.api.enterprise_schemas import (
    CreateEnterpriseWorkspaceRequest,
    InviteWorkspaceMemberRequest,
    WorkspaceInvitationResponse,
    WorkspaceListResponse,
    WorkspaceMemberListResponse,
    WorkspaceMemberResponse,
    WorkspaceMembershipResponse,
    WorkspaceSummaryResponse,
)
from ai_platform_api.modules.identity.application.enterprise import (
    EnterpriseWorkspaceService,
    WorkspaceMemberSummary,
    WorkspaceSummary,
)

router = APIRouter(prefix="/workspaces", tags=["工作空间"])


def _workspace_response(workspace: WorkspaceSummary) -> WorkspaceSummaryResponse:
    return WorkspaceSummaryResponse(
        workspace_id=workspace.workspace_id,
        workspace_type=workspace.workspace_type,
        name=workspace.name,
        status=workspace.status,
        membership_type=workspace.membership_type,
        membership_status=workspace.membership_status,
    )


def _member_response(member: WorkspaceMemberSummary) -> WorkspaceMemberResponse:
    return WorkspaceMemberResponse(
        account_id=member.account_id,
        display_name=member.display_name,
        membership_type=member.membership_type,
        status=member.status,
    )


@router.post(
    "/enterprise",
    response_model=WorkspaceSummaryResponse,
    operation_id="createEnterpriseWorkspace",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
def create_enterprise_workspace(
    body: CreateEnterpriseWorkspaceRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseWorkspaceService, Depends(enterprise_workspace_service)],
) -> WorkspaceSummaryResponse:
    return _workspace_response(service.create(context, name=body.name))


@router.get(
    "",
    response_model=WorkspaceListResponse,
    operation_id="listAccessibleWorkspaces",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_workspaces(
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseWorkspaceService, Depends(enterprise_workspace_service)],
) -> WorkspaceListResponse:
    return WorkspaceListResponse(
        items=[_workspace_response(item) for item in service.list_workspaces(context)]
    )


@router.post(
    "/{workspace_id}/invitations",
    response_model=WorkspaceInvitationResponse,
    operation_id="inviteEnterpriseWorkspaceMember",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def invite_workspace_member(
    workspace_id: UUID,
    body: InviteWorkspaceMemberRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseWorkspaceService, Depends(enterprise_workspace_service)],
) -> WorkspaceInvitationResponse:
    invitation = service.invite(
        context,
        workspace_id=workspace_id,
        login_name=body.login_name,
    )
    return WorkspaceInvitationResponse(
        invitation_id=invitation.invitation_id,
        workspace_id=invitation.workspace_id,
        status=invitation.status,
        expires_at=invitation.expires_at,
    )


@router.post(
    "/invitations/{invitation_id}/accept",
    response_model=WorkspaceSummaryResponse,
    operation_id="acceptEnterpriseWorkspaceInvitation",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def accept_workspace_invitation(
    invitation_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseWorkspaceService, Depends(enterprise_workspace_service)],
) -> WorkspaceSummaryResponse:
    return _workspace_response(service.accept_invitation(context, invitation_id=invitation_id))


@router.post(
    "/{workspace_id}/switch",
    response_model=WorkspaceSummaryResponse,
    operation_id="switchWorkspaceContext",
    responses=error_responses(400, 401, 403, 422, 500),
)
def switch_workspace(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseWorkspaceService, Depends(enterprise_workspace_service)],
) -> WorkspaceSummaryResponse:
    # 当前 Header 只证明来源空间；目标空间必须由服务端再次查询成员事实。
    return _workspace_response(service.switch(context, workspace_id=workspace_id))


@router.post(
    "/{workspace_id}/leave",
    response_model=WorkspaceMembershipResponse,
    operation_id="leaveEnterpriseWorkspace",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def leave_workspace(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseWorkspaceService, Depends(enterprise_workspace_service)],
) -> WorkspaceMembershipResponse:
    membership = service.leave(context, workspace_id=workspace_id)
    return WorkspaceMembershipResponse(
        account_id=membership.account_id,
        membership_type=membership.membership_type,
        status=membership.status,
    )


@router.post(
    "/{workspace_id}/members/{account_id}/disable",
    response_model=WorkspaceMembershipResponse,
    operation_id="disableEnterpriseWorkspaceMember",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def disable_workspace_member(
    workspace_id: UUID,
    account_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseWorkspaceService, Depends(enterprise_workspace_service)],
) -> WorkspaceMembershipResponse:
    membership = service.disable_member(
        context,
        workspace_id=workspace_id,
        target_account_id=account_id,
    )
    return WorkspaceMembershipResponse(
        account_id=membership.account_id,
        membership_type=membership.membership_type,
        status=membership.status,
    )


@router.get(
    "/{workspace_id}/members",
    response_model=WorkspaceMemberListResponse,
    operation_id="listEnterpriseWorkspaceMembers",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_workspace_members(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseWorkspaceService, Depends(enterprise_workspace_service)],
) -> WorkspaceMemberListResponse:
    return WorkspaceMemberListResponse(
        items=[
            _member_response(item)
            for item in service.list_members(context, workspace_id=workspace_id)
        ]
    )
