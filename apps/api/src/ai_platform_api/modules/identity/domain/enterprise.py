from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter

MembershipType = Literal["owner", "member"]
InvitationStatus = Literal["pending", "accepted", "cancelled", "expired"]


@dataclass(frozen=True)
class EnterpriseWorkspace:
    workspace_id: UUID
    name: str
    created_by_account_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class WorkspaceMembership:
    membership_id: UUID
    workspace_id: UUID
    account_id: UUID
    membership_type: MembershipType
    status: Literal["active", "disabled", "left"]
    created_at: datetime
    updated_at: datetime
    version: int

    def leave(self, *, occurred_at: datetime) -> WorkspaceMembership:
        if self.membership_type == "owner" or self.status != "active":
            raise InvalidMembershipTransitionError
        return replace(self, status="left", updated_at=occurred_at, version=self.version + 1)

    def disable(self, *, occurred_at: datetime) -> WorkspaceMembership:
        if self.membership_type == "owner" or self.status != "active":
            raise InvalidMembershipTransitionError
        return replace(self, status="disabled", updated_at=occurred_at, version=self.version + 1)

    def activate_as_member(self, *, occurred_at: datetime) -> WorkspaceMembership:
        if self.membership_type == "owner" or self.status == "active":
            raise InvalidMembershipTransitionError
        return replace(
            self,
            membership_type="member",
            status="active",
            updated_at=occurred_at,
            version=self.version + 1,
        )


@dataclass(frozen=True)
class WorkspaceInvitation:
    invitation_id: UUID
    workspace_id: UUID
    invited_account_id: UUID
    invited_by_account_id: UUID
    status: InvitationStatus
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None = None

    def accept(self, *, account_id: UUID, occurred_at: datetime) -> WorkspaceInvitation:
        if (
            self.invited_account_id != account_id
            or self.status != "pending"
            or self.expires_at <= occurred_at
        ):
            raise InvalidInvitationTransitionError
        return replace(self, status="accepted", accepted_at=occurred_at)

    def expire(self, *, occurred_at: datetime) -> WorkspaceInvitation:
        if self.status != "pending" or self.expires_at > occurred_at:
            raise InvalidInvitationTransitionError
        return replace(self, status="expired")


@dataclass(frozen=True)
class WorkspaceRecord:
    workspace_id: UUID
    workspace_type: Literal["personal", "enterprise"]
    name: str
    status: Literal["active", "suspended", "archived"]


@dataclass(frozen=True)
class WorkspaceSummary(WorkspaceRecord):
    membership_type: MembershipType
    membership_status: Literal["active", "disabled", "left"]


@dataclass(frozen=True)
class WorkspaceMemberSummary:
    account_id: UUID
    display_name: str
    membership_type: MembershipType
    status: Literal["active", "disabled", "left"]


class InvalidMembershipTransitionError(Exception):
    """成员状态不允许当前转换，尤其禁止企业所有者离开或被停用。"""


class InvalidInvitationTransitionError(Exception):
    """邀请不属于当前账号、已处理或已过期时拒绝接受。"""


class EnterpriseWriteConflictError(Exception):
    """数据库唯一约束关闭并发邀请、加入或空间创建竞态。"""


class EnterpriseRepository(Protocol):
    def add_workspace(
        self,
        workspace: EnterpriseWorkspace,
        owner: WorkspaceMembership,
    ) -> None: ...

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

    def find_active_account_id(self, login_name: str) -> UUID | None: ...

    def add_invitation(self, invitation: WorkspaceInvitation) -> None: ...

    def get_invitation(
        self,
        invitation_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceInvitation | None: ...

    def get_pending_invitation(
        self,
        workspace_id: UUID,
        invited_account_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceInvitation | None: ...

    def save_invitation(self, invitation: WorkspaceInvitation) -> None: ...

    def save_membership(self, membership: WorkspaceMembership) -> None: ...

    def add_membership(self, membership: WorkspaceMembership) -> None: ...

    def list_workspaces(self, account_id: UUID) -> tuple[WorkspaceSummary, ...]: ...

    def list_members(self, workspace_id: UUID) -> tuple[WorkspaceMemberSummary, ...]: ...


class EnterpriseUnitOfWork(Protocol):
    @property
    def enterprise(self) -> EnterpriseRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> EnterpriseUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
