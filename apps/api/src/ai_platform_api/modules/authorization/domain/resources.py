"""定义菜单、页面、接口和权限码注册契约及完整性约束。"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

PermissionScope = Literal["platform", "account", "workspace", "resource", "field"]
ResourceStatus = Literal["active", "disabled"]
PageAccessLevel = Literal["public", "authenticated", "authorized", "platform_admin"]
ApiAccessLevel = Literal["public", "authenticated", "authorized", "platform_admin"]
MenuType = Literal["directory", "page", "action"]
MenuSource = Literal["system", "workspace"]
HttpMethod = Literal["DELETE", "GET", "PATCH", "POST", "PUT"]
RiskLevel = Literal["low", "normal", "high", "critical"]
MenuActionType = Literal["query", "mutation", "publish", "approve"]

PERMISSION_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){2,}$")
REGISTRY_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
COMPONENT_KEY_PATTERN = re.compile(r"^[A-Z][A-Za-z0-9]*$")


@dataclass(frozen=True)
class Permission:
    """把稳定权限码绑定到资源类型、动作和启用状态。"""

    code: str
    resource_type: str
    action: str
    scope: PermissionScope
    status: ResourceStatus


@dataclass(frozen=True)
class PageResource:
    """描述受菜单和权限共同控制的前端页面资源。"""

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
    """描述后端接口的 Method、路径模板、资源类型和权限码。"""

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
    """描述平台注册菜单的层级、页面绑定、权限码和来源。"""

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
class MenuApiBinding:
    """把菜单动作绑定到后端 API，确保页面入口和接口权限使用同一注册事实。"""

    menu_id: UUID
    api_resource_id: UUID
    action_type: MenuActionType


@dataclass(frozen=True)
class ResourceRegistry:
    """汇总权限、页面、API、菜单及其绑定，并提供跨引用完整性校验。"""

    schema_version: int
    registry_version: int
    permissions: tuple[Permission, ...]
    page_resources: tuple[PageResource, ...]
    api_resources: tuple[ApiResource, ...]
    menus: tuple[Menu, ...]
    menu_api_bindings: tuple[MenuApiBinding, ...]

    @property
    def platform_menu_ids(self) -> frozenset[UUID]:
        platform_page_ids = {
            item.page_resource_id
            for item in self.page_resources
            if item.access_level == "platform_admin"
        }
        selected = {
            item.menu_id for item in self.menus if item.page_resource_id in platform_page_ids
        }
        # 平台目录与动作都由页面关系推导，避免新增一个可被工作空间伪造的 scope 字段。
        while True:
            selected_keys = {item.menu_key for item in self.menus if item.menu_id in selected}
            parent_keys = {
                item.parent_menu_key
                for item in self.menus
                if item.menu_id in selected and item.parent_menu_key is not None
            }
            expanded = selected | {
                item.menu_id
                for item in self.menus
                if item.menu_key in parent_keys or item.parent_menu_key in selected_keys
            }
            if expanded == selected:
                return frozenset(selected)
            selected = expanded

    @property
    def workspace_menus(self) -> tuple[Menu, ...]:
        platform_ids = self.platform_menu_ids
        return tuple(item for item in self.menus if item.menu_id not in platform_ids)

    @property
    def workspace_menu_api_bindings(self) -> tuple[MenuApiBinding, ...]:
        platform_ids = self.platform_menu_ids
        return tuple(item for item in self.menu_api_bindings if item.menu_id not in platform_ids)

    def violations(self) -> tuple[str, ...]:
        # 长函数保留原因: 校验器需要一次聚合全部问题，才能给发布者返回完整而稳定的问题清单。
        violations: list[str] = []
        # 1. 建立各资源唯一索引并验证注册表版本，为后续跨引用检查提供确定查找结果。
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
        api_by_id = _unique_index(
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

        # 2. 校验 Permission、页面和接口自身格式及访问级别，不在此阶段判断菜单引用。
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

        # 3. 校验菜单父子关系、页面和权限绑定，并收集已经被导航消费的资源。
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
                elif (
                    menu.menu_type == "page"
                    and menu_by_key[menu.parent_menu_key].menu_type != "directory"
                ):
                    violations.append(f"{location}: 页面菜单只能放在目录下")
                elif (
                    menu.menu_type == "action"
                    and menu_by_key[menu.parent_menu_key].menu_type != "page"
                ):
                    violations.append(f"{location}: 动作菜单只能放在页面下")
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
                if bound_page is not None:
                    if bound_page.access_level == "authorized" and menu.permission_code is None:
                        violations.append(f"{location}: 工作空间页面菜单必须绑定 permission_code")
                    if (
                        bound_page.access_level == "platform_admin"
                        and menu.permission_code is not None
                    ):
                        violations.append(f"{location}: 平台管理员页面菜单不能绑定工作空间权限")
            elif menu.page_resource_id is not None:
                violations.append(f"{location}: 动作菜单不能直接绑定页面")
            elif menu.menu_type == "action" and menu.parent_menu_key is None:
                violations.append(f"{location}: 动作菜单必须绑定所属页面")
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

        # 4. 校验动作菜单与接口的一一权限边界，区分平台级与工作空间级资源。
        referenced_action_menus: set[UUID] = set()
        referenced_protected_apis: set[UUID] = set()
        seen_bindings: set[tuple[UUID, UUID]] = set()
        for binding in self.menu_api_bindings:
            location = f"menu_api_bindings[{binding.menu_id}:{binding.api_resource_id}]"
            binding_key = (binding.menu_id, binding.api_resource_id)
            if binding_key in seen_bindings:
                violations.append(f"{location}: 菜单接口绑定重复")
            seen_bindings.add(binding_key)
            bound_menu = next(
                (item for item in self.menus if item.menu_id == binding.menu_id),
                None,
            )
            bound_api = api_by_id.get(binding.api_resource_id)
            if bound_menu is None or bound_menu.menu_type != "action":
                violations.append(f"{location}: 必须引用已注册动作菜单")
            else:
                referenced_action_menus.add(bound_menu.menu_id)
            if bound_api is None:
                violations.append(f"{location}: api_resource_id 指向未注册接口")
            elif bound_api.access_level not in {"authorized", "platform_admin"}:
                violations.append(f"{location}: 只允许绑定后端受保护接口")
            else:
                referenced_protected_apis.add(bound_api.api_resource_id)
            if bound_menu is not None and bound_api is not None:
                if bound_menu.status != "active" or bound_api.status != "active":
                    violations.append(f"{location}: 启用绑定不能引用停用资源")
                if bound_menu.permission_code != bound_api.permission_code:
                    violations.append(f"{location}: 动作菜单与接口必须使用同一 permission_code")
                if (bound_menu.menu_id in self.platform_menu_ids) != (
                    bound_api.access_level == "platform_admin"
                ):
                    violations.append(f"{location}: 平台菜单与接口访问级别不一致")
                if binding.action_type == "query" and bound_api.method != "GET":
                    violations.append(f"{location}: query 绑定只能引用 GET 接口")
                if binding.action_type != "query" and bound_api.method == "GET":
                    violations.append(f"{location}: GET 接口只能使用 query 绑定")

        for menu in self.menus:
            if (
                menu.status == "active"
                and menu.menu_type == "action"
                and menu.menu_id not in referenced_action_menus
            ):
                violations.append(f"menus[{menu.menu_key}]: 动作菜单未绑定任何接口")
        for api in self.api_resources:
            if (
                api.status == "active"
                and api.access_level in {"authorized", "platform_admin"}
                and api.api_resource_id not in referenced_protected_apis
            ):
                violations.append(f"api_resources[{api.api_key}]: 受保护接口未绑定任何动作菜单")

        # 5. 最后检查授权页面、受保护接口和启用 Permission 是否存在未绑定孤岛。
        for page in self.page_resources:
            if (
                page.status == "active"
                and page.access_level in {"authorized", "platform_admin"}
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
    """表示资源注册表无效错误，由协议层映射为稳定错误码。"""

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
        violations.append(
            f"{location}: public/authenticated/platform_admin 资源不能绑定 permission_code"
        )


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
