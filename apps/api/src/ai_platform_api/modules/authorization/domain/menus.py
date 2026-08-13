from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter

from ai_platform_api.modules.authorization.domain.resources import (
    MenuActionType,
    MenuSource,
    MenuType,
    ResourceStatus,
)


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


MenuReleaseStatus = Literal["draft", "validated", "approved", "rejected", "published"]
MenuReleaseKind = Literal["standard", "rollback"]


@dataclass(frozen=True)
class MenuSnapshotItem:
    menu_id: UUID
    menu_key: str
    parent_menu_id: UUID | None
    name: str
    menu_type: MenuType
    page_resource_id: UUID | None
    permission_code: str | None
    icon_key: str | None
    sort_order: int
    source: MenuSource
    status: ResourceStatus
    visible: bool


@dataclass(frozen=True)
class MenuReleaseSnapshot:
    schema_version: int
    registry_version: int
    workspace_id: UUID
    menu_version: int
    menus: tuple[MenuSnapshotItem, ...]
    role_menus: tuple[RoleMenuVisibility, ...]
    menu_api_bindings: tuple[tuple[UUID, UUID, MenuActionType], ...]


@dataclass(frozen=True)
class MenuRelease:
    release_id: UUID
    workspace_id: UUID
    release_number: int
    release_kind: MenuReleaseKind
    source_release_id: UUID | None
    status: MenuReleaseStatus
    snapshot: MenuReleaseSnapshot
    snapshot_digest: str
    validation_errors: tuple[str, ...]
    rejection_reason: str | None
    created_by_account_id: UUID
    decided_by_account_id: UUID | None
    created_at: datetime
    validated_at: datetime | None
    decided_at: datetime | None
    published_at: datetime | None
    version: int

    def record_validation(
        self,
        errors: tuple[str, ...],
        *,
        occurred_at: datetime,
    ) -> MenuRelease:
        if self.status != "draft":
            raise InvalidMenuReleaseTransitionError
        next_status: MenuReleaseStatus = "validated" if not errors else "draft"
        return replace(
            self,
            status=next_status,
            validation_errors=errors,
            validated_at=occurred_at,
            version=self.version + 1,
        )

    def decide(
        self,
        *,
        approved: bool,
        account_id: UUID,
        reason: str | None,
        occurred_at: datetime,
    ) -> MenuRelease:
        if (
            self.status != "validated"
            or self.validation_errors
            or (not approved and (reason is None or not reason.strip()))
        ):
            raise InvalidMenuReleaseTransitionError
        return replace(
            self,
            status="approved" if approved else "rejected",
            rejection_reason=None if approved else reason,
            decided_by_account_id=account_id,
            decided_at=occurred_at,
            version=self.version + 1,
        )

    def publish(self, *, occurred_at: datetime) -> MenuRelease:
        if self.status != "approved" or self.validation_errors:
            raise InvalidMenuReleaseTransitionError
        return replace(
            self,
            status="published",
            published_at=occurred_at,
            version=self.version + 1,
        )


class InvalidMenuReleaseTransitionError(Exception):
    """菜单发布状态或前置校验不允许当前转换。"""


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


class MenuReleaseRepository(MenuConfigurationRepository, Protocol):
    """在菜单配置仓储之上增加不可变发布历史与当前指针能力。"""

    def list_all_role_menus(self, workspace_id: UUID) -> tuple[RoleMenuVisibility, ...]: ...

    def next_release_number(self, workspace_id: UUID) -> int: ...

    def add_release(self, release: MenuRelease) -> None: ...

    def get_release(self, workspace_id: UUID, release_id: UUID) -> MenuRelease | None: ...

    def list_releases(self, workspace_id: UUID) -> tuple[MenuRelease, ...]: ...

    def save_release(self, release: MenuRelease) -> None: ...

    def get_current_release_id(self, workspace_id: UUID) -> UUID | None: ...

    def set_current_release(
        self,
        workspace_id: UUID,
        release_id: UUID,
        *,
        published_at: datetime,
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


class MenuReleaseUnitOfWork(Protocol):
    @property
    def menus(self) -> MenuReleaseRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> MenuReleaseUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
