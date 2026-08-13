from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from types import TracebackType
from typing import cast
from uuid import UUID

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import delete, func, insert, select, update
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
from ai_platform_api.modules.identity.domain.entitlements import default_entitlement
from ai_platform_api.modules.identity.domain.models import (
    MembershipStatus,
    WorkspaceStatus,
    WorkspaceType,
)
from ai_platform_api.modules.identity.domain.roles import system_role_seed
from ai_platform_api.persistence.tables import (
    accounts,
    membership_departments,
    membership_positions,
    role_bindings,
    roles,
    workspace_entitlements,
    workspace_feature_settings,
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
                    role_version=1,
                    status="active",
                    **audit_values,
                )
            )
            self.add_membership(owner)
            entitlement, feature_settings = default_entitlement(
                workspace_id=workspace.workspace_id,
                workspace_type="enterprise",
                occurred_at=workspace.created_at,
            )
            self._session.execute(
                insert(workspace_entitlements).values(
                    workspace_id=entitlement.workspace_id,
                    plan_code=entitlement.plan_code,
                    max_storage_bytes=entitlement.max_storage_bytes,
                    max_members=entitlement.max_members,
                    max_knowledge_bases=entitlement.max_knowledge_bases,
                    max_published_agents=entitlement.max_published_agents,
                    max_monthly_questions=entitlement.max_monthly_questions,
                    open_api_allowed=entitlement.open_api_allowed,
                    public_publish_allowed=entitlement.public_publish_allowed,
                    created_at=entitlement.created_at,
                    updated_at=entitlement.updated_at,
                    version=entitlement.version,
                )
            )
            self._session.execute(
                insert(workspace_feature_settings).values(
                    workspace_id=feature_settings.workspace_id,
                    open_api_enabled=feature_settings.open_api_enabled,
                    updated_at=feature_settings.updated_at,
                    version=feature_settings.version,
                )
            )
            system_roles, system_bindings = system_role_seed(
                workspace_id=workspace.workspace_id,
                owner_membership_id=owner.membership_id,
                occurred_at=workspace.created_at,
            )
            self._session.execute(
                insert(roles),
                [
                    {
                        "role_id": role.role_id,
                        "workspace_id": role.workspace_id,
                        "role_key": role.role_key,
                        "name": role.name,
                        "status": role.status,
                        "system_managed": role.system_managed,
                        "created_at": role.created_at,
                        "updated_at": role.updated_at,
                        "version": role.version,
                    }
                    for role in system_roles
                ],
            )
            self._session.execute(
                insert(role_bindings),
                [
                    {
                        "binding_id": binding.binding_id,
                        "workspace_id": binding.workspace_id,
                        "role_id": binding.role_id,
                        "scope_type": binding.scope_type,
                        "department_id": binding.department_id,
                        "membership_id": binding.membership_id,
                        "status": binding.status,
                        "created_at": binding.created_at,
                        "revoked_at": binding.revoked_at,
                        "version": binding.version,
                    }
                    for binding in system_bindings
                ],
            )
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
        previous_status = self._session.scalar(
            select(workspace_memberships.c.status).where(
                workspace_memberships.c.membership_id == membership.membership_id,
                workspace_memberships.c.workspace_id == membership.workspace_id,
            )
        )
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
                update(role_bindings)
                .where(
                    role_bindings.c.workspace_id == membership.workspace_id,
                    role_bindings.c.membership_id == membership.membership_id,
                    role_bindings.c.status == "active",
                    role_bindings.c.role_id.in_(
                        select(roles.c.role_id).where(
                            roles.c.workspace_id == membership.workspace_id,
                            roles.c.system_managed.is_(False),
                        )
                    ),
                )
                .values(
                    status="revoked",
                    revoked_at=membership.updated_at,
                    version=role_bindings.c.version + 1,
                )
            )
            # 成员状态变化会改变有效角色，即使没有自定义成员绑定也必须推进版本。
        if previous_status != membership.status:
            self._session.execute(
                update(workspaces)
                .where(workspaces.c.workspace_id == membership.workspace_id)
                .values(role_version=workspaces.c.role_version + 1)
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

    def member_capacity_available(self, workspace_id: UUID) -> bool:
        limit = self._session.scalar(
            select(workspace_entitlements.c.max_members).where(
                workspace_entitlements.c.workspace_id == workspace_id
            )
        )
        active_members = self._session.scalar(
            select(func.count())
            .select_from(workspace_memberships)
            .where(
                workspace_memberships.c.workspace_id == workspace_id,
                workspace_memberships.c.status == "active",
            )
        )
        return limit is not None and int(active_members or 0) < limit


class SqlAlchemyEnterpriseUnitOfWork:
    """企业空间、成员、邀请、审计与 Outbox 共用一个数据库事务。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyEnterpriseRepository,
                SqlAlchemyAuditWriter,
                SqlAlchemyOutboxWriter,
            ]
            | None
        ] = ContextVar("enterprise_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyEnterpriseUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Enterprise Unit of Work 不允许在同一上下文重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyEnterpriseRepository(session),
                SqlAlchemyAuditWriter(session),
                SqlAlchemyOutboxWriter(session),
            )
        )
        return self

    def _current(
        self,
    ) -> tuple[
        Session,
        SqlAlchemyEnterpriseRepository,
        SqlAlchemyAuditWriter,
        SqlAlchemyOutboxWriter,
    ]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Enterprise Unit of Work 尚未进入事务范围")
        return state

    @property
    def enterprise(self) -> SqlAlchemyEnterpriseRepository:
        return self._current()[1]

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        return self._current()[2]

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        return self._current()[3]

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        state = self._state.get()
        if state is not None:
            session = state[0]
            if exc_type is not None:
                session.rollback()
            session.close()
            self._state.set(None)

    def commit(self) -> None:
        session = self._current()[0]
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise EnterpriseWriteConflictError from error
