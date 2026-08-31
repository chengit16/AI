"""定义团队管理用例返回给协议层的稳定只读结果。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from ai_platform_api.modules.identity.domain.enterprise import (
    WorkspaceInvitation,
    WorkspaceMembership,
)
from ai_platform_api.modules.identity.domain.team_management import TeamManagementSnapshot


@dataclass(frozen=True)
class TeamWorkspaceView:
    """向协议层暴露企业空间的低敏摘要。"""

    workspace_id: UUID
    name: str
    status: Literal["active", "suspended", "archived"]


@dataclass(frozen=True)
class TeamManagementStatisticsView:
    """向协议层暴露团队治理统计。"""

    active_members: int
    disabled_members: int
    pending_invitations: int
    departments: int
    positions: int


@dataclass(frozen=True)
class TeamEffectiveRoleView:
    """解释成员有效角色及其授权来源。"""

    role_id: UUID
    role_key: str
    name: str
    source_types: tuple[Literal["workspace", "department", "member"], ...]


@dataclass(frozen=True)
class TeamMemberView:
    """向协议层暴露成员生命周期、组织与角色结果。"""

    account_id: UUID
    display_name: str | None
    login_name: str | None
    membership_type: Literal["owner", "member"]
    status: Literal["active", "disabled", "left"]
    department_ids: tuple[UUID, ...]
    primary_department_id: UUID | None
    position_ids: tuple[UUID, ...]
    direct_role_ids: tuple[UUID, ...]
    effective_roles: tuple[TeamEffectiveRoleView, ...]
    joined_at: datetime
    updated_at: datetime
    last_active_at: datetime | None
    version: int


@dataclass(frozen=True)
class TeamInvitationView:
    """向协议层暴露邀请目标与当前有效状态。"""

    invitation_id: UUID
    invited_account_id: UUID
    invited_display_name: str | None
    invited_login_name: str | None
    invited_by_display_name: str | None
    status: Literal["pending", "accepted", "cancelled", "expired"]
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None


@dataclass(frozen=True)
class TeamDepartmentView:
    """向协议层暴露部门层级与有效状态。"""

    department_id: UUID
    parent_department_id: UUID | None
    name: str
    status: Literal["active", "disabled"]
    effective_active: bool
    depth: int
    version: int


@dataclass(frozen=True)
class TeamPositionView:
    """向协议层暴露岗位归属与有效状态。"""

    position_id: UUID
    department_id: UUID
    name: str
    status: Literal["active", "disabled"]
    effective_active: bool
    version: int


@dataclass(frozen=True)
class TeamRoleView:
    """向协议层暴露允许直接分配的自定义角色。"""

    role_id: UUID
    role_key: str
    name: str


@dataclass(frozen=True)
class TeamAuditView:
    """向协议层暴露低敏团队治理审计。"""

    audit_id: UUID
    actor_display_name: str | None
    action: str
    resource_type: str
    resource_id: UUID
    outcome: Literal["succeeded", "denied", "failed"]
    occurred_at: datetime


@dataclass(frozen=True)
class TeamManagementView:
    """聚合协议层渲染团队管理页所需的全部稳定结果。"""

    workspace: TeamWorkspaceView
    statistics: TeamManagementStatisticsView
    members: tuple[TeamMemberView, ...]
    invitations: tuple[TeamInvitationView, ...]
    departments: tuple[TeamDepartmentView, ...]
    positions: tuple[TeamPositionView, ...]
    roles: tuple[TeamRoleView, ...]
    recent_audits: tuple[TeamAuditView, ...]
    generated_at: datetime


@dataclass(frozen=True)
class MembershipLifecycleView:
    """返回成员写操作后的最小生命周期结果。"""

    account_id: UUID
    membership_type: Literal["owner", "member"]
    status: Literal["active", "disabled", "left"]
    version: int


@dataclass(frozen=True)
class InvitationLifecycleView:
    """返回邀请写操作后的最小生命周期结果。"""

    invitation_id: UUID
    workspace_id: UUID
    status: Literal["pending", "accepted", "cancelled", "expired"]
    expires_at: datetime


def team_management_view(snapshot: TeamManagementSnapshot) -> TeamManagementView:
    """在 Application 边缘冻结协议层可消费的团队结果。"""

    return TeamManagementView(
        workspace=TeamWorkspaceView(
            snapshot.workspace.workspace_id, snapshot.workspace.name, snapshot.workspace.status
        ),
        statistics=TeamManagementStatisticsView(**vars(snapshot.statistics)),
        members=tuple(
            TeamMemberView(
                account_id=item.account_id,
                display_name=item.display_name,
                login_name=item.login_name,
                membership_type=item.membership_type,
                status=item.status,
                department_ids=item.department_ids,
                primary_department_id=item.primary_department_id,
                position_ids=item.position_ids,
                direct_role_ids=item.direct_role_ids,
                effective_roles=tuple(
                    TeamEffectiveRoleView(**vars(role)) for role in item.effective_roles
                ),
                joined_at=item.joined_at,
                updated_at=item.updated_at,
                last_active_at=item.last_active_at,
                version=item.version,
            )
            for item in snapshot.members
        ),
        invitations=tuple(TeamInvitationView(**vars(item)) for item in snapshot.invitations),
        departments=tuple(TeamDepartmentView(**vars(item)) for item in snapshot.departments),
        positions=tuple(TeamPositionView(**vars(item)) for item in snapshot.positions),
        roles=tuple(TeamRoleView(**vars(item)) for item in snapshot.roles),
        recent_audits=tuple(TeamAuditView(**vars(item)) for item in snapshot.recent_audits),
        generated_at=snapshot.generated_at,
    )


def membership_lifecycle_view(membership: WorkspaceMembership) -> MembershipLifecycleView:
    """裁剪成员写入结果，避免协议层依赖领域实体。"""

    return MembershipLifecycleView(
        membership.account_id, membership.membership_type, membership.status, membership.version
    )


def invitation_lifecycle_view(invitation: WorkspaceInvitation) -> InvitationLifecycleView:
    """裁剪邀请写入结果，避免协议层依赖领域实体。"""

    return InvitationLifecycleView(
        invitation.invitation_id, invitation.workspace_id, invitation.status, invitation.expires_at
    )
