from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Any, cast
from uuid import UUID

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import insert, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.identity.domain.enterprise import (
    MembershipType,
    WorkspaceMembership,
    WorkspaceRecord,
)
from ai_platform_api.modules.identity.domain.models import (
    MembershipStatus,
    WorkspaceStatus,
    WorkspaceType,
)
from ai_platform_api.modules.identity.domain.organization import Department, OrganizationStatus
from ai_platform_api.modules.identity.domain.roles import (
    Role,
    RoleBinding,
    RoleBindingStatus,
    RoleScopeType,
    RoleStatus,
    RoleWriteConflictError,
)
from ai_platform_api.persistence.tables import (
    departments,
    membership_departments,
    role_bindings,
    roles,
    workspace_memberships,
    workspaces,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyRoleRepository:
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

    def get_role_version(self, workspace_id: UUID, *, for_update: bool = False) -> int | None:
        statement = select(workspaces.c.role_version).where(
            workspaces.c.workspace_id == workspace_id
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def bump_role_version(self, workspace_id: UUID, expected_version: int) -> int:
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(workspaces)
                .where(
                    workspaces.c.workspace_id == workspace_id,
                    workspaces.c.role_version == expected_version,
                )
                .values(role_version=expected_version + 1)
            ),
        )
        if result.rowcount != 1:
            raise ValueError("角色版本已变化")
        return expected_version + 1

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
            row.membership_id,
            row.workspace_id,
            row.account_id,
            cast("MembershipType", row.membership_type),
            cast("MembershipStatus", row.status),
            row.created_at,
            row.updated_at,
            row.version,
        )

    def list_roles(self, workspace_id: UUID, *, for_update: bool = False) -> tuple[Role, ...]:
        statement = (
            select(roles)
            .where(roles.c.workspace_id == workspace_id)
            .order_by(roles.c.system_managed.desc(), roles.c.role_key, roles.c.role_id)
        )
        if for_update:
            statement = statement.with_for_update()
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
            for row in self._session.execute(statement)
        )

    def add_role(self, role: Role) -> None:
        try:
            self._session.execute(
                insert(roles).values(
                    role_id=role.role_id,
                    workspace_id=role.workspace_id,
                    role_key=role.role_key,
                    name=role.name,
                    status=role.status,
                    system_managed=role.system_managed,
                    created_at=role.created_at,
                    updated_at=role.updated_at,
                    version=role.version,
                )
            )
        except IntegrityError as error:
            raise RoleWriteConflictError from error

    def save_role(self, role: Role) -> None:
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(roles)
                .where(
                    roles.c.workspace_id == role.workspace_id,
                    roles.c.role_id == role.role_id,
                    roles.c.version == role.version - 1,
                )
                .values(
                    name=role.name,
                    status=role.status,
                    updated_at=role.updated_at,
                    version=role.version,
                )
            ),
        )
        if result.rowcount != 1:
            raise RoleWriteConflictError

    def list_bindings(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> tuple[RoleBinding, ...]:
        statement = (
            select(role_bindings)
            .where(role_bindings.c.workspace_id == workspace_id)
            .order_by(role_bindings.c.created_at, role_bindings.c.binding_id)
        )
        if for_update:
            statement = statement.with_for_update()
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
            for row in self._session.execute(statement)
        )

    def add_binding(self, binding: RoleBinding) -> None:
        try:
            self._session.execute(
                insert(role_bindings).values(
                    binding_id=binding.binding_id,
                    workspace_id=binding.workspace_id,
                    role_id=binding.role_id,
                    scope_type=binding.scope_type,
                    department_id=binding.department_id,
                    membership_id=binding.membership_id,
                    status=binding.status,
                    created_at=binding.created_at,
                    revoked_at=binding.revoked_at,
                    version=binding.version,
                )
            )
        except IntegrityError as error:
            raise RoleWriteConflictError from error

    def save_binding(self, binding: RoleBinding) -> None:
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(role_bindings)
                .where(
                    role_bindings.c.workspace_id == binding.workspace_id,
                    role_bindings.c.binding_id == binding.binding_id,
                    role_bindings.c.version == binding.version - 1,
                )
                .values(
                    status=binding.status,
                    revoked_at=binding.revoked_at,
                    version=binding.version,
                )
            ),
        )
        if result.rowcount != 1:
            raise RoleWriteConflictError

    def list_departments(self, workspace_id: UUID) -> tuple[Department, ...]:
        rows = self._session.execute(
            select(departments)
            .where(departments.c.workspace_id == workspace_id)
            .order_by(departments.c.department_id)
        )
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
            for row in rows
        )

    def list_membership_department_ids(
        self, workspace_id: UUID, membership_id: UUID
    ) -> tuple[UUID, ...]:
        return tuple(
            self._session.scalars(
                select(membership_departments.c.department_id)
                .where(
                    membership_departments.c.workspace_id == workspace_id,
                    membership_departments.c.membership_id == membership_id,
                )
                .order_by(membership_departments.c.department_id)
            )
        )


class SqlAlchemyRoleUnitOfWork:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._roles: SqlAlchemyRoleRepository | None = None
        self._audit: SqlAlchemyAuditWriter | None = None
        self._outbox: SqlAlchemyOutboxWriter | None = None

    def __enter__(self) -> SqlAlchemyRoleUnitOfWork:
        self._session = self._session_factory()
        self._roles = SqlAlchemyRoleRepository(self._session)
        self._audit = SqlAlchemyAuditWriter(self._session)
        self._outbox = SqlAlchemyOutboxWriter(self._session)
        return self

    @property
    def roles(self) -> SqlAlchemyRoleRepository:
        if self._roles is None:
            raise RuntimeError("Role Unit of Work 尚未进入事务范围")
        return self._roles

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        if self._audit is None:
            raise RuntimeError("Role Unit of Work 尚未进入事务范围")
        return self._audit

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        if self._outbox is None:
            raise RuntimeError("Role Unit of Work 尚未进入事务范围")
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
            self._roles = None
            self._audit = None
            self._outbox = None

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Role Unit of Work 尚未进入事务范围")
        try:
            self._session.commit()
        except IntegrityError as error:
            self._session.rollback()
            raise RoleWriteConflictError from error
