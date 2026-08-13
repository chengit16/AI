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
class _Circuit:
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
    ) -> None:
        self._routes = routes
        self._providers = providers
        self._policy = policy
        self._usage_recorder = usage_recorder
        self._clock = clock
        self._circuits: dict[str, _Circuit] = {}

    def invoke(self, request: ModelRequest) -> ModelResult:
        self._validate_request(request)
        routes = self._eligible_routes(request)
        if not routes:
            if not request.external_data_allowed and any(
                route.active and route.location == "external" for route in self._routes
            ):
                raise ModelDataBoundaryDeniedError
            raise ModelRouteUnavailableError

        attempts: list[ModelAttempt] = []
        started_total_at = self._clock()
        for route in routes:
            if (self._clock() - started_total_at) * 1000 >= self._policy.total_timeout_ms:
                break
            circuit = self._circuits.setdefault(route.route_id, _Circuit())
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

            provider = self._providers.get(route.provider_id)
            if provider is None:
                self._record_failure(route, circuit)
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

            for attempt_no in range(1, self._policy.max_attempts_per_route + 1):
                started_at = self._clock()
                try:
                    response = provider.invoke(
                        request, route.model_id, self._policy.attempt_timeout_ms
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
                    self._record_failure(route, circuit)
                    if (
                        not error.retryable
                        or attempt_no == self._policy.max_attempts_per_route
                        or circuit.opened_at is not None
                    ):
                        break
                    continue

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

            # 一个路由的错误若允许备用，继续到下一路由；认证、策略和非法请求错误会在此终止。
            latest_failure = attempts[-1]
            if latest_failure.failure_kind in {
                "authentication",
                "content_policy",
                "invalid_request",
                "capability_unsupported",
            }:
                break

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
        return tuple(
            route
            for route in self._routes
            if route.active
            and (request.external_data_allowed or route.location == "private")
            and request.required_capabilities.issubset(route.capabilities)
        )

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

    def _is_circuit_open(self, circuit: _Circuit) -> bool:
        if circuit.opened_at is None:
            return False
        if (self._clock() - circuit.opened_at) * 1000 >= self._policy.circuit_recovery_ms:
            circuit.opened_at = None
            circuit.consecutive_failures = 0
            return False
        return True

    def _record_failure(self, route: ModelRoute, circuit: _Circuit) -> None:
        del route
        circuit.consecutive_failures += 1
        if circuit.consecutive_failures >= self._policy.circuit_failure_threshold:
            circuit.opened_at = self._clock()

    @staticmethod
    def _record_success(circuit: _Circuit) -> None:
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
