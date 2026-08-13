from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from ai_platform_api.modules.authorization.domain.menus import (
    MenuReleaseSnapshot,
    MenuSnapshotItem,
    RoleMenuVisibility,
)
from ai_platform_api.modules.authorization.domain.resources import (
    MenuActionType,
    MenuSource,
    MenuType,
    ResourceStatus,
)


def menu_snapshot_to_dict(snapshot: MenuReleaseSnapshot) -> dict[str, object]:
    return {
        "schema_version": snapshot.schema_version,
        "registry_version": snapshot.registry_version,
        "workspace_id": str(snapshot.workspace_id),
        "menu_version": snapshot.menu_version,
        "menus": [
            {
                "menu_id": str(item.menu_id),
                "menu_key": item.menu_key,
                "parent_menu_id": str(item.parent_menu_id) if item.parent_menu_id else None,
                "name": item.name,
                "menu_type": item.menu_type,
                "page_resource_id": (str(item.page_resource_id) if item.page_resource_id else None),
                "permission_code": item.permission_code,
                "icon_key": item.icon_key,
                "sort_order": item.sort_order,
                "source": item.source,
                "status": item.status,
                "visible": item.visible,
            }
            for item in snapshot.menus
        ],
        "role_menus": [
            {
                "workspace_id": str(item.workspace_id),
                "role_id": str(item.role_id),
                "menu_id": str(item.menu_id),
                "visible": item.visible,
            }
            for item in snapshot.role_menus
        ],
        "menu_api_bindings": [
            {
                "menu_id": str(menu_id),
                "api_resource_id": str(api_resource_id),
                "action_type": action_type,
            }
            for menu_id, api_resource_id, action_type in snapshot.menu_api_bindings
        ],
    }


def menu_snapshot_from_dict(document: dict[str, Any]) -> MenuReleaseSnapshot:
    return MenuReleaseSnapshot(
        int(document["schema_version"]),
        int(document["registry_version"]),
        UUID(document["workspace_id"]),
        int(document["menu_version"]),
        tuple(
            MenuSnapshotItem(
                UUID(item["menu_id"]),
                item["menu_key"],
                UUID(item["parent_menu_id"]) if item["parent_menu_id"] else None,
                item["name"],
                cast("MenuType", item["menu_type"]),
                UUID(item["page_resource_id"]) if item["page_resource_id"] else None,
                item["permission_code"],
                item["icon_key"],
                int(item["sort_order"]),
                cast("MenuSource", item["source"]),
                cast("ResourceStatus", item["status"]),
                bool(item["visible"]),
            )
            for raw in document["menus"]
            for item in [cast(dict[str, Any], raw)]
        ),
        tuple(
            RoleMenuVisibility(
                UUID(item["workspace_id"]),
                UUID(item["role_id"]),
                UUID(item["menu_id"]),
                bool(item["visible"]),
            )
            for raw in document["role_menus"]
            for item in [cast(dict[str, Any], raw)]
        ),
        tuple(
            (
                UUID(item["menu_id"]),
                UUID(item["api_resource_id"]),
                cast("MenuActionType", item["action_type"]),
            )
            for raw in document["menu_api_bindings"]
            for item in [cast(dict[str, Any], raw)]
        ),
    )
