"""执行模型路由、有限重试、熔断、降级和用量记录。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import monotonic

from ai_platform_api.modules.model_gateway.domain.errors import (
    ModelDataBoundaryDeniedError,
    ModelGatewayUnavailableError,
    ModelRequestRejectedError,
    ModelRouteUnavailableError,
    ProviderFailureKind,
    ProviderInvocationError,
)
from ai_platform_api.modules.model_gateway.domain.models import (
    AttemptStatus,
    GatewayPolicy,
    ModelAttempt,
    ModelProvider,
    ModelRequest,
    ModelResult,
    ModelRoute,
    ModelUsageRecorder,
    ProviderResponse,
    TokenUsage,
)

MICRO_UNITS_PER_MILLION = 1_000_000


@dataclass
class CircuitState:
    """记录连续失败次数和熔断开启时间，成功调用会重置状态。"""

    consecutive_failures: int = 0
    opened_at: float | None = None


class ModelGateway:
    """统一执行模型路由、有限重试、熔断、降级和 Usage 记录。"""

    def __init__(
        self,
        routes: tuple[ModelRoute, ...],
        providers: Mapping[str, ModelProvider],
        policy: GatewayPolicy,
        usage_recorder: ModelUsageRecorder | None = None,
        clock: Callable[[], float] = monotonic,
        circuit_states: dict[str, CircuitState] | None = None,
    ) -> None:
        self._routes = routes
        self._providers = providers
        self._policy = policy
        self._usage_recorder = usage_recorder
        self._clock = clock
        # 运行服务可跨请求复用同一状态表；默认仍保持独立实例，兼容阶段 0 的纯领域测试。
        self._circuits = circuit_states if circuit_states is not None else {}

    def invoke(self, request: ModelRequest) -> ModelResult:
        """按策略筛选路由并执行有限重试、熔断和降级，完整记录每次尝试。"""

        # 长函数保留原因: 总预算、逐路由熔断、重试和尝试审计共享同一调用状态机。
        # 1. 先执行预算与内容边界校验，再按能力、位置和成本筛选候选路由。
        self._validate_request(request)
        routes = self._eligible_routes(request)
        if not routes:
            if not request.external_data_allowed and any(
                route.active and route.location == "external" for route in self._routes
            ):
                raise ModelDataBoundaryDeniedError()
            raise ModelRouteUnavailableError

        # 2. 路由共享总超时预算；每个供应商调用仍受单次超时和熔断状态约束。
        attempts: list[ModelAttempt] = []
        started_total_at = self._clock()
        for route in routes:
            if (self._clock() - started_total_at) * 1000 >= self._policy.total_timeout_ms:
                break
            circuit = self._circuits.setdefault(route.route_id, CircuitState())
            if self._is_circuit_open(circuit):
                attempts.append(
                    self._attempt(
                        route,
                        attempt_no=0,
                        status="circuit_open",
                        duration_ms=0,
                        failure_kind=None,
                        provider_request_id=None,
                        usage=None,
                        trace_id=request.trace_id,
                        traceparent=request.traceparent,
                    )
                )
                continue

            # 3. 缺失 Adapter 也记录为一次失败尝试，保证路由问题可审计。
            provider = self._providers.get(route.provider_id)
            if provider is None:
                self._record_failure(circuit)
                attempts.append(
                    self._attempt(
                        route,
                        attempt_no=1,
                        status="failed",
                        duration_ms=0,
                        failure_kind="unavailable",
                        provider_request_id=None,
                        usage=None,
                        trace_id=request.trace_id,
                        traceparent=request.traceparent,
                    )
                )
                continue

            fallback_allowed = True
            for attempt_no in range(1, self._policy.max_attempts_per_route + 1):
                remaining_ms = self._policy.total_timeout_ms - int(
                    (self._clock() - started_total_at) * 1000
                )
                if remaining_ms <= 0:
                    break
                started_at = self._clock()
                # 4. 单路由只执行有限重试，失败类型决定是否重试、熔断或允许跨路由降级。
                try:
                    response = provider.invoke(
                        request,
                        route.model_id,
                        min(self._policy.attempt_timeout_ms, remaining_ms),
                    )
                    self._validate_response(response)
                except ProviderInvocationError as error:
                    duration_ms = max(0, int((self._clock() - started_at) * 1000))
                    attempts.append(
                        self._attempt(
                            route,
                            attempt_no=attempt_no,
                            status="failed",
                            duration_ms=duration_ms,
                            failure_kind=error.kind,
                            provider_request_id=error.provider_request_id,
                            usage=None,
                            trace_id=request.trace_id,
                            traceparent=request.traceparent,
                        )
                    )
                    fallback_allowed = error.fallback_allowed
                    if error.kind in {"timeout", "rate_limited", "unavailable", "internal"}:
                        self._record_failure(circuit)
                    if (
                        not error.retryable
                        or attempt_no == self._policy.max_attempts_per_route
                        or circuit.opened_at is not None
                    ):
                        break
                    continue

                # 5. 成功响应先校验再计费和返回，供应商 Usage 缺失时使用零值而非猜测账单。
                duration_ms = max(0, int((self._clock() - started_at) * 1000))
                attempt = self._attempt(
                    route,
                    attempt_no=attempt_no,
                    status="succeeded",
                    duration_ms=duration_ms,
                    failure_kind=None,
                    provider_request_id=response.provider_request_id,
                    usage=response.usage,
                    trace_id=request.trace_id,
                    traceparent=request.traceparent,
                )
                attempts.append(attempt)
                self._record_success(circuit)
                if self._usage_recorder is not None:
                    self._usage_recorder.record(
                        request.invocation_id, request.workspace_id, attempt
                    )
                return ModelResult(
                    content=response.content,
                    finish_reason=response.finish_reason,
                    provider_id=route.provider_id,
                    model_id=route.model_id,
                    usage=response.usage or TokenUsage(0, 0),
                    estimated_cost_microunits=attempt.estimated_cost_microunits,
                    currency="CNY",
                    degraded=False,
                    degradation_reason=None,
                    attempts=tuple(attempts),
                )

            # Adapter 明确禁止备用时立即终止，避免认证或内容策略错误被其他模型绕过。
            if not fallback_allowed:
                break

        # 6. 所有路由失败后仅允许显式规则降级；数据边界失败绝不转换为普通兜底回答。
        if attempts and all(attempt.failure_kind == "data_boundary" for attempt in attempts):
            raise ModelDataBoundaryDeniedError(tuple(attempts))
        if self._policy.rule_degradation_message is not None:
            return ModelResult(
                content=self._policy.rule_degradation_message,
                finish_reason="rule_degradation",
                provider_id=None,
                model_id=None,
                usage=TokenUsage(0, 0),
                estimated_cost_microunits=0,
                currency="CNY",
                degraded=True,
                degradation_reason="all_routes_failed",
                attempts=tuple(attempts),
            )
        raise ModelGatewayUnavailableError(tuple(attempts))

    def _validate_request(self, request: ModelRequest) -> None:
        if request.prompt_characters > self._policy.max_prompt_characters:
            raise ModelRequestRejectedError
        if request.max_output_tokens > self._policy.max_output_tokens:
            raise ModelRequestRejectedError

    def _eligible_routes(self, request: ModelRequest) -> tuple[ModelRoute, ...]:
        eligible = tuple(
            route
            for route in self._routes
            if route.active
            and (request.external_data_allowed or route.location == "private")
            and request.required_capabilities.issubset(route.capabilities)
        )
        affordable = tuple(
            route
            for route in eligible
            if self._estimated_request_cost(request, route)
            <= self._policy.max_estimated_cost_microunits
        )
        if eligible and not affordable:
            raise ModelRequestRejectedError
        return affordable

    @staticmethod
    def _estimated_request_cost(request: ModelRequest, route: ModelRoute) -> int:
        # 以一个字符最多消耗一个输入 Token 做保守准入估算，实际账单仍以供应商 Usage 为准。
        return (
            request.prompt_characters * route.input_price_microunits_per_million_tokens
            + request.max_output_tokens * route.output_price_microunits_per_million_tokens
        ) // MICRO_UNITS_PER_MILLION

    def _validate_response(self, response: ProviderResponse) -> None:
        if (
            not response.content.strip()
            or len(response.content) > self._policy.max_response_characters
        ):
            raise ProviderInvocationError(
                kind="invalid_response",
                retryable=False,
                fallback_allowed=True,
                provider_request_id=response.provider_request_id,
            )
        if (
            response.usage is not None
            and response.usage.output_tokens > self._policy.max_output_tokens
        ):
            raise ProviderInvocationError(
                kind="invalid_response",
                retryable=False,
                fallback_allowed=True,
                provider_request_id=response.provider_request_id,
            )

    def _is_circuit_open(self, circuit: CircuitState) -> bool:
        if circuit.opened_at is None:
            return False
        if (self._clock() - circuit.opened_at) * 1000 >= self._policy.circuit_recovery_ms:
            circuit.opened_at = None
            circuit.consecutive_failures = 0
            return False
        return True

    def _record_failure(self, circuit: CircuitState) -> None:
        circuit.consecutive_failures += 1
        if circuit.consecutive_failures >= self._policy.circuit_failure_threshold:
            circuit.opened_at = self._clock()

    @staticmethod
    def _record_success(circuit: CircuitState) -> None:
        circuit.consecutive_failures = 0
        circuit.opened_at = None

    @staticmethod
    def _attempt(
        route: ModelRoute,
        *,
        attempt_no: int,
        status: AttemptStatus,
        duration_ms: int,
        failure_kind: ProviderFailureKind | None,
        provider_request_id: str | None,
        usage: TokenUsage | None,
        trace_id: str,
        traceparent: str,
    ) -> ModelAttempt:
        cost = 0
        if usage is not None:
            cost = (
                usage.input_tokens * route.input_price_microunits_per_million_tokens
                + usage.output_tokens * route.output_price_microunits_per_million_tokens
            ) // MICRO_UNITS_PER_MILLION
        return ModelAttempt(
            route_id=route.route_id,
            provider_id=route.provider_id,
            model_id=route.model_id,
            attempt_no=attempt_no,
            status=status,
            duration_ms=duration_ms,
            trace_id=trace_id,
            traceparent=traceparent,
            failure_kind=failure_kind,
            provider_request_id=provider_request_id,
            usage=usage,
            estimated_cost_microunits=cost,
        )
