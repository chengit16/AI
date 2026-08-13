from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

SecurityLevel = Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]

SECURITY_LEVEL_RANK: dict[SecurityLevel, int] = {
    "PUBLIC": 0,
    "INTERNAL": 1,
    "CONFIDENTIAL": 2,
    "RESTRICTED": 3,
}


@dataclass(frozen=True)
class FieldRule:
    resource_type: str
    field_name: str
    security_level: SecurityLevel
    inherit_resource_level: bool = False


@dataclass(frozen=True)
class FieldPolicyRegistry:
    schema_version: int
    registry_version: int
    rules: tuple[FieldRule, ...]

    def assert_valid(self) -> None:
        if self.schema_version != 1 or self.registry_version < 1:
            raise ValueError("字段策略注册表版本无效")
        keys = [(rule.resource_type, rule.field_name) for rule in self.rules]
        if len(keys) != len(set(keys)):
            raise ValueError("字段策略注册表存在重复字段")
        if any(not resource_type or not field_name for resource_type, field_name in keys):
            raise ValueError("字段策略注册表的资源类型和字段名不能为空")

    def fields_for(self, resource_type: str) -> frozenset[str]:
        return frozenset(
            rule.field_name for rule in self.rules if rule.resource_type == resource_type
        )

    def field_mask(
        self,
        resource_type: str,
        maximum_security_level: SecurityLevel,
        attributes: Mapping[str, object],
    ) -> frozenset[str]:
        resource_level = _security_level(attributes.get("security_level"))
        clearance = SECURITY_LEVEL_RANK[maximum_security_level]
        return frozenset(
            rule.field_name
            for rule in self.rules
            if rule.resource_type == resource_type
            and _required_rank(rule, resource_level) > clearance
        )

    def contains_sensitive_fields(self, resource_type: str) -> bool:
        return any(
            rule.resource_type == resource_type
            and SECURITY_LEVEL_RANK[rule.security_level] > SECURITY_LEVEL_RANK["PUBLIC"]
            for rule in self.rules
        )


def _required_rank(rule: FieldRule, resource_level: SecurityLevel | None) -> int:
    rank = SECURITY_LEVEL_RANK[rule.security_level]
    if rule.inherit_resource_level and resource_level is not None:
        return max(rank, SECURITY_LEVEL_RANK[resource_level])
    return rank


def _security_level(value: object) -> SecurityLevel | None:
    if value in SECURITY_LEVEL_RANK:
        return cast(SecurityLevel, value)
    return None
