from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.model_gateway.domain.errors import ProviderFailureKind

ModelCapability = Literal["generation", "streaming", "tools", "structured_output"]
ProviderLocation = Literal["external", "private"]
AttemptStatus = Literal["succeeded", "failed", "circuit_open"]


@dataclass(frozen=True)
class ModelMessage:
    role: Literal["system", "user", "assistant"]
    content: str

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("模型消息内容不能为空")


@dataclass(frozen=True)
class ModelRequest:
    invocation_id: UUID
    workspace_id: UUID
    trace_id: str
    traceparent: str
    task_type: str
    messages: tuple[ModelMessage, ...]
    required_capabilities: frozenset[ModelCapability]
    max_output_tokens: int
    external_data_allowed: bool
    security_level: SecurityLevel = "PUBLIC"

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("模型请求至少包含一条消息")
        if not self.task_type.strip() or len(self.trace_id) < 16:
            raise ValueError("模型任务类型或 Trace 无效")
        if self.max_output_tokens < 1:
            raise ValueError("模型输出 Token 上限必须为正整数")

    @property
    def prompt_characters(self) -> int:
        return sum(len(message.content) for message in self.messages)


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("Token 用量不能为负数")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ProviderResponse:
    content: str
    finish_reason: str
    usage: TokenUsage | None
    provider_request_id: str | None = None


@dataclass(frozen=True)
class ModelRoute:
    route_id: str
    provider_id: str
    model_id: str
    location: ProviderLocation
    capabilities: frozenset[ModelCapability]
    input_price_microunits_per_million_tokens: int
    output_price_microunits_per_million_tokens: int
    active: bool = True

    def __post_init__(self) -> None:
        if not self.route_id or not self.provider_id or not self.model_id:
            raise ValueError("模型路由标识不能为空")
        if "generation" not in self.capabilities:
            raise ValueError("生成模型路由必须声明 generation 能力")
        if (
            min(
                self.input_price_microunits_per_million_tokens,
                self.output_price_microunits_per_million_tokens,
            )
            < 0
        ):
            raise ValueError("模型价格不能为负数")


@dataclass(frozen=True)
class GatewayPolicy:
    attempt_timeout_ms: int
    total_timeout_ms: int
    max_attempts_per_route: int
    max_prompt_characters: int
    max_output_tokens: int
    max_response_characters: int
    circuit_failure_threshold: int
    circuit_recovery_ms: int
    rule_degradation_message: str | None = None
    max_estimated_cost_microunits: int = 50_000_000

    def __post_init__(self) -> None:
        positive_values = (
            self.attempt_timeout_ms,
            self.total_timeout_ms,
            self.max_attempts_per_route,
            self.max_prompt_characters,
            self.max_output_tokens,
            self.max_response_characters,
            self.circuit_failure_threshold,
            self.circuit_recovery_ms,
            self.max_estimated_cost_microunits,
        )
        if min(positive_values) < 1:
            raise ValueError("模型网关预算和熔断参数必须为正整数")
        if self.attempt_timeout_ms > self.total_timeout_ms:
            raise ValueError("单次模型超时不能超过总超时")
        if self.rule_degradation_message is not None and not self.rule_degradation_message.strip():
            raise ValueError("规则降级消息不能为空字符串")


@dataclass(frozen=True)
class ModelAttempt:
    route_id: str
    provider_id: str
    model_id: str
    attempt_no: int
    status: AttemptStatus
    duration_ms: int
    trace_id: str
    traceparent: str
    failure_kind: ProviderFailureKind | None
    provider_request_id: str | None
    usage: TokenUsage | None
    estimated_cost_microunits: int


@dataclass(frozen=True)
class ModelResult:
    content: str
    finish_reason: str
    provider_id: str | None
    model_id: str | None
    usage: TokenUsage
    estimated_cost_microunits: int
    currency: str
    degraded: bool
    degradation_reason: str | None
    attempts: tuple[ModelAttempt, ...]
    runtime_config_version_id: UUID | None = None


class ModelProvider(Protocol):
    provider_id: str

    def invoke(self, request: ModelRequest, model_id: str, timeout_ms: int) -> ProviderResponse: ...


class ModelUsageRecorder(Protocol):
    def record(self, invocation_id: UUID, workspace_id: UUID, attempt: ModelAttempt) -> None: ...
