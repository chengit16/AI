"""实现团队管理聚合读取与成员治理的 PostgreSQL 原子事务。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime
from types import TracebackType
from typing import Any, cast
from uuid import UUID, uuid4

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.identity.domain.enterprise import (
    InvitationStatus,
    MembershipType,
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceRecord,
)
from ai_platform_api.modules.identity.domain.models import (
    MembershipStatus,
    WorkspaceStatus,
    WorkspaceType,
)
from ai_platform_api.modules.identity.domain.organization import (
    Department,
    DepartmentSummary,
    OrganizationStatus,
    PositionSummary,
    summarize_departments,
)
from ai_platform_api.modules.identity.domain.roles import (
    Role,
    RoleBinding,
    RoleBindingStatus,
    RoleScopeType,
    RoleStatus,
    resolve_effective_roles,
)
from ai_platform_api.modules.identity.domain.team_management import (
    TeamAuditOutcome,
    TeamAuditSummary,
    TeamEffectiveRoleSummary,
    TeamInvitationStatus,
    TeamInvitationSummary,
    TeamManagementSnapshot,
    TeamManagementStatistics,
    TeamManagementWriteConflictError,
    TeamMemberSummary,
    TeamRoleSummary,
)
from ai_platform_api.persistence.tables import (
    accounts,
    audit_records,
    departments,
    membership_departments,
    membership_positions,
    positions,
    role_bindings,
    roles,
    workspace_invitations,
    workspace_memberships,
    workspaces,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyTeamManagementRepository:
    """复用身份、组织与角色表，向团队页提供一个一致的深模块接口。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_workspace(
        self, workspace_id: UUID, *, for_update: bool = False
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
            row.workspace_id,
            cast("WorkspaceType", row.workspace_type),
            row.name,
            cast("WorkspaceStatus", row.status),
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
        return None if row is None else _membership(row)

    def get_invitation(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceInvitation | None:
        statement = select(workspace_invitations).where(
            workspace_invitations.c.workspace_id == workspace_id,
            workspace_invitations.c.invitation_id == invitation_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return WorkspaceInvitation(
            row.invitation_id,
            row.workspace_id,
            row.invited_account_id,
            row.invited_by_account_id,
            cast("InvitationStatus", row.status),
            row.created_at,
            row.expires_at,
            row.accepted_at,
        )

    def save_invitation(self, invitation: WorkspaceInvitation) -> None:
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(workspace_invitations)
                .where(
                    workspace_invitations.c.workspace_id == invitation.workspace_id,
                    workspace_invitations.c.invitation_id == invitation.invitation_id,
                    workspace_invitations.c.status == "pending",
                )
                .values(status=invitation.status, accepted_at=invitation.accepted_at)
            ),
        )
        if result.rowcount != 1:
            raise TeamManagementWriteConflictError

    def get_snapshot(
        self,
        workspace_id: UUID,
        *,
        generated_at: datetime,
        audit_limit: int,
        mask_display_name: bool,
        mask_login_name: bool,
    ) -> TeamManagementSnapshot | None:
        """在同一只读事务中聚合团队、组织、角色、邀请与审计事实。"""

        workspace = self.get_workspace(workspace_id)
        if workspace is None:
            return None
        department_entities = self._department_entities(workspace_id)
        department_summaries = summarize_departments(department_entities)
        position_summaries = self.list_positions(workspace_id)
        role_entities = self.list_roles(workspace_id)
        binding_entities = self._role_bindings(workspace_id)
        members = self._members(
            workspace_id,
            roles_value=role_entities,
            bindings=binding_entities,
            departments_value=department_entities,
            mask_display_name=mask_display_name,
            mask_login_name=mask_login_name,
        )
        invitations = self._invitations(
            workspace_id,
            generated_at=generated_at,
            mask_display_name=mask_display_name,
            mask_login_name=mask_login_name,
        )
        return TeamManagementSnapshot(
            workspace=workspace,
            statistics=TeamManagementStatistics(
                active_members=sum(item.status == "active" for item in members),
                disabled_members=sum(item.status == "disabled" for item in members),
                pending_invitations=sum(item.status == "pending" for item in invitations),
                departments=len(department_summaries),
                positions=len(position_summaries),
            ),
            members=members,
            invitations=invitations,
            departments=department_summaries,
            positions=position_summaries,
            roles=tuple(
                TeamRoleSummary(role.role_id, role.role_key, role.name)
                for role in role_entities
                if not role.system_managed and role.status == "active"
            ),
            recent_audits=self._audits(
                workspace_id,
                limit=audit_limit,
                mask_display_name=mask_display_name,
            ),
            generated_at=generated_at,
        )

    def list_departments(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> tuple[DepartmentSummary, ...]:
        statement = select(departments).where(departments.c.workspace_id == workspace_id)
        if for_update:
            statement = statement.with_for_update()
        return summarize_departments(
            tuple(_department(row) for row in self._session.execute(statement))
        )

    def list_positions(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> tuple[PositionSummary, ...]:
        departments_value = self._department_entities(workspace_id, for_update=for_update)
        effective = {
            item.department_id: item.effective_active
            for item in summarize_departments(departments_value)
        }
        statement = (
            select(positions)
            .where(positions.c.workspace_id == workspace_id)
            .order_by(positions.c.name, positions.c.position_id)
        )
        if for_update:
            statement = statement.with_for_update()
        return tuple(
            PositionSummary(
                row.position_id,
                row.department_id,
                row.name,
                cast("OrganizationStatus", row.status),
                row.status == "active" and effective.get(row.department_id, False),
                row.version,
            )
            for row in self._session.execute(statement)
        )

    def list_roles(self, workspace_id: UUID, *, for_update: bool = False) -> tuple[Role, ...]:
        statement = (
            select(roles)
            .where(roles.c.workspace_id == workspace_id)
            .order_by(roles.c.system_managed.desc(), roles.c.role_key, roles.c.role_id)
        )
        if for_update:
            statement = statement.with_for_update()
        return tuple(_role(row) for row in self._session.execute(statement))

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
    ) -> WorkspaceMembership:
        """整体替换组织和直接角色，并以成员版本关闭并发覆盖。"""

        if membership.version != expected_version:
            raise TeamManagementWriteConflictError
        existing_direct = {
            binding.role_id: binding
            for binding in self._role_bindings(workspace_id, for_update=True)
            if binding.scope_type == "member"
            and binding.membership_id == membership.membership_id
            and binding.status == "active"
            and binding.role_id
            in {role.role_id for role in self.list_roles(workspace_id) if not role.system_managed}
        }
        self._replace_organization(
            workspace_id=workspace_id,
            membership_id=membership.membership_id,
            department_ids=department_ids,
            primary_department_id=primary_department_id,
            position_ids=position_ids,
            occurred_at=occurred_at,
        )
        self._replace_direct_roles(
            workspace_id=workspace_id,
            membership_id=membership.membership_id,
            desired_role_ids=direct_role_ids,
            existing=existing_direct,
            occurred_at=occurred_at,
        )
        updated = WorkspaceMembership(
            membership.membership_id,
            membership.workspace_id,
            membership.account_id,
            membership.membership_type,
            membership.status,
            membership.created_at,
            occurred_at,
            membership.version + 1,
        )
        self._update_membership_version(updated, expected_version=expected_version)
        self._bump_role_version(workspace_id)
        return updated

    def save_membership(self, membership: WorkspaceMembership, *, clear_assignments: bool) -> None:
        """保存成员状态；停用保留配置，移除才撤销组织和直接自定义角色。"""

        if clear_assignments:
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
        self._update_membership_version(membership, expected_version=membership.version - 1)
        self._bump_role_version(membership.workspace_id)

    def _department_entities(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> tuple[Department, ...]:
        statement = select(departments).where(departments.c.workspace_id == workspace_id)
        if for_update:
            statement = statement.with_for_update()
        return tuple(_department(row) for row in self._session.execute(statement))

    def _role_bindings(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> tuple[RoleBinding, ...]:
        statement = select(role_bindings).where(role_bindings.c.workspace_id == workspace_id)
        if for_update:
            statement = statement.with_for_update()
        return tuple(_binding(row) for row in self._session.execute(statement))

    def _members(
        self,
        workspace_id: UUID,
        *,
        roles_value: tuple[Role, ...],
        bindings: tuple[RoleBinding, ...],
        departments_value: tuple[Department, ...],
        mask_display_name: bool,
        mask_login_name: bool,
    ) -> tuple[TeamMemberSummary, ...]:
        # 1. 一次冻结成员、角色版本和最后活跃事实；缺失审计时保持空值，不用会话时间替代。
        rows = self._session.execute(
            select(workspace_memberships, accounts.c.display_name, accounts.c.login_name)
            .join(accounts, accounts.c.account_id == workspace_memberships.c.account_id)
            .where(workspace_memberships.c.workspace_id == workspace_id)
            .order_by(
                workspace_memberships.c.membership_type,
                accounts.c.display_name,
                workspace_memberships.c.account_id,
            )
        ).all()
        role_version = int(
            self._session.scalar(
                select(workspaces.c.role_version).where(workspaces.c.workspace_id == workspace_id)
            )
            or 1
        )
        last_active: dict[UUID, datetime] = {
            row.user_id: row.last_active_at
            for row in self._session.execute(
                select(
                    audit_records.c.user_id,
                    func.max(audit_records.c.occurred_at).label("last_active_at"),
                )
                .where(
                    audit_records.c.workspace_id == workspace_id,
                    audit_records.c.user_id.is_not(None),
                )
                .group_by(audit_records.c.user_id)
                .order_by(audit_records.c.user_id)
            )
            if row.user_id is not None and row.last_active_at is not None
        }
        result: list[TeamMemberSummary] = []
        # 2. 逐成员解析组织关系和有效角色来源，所有关系均限定在当前工作空间。
        for row in rows:
            membership = _membership(row)
            department_rows = self._session.execute(
                select(
                    membership_departments.c.department_id,
                    membership_departments.c.is_primary,
                ).where(
                    membership_departments.c.workspace_id == workspace_id,
                    membership_departments.c.membership_id == membership.membership_id,
                )
            ).all()
            department_ids = tuple(
                sorted(
                    (item.department_id for item in department_rows), key=lambda value: value.int
                )
            )
            position_ids = tuple(
                self._session.scalars(
                    select(membership_positions.c.position_id)
                    .where(
                        membership_positions.c.workspace_id == workspace_id,
                        membership_positions.c.membership_id == membership.membership_id,
                    )
                    .order_by(membership_positions.c.position_id)
                )
            )
            effective = resolve_effective_roles(
                membership=membership,
                role_version=role_version,
                roles=roles_value,
                bindings=bindings,
                departments=departments_value,
                assigned_department_ids=department_ids,
            )
            direct_role_ids = tuple(
                sorted(
                    {
                        binding.role_id
                        for binding in bindings
                        if binding.scope_type == "member"
                        and binding.membership_id == membership.membership_id
                        and binding.status == "active"
                        and any(
                            role.role_id == binding.role_id and not role.system_managed
                            for role in roles_value
                        )
                    },
                    key=lambda value: value.int,
                )
            )
            # 3. 最终映射才应用字段遮罩，账号标识本身由应用层在查询前执行失败关闭。
            result.append(
                TeamMemberSummary(
                    account_id=membership.account_id,
                    display_name=None if mask_display_name else row.display_name,
                    login_name=None if mask_login_name else row.login_name,
                    membership_type=membership.membership_type,
                    status=membership.status,
                    department_ids=department_ids,
                    primary_department_id=next(
                        (item.department_id for item in department_rows if item.is_primary),
                        None,
                    ),
                    position_ids=position_ids,
                    direct_role_ids=direct_role_ids,
                    effective_roles=tuple(
                        TeamEffectiveRoleSummary(
                            role.role_id,
                            role.role_key,
                            role.name,
                            tuple(dict.fromkeys(source.scope_type for source in role.sources)),
                        )
                        for role in effective.roles
                    ),
                    joined_at=membership.created_at,
                    updated_at=membership.updated_at,
                    last_active_at=last_active.get(membership.account_id),
                    version=membership.version,
                )
            )
        return tuple(result)

    def _invitations(
        self,
        workspace_id: UUID,
        *,
        generated_at: datetime,
        mask_display_name: bool,
        mask_login_name: bool,
    ) -> tuple[TeamInvitationSummary, ...]:
        inviter = accounts.alias("inviter_accounts")
        invited = accounts.alias("invited_accounts")
        rows = self._session.execute(
            select(
                workspace_invitations,
                invited.c.display_name.label("invited_display_name"),
                invited.c.login_name.label("invited_login_name"),
                inviter.c.display_name.label("inviter_display_name"),
            )
            .join(invited, invited.c.account_id == workspace_invitations.c.invited_account_id)
            .join(inviter, inviter.c.account_id == workspace_invitations.c.invited_by_account_id)
            .where(workspace_invitations.c.workspace_id == workspace_id)
            .order_by(workspace_invitations.c.created_at.desc())
        ).all()
        return tuple(
            TeamInvitationSummary(
                invitation_id=row.invitation_id,
                invited_account_id=row.invited_account_id,
                invited_display_name=None if mask_display_name else row.invited_display_name,
                invited_login_name=None if mask_login_name else row.invited_login_name,
                invited_by_display_name=None if mask_display_name else row.inviter_display_name,
                status=cast(
                    "TeamInvitationStatus",
                    "expired"
                    if row.status == "pending" and row.expires_at <= generated_at
                    else row.status,
                ),
                created_at=row.created_at,
                expires_at=row.expires_at,
                accepted_at=row.accepted_at,
            )
            for row in rows
        )

    def _audits(
        self, workspace_id: UUID, *, limit: int, mask_display_name: bool
    ) -> tuple[TeamAuditSummary, ...]:
        rows = self._session.execute(
            select(audit_records, accounts.c.display_name)
            .outerjoin(accounts, accounts.c.account_id == audit_records.c.user_id)
            .where(
                audit_records.c.workspace_id == workspace_id,
                audit_records.c.resource_type.in_(
                    ("workspace_invitation", "workspace_membership", "organization_assignment")
                ),
            )
            .order_by(audit_records.c.occurred_at.desc(), audit_records.c.audit_id)
            .limit(limit)
        ).all()
        return tuple(
            TeamAuditSummary(
                row.audit_id,
                None if mask_display_name else row.display_name,
                row.action,
                row.resource_type,
                row.resource_id,
                cast("TeamAuditOutcome", row.outcome),
                row.occurred_at,
            )
            for row in rows
        )

    def _replace_organization(
        self,
        *,
        workspace_id: UUID,
        membership_id: UUID,
        department_ids: tuple[UUID, ...],
        primary_department_id: UUID | None,
        position_ids: tuple[UUID, ...],
        occurred_at: datetime,
    ) -> None:
        # 1. 整体替换先删除旧岗位和部门关系，异常时由外层 UoW 回滚，避免半更新组织事实。
        self._session.execute(
            delete(membership_positions).where(
                membership_positions.c.workspace_id == workspace_id,
                membership_positions.c.membership_id == membership_id,
            )
        )
        self._session.execute(
            delete(membership_departments).where(
                membership_departments.c.workspace_id == workspace_id,
                membership_departments.c.membership_id == membership_id,
            )
        )
        if department_ids:
            self._session.execute(
                insert(membership_departments),
                [
                    {
                        "workspace_id": workspace_id,
                        "membership_id": membership_id,
                        "department_id": department_id,
                        "is_primary": department_id == primary_department_id,
                        "assigned_at": occurred_at,
                    }
                    for department_id in department_ids
                ],
            )
        # 2. 岗位必须重新解析到当前空间的所属部门，缺少引用时拒绝写入而不是保留悬空关系。
        if position_ids:
            department_by_position: dict[UUID, UUID] = {
                row.position_id: row.department_id
                for row in self._session.execute(
                    select(positions.c.position_id, positions.c.department_id).where(
                        positions.c.workspace_id == workspace_id,
                        positions.c.position_id.in_(position_ids),
                    )
                )
            }
            if len(department_by_position) != len(position_ids):
                raise TeamManagementWriteConflictError
            self._session.execute(
                insert(membership_positions),
                [
                    {
                        "workspace_id": workspace_id,
                        "membership_id": membership_id,
                        "position_id": position_id,
                        "department_id": department_by_position[position_id],
                        "assigned_at": occurred_at,
                    }
                    for position_id in position_ids
                ],
            )

    def _replace_direct_roles(
        self,
        *,
        workspace_id: UUID,
        membership_id: UUID,
        desired_role_ids: tuple[UUID, ...],
        existing: dict[UUID, RoleBinding],
        occurred_at: datetime,
    ) -> None:
        desired = set(desired_role_ids)
        for role_id, binding in existing.items():
            if role_id not in desired:
                self._session.execute(
                    update(role_bindings)
                    .where(
                        role_bindings.c.workspace_id == workspace_id,
                        role_bindings.c.binding_id == binding.binding_id,
                        role_bindings.c.status == "active",
                    )
                    .values(status="revoked", revoked_at=occurred_at, version=binding.version + 1)
                )
        new_ids = desired.difference(existing)
        if new_ids:
            self._session.execute(
                insert(role_bindings),
                [
                    {
                        "binding_id": uuid4(),
                        "workspace_id": workspace_id,
                        "role_id": role_id,
                        "scope_type": "member",
                        "department_id": None,
                        "membership_id": membership_id,
                        "status": "active",
                        "created_at": occurred_at,
                        "revoked_at": None,
                        "version": 1,
                    }
                    for role_id in sorted(new_ids, key=lambda value: value.int)
                ],
            )

    def _update_membership_version(
        self, membership: WorkspaceMembership, *, expected_version: int
    ) -> None:
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(workspace_memberships)
                .where(
                    workspace_memberships.c.workspace_id == membership.workspace_id,
                    workspace_memberships.c.membership_id == membership.membership_id,
                    workspace_memberships.c.version == expected_version,
                )
                .values(
                    status=membership.status,
                    membership_type=membership.membership_type,
                    updated_at=membership.updated_at,
                    version=membership.version,
                )
            ),
        )
        if result.rowcount != 1:
            raise TeamManagementWriteConflictError

    def _bump_role_version(self, workspace_id: UUID) -> None:
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(workspaces)
                .where(workspaces.c.workspace_id == workspace_id)
                .values(role_version=workspaces.c.role_version + 1)
            ),
        )
        if result.rowcount != 1:
            raise TeamManagementWriteConflictError


class SqlAlchemyTeamManagementUnitOfWork:
    """为团队治理复用单一 Session，确保全部事实同成同败。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyTeamManagementRepository,
                SqlAlchemyAuditWriter,
                SqlAlchemyOutboxWriter,
            ]
            | None
        ] = ContextVar("team_management_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyTeamManagementUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Team Management Unit of Work 不允许重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyTeamManagementRepository(session),
                SqlAlchemyAuditWriter(session),
                SqlAlchemyOutboxWriter(session),
            )
        )
        return self

    def _current(
        self,
    ) -> tuple[
        Session,
        SqlAlchemyTeamManagementRepository,
        SqlAlchemyAuditWriter,
        SqlAlchemyOutboxWriter,
    ]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Team Management Unit of Work 尚未进入事务范围")
        return state

    @property
    def team(self) -> SqlAlchemyTeamManagementRepository:
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
            raise TeamManagementWriteConflictError from error


def _membership(row: Any) -> WorkspaceMembership:
    return WorkspaceMembership(
        row.membership_id,
        row.workspace_id,
        row.account_id,
        cast("MembershipType", row.membership_type),
        cast("MembershipStatus", row.status),
        row.created_at,
        row.updated_at,
        row.version,
    )


def _department(row: Any) -> Department:
    return Department(
        row.department_id,
        row.workspace_id,
        row.parent_department_id,
        row.name,
        cast("OrganizationStatus", row.status),
        row.created_at,
        row.updated_at,
        row.version,
    )


def _role(row: Any) -> Role:
    return Role(
        row.role_id,
        row.workspace_id,
        row.role_key,
        row.name,
        cast("RoleStatus", row.status),
        row.system_managed,
        row.created_at,
        row.updated_at,
        row.version,
    )


def _binding(row: Any) -> RoleBinding:
    return RoleBinding(
        row.binding_id,
        row.workspace_id,
        row.role_id,
        cast("RoleScopeType", row.scope_type),
        row.department_id,
        row.membership_id,
        cast("RoleBindingStatus", row.status),
        row.created_at,
        row.revoked_at,
        row.version,
    )
