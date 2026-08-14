from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.domain.resources import (
    Menu,
    MenuApiBinding,
    ResourceRegistry,
    ResourceRegistryInvalidError,
)

from scripts.generate_resource_registry import registry_openapi_violations

ROOT = Path(__file__).parents[2]
REGISTRY_PATH = ROOT / "contracts/authorization/resource-registry.v1.json"


def registry() -> ResourceRegistry:
    return load_resource_registry(REGISTRY_PATH)


def test_frozen_registry_is_valid_and_covers_openapi() -> None:
    resource_registry = registry()

    assert resource_registry.schema_version == 1
    assert resource_registry.registry_version == 7
    assert len(resource_registry.permissions) == 45
    assert len(resource_registry.page_resources) == 5
    assert len(resource_registry.api_resources) == 64
    assert len(resource_registry.menus) == 49
    assert len(resource_registry.menu_api_bindings) == 46
    assert registry_openapi_violations() == ()


def test_registry_rejects_duplicate_dangling_and_unbound_resources() -> None:
    valid = registry()
    duplicate_api = replace(
        valid.api_resources[0],
        api_resource_id=UUID("81000000-0000-4000-8000-000000000099"),
    )
    dangling_page = replace(
        valid.page_resources[1],
        page_resource_id=UUID("80000000-0000-4000-8000-000000000099"),
        page_key="workspace.unbound",
        route="/workspace/unbound",
        component_key="UnboundPage",
    )
    invalid = replace(
        valid,
        api_resources=(*valid.api_resources, duplicate_api),
        page_resources=(*valid.page_resources, dangling_page),
        menus=(
            *valid.menus,
            Menu(
                menu_id=UUID("82000000-0000-4000-8000-000000000099"),
                menu_key="navigation.workspace.dangling",
                parent_menu_key="navigation.missing",
                name="合成悬空菜单",
                menu_type="page",
                page_resource_id=UUID("80000000-0000-4000-8000-000000000098"),
                permission_code="workspace.missing.access",
                icon_key="circle",
                sort_order=900,
                source="system",
                status="active",
            ),
        ),
    )

    with pytest.raises(ResourceRegistryInvalidError) as captured:
        invalid.assert_valid()

    violations = captured.value.violations
    assert any("api_resources.api_key: 存在重复值" in item for item in violations)
    assert any("授权页面未绑定任何菜单" in item for item in violations)
    assert any("parent_menu_key 指向未注册菜单" in item for item in violations)
    assert any("page_resource_id 指向未注册页面" in item for item in violations)
    assert any("permission_code 指向未注册权限" in item for item in violations)


def test_registry_rejects_menu_cycle_and_access_level_bypass() -> None:
    valid = registry()
    first = replace(valid.menus[1], parent_menu_key=valid.menus[2].menu_key)
    second = replace(valid.menus[2], parent_menu_key=valid.menus[1].menu_key)
    public_with_permission = replace(
        valid.page_resources[0],
        permission_code="workspace.overview.access",
    )
    invalid = replace(
        valid,
        page_resources=(public_with_permission, *valid.page_resources[1:]),
        menus=(valid.menus[0], first, second, *valid.menus[3:]),
    )

    violations = invalid.violations()

    assert any("菜单父子关系存在循环" in item for item in violations)
    assert any("public/authenticated 资源不能绑定 permission_code" in item for item in violations)


def test_registry_rejects_active_resources_that_reference_disabled_entries() -> None:
    valid = registry()
    disabled_permission = replace(valid.permissions[0], status="disabled")
    disabled_page = replace(valid.page_resources[2], status="disabled")
    invalid = replace(
        valid,
        permissions=(disabled_permission, *valid.permissions[1:]),
        page_resources=(
            valid.page_resources[0],
            valid.page_resources[1],
            disabled_page,
            *valid.page_resources[3:],
        ),
    )

    violations = invalid.violations()

    assert any("authorized 资源不能引用停用权限" in item for item in violations)
    assert any("启用菜单不能引用停用权限" in item for item in violations)
    assert any("启用菜单不能引用停用页面" in item for item in violations)


def test_registry_rejects_menu_api_permission_mismatch_and_unbound_api() -> None:
    valid = registry()
    mismatched = replace(
        valid.menu_api_bindings[0],
        api_resource_id=valid.api_resources[13].api_resource_id,
    )
    invalid = replace(
        valid,
        menu_api_bindings=(
            mismatched,
            *valid.menu_api_bindings[1:-1],
            MenuApiBinding(
                valid.menu_api_bindings[-1].menu_id,
                UUID("81000000-0000-4000-8000-000000000099"),
                "mutation",
            ),
        ),
    )

    violations = invalid.violations()

    assert any("动作菜单与接口必须使用同一 permission_code" in item for item in violations)
    assert any("api_resource_id 指向未注册接口" in item for item in violations)
    assert any("授权接口未绑定任何动作菜单" in item for item in violations)
