"""映射企业空间创建、成员邀请、加入、停用和离开 HTTP 协议。"""

from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.application.fields import FieldProjectionService
from ai_platform_api.modules.identity.api.dependencies import (
    enterprise_workspace_service,
    field_projection_service,
    trusted_request_context,
)
from ai_platform_api.modules.identity.api.enterprise_schemas import (
    CreateEnterpriseWorkspaceRequest,
    EnterpriseConsoleRecentDocumentResponse,
    EnterpriseConsoleResponse,
    EnterpriseConsoleStatisticsResponse,
    EnterpriseConsoleTrendPointResponse,
    EnterpriseConsoleWorkspaceResponse,
    InviteWorkspaceMemberRequest,
    WorkspaceInvitationResponse,
    WorkspaceListResponse,
    WorkspaceMemberListResponse,
    WorkspaceMemberProjectionResponse,
    WorkspaceMemberResponse,
    WorkspaceMembershipResponse,
    WorkspaceSummaryResponse,
)
from ai_platform_api.modules.identity.application.enterprise import (
    EnterpriseConsoleSnapshot,
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


def _console_response(snapshot: EnterpriseConsoleSnapshot) -> EnterpriseConsoleResponse:
    """将企业控制台领域快照映射为不含敏感字段的 HTTP 响应。"""

    return EnterpriseConsoleResponse(
        workspace=EnterpriseConsoleWorkspaceResponse(
            workspace_id=snapshot.workspace.workspace_id,
            workspace_type="enterprise",
            name=snapshot.workspace.name,
            status=snapshot.workspace.status,
            description=snapshot.profile_description,
            logo_url=snapshot.profile_logo_url,
        ),
        statistics=EnterpriseConsoleStatisticsResponse(
            active_member_count=snapshot.statistics.active_member_count,
            active_knowledge_base_count=snapshot.statistics.active_knowledge_base_count,
            active_document_count=snapshot.statistics.active_document_count,
            published_document_count=snapshot.statistics.published_document_count,
            processing_document_count=snapshot.statistics.processing_document_count,
            failed_document_count=snapshot.statistics.failed_document_count,
            storage_used_bytes=snapshot.statistics.storage_used_bytes,
            storage_limit_bytes=snapshot.statistics.storage_limit_bytes,
        ),
        trend=[
            EnterpriseConsoleTrendPointResponse(
                period=point.period, document_count=point.document_count
            )
            for point in snapshot.trend
        ],
        recent_documents=[
            EnterpriseConsoleRecentDocumentResponse(
                document_id=document.document_id,
                knowledge_base_id=document.knowledge_base_id,
                knowledge_base_name=document.knowledge_base_name,
                title=document.title,
                updated_at=document.updated_at,
                published_at=document.published_at,
                status=document.status,
            )
            for document in snapshot.recent_documents
        ],
        generated_at=snapshot.generated_at,
        time_window_start=snapshot.time_window_start,
        time_window_end=snapshot.time_window_end,
        consistency=snapshot.consistency,
    )


def _member_response(
    member: WorkspaceMemberSummary,
    projection: FieldProjectionService,
    field_mask: frozenset[str],
) -> WorkspaceMemberResponse | WorkspaceMemberProjectionResponse:
    visible = projection.response(
        "workspace_member",
        {
            "account_id": member.account_id,
            "display_name": member.display_name,
            "membership_type": member.membership_type,
            "status": member.status,
        },
        field_mask,
    )
    if set(visible) == {"account_id", "display_name", "membership_type", "status"}:
        return WorkspaceMemberResponse(
            account_id=cast(UUID, visible["account_id"]),
            display_name=cast(str, visible["display_name"]),
            membership_type=cast(Literal["owner", "member"], visible["membership_type"]),
            status=cast(Literal["active", "disabled", "left"], visible["status"]),
        )
    return WorkspaceMemberProjectionResponse(
        account_id=cast(UUID | None, visible.get("account_id")),
        display_name=cast(str | None, visible.get("display_name")),
        membership_type=cast(Literal["owner", "member"] | None, visible.get("membership_type")),
        status=cast(Literal["active", "disabled", "left"] | None, visible.get("status")),
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
    """创建企业工作空间；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

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
    """列出工作空间集合；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

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
    """处理邀请工作空间成员；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

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
    """接受工作空间邀请；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

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
    """切换工作空间；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

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
    """退出工作空间；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

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
    """停用工作空间成员；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

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
    response_model_exclude_none=True,
    operation_id="listEnterpriseWorkspaceMembers",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_workspace_members(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseWorkspaceService, Depends(enterprise_workspace_service)],
    projection: Annotated[FieldProjectionService, Depends(field_projection_service)],
) -> WorkspaceMemberListResponse:
    """列出工作空间成员集合；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    return WorkspaceMemberListResponse(
        items=[
            _member_response(item, projection, context.authorized_field_mask)
            for item in service.list_members(context, workspace_id=workspace_id)
        ]
    )


@router.get(
    "/{workspace_id}/enterprise-console",
    response_model=EnterpriseConsoleResponse,
    operation_id="getEnterpriseConsole",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def get_enterprise_console(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseWorkspaceService, Depends(enterprise_workspace_service)],
    trend_months: Annotated[int, Query(ge=1, le=12)] = 6,
    recent_limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> EnterpriseConsoleResponse:
    """读取企业控制台聚合；认证、PDP 和空间隔离由统一依赖及应用服务执行。"""

    return _console_response(
        service.get_console_snapshot(
            context,
            workspace_id=workspace_id,
            trend_months=trend_months,
            recent_limit=recent_limit,
        )
    )
