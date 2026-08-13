from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import Any, cast
from uuid import UUID

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import delete, insert, select, update
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
from ai_platform_api.modules.identity.domain.organization import (
    Department,
    DepartmentClosure,
    OrganizationAssignment,
    OrganizationStatus,
    OrganizationWriteConflictError,
    Position,
)
from ai_platform_api.persistence.tables import (
    department_closure,
    departments,
    membership_departments,
    membership_positions,
    positions,
    workspace_memberships,
    workspaces,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyOrganizationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

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

    def list_departments(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> tuple[Department, ...]:
        statement = (
            select(departments)
            .where(departments.c.workspace_id == workspace_id)
            .order_by(departments.c.department_id)
        )
        if for_update:
            statement = statement.with_for_update()
        rows = self._session.execute(statement).all()
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

    def add_department(self, department: Department) -> None:
        try:
            self._session.execute(
                insert(departments).values(
                    department_id=department.department_id,
                    workspace_id=department.workspace_id,
                    parent_department_id=department.parent_department_id,
                    name=department.name,
                    status=department.status,
                    created_at=department.created_at,
                    updated_at=department.updated_at,
                    version=department.version,
                )
            )
        except IntegrityError as error:
            raise OrganizationWriteConflictError from error

    def save_department(self, department: Department) -> None:
        try:
            result = cast(
                "CursorResult[Any]",
                self._session.execute(
                    update(departments)
                    .where(
                        departments.c.workspace_id == department.workspace_id,
                        departments.c.department_id == department.department_id,
                        departments.c.version == department.version - 1,
                    )
                    .values(
                        parent_department_id=department.parent_department_id,
                        name=department.name,
                        status=department.status,
                        updated_at=department.updated_at,
                        version=department.version,
                    )
                ),
            )
        except IntegrityError as error:
            raise OrganizationWriteConflictError from error
        if result.rowcount != 1:
            raise OrganizationWriteConflictError

    def replace_department_closure(
        self,
        workspace_id: UUID,
        closures: tuple[DepartmentClosure, ...],
    ) -> None:
        self._session.execute(
            delete(department_closure).where(department_closure.c.workspace_id == workspace_id)
        )
        if closures:
            try:
                self._session.execute(
                    insert(department_closure),
                    [
                        {
                            "workspace_id": workspace_id,
                            "ancestor_department_id": row.ancestor_department_id,
                            "descendant_department_id": row.descendant_department_id,
                            "depth": row.depth,
                        }
                        for row in closures
                    ],
                )
            except IntegrityError as error:
                raise OrganizationWriteConflictError from error

    def list_positions(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> tuple[Position, ...]:
        statement = (
            select(positions)
            .where(positions.c.workspace_id == workspace_id)
            .order_by(positions.c.department_id, positions.c.name, positions.c.position_id)
        )
        if for_update:
            statement = statement.with_for_update()
        rows = self._session.execute(statement).all()
        return tuple(
            Position(
                row.position_id,
                row.workspace_id,
                row.department_id,
                row.name,
                cast("OrganizationStatus", row.status),
                row.created_at,
                row.updated_at,
                row.version,
            )
            for row in rows
        )

    def add_position(self, position: Position) -> None:
        try:
            self._session.execute(
                insert(positions).values(
                    position_id=position.position_id,
                    workspace_id=position.workspace_id,
                    department_id=position.department_id,
                    name=position.name,
                    status=position.status,
                    created_at=position.created_at,
                    updated_at=position.updated_at,
                    version=position.version,
                )
            )
        except IntegrityError as error:
            raise OrganizationWriteConflictError from error

    def save_position(self, position: Position) -> None:
        try:
            result = cast(
                "CursorResult[Any]",
                self._session.execute(
                    update(positions)
                    .where(
                        positions.c.workspace_id == position.workspace_id,
                        positions.c.position_id == position.position_id,
                        positions.c.version == position.version - 1,
                    )
                    .values(
                        department_id=position.department_id,
                        name=position.name,
                        status=position.status,
                        updated_at=position.updated_at,
                        version=position.version,
                    )
                ),
            )
        except IntegrityError as error:
            raise OrganizationWriteConflictError from error
        if result.rowcount != 1:
            raise OrganizationWriteConflictError

    def replace_assignment(
        self,
        *,
        workspace_id: UUID,
        membership: WorkspaceMembership,
        department_ids: tuple[UUID, ...],
        primary_department_id: UUID | None,
        position_ids: tuple[UUID, ...],
        occurred_at: datetime,
    ) -> WorkspaceMembership:
        self._session.execute(
            delete(membership_positions).where(
                membership_positions.c.workspace_id == workspace_id,
                membership_positions.c.membership_id == membership.membership_id,
            )
        )
        self._session.execute(
            delete(membership_departments).where(
                membership_departments.c.workspace_id == workspace_id,
                membership_departments.c.membership_id == membership.membership_id,
            )
        )
        if department_ids:
            try:
                self._session.execute(
                    insert(membership_departments),
                    [
                        {
                            "workspace_id": workspace_id,
                            "membership_id": membership.membership_id,
                            "department_id": department_id,
                            "is_primary": department_id == primary_department_id,
                            "assigned_at": occurred_at,
                        }
                        for department_id in department_ids
                    ],
                )
            except IntegrityError as error:
                raise OrganizationWriteConflictError from error
        if position_ids:
            position_rows = self._session.execute(
                select(positions.c.position_id, positions.c.department_id).where(
                    positions.c.workspace_id == workspace_id,
                    positions.c.position_id.in_(position_ids),
                )
            ).all()
            department_by_position = {row.position_id: row.department_id for row in position_rows}
            if len(department_by_position) != len(position_ids):
                raise OrganizationWriteConflictError
            try:
                self._session.execute(
                    insert(membership_positions),
                    [
                        {
                            "workspace_id": workspace_id,
                            "membership_id": membership.membership_id,
                            "position_id": position_id,
                            "department_id": department_by_position[position_id],
                            "assigned_at": occurred_at,
                        }
                        for position_id in position_ids
                    ],
                )
            except IntegrityError as error:
                raise OrganizationWriteConflictError from error
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
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(workspace_memberships)
                .where(
                    workspace_memberships.c.workspace_id == workspace_id,
                    workspace_memberships.c.membership_id == membership.membership_id,
                    workspace_memberships.c.version == membership.version,
                )
                .values(updated_at=occurred_at, version=updated.version)
            ),
        )
        if result.rowcount != 1:
            raise OrganizationWriteConflictError
        return updated

    def get_assignment(
        self,
        workspace_id: UUID,
        membership: WorkspaceMembership,
    ) -> OrganizationAssignment:
        department_rows = self._session.execute(
            select(
                membership_departments.c.department_id,
                membership_departments.c.is_primary,
            )
            .where(
                membership_departments.c.workspace_id == workspace_id,
                membership_departments.c.membership_id == membership.membership_id,
            )
            .order_by(membership_departments.c.department_id)
        ).all()
        position_ids = tuple(
            self._session.execute(
                select(membership_positions.c.position_id)
                .where(
                    membership_positions.c.workspace_id == workspace_id,
                    membership_positions.c.membership_id == membership.membership_id,
                )
                .order_by(membership_positions.c.position_id)
            ).scalars()
        )
        return OrganizationAssignment(
            membership.account_id,
            tuple(row.department_id for row in department_rows),
            next((row.department_id for row in department_rows if row.is_primary), None),
            position_ids,
            membership.version,
        )


class SqlAlchemyOrganizationUnitOfWork:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._organization: SqlAlchemyOrganizationRepository | None = None
        self._audit: SqlAlchemyAuditWriter | None = None
        self._outbox: SqlAlchemyOutboxWriter | None = None

    def __enter__(self) -> SqlAlchemyOrganizationUnitOfWork:
        self._session = self._session_factory()
        self._organization = SqlAlchemyOrganizationRepository(self._session)
        self._audit = SqlAlchemyAuditWriter(self._session)
        self._outbox = SqlAlchemyOutboxWriter(self._session)
        return self

    @property
    def organization(self) -> SqlAlchemyOrganizationRepository:
        if self._organization is None:
            raise RuntimeError("Organization Unit of Work 尚未进入事务范围")
        return self._organization

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        if self._audit is None:
            raise RuntimeError("Organization Unit of Work 尚未进入事务范围")
        return self._audit

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        if self._outbox is None:
            raise RuntimeError("Organization Unit of Work 尚未进入事务范围")
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
            self._organization = None
            self._audit = None
            self._outbox = None

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Organization Unit of Work 尚未进入事务范围")
        try:
            self._session.commit()
        except IntegrityError as error:
            self._session.rollback()
            raise OrganizationWriteConflictError from error
