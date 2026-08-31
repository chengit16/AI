"""定义企业团队管理聚合、成员治理状态和持久化端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter

from ai_platform_api.modules.identity.domain.enterprise import (
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceRecord,
)
from ai_platform_api.modules.identity.domain.organization import (
    DepartmentSummary,
    PositionSummary,
)
from ai_platform_api.modules.identity.domain.roles import Role

TeamInvitationStatus = Literal["pending", "accepted", "cancelled", "expired"]
TeamAuditOutcome = Literal["succeeded", "denied", "failed"]


@dataclass(frozen=True)
class TeamRoleSummary:
    """描述团队页可分配的活动自定义角色。"""

    role_id: UUID
    role_key: str
    name: str


@dataclass(frozen=True)
class TeamEffectiveRoleSummary:
    """解释成员有效角色及其空间、部门或直接成员来源。"""

    role_id: UUID
    role_key: str
    name: str
    source_types: tuple[Literal["workspace", "department", "member"], ...]


@dataclass(frozen=True)
class TeamMemberSummary:
    """汇总成员身份、组织归属、直接角色与最近活动事实。"""

    account_id: UUID
    display_name: str | None
    login_name: str | None
    membership_type: Literal["owner", "member"]
    status: Literal["active", "disabled", "left"]
    department_ids: tuple[UUID, ...]
    primary_department_id: UUID | None
    position_ids: tuple[UUID, ...]
    direct_role_ids: tuple[UUID, ...]
    effective_roles: tuple[TeamEffectiveRoleSummary, ...]
    joined_at: datetime
    updated_at: datetime
    last_active_at: datetime | None
    version: int


@dataclass(frozen=True)
class TeamInvitationSummary:
    """汇总邀请目标、邀请人和按当前时间计算的有效状态。"""

    invitation_id: UUID
    invited_account_id: UUID
    invited_display_name: str | None
    invited_login_name: str | None
    invited_by_display_name: str | None
    status: TeamInvitationStatus
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None


@dataclass(frozen=True)
class TeamAuditSummary:
    """提供团队治理时间线所需的低敏审计摘要。"""

    audit_id: UUID
    actor_display_name: str | None
    action: str
    resource_type: str
    resource_id: UUID
    outcome: TeamAuditOutcome
    occurred_at: datetime


@dataclass(frozen=True)
class TeamManagementStatistics:
    """记录团队页顶部使用的成员与邀请治理计数。"""

    active_members: int
    disabled_members: int
    pending_invitations: int
    departments: int
    positions: int


@dataclass(frozen=True)
class TeamManagementSnapshot:
    """团队管理统一读模型，避免页面拼接多个可漂移请求。"""

    workspace: WorkspaceRecord
    statistics: TeamManagementStatistics
    members: tuple[TeamMemberSummary, ...]
    invitations: tuple[TeamInvitationSummary, ...]
    departments: tuple[DepartmentSummary, ...]
    positions: tuple[PositionSummary, ...]
    roles: tuple[TeamRoleSummary, ...]
    recent_audits: tuple[TeamAuditSummary, ...]
    generated_at: datetime


class TeamManagementWriteConflictError(Exception):
    """乐观版本、状态或数据库约束变化时拒绝覆盖团队事实。"""


class TeamManagementRepository(Protocol):
    """在单一空间和事务内读取、校验并修改团队治理事实。"""

    def get_workspace(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> WorkspaceRecord | None: ...

    def get_membership(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceMembership | None: ...

    def get_invitation(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceInvitation | None: ...

    def save_invitation(self, invitation: WorkspaceInvitation) -> None: ...

    def get_snapshot(
        self,
        workspace_id: UUID,
        *,
        generated_at: datetime,
        audit_limit: int,
        mask_display_name: bool,
        mask_login_name: bool,
    ) -> TeamManagementSnapshot | None: ...

    def list_departments(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> tuple[DepartmentSummary, ...]: ...

    def list_positions(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> tuple[PositionSummary, ...]: ...

    def list_roles(self, workspace_id: UUID, *, for_update: bool = False) -> tuple[Role, ...]: ...

    def replace_member_configuration(
        self,
        *,
        workspace_id: UUID,
        membership: WorkspaceMembership,
        expected_version: int,
        department_ids: tuple[UUID, ...],
        primary_department_id: UUID | None,
        position_ids: tuple[UUID, ...],
        direct_role_ids: tuple[UUID, ...],
        occurred_at: datetime,
    ) -> WorkspaceMembership: ...

    def save_membership(
        self,
        membership: WorkspaceMembership,
        *,
        clear_assignments: bool,
    ) -> None: ...


class TeamManagementUnitOfWork(Protocol):
    """保证成员、组织、角色、审计和 Outbox 事实原子提交。"""

    @property
    def team(self) -> TeamManagementRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> TeamManagementUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
