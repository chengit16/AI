from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import cast
from uuid import UUID

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.identity.domain.enterprise import (
    EnterpriseWorkspace,
    EnterpriseWriteConflictError,
    InvitationStatus,
    MembershipType,
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceMemberSummary,
    WorkspaceRecord,
    WorkspaceSummary,
)
from ai_platform_api.modules.identity.domain.models import (
    MembershipStatus,
    WorkspaceStatus,
    WorkspaceType,
)
from ai_platform_api.persistence.tables import (
    accounts,
    membership_departments,
    membership_positions,
    workspace_invitations,
    workspace_memberships,
    workspaces,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyEnterpriseRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_workspace(
        self,
        workspace: EnterpriseWorkspace,
        owner: WorkspaceMembership,
    ) -> None:
        audit_values = {
            "created_at": workspace.created_at,
            "created_by_actor_id": workspace.created_by_account_id,
            "updated_at": workspace.created_at,
            "updated_by_actor_id": workspace.created_by_account_id,
            "version": 1,
        }
        try:
            self._session.execute(
                insert(workspaces).values(
                    workspace_id=workspace.workspace_id,
                    workspace_type="enterprise",
                    name=workspace.name,
                    owner_account_id=None,
                    entitlement_version=1,
                    status="active",
                    **audit_values,
                )
            )
            self.add_membership(owner)
        except IntegrityError as error:
            raise EnterpriseWriteConflictError from error

    def get_workspace(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceRecord | None:
        statement = select(
            workspaces.c.workspace_id,
            workspaces.c.workspace_type,
            workspaces.c.name,
            workspaces.c.status,
        ).where(workspaces.c.workspace_id == workspace_id)
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return WorkspaceRecord(
            workspace_id=row.workspace_id,
            workspace_type=cast("WorkspaceType", row.workspace_type),
            name=row.name,
            status=cast("WorkspaceStatus", row.status),
        )

    def get_membership(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceMembership | None:
        statement = select(workspace_memberships).where(
            workspace_memberships.c.workspace_id == workspace_id,
            workspace_memberships.c.account_id == account_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return WorkspaceMembership(
            membership_id=row.membership_id,
            workspace_id=row.workspace_id,
            account_id=row.account_id,
            membership_type=cast("MembershipType", row.membership_type),
            status=cast("MembershipStatus", row.status),
            created_at=row.created_at,
            updated_at=row.updated_at,
            version=row.version,
        )

    def find_active_account_id(self, login_name: str) -> UUID | None:
        return self._session.scalar(
            select(accounts.c.account_id).where(
                accounts.c.login_name == login_name,
                accounts.c.status == "active",
            )
        )

    def add_invitation(self, invitation: WorkspaceInvitation) -> None:
        try:
            self._session.execute(
                insert(workspace_invitations).values(
                    invitation_id=invitation.invitation_id,
                    workspace_id=invitation.workspace_id,
                    invited_account_id=invitation.invited_account_id,
                    invited_by_account_id=invitation.invited_by_account_id,
                    status=invitation.status,
                    created_at=invitation.created_at,
                    expires_at=invitation.expires_at,
                    accepted_at=invitation.accepted_at,
                )
            )
        except IntegrityError as error:
            raise EnterpriseWriteConflictError from error

    def get_invitation(
        self,
        invitation_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceInvitation | None:
        statement = select(workspace_invitations).where(
            workspace_invitations.c.invitation_id == invitation_id
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return WorkspaceInvitation(
            invitation_id=row.invitation_id,
            workspace_id=row.workspace_id,
            invited_account_id=row.invited_account_id,
            invited_by_account_id=row.invited_by_account_id,
            status=cast("InvitationStatus", row.status),
            created_at=row.created_at,
            expires_at=row.expires_at,
            accepted_at=row.accepted_at,
        )

    def get_pending_invitation(
        self,
        workspace_id: UUID,
        invited_account_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceInvitation | None:
        statement = select(workspace_invitations).where(
            workspace_invitations.c.workspace_id == workspace_id,
            workspace_invitations.c.invited_account_id == invited_account_id,
            workspace_invitations.c.status == "pending",
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return WorkspaceInvitation(
            invitation_id=row.invitation_id,
            workspace_id=row.workspace_id,
            invited_account_id=row.invited_account_id,
            invited_by_account_id=row.invited_by_account_id,
            status=cast("InvitationStatus", row.status),
            created_at=row.created_at,
            expires_at=row.expires_at,
            accepted_at=row.accepted_at,
        )

    def save_invitation(self, invitation: WorkspaceInvitation) -> None:
        self._session.execute(
            update(workspace_invitations)
            .where(workspace_invitations.c.invitation_id == invitation.invitation_id)
            .values(status=invitation.status, accepted_at=invitation.accepted_at)
        )

    def save_membership(self, membership: WorkspaceMembership) -> None:
        if membership.status != "active":
            # 离开或停用必须同步撤销组织归属，重新加入不能隐式恢复旧权限范围。
            self._session.execute(
                delete(membership_positions).where(
                    membership_positions.c.workspace_id == membership.workspace_id,
                    membership_positions.c.membership_id == membership.membership_id,
                )
            )
            self._session.execute(
                delete(membership_departments).where(
                    membership_departments.c.workspace_id == membership.workspace_id,
                    membership_departments.c.membership_id == membership.membership_id,
                )
            )
        self._session.execute(
            update(workspace_memberships)
            .where(
                workspace_memberships.c.membership_id == membership.membership_id,
                workspace_memberships.c.workspace_id == membership.workspace_id,
            )
            .values(
                membership_type=membership.membership_type,
                status=membership.status,
                updated_at=membership.updated_at,
                version=membership.version,
            )
        )

    def add_membership(self, membership: WorkspaceMembership) -> None:
        try:
            self._session.execute(
                insert(workspace_memberships).values(
                    membership_id=membership.membership_id,
                    workspace_id=membership.workspace_id,
                    account_id=membership.account_id,
                    membership_type=membership.membership_type,
                    status=membership.status,
                    created_at=membership.created_at,
                    updated_at=membership.updated_at,
                    version=membership.version,
                )
            )
        except IntegrityError as error:
            raise EnterpriseWriteConflictError from error

    def list_workspaces(self, account_id: UUID) -> tuple[WorkspaceSummary, ...]:
        rows = self._session.execute(
            select(
                workspaces.c.workspace_id,
                workspaces.c.workspace_type,
                workspaces.c.name,
                workspaces.c.status,
                workspace_memberships.c.membership_type,
                workspace_memberships.c.status.label("membership_status"),
            )
            .join(
                workspace_memberships,
                workspace_memberships.c.workspace_id == workspaces.c.workspace_id,
            )
            .where(
                workspace_memberships.c.account_id == account_id,
                workspace_memberships.c.status == "active",
                workspaces.c.status == "active",
            )
            .order_by(
                workspaces.c.workspace_type, workspaces.c.created_at, workspaces.c.workspace_id
            )
        ).all()
        return tuple(
            WorkspaceSummary(
                workspace_id=row.workspace_id,
                workspace_type=cast("WorkspaceType", row.workspace_type),
                name=row.name,
                status=cast("WorkspaceStatus", row.status),
                membership_type=cast("MembershipType", row.membership_type),
                membership_status=cast("MembershipStatus", row.membership_status),
            )
            for row in rows
        )

    def list_members(self, workspace_id: UUID) -> tuple[WorkspaceMemberSummary, ...]:
        rows = self._session.execute(
            select(
                accounts.c.account_id,
                accounts.c.display_name,
                workspace_memberships.c.membership_type,
                workspace_memberships.c.status,
            )
            .join(
                workspace_memberships,
                workspace_memberships.c.account_id == accounts.c.account_id,
            )
            .where(workspace_memberships.c.workspace_id == workspace_id)
            .order_by(
                workspace_memberships.c.membership_type,
                accounts.c.display_name,
                accounts.c.account_id,
            )
        ).all()
        return tuple(
            WorkspaceMemberSummary(
                account_id=row.account_id,
                display_name=row.display_name,
                membership_type=cast("MembershipType", row.membership_type),
                status=cast("MembershipStatus", row.status),
            )
            for row in rows
        )


class SqlAlchemyEnterpriseUnitOfWork:
    """企业空间、成员、邀请、审计与 Outbox 共用一个数据库事务。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._enterprise: SqlAlchemyEnterpriseRepository | None = None
        self._audit: SqlAlchemyAuditWriter | None = None
        self._outbox: SqlAlchemyOutboxWriter | None = None

    def __enter__(self) -> SqlAlchemyEnterpriseUnitOfWork:
        self._session = self._session_factory()
        self._enterprise = SqlAlchemyEnterpriseRepository(self._session)
        self._audit = SqlAlchemyAuditWriter(self._session)
        self._outbox = SqlAlchemyOutboxWriter(self._session)
        return self

    @property
    def enterprise(self) -> SqlAlchemyEnterpriseRepository:
        if self._enterprise is None:
            raise RuntimeError("Enterprise Unit of Work 尚未进入事务范围")
        return self._enterprise

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        if self._audit is None:
            raise RuntimeError("Enterprise Unit of Work 尚未进入事务范围")
        return self._audit

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        if self._outbox is None:
            raise RuntimeError("Enterprise Unit of Work 尚未进入事务范围")
        return self._outbox

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is not None:
            if exc_type is not None:
                self._session.rollback()
            self._session.close()
            self._session = None
            self._enterprise = None
            self._audit = None
            self._outbox = None

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Enterprise Unit of Work 尚未进入事务范围")
        try:
            self._session.commit()
        except IntegrityError as error:
            self._session.rollback()
            raise EnterpriseWriteConflictError from error
