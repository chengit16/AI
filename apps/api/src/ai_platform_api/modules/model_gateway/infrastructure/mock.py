"""提供不联网的确定性模型 Provider 和调用事实记录器。"""

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from ai_platform_api.modules.model_gateway.domain.errors import ProviderInvocationError
from ai_platform_api.modules.model_gateway.domain.models import (
    ModelAttempt,
    ModelRequest,
    ProviderResponse,
    TokenUsage,
)

MockProviderOutcome = Literal[
    "success",
    "timeout",
    "rate_limited",
    "unavailable",
    "authentication",
    "capability_unsupported",
    "content_policy",
    "invalid_request",
    "invalid_response",
]


@dataclass
class MockProvider:
    """不联网的确定性 Provider，用固定结果覆盖网关故障矩阵。"""

    provider_id: str
    outcomes: list[MockProviderOutcome] = field(default_factory=lambda: ["success"])
    response_prefix: str = "Mock 答案"
    calls: list[tuple[UUID, str, int]] = field(default_factory=list)
    _call_count: int = 0

    def invoke(self, request: ModelRequest, model_id: str, timeout_ms: int) -> ProviderResponse:
        self.calls.append((request.invocation_id, model_id, timeout_ms))
        outcome = self.outcomes[min(self._call_count, len(self.outcomes) - 1)]
        self._call_count += 1
        if outcome != "success":
            retryable = outcome in {"timeout", "rate_limited", "unavailable"}
            raise ProviderInvocationError(
                kind=outcome,
                retryable=retryable,
                fallback_allowed=retryable or outcome == "invalid_response",
                provider_request_id=f"mock-{self._call_count:04d}",
            )
        input_tokens = max(1, request.prompt_characters // 4)
        output_tokens = min(request.max_output_tokens, 8)
        return ProviderResponse(
            content=f"{self.response_prefix}: {request.messages[-1].content}",
            finish_reason="stop",
            usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
            provider_request_id=f"mock-{self._call_count:04d}",
        )


@dataclass
class InMemoryUsageRecorder:
    """为领域测试收集模型尝试记录，不参与生产持久化。"""

    records: list[tuple[UUID, UUID, ModelAttempt]] = field(default_factory=list)

    def record(self, invocation_id: UUID, workspace_id: UUID, attempt: ModelAttempt) -> None:
        self.records.append((invocation_id, workspace_id, attempt))
