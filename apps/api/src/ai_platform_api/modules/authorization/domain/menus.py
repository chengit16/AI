from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter


@dataclass(frozen=True)
class WorkspaceMenuOverride:
    workspace_id: UUID
    menu_id: UUID
    parent_menu_id: UUID | None
    name: str
    icon_key: str | None
    sort_order: int
    visible: bool
    version: int = 1


@dataclass(frozen=True)
class RoleMenuVisibility:
    workspace_id: UUID
    role_id: UUID
    menu_id: UUID
    visible: bool


@dataclass(frozen=True)
class MenuConfiguration:
    workspace_id: UUID
    menu_version: int
    overrides: tuple[WorkspaceMenuOverride, ...]


class MenuConfigurationWriteConflictError(Exception):
    """菜单配置并发变化或数据库约束拒绝本次整体替换。"""


class MenuConfigurationRepository(Protocol):
    def get_workspace_access(
        self,
        workspace_id: UUID,
        account_id: UUID,
    ) -> tuple[str, str, str] | None: ...

    def get_menu_version(self, workspace_id: UUID, *, for_update: bool = False) -> int | None: ...

    def bump_menu_version(self, workspace_id: UUID, expected_version: int) -> int: ...

    def get_role(self, workspace_id: UUID, role_id: UUID) -> tuple[str, str] | None: ...

    def list_registered_bindings(self) -> tuple[tuple[UUID, UUID, str], ...]: ...

    def list_overrides(self, workspace_id: UUID) -> tuple[WorkspaceMenuOverride, ...]: ...

    def replace_overrides(
        self,
        workspace_id: UUID,
        overrides: tuple[WorkspaceMenuOverride, ...],
    ) -> None: ...

    def list_role_menus(
        self,
        workspace_id: UUID,
        role_id: UUID,
    ) -> tuple[RoleMenuVisibility, ...]: ...

    def replace_role_menus(
        self,
        workspace_id: UUID,
        role_id: UUID,
        entries: tuple[RoleMenuVisibility, ...],
    ) -> None: ...


class MenuConfigurationUnitOfWork(Protocol):
    @property
    def menus(self) -> MenuConfigurationRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> MenuConfigurationUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
