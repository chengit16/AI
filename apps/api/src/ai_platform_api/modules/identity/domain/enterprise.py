"""定义企业空间、成员邀请和成员生命周期领域模型及端口。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter

from ai_platform_api.modules.authorization.domain.fields import SecurityLevel

MembershipType = Literal["owner", "member"]
InvitationStatus = Literal["pending", "accepted", "cancelled", "expired"]


@dataclass(frozen=True)
class EnterpriseWorkspace:
    """记录企业空间的展示名称及创建审计信息。"""

    workspace_id: UUID
    name: str
    created_by_account_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class WorkspaceMembership:
    """表示账号在工作空间内唯一且可转换状态的成员身份。"""

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
    """保存企业空间邀请的有效期、接受对象和处理状态。"""

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
    """提供企业用例校验空间类型和状态所需的最小记录。"""

    workspace_id: UUID
    workspace_type: Literal["personal", "enterprise"]
    name: str
    status: Literal["active", "suspended", "archived"]


@dataclass(frozen=True)
class WorkspaceSummary(WorkspaceRecord):
    """描述账号可访问空间及其当前成员身份和状态。"""

    membership_type: MembershipType
    membership_status: Literal["active", "disabled", "left"]


@dataclass(frozen=True)
class WorkspaceMemberSummary:
    """汇总企业成员账号、名称、身份类型和启用状态。"""

    account_id: UUID
    display_name: str
    membership_type: MembershipType
    status: Literal["active", "disabled", "left"]


@dataclass(frozen=True)
class EnterpriseConsoleTrendPoint:
    """固定六个月窗口内企业文档增长的低敏统计点。"""

    period: str
    document_count: int


@dataclass(frozen=True)
class EnterpriseConsoleRecentDocument:
    """企业控制台最近内容摘要，不包含正文、对象键和解析产物。"""

    document_id: UUID
    knowledge_base_id: UUID
    knowledge_base_name: str
    title: str
    updated_at: datetime
    published_at: datetime | None
    status: Literal["published", "unpublished"]


@dataclass(frozen=True)
class EnterpriseConsoleStatistics:
    """企业空间统计快照；所有数量必须来自同一数据库事务。"""

    active_member_count: int
    active_knowledge_base_count: int
    active_document_count: int
    published_document_count: int
    processing_document_count: int
    failed_document_count: int
    storage_used_bytes: int
    storage_limit_bytes: int


@dataclass(frozen=True)
class EnterpriseConsoleSnapshot:
    """企业控制台只读聚合，明确统计时间窗和最终一致性语义。"""

    workspace: WorkspaceRecord
    statistics: EnterpriseConsoleStatistics
    trend: tuple[EnterpriseConsoleTrendPoint, ...]
    recent_documents: tuple[EnterpriseConsoleRecentDocument, ...]
    generated_at: datetime
    time_window_start: datetime
    time_window_end: datetime
    consistency: Literal["eventually_consistent"]
    profile_description: str | None
    profile_logo_url: str | None


class InvalidMembershipTransitionError(Exception):
    """成员状态不允许当前转换，尤其禁止企业所有者离开或被停用。"""


class InvalidInvitationTransitionError(Exception):
    """邀请不属于当前账号、已处理或已过期时拒绝接受。"""


class EnterpriseWriteConflictError(Exception):
    """数据库唯一约束关闭并发邀请、加入或空间创建竞态。"""


class EnterpriseRepository(Protocol):
    """在空间隔离范围内维护企业、成员和邀请事实。"""

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

    def member_capacity_available(self, workspace_id: UUID) -> bool: ...

    def get_console_snapshot(
        self,
        workspace_id: UUID,
        *,
        generated_at: datetime,
        trend_months: int,
        recent_limit: int,
        maximum_security_level: SecurityLevel,
        include_recent_documents: bool,
    ) -> EnterpriseConsoleSnapshot | None: ...


class EnterpriseUnitOfWork(Protocol):
    """保证企业成员变更、审计、Outbox 和配额记录原子提交。"""

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
