"""建立平台管理员可信上下文并拒绝工作空间角色冒充平台权限。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ai_platform_backend.safety import RagSafetyGate, serialize_for_safety

from ai_platform_api.modules.authorization.application.fields import FieldProjectionService
from ai_platform_api.modules.model_gateway.domain.errors import ModelContextSafetyDeniedError
from ai_platform_api.modules.model_gateway.domain.models import ModelMessage


@dataclass(frozen=True)
class AuthorizedModelContextBuilder:
    """在字段投影后构建模型上下文，禁止秘密级字段越过供应商边界。"""

    projection: FieldProjectionService
    safety: RagSafetyGate = field(default_factory=RagSafetyGate)

    def build_message(
        self,
        *,
        resource_type: str,
        payload: Mapping[str, object],
        field_mask: frozenset[str],
    ) -> ModelMessage:
        """模型只接收已投影上下文，Provider 和 Prompt 不能看见被遮罩的原始值。"""

        content = self.projection.model_context(resource_type, payload, field_mask)
        return ModelMessage(role="system", content=content)

    def build_user_message(self, query: str) -> ModelMessage:
        """只把通过入口安全门的规范化用户问题交给模型。"""

        decision = self.safety.inspect_user_query(query)
        if not decision.allowed:
            raise ModelContextSafetyDeniedError
        return ModelMessage(role="user", content=decision.normalized_text)

    def build_untrusted_evidence_message(
        self,
        *,
        resource_type: str,
        payload: Mapping[str, object],
        field_mask: frozenset[str],
    ) -> ModelMessage:
        """先投影字段再检查文档内容，并以固定标签隔离非可信证据。"""

        projected = self.projection.project(resource_type, payload, field_mask)
        wrapped = self.safety.wrap_untrusted_evidence(serialize_for_safety(projected))
        if wrapped is None:
            raise ModelContextSafetyDeniedError
        return ModelMessage(role="user", content=wrapped)
