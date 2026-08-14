"""定义阶段 0 模型网关请求、路由、尝试、用量和 Provider 端口。"""

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
    """保存模型角色和文本内容，禁止在此携带未投影业务对象。"""

    role: Literal["system", "user", "assistant"]
    content: str

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("模型消息内容不能为空")


@dataclass(frozen=True)
class ModelRequest:
    """定义模型操作的请求字段与协议校验边界。"""

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
    """记录供应商返回的输入和输出 Token 数量。"""

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
    """定义供应商操作的稳定响应结构。"""

    content: str
    finish_reason: str
    usage: TokenUsage | None
    provider_request_id: str | None = None


@dataclass(frozen=True)
class ModelRoute:
    """定义供应商、模型、位置、能力、成本和活动状态的候选路由。"""

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
    """限制请求、响应、单次/总超时、重试、熔断、成本和规则降级。"""

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
    """记录单次路由尝试的状态、耗时、失败类型、Usage 和估算成本。"""

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
    """返回模型文本、结束原因、实际路由、成本、降级状态和完整尝试链。"""

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
    """以模型标识和超时调用供应商，并把错误分类为可重试、可降级或失败关闭。"""

    provider_id: str

    def invoke(self, request: ModelRequest, model_id: str, timeout_ms: int) -> ProviderResponse: ...


class ModelUsageRecorder(Protocol):
    """按调用、空间和尝试记录模型用量，重复写入必须保持幂等。"""

    def record(self, invocation_id: UUID, workspace_id: UUID, attempt: ModelAttempt) -> None: ...
