"""实现角色授权、主体解析和组织范围展开的 PostgreSQL Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from types import TracebackType
from typing import Any, cast
from uuid import UUID

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.authorization.domain.grants import (
    PolicySubject,
    RolePermissionGrant,
    RolePermissionWriteConflictError,
)
from ai_platform_api.modules.authorization.domain.policy import DataScopeType
from ai_platform_api.modules.identity.domain.enterprise import MembershipType, WorkspaceMembership
from ai_platform_api.modules.identity.domain.organization import Department, OrganizationStatus
from ai_platform_api.modules.identity.domain.roles import (
    Role,
    RoleBinding,
    RoleBindingStatus,
    RoleScopeType,
    RoleStatus,
    resolve_effective_roles,
)
from ai_platform_api.persistence.tables import (
    department_closure,
    departments,
    membership_departments,
    role_bindings,
    role_permission_grants,
    roles,
    workspace_memberships,
    workspaces,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyPolicyGrantRepository:
    """从同一事务快照恢复主体角色与授权范围，避免依赖前端声明。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def resolve_subject(self, context: RequestContext) -> PolicySubject | None:
        if context.user_id is None:
            return None
        with self._session_factory() as session:
            # 1. 工作空间和成员关系必须在同一数据库快照中保持有效，否则主体立即失效。
            workspace_row = session.execute(
                select(workspaces.c.status, workspaces.c.role_version).where(
                    workspaces.c.workspace_id == context.workspace_id
                )
            ).one_or_none()
            membership_row = session.execute(
                select(workspace_memberships).where(
                    workspace_memberships.c.workspace_id == context.workspace_id,
                    workspace_memberships.c.account_id == context.user_id,
                )
            ).one_or_none()
            if (
                workspace_row is None
                or workspace_row.status != "active"
                or membership_row is None
                or membership_row.status != "active"
            ):
                return None
            membership = WorkspaceMembership(
                membership_row.membership_id,
                membership_row.workspace_id,
                membership_row.account_id,
                cast("MembershipType", membership_row.membership_type),
                membership_row.status,
                membership_row.created_at,
                membership_row.updated_at,
                membership_row.version,
            )
            # 2. 使用服务端组织和角色绑定计算有效角色，最终只向 PDP 返回最小主体事实。
            effective = resolve_effective_roles(
                membership=membership,
                role_version=workspace_row.role_version,
                roles=_roles(session, context.workspace_id),
                bindings=_bindings(session, context.workspace_id),
                departments=_departments(session, context.workspace_id),
                assigned_department_ids=tuple(
                    session.scalars(
                        select(membership_departments.c.department_id).where(
                            membership_departments.c.workspace_id == context.workspace_id,
                            membership_departments.c.membership_id == membership.membership_id,
                        )
                    )
                ),
            )
            return PolicySubject(
                account_id=context.user_id,
                membership_id=membership.membership_id,
                role_version=effective.role_version,
                role_ids=frozenset(role.role_id for role in effective.roles),
            )

    def list_role_grants(
        self,
        workspace_id: UUID,
        role_ids: frozenset[UUID],
    ) -> tuple[RolePermissionGrant, ...]:
        if not role_ids:
            return ()
        with self._session_factory() as session:
            rows = session.execute(
                select(role_permission_grants).where(
                    role_permission_grants.c.workspace_id == workspace_id,
                    role_permission_grants.c.role_id.in_(role_ids),
                )
            )
            return tuple(_grant(row) for row in rows)

    def expand_department_tree(
        self,
        workspace_id: UUID,
        department_ids: frozenset[UUID],
    ) -> frozenset[UUID]:
        if not department_ids:
            return frozenset()
        with self._session_factory() as session:
            return frozenset(
                session.scalars(
                    select(department_closure.c.descendant_department_id)
                    .join(
                        departments,
                        (departments.c.workspace_id == department_closure.c.workspace_id)
                        & (
                            departments.c.department_id
                            == department_closure.c.descendant_department_id
                        ),
                    )
                    .where(
                        department_closure.c.workspace_id == workspace_id,
                        department_closure.c.ancestor_department_id.in_(department_ids),
                        departments.c.status == "active",
                    )
                )
            )


class SqlAlchemyTransactionalPolicyGrantRepository:
    """在调用方事务快照内解析授权事实，供批准等原子业务复核使用。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve_subject(self, context: RequestContext) -> PolicySubject | None:
        """使用当前 Session 恢复活动成员和有效角色，不开启旁路事务。"""

        # 1. 先锁定工作空间与成员当前状态，失效主体不得进入 PDP。
        if context.user_id is None:
            return None
        workspace_row = self._session.execute(
            select(workspaces.c.status, workspaces.c.role_version).where(
                workspaces.c.workspace_id == context.workspace_id
            )
        ).one_or_none()
        membership_row = self._session.execute(
            select(workspace_memberships).where(
                workspace_memberships.c.workspace_id == context.workspace_id,
                workspace_memberships.c.account_id == context.user_id,
            )
        ).one_or_none()
        if (
            workspace_row is None
            or workspace_row.status != "active"
            or membership_row is None
            or membership_row.status != "active"
        ):
            return None
        membership = WorkspaceMembership(
            membership_row.membership_id,
            membership_row.workspace_id,
            membership_row.account_id,
            cast("MembershipType", membership_row.membership_type),
            membership_row.status,
            membership_row.created_at,
            membership_row.updated_at,
            membership_row.version,
        )
        # 2. 在同一事务快照展开组织与角色，生成最小可追溯主体事实。
        effective = resolve_effective_roles(
            membership=membership,
            role_version=workspace_row.role_version,
            roles=_roles(self._session, context.workspace_id),
            bindings=_bindings(self._session, context.workspace_id),
            departments=_departments(self._session, context.workspace_id),
            assigned_department_ids=tuple(
                self._session.scalars(
                    select(membership_departments.c.department_id).where(
                        membership_departments.c.workspace_id == context.workspace_id,
                        membership_departments.c.membership_id == membership.membership_id,
                    )
                )
            ),
        )
        return PolicySubject(
            account_id=context.user_id,
            membership_id=membership.membership_id,
            role_version=effective.role_version,
            role_ids=frozenset(role.role_id for role in effective.roles),
        )

    def list_role_grants(
        self,
        workspace_id: UUID,
        role_ids: frozenset[UUID],
    ) -> tuple[RolePermissionGrant, ...]:
        if not role_ids:
            return ()
        return tuple(
            _grant(row)
            for row in self._session.execute(
                select(role_permission_grants).where(
                    role_permission_grants.c.workspace_id == workspace_id,
                    role_permission_grants.c.role_id.in_(role_ids),
                )
            )
        )

    def expand_department_tree(
        self,
        workspace_id: UUID,
        department_ids: frozenset[UUID],
    ) -> frozenset[UUID]:
        if not department_ids:
            return frozenset()
        return frozenset(
            self._session.scalars(
                select(department_closure.c.descendant_department_id)
                .join(
                    departments,
                    (departments.c.workspace_id == department_closure.c.workspace_id)
                    & (
                        departments.c.department_id == department_closure.c.descendant_department_id
                    ),
                )
                .where(
                    department_closure.c.workspace_id == workspace_id,
                    department_closure.c.ancestor_department_id.in_(department_ids),
                    departments.c.status == "active",
                )
            )
        )


class SqlAlchemyRolePermissionRepository:
    """在工作空间隔离下整体维护角色授权项和角色版本。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve_subject(self, context: RequestContext) -> PolicySubject | None:
        raise NotImplementedError("写事务不负责策略主体解析")

    def get_requester_membership_type(
        self,
        workspace_id: UUID,
        account_id: UUID,
    ) -> str | None:
        return self._session.scalar(
            select(workspace_memberships.c.membership_type).where(
                workspace_memberships.c.workspace_id == workspace_id,
                workspace_memberships.c.account_id == account_id,
                workspace_memberships.c.status == "active",
            )
        )

    def get_role(self, workspace_id: UUID, role_id: UUID) -> tuple[str, bool, str] | None:
        row = self._session.execute(
            select(roles.c.role_key, roles.c.system_managed, roles.c.status).where(
                roles.c.workspace_id == workspace_id,
                roles.c.role_id == role_id,
            )
        ).one_or_none()
        return tuple(row) if row is not None else None

    def list_role_grants(
        self,
        workspace_id: UUID,
        role_ids: frozenset[UUID],
    ) -> tuple[RolePermissionGrant, ...]:
        if not role_ids:
            return ()
        rows = self._session.execute(
            select(role_permission_grants).where(
                role_permission_grants.c.workspace_id == workspace_id,
                role_permission_grants.c.role_id.in_(role_ids),
            )
        )
        return tuple(_grant(row) for row in rows)

    def expand_department_tree(
        self,
        workspace_id: UUID,
        department_ids: frozenset[UUID],
    ) -> frozenset[UUID]:
        if not department_ids:
            return frozenset()
        return frozenset(
            self._session.scalars(
                select(department_closure.c.descendant_department_id).where(
                    department_closure.c.workspace_id == workspace_id,
                    department_closure.c.ancestor_department_id.in_(department_ids),
                )
            )
        )

    def replace_role_grants(
        self,
        workspace_id: UUID,
        role_id: UUID,
        grants: tuple[RolePermissionGrant, ...],
    ) -> None:
        self._session.execute(
            delete(role_permission_grants).where(
                role_permission_grants.c.workspace_id == workspace_id,
                role_permission_grants.c.role_id == role_id,
            )
        )
        if grants:
            self._session.execute(
                insert(role_permission_grants),
                [
                    {
                        "workspace_id": grant.workspace_id,
                        "role_id": grant.role_id,
                        "permission_code": grant.permission_code,
                        "scope_type": grant.scope_type,
                        "department_ids": list(grant.department_ids),
                        "resource_ids": list(grant.resource_ids),
                        "maximum_security_level": grant.maximum_security_level,
                        "field_mask": sorted(grant.field_mask),
                    }
                    for grant in grants
                ],
            )

    def bump_role_version(self, workspace_id: UUID) -> int:
        version = self._session.scalar(
            update(workspaces)
            .where(workspaces.c.workspace_id == workspace_id)
            .values(role_version=workspaces.c.role_version + 1)
            .returning(workspaces.c.role_version)
        )
        if version is None:
            raise RolePermissionWriteConflictError
        return cast(int, version)


class SqlAlchemyRolePermissionUnitOfWork:
    """保证角色权限、版本、审计和 Outbox 使用同一 Session 提交。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyRolePermissionRepository,
                SqlAlchemyAuditWriter,
                SqlAlchemyOutboxWriter,
            ]
            | None
        ] = ContextVar("role_permission_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyRolePermissionUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Role Permission Unit of Work 不允许在同一上下文重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyRolePermissionRepository(session),
                SqlAlchemyAuditWriter(session),
                SqlAlchemyOutboxWriter(session),
            )
        )
        return self

    def _current(
        self,
    ) -> tuple[
        Session,
        SqlAlchemyRolePermissionRepository,
        SqlAlchemyAuditWriter,
        SqlAlchemyOutboxWriter,
    ]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Role Permission Unit of Work 尚未进入事务范围")
        return state

    @property
    def permissions(self) -> SqlAlchemyRolePermissionRepository:
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
        try:
            self._current()[0].commit()
        except IntegrityError as error:
            self._current()[0].rollback()
            raise RolePermissionWriteConflictError from error


def _grant(row: Row[Any]) -> RolePermissionGrant:
    return RolePermissionGrant(
        workspace_id=row.workspace_id,
        role_id=row.role_id,
        permission_code=row.permission_code,
        scope_type=cast("DataScopeType", row.scope_type),
        department_ids=frozenset(row.department_ids),
        resource_ids=frozenset(row.resource_ids),
        maximum_security_level=cast("SecurityLevel", row.maximum_security_level),
        field_mask=frozenset(row.field_mask),
    )


def _roles(session: Session, workspace_id: UUID) -> tuple[Role, ...]:
    return tuple(
        Role(
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
        for row in session.execute(select(roles).where(roles.c.workspace_id == workspace_id))
    )


def _bindings(session: Session, workspace_id: UUID) -> tuple[RoleBinding, ...]:
    return tuple(
        RoleBinding(
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
        for row in session.execute(
            select(role_bindings).where(role_bindings.c.workspace_id == workspace_id)
        )
    )


def _departments(session: Session, workspace_id: UUID) -> tuple[Department, ...]:
    return tuple(
        Department(
            row.department_id,
            row.workspace_id,
            row.parent_department_id,
            row.name,
            cast("OrganizationStatus", row.status),
            row.created_at,
            row.updated_at,
            row.version,
        )
        for row in session.execute(
            select(departments).where(departments.c.workspace_id == workspace_id)
        )
    )
