from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from ai_platform_api.modules.authorization.domain.fields import FieldPolicyRegistry


@dataclass(frozen=True)
class FieldProjectionService:
    """所有数据出口复用同一投影语义，受限值不会先进入下游再尝试清洗。"""

    registry: FieldPolicyRegistry

    def project(
        self,
        resource_type: str,
        payload: Mapping[str, object],
        field_mask: frozenset[str],
    ) -> dict[str, object]:
        registered = self.registry.fields_for(resource_type)
        if not registered:
            # 未注册资源没有可证明安全的字段，数据出口必须失败关闭。
            return {}
        return {
            key: value
            for key, value in payload.items()
            if key not in field_mask and key in registered
        }

    def response(
        self,
        resource_type: str,
        payload: Mapping[str, object],
        field_mask: frozenset[str],
    ) -> dict[str, object]:
        return self.project(resource_type, payload, field_mask)

    def log_attributes(
        self,
        resource_type: str,
        payload: Mapping[str, object],
        field_mask: frozenset[str],
    ) -> dict[str, object]:
        return self.project(resource_type, payload, field_mask)

    def retrieval_metadata(
        self,
        resource_type: str,
        payload: Mapping[str, object],
        field_mask: frozenset[str],
    ) -> dict[str, object]:
        return self.project(resource_type, payload, field_mask)

    def model_context(
        self,
        resource_type: str,
        payload: Mapping[str, object],
        field_mask: frozenset[str],
    ) -> str:
        projected = self.project(resource_type, payload, field_mask)
        return json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
