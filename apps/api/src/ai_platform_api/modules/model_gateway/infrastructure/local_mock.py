"""为显式启用的内置 Provider 提供不联网的确定性模型 Adapter。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from ai_platform_api.modules.model_gateway.domain.configuration import RuntimeProviderAccess
from ai_platform_api.modules.model_gateway.domain.models import (
    ModelProvider,
    ModelRequest,
    ProviderResponse,
    TokenUsage,
)


class ProviderFactory(Protocol):
    """描述真实供应商工厂的最小结构，避免基础设施反向依赖 application 层。"""

    def create(self, access: RuntimeProviderAccess) -> ModelProvider: ...


class LocalMockRuntimeProviderFactory:
    """仅对固定 UUID 与 Key 同时匹配的内置配置改走本地 Adapter。"""

    def __init__(
        self,
        fallback: ProviderFactory,
        *,
        provider_id: UUID,
        provider_key: str,
    ) -> None:
        self._fallback = fallback
        self._provider_id = provider_id
        self._provider_key = provider_key

    def create(self, access: RuntimeProviderAccess) -> ModelProvider:
        configuration = access.configuration
        if (
            configuration.provider_id == self._provider_id
            and configuration.provider_key == self._provider_key
        ):
            return LocalDeterministicModelProvider(str(configuration.provider_id))
        return self._fallback.create(access)


@dataclass
class LocalDeterministicModelProvider:
    """返回不含证据正文的合成答案，使本地页面可验证完整问答与引用流程。"""

    provider_id: str

    def invoke(
        self,
        request: ModelRequest,
        model_id: str,
        timeout_ms: int,
    ) -> ProviderResponse:
        del model_id, timeout_ms
        query = request.messages[-1].content
        content = f"本地 Mock 回答: 已完成对“{query}”的知识检索, 请结合下方来源核对。"
        return ProviderResponse(
            content=content,
            finish_reason="stop",
            usage=TokenUsage(
                input_tokens=max(1, request.prompt_characters // 4),
                output_tokens=max(1, len(content) // 4),
            ),
            provider_request_id=f"local-mock-{str(request.invocation_id)[:8]}",
        )
