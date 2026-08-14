"""在数据离开责任模块前执行字段级 ABAC 投影和敏感出口裁剪。"""

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
        """按字段策略和授权结果生成最小投影，未注册字段默认拒绝输出。"""

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
        """过滤 HTTP 响应字段，避免服务端已读取的敏感值越过协议边界。"""

        return self.project(resource_type, payload, field_mask)

    def log_attributes(
        self,
        resource_type: str,
        payload: Mapping[str, object],
        field_mask: frozenset[str],
    ) -> dict[str, object]:
        """过滤日志属性，禁止秘密级字段和原始敏感值进入日志。"""

        return self.project(resource_type, payload, field_mask)

    def retrieval_metadata(
        self,
        resource_type: str,
        payload: Mapping[str, object],
        field_mask: frozenset[str],
    ) -> dict[str, object]:
        """过滤检索元数据，防止无权字段通过召回结果侧漏。"""

        return self.project(resource_type, payload, field_mask)

    def model_context(
        self,
        resource_type: str,
        payload: Mapping[str, object],
        field_mask: frozenset[str],
    ) -> str:
        """过滤模型上下文，确保字段级 ABAC 在调用供应商前已执行。"""

        projected = self.project(resource_type, payload, field_mask)
        return json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
