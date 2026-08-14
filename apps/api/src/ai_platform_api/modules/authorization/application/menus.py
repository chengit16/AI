from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.menus import (
    MenuConfiguration,
    MenuConfigurationRepository,
    MenuConfigurationUnitOfWork,
    MenuConfigurationWriteConflictError,
    RoleMenuVisibility,
    WorkspaceMenuOverride,
)
from ai_platform_api.modules.authorization.domain.resources import Menu, ResourceRegistry
from ai_platform_api.modules.integration.domain.events import IntegrationEvent

ICON_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

__all__ = [
    "MenuConfigurationService",
    "WorkspaceMenuOverride",
]


class MenuConfigurationDeniedError(PlatformError):
    error_code = "POLICY_DENIED"


class MenuConfigurationNotFoundError(PlatformError):
    error_code = "RESOURCE_NOT_FOUND"


class MenuConfigurationValidationError(PlatformError):
    error_code = "VALIDATION_ERROR"


class MenuConfigurationConflictError(PlatformError):
    error_code = "ROLE_CONFLICT"


class MenuConfigurationService:
    """只接受注册资源标识，把树校验、隔离与事务事实隐藏在统一接口后。"""

    def __init__(
        self,
        registry: ResourceRegistry,
        unit_of_work: MenuConfigurationUnitOfWork,
    ) -> None:
        self._registry = registry
        self._unit_of_work = unit_of_work

    def get_workspace(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
    ) -> MenuConfiguration:
        account_id = _browser_account(context, workspace_id)
        with self._unit_of_work as unit_of_work:
            _require_owner(unit_of_work.menus, workspace_id, account_id)
            self._assert_binding_mirror(unit_of_work.menus)
            version = unit_of_work.menus.get_menu_version(workspace_id)
            if version is None:
                raise MenuConfigurationNotFoundError
            return MenuConfiguration(
                workspace_id,
                version,
                unit_of_work.menus.list_overrides(workspace_id),
            )

    def replace_workspace(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        entries: tuple[tuple[UUID, UUID | None, str, str | None, int, bool], ...],
    ) -> MenuConfiguration:
        account_id = _browser_account(context, workspace_id)
        overrides = tuple(
            WorkspaceMenuOverride(
                workspace_id,
                menu_id,
                parent_menu_id,
                name.strip(),
                icon_key,
                sort_order,
                visible,
            )
            for menu_id, parent_menu_id, name, icon_key, sort_order, visible in entries
        )
        self._validate_overrides(overrides)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                _require_owner(unit_of_work.menus, workspace_id, account_id)
                self._assert_binding_mirror(unit_of_work.menus)
                current_version = unit_of_work.menus.get_menu_version(
                    workspace_id,
                    for_update=True,
                )
                if current_version is None:
                    raise MenuConfigurationNotFoundError
                menu_version = unit_of_work.menus.bump_menu_version(
                    workspace_id,
                    current_version,
                )
                unit_of_work.menus.replace_overrides(workspace_id, overrides)
                _record_change(
                    unit_of_work,
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=workspace_id,
                    menu_version=menu_version,
                    action="authorization.menu_configuration.replace",
                    event_type="authorization.menu_configuration.replaced",
                    attributes={"override_count": len(overrides)},
                    occurred_at=now,
                )
                unit_of_work.commit()
        except MenuConfigurationWriteConflictError as error:
            raise MenuConfigurationConflictError from error
        return MenuConfiguration(workspace_id, menu_version, overrides)

    def get_role(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        role_id: UUID,
    ) -> tuple[RoleMenuVisibility, ...]:
        account_id = _browser_account(context, workspace_id)
        with self._unit_of_work as unit_of_work:
            _require_owner(unit_of_work.menus, workspace_id, account_id)
            self._assert_binding_mirror(unit_of_work.menus)
            _require_role(unit_of_work.menus, workspace_id, role_id)
            return unit_of_work.menus.list_role_menus(workspace_id, role_id)

    def replace_role(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        role_id: UUID,
        entries: tuple[tuple[UUID, bool], ...],
    ) -> tuple[RoleMenuVisibility, ...]:
        account_id = _browser_account(context, workspace_id)
        role_menus = tuple(
            RoleMenuVisibility(workspace_id, role_id, menu_id, visible)
            for menu_id, visible in entries
        )
        self._validate_role_menus(role_menus)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                _require_owner(unit_of_work.menus, workspace_id, account_id)
                self._assert_binding_mirror(unit_of_work.menus)
                _require_role(unit_of_work.menus, workspace_id, role_id)
                current_version = unit_of_work.menus.get_menu_version(
                    workspace_id,
                    for_update=True,
                )
                if current_version is None:
                    raise MenuConfigurationNotFoundError
                menu_version = unit_of_work.menus.bump_menu_version(
                    workspace_id,
                    current_version,
                )
                unit_of_work.menus.replace_role_menus(workspace_id, role_id, role_menus)
                _record_change(
                    unit_of_work,
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=role_id,
                    menu_version=menu_version,
                    action="authorization.role_menus.replace",
                    event_type="authorization.role_menus.replaced",
                    attributes={"entry_count": len(role_menus)},
                    occurred_at=now,
                )
                unit_of_work.commit()
        except MenuConfigurationWriteConflictError as error:
            raise MenuConfigurationConflictError from error
        return role_menus

    def _validate_overrides(self, overrides: tuple[WorkspaceMenuOverride, ...]) -> None:
        menu_by_id = {
            item.menu_id: item for item in self._registry.workspace_menus if item.status == "active"
        }
        if len(overrides) > len(menu_by_id) or len({item.menu_id for item in overrides}) != len(
            overrides
        ):
            raise MenuConfigurationValidationError
        override_by_id = {item.menu_id: item for item in overrides}
        for override in overrides:
            menu = menu_by_id.get(override.menu_id)
            parent = menu_by_id.get(override.parent_menu_id) if override.parent_menu_id else None
            if (
                menu is None
                or not override.name
                or len(override.name) > 80
                or override.sort_order < 0
                or (
                    override.icon_key is not None
                    and ICON_KEY_PATTERN.fullmatch(override.icon_key) is None
                )
            ):
                raise MenuConfigurationValidationError
            if override.parent_menu_id is not None and parent is None:
                raise MenuConfigurationValidationError
            if not _valid_parent(menu, parent):
                raise MenuConfigurationValidationError

        # 未覆盖条目仍沿用平台注册父节点，循环检查必须在最终合并树上执行。
        parent_by_id = {
            menu.menu_id: (
                override_by_id[menu.menu_id].parent_menu_id
                if menu.menu_id in override_by_id
                else _parent_id(menu, self._registry.workspace_menus)
            )
            for menu in menu_by_id.values()
        }
        for menu_id in parent_by_id:
            visited: set[UUID] = set()
            current: UUID | None = menu_id
            while current is not None:
                if current in visited:
                    raise MenuConfigurationValidationError
                visited.add(current)
                current = parent_by_id.get(current)

    def _validate_role_menus(self, entries: tuple[RoleMenuVisibility, ...]) -> None:
        registered = {
            item.menu_id for item in self._registry.workspace_menus if item.status == "active"
        }
        menu_ids = {item.menu_id for item in entries}
        if (
            len(entries) > len(registered)
            or len(menu_ids) != len(entries)
            or not menu_ids.issubset(registered)
        ):
            raise MenuConfigurationValidationError

    def _assert_binding_mirror(self, repository: MenuConfigurationRepository) -> None:
        expected = tuple(
            sorted(
                (
                    binding.menu_id,
                    binding.api_resource_id,
                    binding.action_type,
                )
                for binding in self._registry.workspace_menu_api_bindings
            )
        )
        if repository.list_registered_bindings() != expected:
            # 静态契约与数据库镜像不同步时拒绝管理，避免前端可见动作偏离后端事实。
            raise MenuConfigurationConflictError


def _browser_account(context: RequestContext, workspace_id: UUID) -> UUID:
    if (
        context.user_id is None
        or context.authentication_method != "browser_session"
        or context.workspace_id != workspace_id
    ):
        raise MenuConfigurationDeniedError
    return context.user_id


def _require_owner(
    repository: MenuConfigurationRepository,
    workspace_id: UUID,
    account_id: UUID,
) -> None:
    access = repository.get_workspace_access(workspace_id, account_id)
    if access is None or access[1:] != ("active", "owner"):
        raise MenuConfigurationDeniedError


def _require_role(
    repository: MenuConfigurationRepository,
    workspace_id: UUID,
    role_id: UUID,
) -> None:
    role = repository.get_role(workspace_id, role_id)
    if role is None:
        raise MenuConfigurationNotFoundError
    if role[1] != "active":
        raise MenuConfigurationConflictError


def _valid_parent(menu: Menu, parent: Menu | None) -> bool:
    if menu.menu_type == "directory":
        return parent is None or parent.menu_type == "directory"
    if menu.menu_type == "page":
        return parent is not None and parent.menu_type == "directory"
    return parent is not None and parent.menu_type == "page"


def _parent_id(menu: Menu, menus: tuple[Menu, ...]) -> UUID | None:
    if menu.parent_menu_key is None:
        return None
    parent = next((item for item in menus if item.menu_key == menu.parent_menu_key), None)
    return parent.menu_id if parent is not None else None


def _record_change(
    unit_of_work: MenuConfigurationUnitOfWork,
    *,
    context: RequestContext,
    workspace_id: UUID,
    aggregate_id: UUID,
    menu_version: int,
    action: str,
    event_type: str,
    attributes: dict[str, object],
    occurred_at: datetime,
) -> None:
    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type="menu_configuration",
            resource_id=aggregate_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            attributes={**attributes, "menu_version": menu_version},
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type=event_type,
            workspace_id=workspace_id,
            aggregate_id=aggregate_id,
            aggregate_version=menu_version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload={"menu_version": menu_version},
        )
    )
