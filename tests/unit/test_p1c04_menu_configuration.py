"""验证 P1C-04 菜单树覆盖、角色可见性和权限绑定。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.application.menus import (
    MenuConfigurationConflictError,
    MenuConfigurationDeniedError,
    MenuConfigurationService,
    MenuConfigurationValidationError,
)
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.domain.menus import (
    RoleMenuVisibility,
    WorkspaceMenuOverride,
)

ROOT = Path(__file__).parents[2]
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000096")
OTHER_WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000095")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000096")
ROLE_ID = UUID("70000000-0000-4000-8000-000000000096")
DIRECTORY_ID = UUID("82000000-0000-4000-8000-000000000001")
OVERVIEW_ID = UUID("82000000-0000-4000-8000-000000000002")
ACTION_ID = UUID("82000000-0000-4000-8000-000000000101")
TRACE = TraceContext.continue_from("00-9123456789abcdef0123456789abcdef-9123456789abcdef-01")


class FactWriter:
    def __init__(self) -> None:
        self.values: list[object] = []

    def add(self, value: object) -> None:
        self.values.append(value)


class MenuRepository:
    def __init__(self, *, workspace_type: str = "personal") -> None:
        self.workspace_type = workspace_type
        self.menu_version = 1
        self.overrides: tuple[WorkspaceMenuOverride, ...] = ()
        self.role_entries: tuple[RoleMenuVisibility, ...] = ()
        registry = load_resource_registry(
            ROOT / "contracts/authorization/resource-registry.v1.json"
        )
        self.bindings = tuple(
            sorted(
                (item.menu_id, item.api_resource_id, item.action_type)
                for item in registry.workspace_menu_api_bindings
            )
        )

    def get_workspace_access(
        self,
        workspace_id: UUID,
        account_id: UUID,
    ) -> tuple[str, str, str] | None:
        if workspace_id == WORKSPACE_ID and account_id == ACCOUNT_ID:
            return self.workspace_type, "active", "owner"
        return None

    def get_menu_version(self, workspace_id: UUID, *, for_update: bool = False) -> int | None:
        return self.menu_version if workspace_id == WORKSPACE_ID else None

    def bump_menu_version(self, workspace_id: UUID, expected_version: int) -> int:
        assert workspace_id == WORKSPACE_ID and expected_version == self.menu_version
        self.menu_version += 1
        return self.menu_version

    def get_role(self, workspace_id: UUID, role_id: UUID) -> tuple[str, str] | None:
        if workspace_id == WORKSPACE_ID and role_id == ROLE_ID:
            return "synthetic_role", "active"
        return None

    def list_registered_bindings(self) -> tuple[tuple[UUID, UUID, str], ...]:
        return self.bindings

    def list_overrides(self, workspace_id: UUID) -> tuple[WorkspaceMenuOverride, ...]:
        return self.overrides

    def replace_overrides(
        self,
        workspace_id: UUID,
        overrides: tuple[WorkspaceMenuOverride, ...],
    ) -> None:
        self.overrides = overrides

    def list_role_menus(
        self,
        workspace_id: UUID,
        role_id: UUID,
    ) -> tuple[RoleMenuVisibility, ...]:
        return self.role_entries

    def replace_role_menus(
        self,
        workspace_id: UUID,
        role_id: UUID,
        entries: tuple[RoleMenuVisibility, ...],
    ) -> None:
        self.role_entries = entries


@dataclass
class MenuUnitOfWork:
    menus: MenuRepository

    def __post_init__(self) -> None:
        self.audit = FactWriter()
        self.outbox = FactWriter()
        self.committed = False

    def __enter__(self) -> MenuUnitOfWork:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def commit(self) -> None:
        self.committed = True


def context(workspace_id: UUID = WORKSPACE_ID) -> RequestContext:
    return RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=workspace_id,
        trace=TRACE,
        authentication_method="browser_session",
    )


def menu_service(
    *,
    workspace_type: str = "personal",
) -> tuple[MenuConfigurationService, MenuRepository, MenuUnitOfWork]:
    repository = MenuRepository(workspace_type=workspace_type)
    unit_of_work = MenuUnitOfWork(repository)
    registry = load_resource_registry(ROOT / "contracts/authorization/resource-registry.v1.json")
    return MenuConfigurationService(registry, unit_of_work), repository, unit_of_work


@pytest.mark.parametrize("workspace_type", ["personal", "enterprise"])
def test_personal_and_enterprise_owner_can_replace_registered_menu_overrides(
    workspace_type: str,
) -> None:
    service, repository, unit_of_work = menu_service(workspace_type=workspace_type)

    result = service.replace_workspace(
        context(),
        workspace_id=WORKSPACE_ID,
        entries=((OVERVIEW_ID, DIRECTORY_ID, "我的空间", "panel-top", 25, True),),
    )

    assert result.menu_version == 2
    assert result.overrides == repository.overrides
    assert repository.overrides[0].name == "我的空间"
    assert unit_of_work.committed is True
    assert len(unit_of_work.audit.values) == len(unit_of_work.outbox.values) == 1


def test_unknown_resource_and_cycle_are_rejected_before_write() -> None:
    service, repository, unit_of_work = menu_service()

    with pytest.raises(MenuConfigurationValidationError):
        service.replace_workspace(
            context(),
            workspace_id=WORKSPACE_ID,
            entries=((uuid4(), None, "任意路由入口", None, 1, True),),
        )
    with pytest.raises(MenuConfigurationValidationError):
        service.replace_workspace(
            context(),
            workspace_id=WORKSPACE_ID,
            entries=((DIRECTORY_ID, DIRECTORY_ID, "循环目录", None, 1, True),),
        )

    assert repository.overrides == ()
    assert unit_of_work.committed is False


def test_role_visibility_only_accepts_registered_menu_and_is_workspace_scoped() -> None:
    service, repository, _ = menu_service(workspace_type="enterprise")

    entries = service.replace_role(
        context(),
        workspace_id=WORKSPACE_ID,
        role_id=ROLE_ID,
        entries=((ACTION_ID, False),),
    )

    assert entries == repository.role_entries
    assert entries[0].visible is False
    with pytest.raises(MenuConfigurationValidationError):
        service.replace_role(
            context(),
            workspace_id=WORKSPACE_ID,
            role_id=ROLE_ID,
            entries=((uuid4(), True),),
        )
    with pytest.raises(MenuConfigurationDeniedError):
        service.get_workspace(context(OTHER_WORKSPACE_ID), workspace_id=WORKSPACE_ID)


def test_static_registry_and_database_binding_mirror_must_match() -> None:
    service, repository, _ = menu_service()
    repository.bindings = repository.bindings[:-1]

    with pytest.raises(MenuConfigurationConflictError):
        service.get_workspace(context(), workspace_id=WORKSPACE_ID)
