from uuid import UUID

import pytest
from ai_platform_api.modules.model_gateway.application.gateway import ModelGateway
from ai_platform_api.modules.model_gateway.domain.errors import (
    ModelDataBoundaryDeniedError,
    ModelGatewayUnavailableError,
    ModelRequestRejectedError,
    ModelRouteUnavailableError,
)
from ai_platform_api.modules.model_gateway.domain.models import (
    GatewayPolicy,
    ModelMessage,
    ModelRequest,
    ModelRoute,
)
from ai_platform_api.modules.model_gateway.infrastructure.mock import (
    InMemoryUsageRecorder,
    MockProvider,
)

WORKSPACE_ID = UUID(int=1)
INVOCATION_ID = UUID(int=2)
TRACE_ID = "0123456789abcdef0123456789abcdef"
TRACEPARENT = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"


def request(
    *,
    external_data_allowed: bool = True,
    required_capabilities: frozenset[str] = frozenset({"generation"}),
    max_output_tokens: int = 32,
    prompt: str = "请回答合成问题",
) -> ModelRequest:
    return ModelRequest(
        invocation_id=INVOCATION_ID,
        workspace_id=WORKSPACE_ID,
        trace_id=TRACE_ID,
        traceparent=TRACEPARENT,
        task_type="chat.answer",
        messages=(ModelMessage(role="user", content=prompt),),
        required_capabilities=required_capabilities,  # type: ignore[arg-type]
        max_output_tokens=max_output_tokens,
        external_data_allowed=external_data_allowed,
    )


def route(
    route_id: str,
    provider_id: str,
    *,
    location: str = "external",
    capabilities: frozenset[str] = frozenset({"generation"}),
) -> ModelRoute:
    return ModelRoute(
        route_id=route_id,
        provider_id=provider_id,
        model_id=f"{route_id}-model",
        location=location,  # type: ignore[arg-type]
        capabilities=capabilities,  # type: ignore[arg-type]
        input_price_microunits_per_million_tokens=20_000_000,
        output_price_microunits_per_million_tokens=40_000_000,
    )


def policy(**overrides: object) -> GatewayPolicy:
    values: dict[str, object] = {
        "attempt_timeout_ms": 500,
        "total_timeout_ms": 2_000,
        "max_attempts_per_route": 2,
        "max_prompt_characters": 2_000,
        "max_output_tokens": 256,
        "max_response_characters": 4_000,
        "circuit_failure_threshold": 2,
        "circuit_recovery_ms": 1_000,
        "rule_degradation_message": None,
    }
    values.update(overrides)
    return GatewayPolicy(**values)  # type: ignore[arg-type]


def gateway(
    routes: tuple[ModelRoute, ...],
    providers: dict[str, MockProvider],
    *,
    recorder: InMemoryUsageRecorder | None = None,
    **policy_overrides: object,
) -> ModelGateway:
    return ModelGateway(routes, providers, policy(**policy_overrides), recorder)


def test_mock_success_records_trace_usage_and_estimated_cost() -> None:
    provider = MockProvider("primary", response_prefix="主模型")
    recorder = InMemoryUsageRecorder()

    result = gateway(
        (route("primary-route", "primary"),), {"primary": provider}, recorder=recorder
    ).invoke(request())

    assert result.content.startswith("主模型")
    assert result.degraded is False
    assert result.provider_id == "primary"
    assert result.usage.total_tokens > 0
    assert result.estimated_cost_microunits > 0
    assert len(recorder.records) == 1
    invocation_id, workspace_id, attempt = recorder.records[0]
    assert invocation_id == INVOCATION_ID
    assert workspace_id == WORKSPACE_ID
    assert attempt.trace_id == TRACE_ID
    assert attempt.traceparent == TRACEPARENT
    assert attempt.provider_request_id == "mock-0001"


def test_timeout_retries_primary_then_falls_back_to_backup() -> None:
    primary = MockProvider("primary", outcomes=["timeout", "timeout"])
    backup = MockProvider("backup", response_prefix="备用模型")

    result = gateway(
        (route("primary-route", "primary"), route("backup-route", "backup")),
        {"primary": primary, "backup": backup},
    ).invoke(request())

    assert result.content.startswith("备用模型")
    assert result.provider_id == "backup"
    assert [attempt.failure_kind for attempt in result.attempts] == ["timeout", "timeout", None]
    assert [attempt.attempt_no for attempt in result.attempts] == [1, 2, 1]
    assert len(primary.calls) == 2
    assert len(backup.calls) == 1


def test_content_policy_failure_does_not_bypass_to_backup() -> None:
    primary = MockProvider("primary", outcomes=["content_policy"])
    backup = MockProvider("backup")

    with pytest.raises(ModelGatewayUnavailableError) as error:
        gateway(
            (route("primary-route", "primary"), route("backup-route", "backup")),
            {"primary": primary, "backup": backup},
        ).invoke(request())

    assert [attempt.failure_kind for attempt in error.value.attempts] == ["content_policy"]
    assert backup.calls == []


def test_invalid_request_and_prompt_budget_fail_before_provider_call() -> None:
    provider = MockProvider("primary")
    model_gateway = gateway(
        (route("primary-route", "primary"),),
        {"primary": provider},
        max_prompt_characters=3,
    )

    with pytest.raises(ModelRequestRejectedError):
        model_gateway.invoke(request(prompt="超过限制"))
    assert provider.calls == []

    with pytest.raises(ModelRequestRejectedError):
        gateway(
            (route("primary-route", "primary"),),
            {"primary": provider},
            max_output_tokens=8,
        ).invoke(request(max_output_tokens=9))


def test_estimated_cost_budget_rejects_before_provider_call() -> None:
    provider = MockProvider("primary")

    with pytest.raises(ModelRequestRejectedError):
        gateway(
            (route("primary-route", "primary"),),
            {"primary": provider},
            max_estimated_cost_microunits=1,
        ).invoke(request())

    assert provider.calls == []


def test_private_data_only_uses_private_route() -> None:
    external = MockProvider("external")
    private = MockProvider("private", response_prefix="私有模型")

    result = gateway(
        (
            route("external-route", "external"),
            route("private-route", "private", location="private"),
        ),
        {"external": external, "private": private},
    ).invoke(request(external_data_allowed=False))

    assert result.provider_id == "private"
    assert external.calls == []
    assert len(private.calls) == 1


def test_external_data_denied_when_only_external_route_exists() -> None:
    with pytest.raises(ModelDataBoundaryDeniedError):
        gateway(
            (route("external-route", "external"),),
            {"external": MockProvider("external")},
        ).invoke(request(external_data_allowed=False))


def test_missing_provider_and_no_eligible_capability_are_explicit_errors() -> None:
    with pytest.raises(ModelGatewayUnavailableError):
        gateway((route("primary-route", "missing"),), {}).invoke(request())

    with pytest.raises(ModelRouteUnavailableError):
        gateway((route("primary-route", "primary"),), {"primary": MockProvider("primary")}).invoke(
            request(required_capabilities=frozenset({"generation", "tools"}))
        )


def test_circuit_open_skips_repeated_provider_calls_and_rule_degrades() -> None:
    primary = MockProvider("primary", outcomes=["unavailable"])
    model_gateway = gateway(
        (route("primary-route", "primary"),),
        {"primary": primary},
        circuit_failure_threshold=1,
        rule_degradation_message="当前模型暂不可用, 请稍后重试",
    )

    first = model_gateway.invoke(request())
    second = model_gateway.invoke(request())

    assert first.degraded is True
    assert second.degraded is True
    assert len(primary.calls) == 1
    assert second.attempts[0].status == "circuit_open"


def test_recovery_window_allows_probe_after_circuit_opens() -> None:
    now = [0.0]
    primary = MockProvider("primary", outcomes=["unavailable", "success"])
    model_gateway = ModelGateway(
        (route("primary-route", "primary"),),
        {"primary": primary},
        policy(circuit_failure_threshold=1, circuit_recovery_ms=100),
        clock=lambda: now[0],
    )

    with pytest.raises(ModelGatewayUnavailableError):
        model_gateway.invoke(request())
    with pytest.raises(ModelGatewayUnavailableError):
        model_gateway.invoke(request())
    now[0] = 0.2
    result = model_gateway.invoke(request())

    assert result.degraded is False
    assert result.provider_id == "primary"
    assert len(primary.calls) == 2
