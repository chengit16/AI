from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
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

from ai_platform_api.modules.authorization.domain.menus import (
    MenuConfigurationWriteConflictError,
    MenuRelease,
    RoleMenuVisibility,
    WorkspaceMenuOverride,
)
from ai_platform_api.modules.authorization.infrastructure.menu_serialization import (
    menu_release_from_row,
    menu_release_values,
)
from ai_platform_api.persistence.tables import (
    menu_releases,
    registered_menu_api_bindings,
    role_menus,
    roles,
    workspace_memberships,
    workspace_menu_overrides,
    workspace_menu_publications,
    workspaces,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyMenuConfigurationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_workspace_access(
        self,
        workspace_id: UUID,
        account_id: UUID,
    ) -> tuple[str, str, str] | None:
        row = self._session.execute(
            select(
                workspaces.c.workspace_type,
                workspaces.c.status,
                workspace_memberships.c.membership_type,
            )
            .join(
                workspace_memberships,
                workspace_memberships.c.workspace_id == workspaces.c.workspace_id,
            )
            .where(
                workspaces.c.workspace_id == workspace_id,
                workspace_memberships.c.account_id == account_id,
                workspace_memberships.c.status == "active",
            )
        ).one_or_none()
        return tuple(row) if row is not None else None

    def get_menu_version(self, workspace_id: UUID, *, for_update: bool = False) -> int | None:
        statement = select(workspaces.c.menu_version).where(
            workspaces.c.workspace_id == workspace_id
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def bump_menu_version(self, workspace_id: UUID, expected_version: int) -> int:
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(workspaces)
                .where(
                    workspaces.c.workspace_id == workspace_id,
                    workspaces.c.menu_version == expected_version,
                )
                .values(menu_version=expected_version + 1)
            ),
        )
        if result.rowcount != 1:
            raise MenuConfigurationWriteConflictError
        return expected_version + 1

    def get_role(self, workspace_id: UUID, role_id: UUID) -> tuple[str, str] | None:
        row = self._session.execute(
            select(roles.c.role_key, roles.c.status).where(
                roles.c.workspace_id == workspace_id,
                roles.c.role_id == role_id,
            )
        ).one_or_none()
        return tuple(row) if row is not None else None

    def list_registered_bindings(self) -> tuple[tuple[UUID, UUID, str], ...]:
        rows = self._session.execute(
            select(
                registered_menu_api_bindings.c.menu_id,
                registered_menu_api_bindings.c.api_resource_id,
                registered_menu_api_bindings.c.action_type,
            ).order_by(
                registered_menu_api_bindings.c.menu_id,
                registered_menu_api_bindings.c.api_resource_id,
            )
        )
        return tuple(tuple(row) for row in rows)

    def list_overrides(self, workspace_id: UUID) -> tuple[WorkspaceMenuOverride, ...]:
        rows = self._session.execute(
            select(workspace_menu_overrides)
            .where(workspace_menu_overrides.c.workspace_id == workspace_id)
            .order_by(
                workspace_menu_overrides.c.sort_order,
                workspace_menu_overrides.c.menu_id,
            )
        )
        return tuple(
            WorkspaceMenuOverride(
                row.workspace_id,
                row.menu_id,
                row.parent_menu_id,
                row.name,
                row.icon_key,
                row.sort_order,
                row.visible,
                row.version,
            )
            for row in rows
        )

    def replace_overrides(
        self,
        workspace_id: UUID,
        overrides: tuple[WorkspaceMenuOverride, ...],
    ) -> None:
        self._session.execute(
            delete(workspace_menu_overrides).where(
                workspace_menu_overrides.c.workspace_id == workspace_id
            )
        )
        if overrides:
            self._session.execute(
                insert(workspace_menu_overrides),
                [
                    {
                        "workspace_id": item.workspace_id,
                        "menu_id": item.menu_id,
                        "parent_menu_id": item.parent_menu_id,
                        "name": item.name,
                        "icon_key": item.icon_key,
                        "sort_order": item.sort_order,
                        "visible": item.visible,
                        "version": item.version,
                    }
                    for item in overrides
                ],
            )

    def list_role_menus(
        self,
        workspace_id: UUID,
        role_id: UUID,
    ) -> tuple[RoleMenuVisibility, ...]:
        rows = self._session.execute(
            select(role_menus)
            .where(
                role_menus.c.workspace_id == workspace_id,
                role_menus.c.role_id == role_id,
            )
            .order_by(role_menus.c.menu_id)
        )
        return tuple(
            RoleMenuVisibility(row.workspace_id, row.role_id, row.menu_id, row.visible)
            for row in rows
        )

    def replace_role_menus(
        self,
        workspace_id: UUID,
        role_id: UUID,
        entries: tuple[RoleMenuVisibility, ...],
    ) -> None:
        self._session.execute(
            delete(role_menus).where(
                role_menus.c.workspace_id == workspace_id,
                role_menus.c.role_id == role_id,
            )
        )
        if entries:
            self._session.execute(
                insert(role_menus),
                [
                    {
                        "workspace_id": item.workspace_id,
                        "role_id": item.role_id,
                        "menu_id": item.menu_id,
                        "visible": item.visible,
                    }
                    for item in entries
                ],
            )

    def list_all_role_menus(self, workspace_id: UUID) -> tuple[RoleMenuVisibility, ...]:
        rows = self._session.execute(
            select(role_menus)
            .where(role_menus.c.workspace_id == workspace_id)
            .order_by(role_menus.c.role_id, role_menus.c.menu_id)
        )
        return tuple(
            RoleMenuVisibility(row.workspace_id, row.role_id, row.menu_id, row.visible)
            for row in rows
        )

    def next_release_number(self, workspace_id: UUID) -> int:
        current = self._session.scalar(
            select(menu_releases.c.release_number)
            .where(menu_releases.c.workspace_id == workspace_id)
            .order_by(menu_releases.c.release_number.desc())
            .limit(1)
            .with_for_update()
        )
        return (current or 0) + 1

    def add_release(self, release: MenuRelease) -> None:
        self._session.execute(insert(menu_releases).values(**menu_release_values(release)))

    def get_release(self, workspace_id: UUID, release_id: UUID) -> MenuRelease | None:
        row = self._session.execute(
            select(menu_releases).where(
                menu_releases.c.workspace_id == workspace_id,
                menu_releases.c.release_id == release_id,
            )
        ).one_or_none()
        return menu_release_from_row(row) if row is not None else None

    def list_releases(self, workspace_id: UUID) -> tuple[MenuRelease, ...]:
        rows = self._session.execute(
            select(menu_releases)
            .where(menu_releases.c.workspace_id == workspace_id)
            .order_by(menu_releases.c.release_number.desc())
        )
        return tuple(menu_release_from_row(row) for row in rows)

    def save_release(self, release: MenuRelease) -> None:
        # 快照和摘要只在创建时写入，状态转换不能借更新语句改写发布内容。
        values = {
            "status": release.status,
            "validation_errors": list(release.validation_errors),
            "rejection_reason": release.rejection_reason,
            "decided_by_account_id": release.decided_by_account_id,
            "validated_at": release.validated_at,
            "decided_at": release.decided_at,
            "published_at": release.published_at,
            "version": release.version,
        }
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(menu_releases)
                .where(
                    menu_releases.c.workspace_id == release.workspace_id,
                    menu_releases.c.release_id == release.release_id,
                    menu_releases.c.version == release.version - 1,
                )
                .values(**values)
            ),
        )
        if result.rowcount != 1:
            raise MenuConfigurationWriteConflictError

    def get_current_release_id(self, workspace_id: UUID) -> UUID | None:
        return self._session.scalar(
            select(workspace_menu_publications.c.current_release_id).where(
                workspace_menu_publications.c.workspace_id == workspace_id
            )
        )

    def set_current_release(
        self,
        workspace_id: UUID,
        release_id: UUID,
        *,
        published_at: datetime,
    ) -> None:
        current = self._session.scalar(
            select(workspace_menu_publications.c.workspace_id)
            .where(workspace_menu_publications.c.workspace_id == workspace_id)
            .with_for_update()
        )
        if current is None:
            self._session.execute(
                insert(workspace_menu_publications).values(
                    workspace_id=workspace_id,
                    current_release_id=release_id,
                    published_at=published_at,
                )
            )
        else:
            self._session.execute(
                update(workspace_menu_publications)
                .where(workspace_menu_publications.c.workspace_id == workspace_id)
                .values(current_release_id=release_id, published_at=published_at)
            )


class SqlAlchemyMenuConfigurationUnitOfWork:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyMenuConfigurationRepository,
                SqlAlchemyAuditWriter,
                SqlAlchemyOutboxWriter,
            ]
            | None
        ] = ContextVar("menu_configuration_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyMenuConfigurationUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Menu Configuration Unit of Work 不允许重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyMenuConfigurationRepository(session),
                SqlAlchemyAuditWriter(session),
                SqlAlchemyOutboxWriter(session),
            )
        )
        return self

    def _current(
        self,
    ) -> tuple[
        Session,
        SqlAlchemyMenuConfigurationRepository,
        SqlAlchemyAuditWriter,
        SqlAlchemyOutboxWriter,
    ]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Menu Configuration Unit of Work 尚未进入事务范围")
        return state

    @property
    def menus(self) -> SqlAlchemyMenuConfigurationRepository:
        return self._current()[1]

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        return self._current()[2]

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        return self._current()[3]

    def commit(self) -> None:
        try:
            self._current()[0].commit()
        except IntegrityError as error:
            self._current()[0].rollback()
            raise MenuConfigurationWriteConflictError from error

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        state = self._state.get()
        if state is not None:
            if exc_type is not None:
                state[0].rollback()
            state[0].close()
            self._state.set(None)
