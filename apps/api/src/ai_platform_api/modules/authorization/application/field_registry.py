"""从冻结资源契约构建字段策略注册表并拒绝不一致配置。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from ai_platform_api.modules.authorization.domain.fields import (
    FieldPolicyRegistry,
    FieldRule,
    SecurityLevel,
)


def load_field_policy_registry(path: Path) -> FieldPolicyRegistry:
    """从版本化配置加载字段策略注册表，配置不合法时启动失败。"""

    # 1. 先把外部 JSON 收敛为确定的根对象和规则数组，结构错误直接阻断启动。
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取字段策略注册表: {path}") from error
    if not isinstance(document, dict):
        raise ValueError("字段策略注册表根节点必须是对象")
    raw_rules = document.get("rules")
    if not isinstance(raw_rules, list):
        raise ValueError("字段策略注册表 rules 必须是数组")
    rules: list[FieldRule] = []
    seen_keys: set[tuple[str, str]] = set()
    # 2. 逐条验证密级、字段键和继承语义，再交给领域注册表执行跨规则校验。
    for value in raw_rules:
        if not isinstance(value, dict):
            raise ValueError("字段策略规则必须是对象")
        level = value.get("security_level")
        if level not in {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"}:
            raise ValueError("字段策略密级无效")
        resource_type = value.get("resource_type")
        field_name = value.get("field_name")
        inherit = value.get("inherit_resource_level", False)
        if not isinstance(resource_type, str) or not isinstance(field_name, str):
            raise ValueError("字段策略资源类型和字段名必须是字符串")
        if not isinstance(inherit, bool):
            raise ValueError("inherit_resource_level 必须是布尔值")
        key = (resource_type, field_name)
        if key in seen_keys:
            raise ValueError("字段策略注册表存在重复字段")
        seen_keys.add(key)
        rules.append(
            FieldRule(
                resource_type=resource_type,
                field_name=field_name,
                security_level=cast(SecurityLevel, level),
                inherit_resource_level=inherit,
            )
        )
    schema_version = document.get("schema_version")
    registry_version = document.get("registry_version")
    if not isinstance(schema_version, int) or not isinstance(registry_version, int):
        raise ValueError("字段策略注册表版本必须是整数")
    registry = FieldPolicyRegistry(schema_version, registry_version, tuple(rules))
    registry.assert_valid()
    return registry
