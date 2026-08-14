"""装载并校验页面、接口、菜单和权限码统一资源注册表。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from uuid import UUID

from ai_platform_api.modules.authorization.domain.resources import (
    ApiAccessLevel,
    ApiResource,
    HttpMethod,
    Menu,
    MenuActionType,
    MenuApiBinding,
    MenuSource,
    MenuType,
    PageAccessLevel,
    PageResource,
    Permission,
    PermissionScope,
    ResourceRegistry,
    ResourceStatus,
    RiskLevel,
)


def load_resource_registry(path: Path) -> ResourceRegistry:
    """从版本化资源文件加载菜单、页面、接口和权限绑定。"""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取权限资源注册表: {path}") from error
    return resource_registry_from_dict(_object(document, "registry"))


def resource_registry_from_dict(document: dict[str, object]) -> ResourceRegistry:
    """将已解析配置转换为资源注册表并执行跨引用完整性校验。"""

    permissions = tuple(
        Permission(
            code=_string(item, "code", location),
            resource_type=_string(item, "resource_type", location),
            action=_string(item, "action", location),
            scope=cast(
                PermissionScope,
                _literal(
                    item,
                    "scope",
                    location,
                    ("platform", "account", "workspace", "resource", "field"),
                ),
            ),
            status=_status(item, location),
        )
        for index, value in enumerate(_array(document, "permissions", "registry"))
        for location, item in [(f"permissions[{index}]", _object(value, f"permissions[{index}]"))]
    )
    pages = tuple(
        PageResource(
            page_resource_id=_uuid(item, "page_resource_id", location),
            page_key=_string(item, "page_key", location),
            route=_string(item, "route", location),
            component_key=_string(item, "component_key", location),
            layout_key=_string(item, "layout_key", location),
            access_level=cast(
                PageAccessLevel,
                _literal(
                    item,
                    "access_level",
                    location,
                    ("public", "authenticated", "authorized", "platform_admin"),
                ),
            ),
            permission_code=_optional_string(item, "permission_code", location),
            resource_version=_integer(item, "resource_version", location),
            status=_status(item, location),
        )
        for index, value in enumerate(_array(document, "page_resources", "registry"))
        for location, item in [
            (f"page_resources[{index}]", _object(value, f"page_resources[{index}]"))
        ]
    )
    apis = tuple(
        ApiResource(
            api_resource_id=_uuid(item, "api_resource_id", location),
            api_key=_string(item, "api_key", location),
            operation_id=_string(item, "operation_id", location),
            method=cast(
                HttpMethod,
                _literal(
                    item,
                    "method",
                    location,
                    ("DELETE", "GET", "PATCH", "POST", "PUT"),
                ),
            ),
            path_pattern=_string(item, "path_pattern", location),
            access_level=cast(
                ApiAccessLevel,
                _literal(
                    item,
                    "access_level",
                    location,
                    ("public", "authenticated", "authorized", "platform_admin"),
                ),
            ),
            permission_code=_optional_string(item, "permission_code", location),
            risk_level=cast(
                RiskLevel,
                _literal(
                    item,
                    "risk_level",
                    location,
                    ("low", "normal", "high", "critical"),
                ),
            ),
            status=_status(item, location),
        )
        for index, value in enumerate(_array(document, "api_resources", "registry"))
        for location, item in [
            (f"api_resources[{index}]", _object(value, f"api_resources[{index}]"))
        ]
    )
    menus = tuple(
        Menu(
            menu_id=_uuid(item, "menu_id", location),
            menu_key=_string(item, "menu_key", location),
            parent_menu_key=_optional_string(item, "parent_menu_key", location),
            name=_string(item, "name", location),
            menu_type=cast(
                MenuType,
                _literal(
                    item,
                    "menu_type",
                    location,
                    ("directory", "page", "action"),
                ),
            ),
            page_resource_id=_optional_uuid(item, "page_resource_id", location),
            permission_code=_optional_string(item, "permission_code", location),
            icon_key=_optional_string(item, "icon_key", location),
            sort_order=_integer(item, "sort_order", location),
            source=cast(
                MenuSource,
                _literal(
                    item,
                    "source",
                    location,
                    ("system", "workspace"),
                ),
            ),
            status=_status(item, location),
        )
        for index, value in enumerate(_array(document, "menus", "registry"))
        for location, item in [(f"menus[{index}]", _object(value, f"menus[{index}]"))]
    )
    menu_api_bindings = tuple(
        MenuApiBinding(
            menu_id=_uuid(item, "menu_id", location),
            api_resource_id=_uuid(item, "api_resource_id", location),
            action_type=cast(
                MenuActionType,
                _literal(
                    item,
                    "action_type",
                    location,
                    ("query", "mutation", "publish", "approve"),
                ),
            ),
        )
        for index, value in enumerate(_array(document, "menu_api_bindings", "registry"))
        for location, item in [
            (
                f"menu_api_bindings[{index}]",
                _object(value, f"menu_api_bindings[{index}]"),
            )
        ]
    )
    registry = ResourceRegistry(
        schema_version=_integer(document, "schema_version", "registry"),
        registry_version=_integer(document, "registry_version", "registry"),
        permissions=permissions,
        page_resources=pages,
        api_resources=apis,
        menus=menus,
        menu_api_bindings=menu_api_bindings,
    )
    registry.assert_valid()
    return registry


def _object(value: object, location: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{location}: 必须是对象")
    return cast(dict[str, object], value)


def _array(document: dict[str, object], key: str, location: str) -> list[object]:
    value = document.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{location}.{key}: 必须是数组")
    return cast(list[object], value)


def _string(document: dict[str, object], key: str, location: str) -> str:
    value = document.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{location}.{key}: 必须是字符串")
    return value


def _optional_string(document: dict[str, object], key: str, location: str) -> str | None:
    value = document.get(key)
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"{location}.{key}: 必须是字符串或 null")


def _integer(document: dict[str, object], key: str, location: str) -> int:
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{location}.{key}: 必须是整数")
    return value


def _uuid(document: dict[str, object], key: str, location: str) -> UUID:
    try:
        return UUID(_string(document, key, location))
    except ValueError as error:
        raise ValueError(f"{location}.{key}: 必须是 UUID") from error


def _optional_uuid(document: dict[str, object], key: str, location: str) -> UUID | None:
    value = _optional_string(document, key, location)
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError as error:
        raise ValueError(f"{location}.{key}: 必须是 UUID 或 null") from error


def _status(document: dict[str, object], location: str) -> ResourceStatus:
    return cast(
        ResourceStatus,
        _literal(
            document,
            "status",
            location,
            ("active", "disabled"),
        ),
    )


def _literal(
    document: dict[str, object],
    key: str,
    location: str,
    allowed: tuple[str, ...],
) -> str:
    value = _string(document, key, location)
    if value not in allowed:
        raise ValueError(f"{location}.{key}: 不支持的值 {value}")
    return value
