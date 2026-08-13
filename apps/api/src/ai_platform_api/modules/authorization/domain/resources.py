from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

PermissionScope = Literal["platform", "account", "workspace", "resource", "field"]
ResourceStatus = Literal["active", "disabled"]
PageAccessLevel = Literal["public", "authenticated", "authorized"]
ApiAccessLevel = Literal["public", "authenticated", "authorized"]
MenuType = Literal["directory", "page", "action"]
MenuSource = Literal["system", "workspace"]
HttpMethod = Literal["DELETE", "GET", "PATCH", "POST", "PUT"]
RiskLevel = Literal["low", "normal", "high", "critical"]

PERMISSION_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){2,}$")
REGISTRY_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
COMPONENT_KEY_PATTERN = re.compile(r"^[A-Z][A-Za-z0-9]*$")


@dataclass(frozen=True)
class Permission:
    code: str
    resource_type: str
    action: str
    scope: PermissionScope
    status: ResourceStatus


@dataclass(frozen=True)
class PageResource:
    page_resource_id: UUID
    page_key: str
    route: str
    component_key: str
    layout_key: str
    access_level: PageAccessLevel
    permission_code: str | None
    resource_version: int
    status: ResourceStatus


@dataclass(frozen=True)
class ApiResource:
    api_resource_id: UUID
    api_key: str
    operation_id: str
    method: HttpMethod
    path_pattern: str
    access_level: ApiAccessLevel
    permission_code: str | None
    risk_level: RiskLevel
    status: ResourceStatus


@dataclass(frozen=True)
class Menu:
    menu_id: UUID
    menu_key: str
    parent_menu_key: str | None
    name: str
    menu_type: MenuType
    page_resource_id: UUID | None
    permission_code: str | None
    icon_key: str | None
    sort_order: int
    source: MenuSource
    status: ResourceStatus


@dataclass(frozen=True)
class ResourceRegistry:
    schema_version: int
    registry_version: int
    permissions: tuple[Permission, ...]
    page_resources: tuple[PageResource, ...]
    api_resources: tuple[ApiResource, ...]
    menus: tuple[Menu, ...]

    def violations(self) -> tuple[str, ...]:
        violations: list[str] = []
        if self.schema_version != 1:
            violations.append("schema_version: 当前只支持版本 1")
        if self.registry_version < 1:
            violations.append("registry_version: 必须大于等于 1")

        permission_by_code = _unique_index(
            self.permissions,
            lambda item: item.code,
            "permissions.code",
            violations,
        )
        page_by_id = _unique_index(
            self.page_resources,
            lambda item: item.page_resource_id,
            "page_resources.page_resource_id",
            violations,
        )
        _unique_index(
            self.page_resources,
            lambda item: item.page_key,
            "page_resources.page_key",
            violations,
        )
        _unique_index(
            self.page_resources,
            lambda item: item.route,
            "page_resources.route",
            violations,
        )
        _unique_index(
            self.api_resources,
            lambda item: item.api_resource_id,
            "api_resources.api_resource_id",
            violations,
        )
        _unique_index(
            self.api_resources,
            lambda item: item.api_key,
            "api_resources.api_key",
            violations,
        )
        _unique_index(
            self.api_resources,
            lambda item: item.operation_id,
            "api_resources.operation_id",
            violations,
        )
        _unique_index(
            self.api_resources,
            lambda item: (item.method, item.path_pattern),
            "api_resources.method_path",
            violations,
        )
        menu_by_key = _unique_index(
            self.menus,
            lambda item: item.menu_key,
            "menus.menu_key",
            violations,
        )
        _unique_index(
            self.menus,
            lambda item: item.menu_id,
            "menus.menu_id",
            violations,
        )

        for permission in self.permissions:
            if not PERMISSION_CODE_PATTERN.fullmatch(permission.code):
                violations.append(f"permissions[{permission.code}]: permission_code 格式非法")
            if not permission.resource_type or not permission.action:
                violations.append(f"permissions[{permission.code}]: 资源类型和动作不能为空")

        for page in self.page_resources:
            location = f"page_resources[{page.page_key}]"
            if not REGISTRY_KEY_PATTERN.fullmatch(page.page_key):
                violations.append(f"{location}: page_key 格式非法")
            if not page.route.startswith("/") or "{" in page.route or "}" in page.route:
                violations.append(f"{location}: route 必须是固定的绝对前端路由")
            if not COMPONENT_KEY_PATTERN.fullmatch(page.component_key):
                violations.append(f"{location}: component_key 格式非法")
            if not REGISTRY_KEY_PATTERN.fullmatch(page.layout_key):
                violations.append(f"{location}: layout_key 格式非法")
            if page.resource_version < 1:
                violations.append(f"{location}: resource_version 必须大于等于 1")
            _validate_access_permission(
                location,
                page.access_level,
                page.permission_code,
                permission_by_code,
                violations,
            )

        for api in self.api_resources:
            location = f"api_resources[{api.api_key}]"
            if not REGISTRY_KEY_PATTERN.fullmatch(api.api_key):
                violations.append(f"{location}: api_key 格式非法")
            if not api.operation_id or not api.path_pattern.startswith("/api/v1/"):
                violations.append(f"{location}: operation_id 和 V1 绝对接口路径不能为空")
            _validate_access_permission(
                location,
                api.access_level,
                api.permission_code,
                permission_by_code,
                violations,
            )

        referenced_pages: set[UUID] = set()
        referenced_permissions: set[str] = set()
        for menu in self.menus:
            location = f"menus[{menu.menu_key}]"
            if not REGISTRY_KEY_PATTERN.fullmatch(menu.menu_key):
                violations.append(f"{location}: menu_key 格式非法")
            if menu.parent_menu_key is not None:
                if menu.parent_menu_key == menu.menu_key:
                    violations.append(f"{location}: 菜单不能以自身为父节点")
                elif menu.parent_menu_key not in menu_by_key:
                    violations.append(f"{location}: parent_menu_key 指向未注册菜单")
            if not menu.name.strip():
                violations.append(f"{location}: name 不能为空")
            if menu.sort_order < 0:
                violations.append(f"{location}: sort_order 不能为负数")
            if menu.menu_type == "directory":
                if menu.page_resource_id is not None or menu.permission_code is not None:
                    violations.append(f"{location}: 目录不能绑定页面或权限")
            elif menu.menu_type == "page":
                bound_page = (
                    page_by_id.get(menu.page_resource_id)
                    if menu.page_resource_id is not None
                    else None
                )
                if bound_page is None:
                    violations.append(f"{location}: page_resource_id 指向未注册页面")
                else:
                    referenced_pages.add(bound_page.page_resource_id)
                    if menu.status == "active" and bound_page.status != "active":
                        violations.append(f"{location}: 启用菜单不能引用停用页面")
                    if bound_page.permission_code != menu.permission_code:
                        violations.append(f"{location}: 菜单与页面必须引用同一 permission_code")
                if menu.permission_code is None:
                    violations.append(f"{location}: 页面菜单必须绑定 permission_code")
            elif menu.page_resource_id is not None:
                violations.append(f"{location}: 动作菜单不能直接绑定页面")
            if menu.permission_code is not None:
                referenced_permissions.add(menu.permission_code)
                if menu.permission_code not in permission_by_code:
                    violations.append(f"{location}: permission_code 指向未注册权限")
                elif (
                    menu.status == "active"
                    and permission_by_code[menu.permission_code].status != "active"
                ):
                    violations.append(f"{location}: 启用菜单不能引用停用权限")

        _append_menu_cycle_violations(menu_by_key, violations)

        for page in self.page_resources:
            if (
                page.status == "active"
                and page.access_level == "authorized"
                and page.page_resource_id not in referenced_pages
            ):
                violations.append(f"page_resources[{page.page_key}]: 授权页面未绑定任何菜单")
            if page.permission_code is not None:
                referenced_permissions.add(page.permission_code)
        for api in self.api_resources:
            if api.permission_code is not None:
                referenced_permissions.add(api.permission_code)
        for permission in self.permissions:
            if permission.status == "active" and permission.code not in referenced_permissions:
                violations.append(f"permissions[{permission.code}]: 权限未绑定页面、菜单或接口")
        return tuple(sorted(set(violations)))

    def assert_valid(self) -> None:
        violations = self.violations()
        if violations:
            raise ResourceRegistryInvalidError(violations)


class ResourceRegistryInvalidError(Exception):
    def __init__(self, violations: tuple[str, ...]) -> None:
        self.violations = violations
        super().__init__("; ".join(violations))


def _unique_index[T, K](
    values: tuple[T, ...],
    key: Callable[[T], K],
    label: str,
    violations: list[str],
) -> dict[K, T]:
    result: dict[K, T] = {}
    for value in values:
        item_key = key(value)
        if item_key in result:
            violations.append(f"{label}: 存在重复值 {item_key}")
        else:
            result[item_key] = value
    return result


def _validate_access_permission(
    location: str,
    access_level: PageAccessLevel | ApiAccessLevel,
    permission_code: str | None,
    permission_by_code: dict[str, Permission],
    violations: list[str],
) -> None:
    if access_level == "authorized":
        if permission_code is None:
            violations.append(f"{location}: authorized 资源必须绑定 permission_code")
        elif permission_code not in permission_by_code:
            violations.append(f"{location}: permission_code 指向未注册权限")
        elif permission_by_code[permission_code].status != "active":
            violations.append(f"{location}: authorized 资源不能引用停用权限")
    elif permission_code is not None:
        violations.append(f"{location}: public/authenticated 资源不能绑定 permission_code")


def _append_menu_cycle_violations(
    menu_by_key: dict[str, Menu],
    violations: list[str],
) -> None:
    for menu in menu_by_key.values():
        visited: set[str] = set()
        current: Menu | None = menu
        while current is not None and current.parent_menu_key is not None:
            if current.menu_key in visited:
                violations.append(f"menus[{menu.menu_key}]: 菜单父子关系存在循环")
                break
            visited.add(current.menu_key)
            current = menu_by_key.get(current.parent_menu_key)
