from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ai_platform_api.modules.authorization.application.fields import FieldProjectionService
from ai_platform_api.modules.model_gateway.domain.models import ModelMessage


@dataclass(frozen=True)
class AuthorizedModelContextBuilder:
    projection: FieldProjectionService

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
