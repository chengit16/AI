"""验证 P1D-06 运行配置装配、调用追溯和错误映射。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID, uuid4

import pytest
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.model_gateway.application.runtime_gateway import (
    RuntimeModelGatewayService,
)
from ai_platform_api.modules.model_gateway.domain.configuration import (
    ModelProviderConfiguration,
    RuntimeProviderAccess,
)
from ai_platform_api.modules.model_gateway.domain.configuration_errors import (
    ModelProviderDataPolicyDeniedError,
)
from ai_platform_api.modules.model_gateway.domain.errors import (
    ModelDataBoundaryDeniedError,
    ModelGatewayUnavailableError,
    ProviderInvocationError,
)
from ai_platform_api.modules.model_gateway.domain.models import (
    GatewayPolicy,
    ModelMessage,
    ModelRequest,
)
from ai_platform_api.modules.model_gateway.domain.runtime import (
    AiRuntimeConfigVersion,
    RuntimeComponentVersions,
    RuntimeInvocationOutcome,
    RuntimeRouteSnapshot,
)
from ai_platform_api.modules.model_gateway.domain.runtime_errors import (
    ModelInvocationConflictError,
)
from ai_platform_api.modules.model_gateway.infrastructure import provider_http
from ai_platform_api.modules.model_gateway.infrastructure.mock import MockProvider
from ai_platform_api.modules.model_gateway.infrastructure.provider_http import (
    OpenAiCompatibleRuntimeProvider,
    ValidatedProviderTarget,
)

WORKSPACE_ID = UUID("30000000-0000-4000-8000-000000000606")
PRIMARY_ID = UUID("40000000-0000-4000-8000-000000000606")
BACKUP_ID = UUID("40000000-0000-4000-8000-000000000607")
CONFIG_ID = UUID("50000000-0000-4000-8000-000000000606")
ADMIN_ID = UUID("10000000-0000-4000-8000-000000000606")
NOW = datetime(2026, 8, 14, tzinfo=UTC)


class FixedConfigurationReader:
    def __init__(self, configuration: AiRuntimeConfigVersion) -> None:
        self.configuration = configuration

    def current(self) -> AiRuntimeConfigVersion:
        return self.configuration


class MemoryInvocationStore:
    def __init__(self) -> None:
        self.reserved: set[UUID] = set()
        self.outcomes: list[RuntimeInvocationOutcome] = []

    def reserve(self, request: ModelRequest, runtime_config_version_id: UUID) -> None:
        assert runtime_config_version_id == CONFIG_ID
        if request.invocation_id in self.reserved:
            raise ModelInvocationConflictError
        self.reserved.add(request.invocation_id)

    def complete(self, outcome: RuntimeInvocationOutcome) -> None:
        self.outcomes.append(outcome)


class FixedAccessResolver:
    def __init__(self, denied: frozenset[UUID] = frozenset()) -> None:
        self.denied = denied

    def resolve_runtime_access(
        self, provider_id: UUID, *, security_level: SecurityLevel
    ) -> RuntimeProviderAccess:
        del security_level
        if provider_id in self.denied:
            raise ModelProviderDataPolicyDeniedError
        return RuntimeProviderAccess(provider(provider_id), "synthetic-secret", 7)


class MockFactory:
    def __init__(self, providers: dict[str, MockProvider]) -> None:
        self.providers = providers

    def create(self, access: RuntimeProviderAccess) -> MockProvider:
        return self.providers[str(access.configuration.provider_id)]


def provider(provider_id: UUID) -> ModelProviderConfiguration:
    return ModelProviderConfiguration(
        provider_id=provider_id,
        provider_key=f"provider_{str(provider_id)[-3:]}",
        display_name="合成供应商",
        adapter_kind="openai_compatible",
        base_url="https://api.synthetic.example/v1",
        probe_model_id="synthetic-model",
        location="external",
        declared_capabilities=frozenset({"generation"}),
        policy_review_status="approved",
        max_security_level="CONFIDENTIAL",
        retention_days=0,
        training_usage_allowed=False,
        policy_url="https://policy.synthetic.example/privacy",
        policy_version="v1",
        policy_reviewed_by_account_id=ADMIN_ID,
        policy_reviewed_at=NOW,
        probe_status="passed",
        probed_capabilities=frozenset({"generation"}),
        last_probe_error_code=None,
        last_probed_at=NOW,
        status="active",
        created_by_account_id=ADMIN_ID,
        created_at=NOW,
        updated_by_account_id=ADMIN_ID,
        updated_at=NOW,
        version=4,
    )


def configuration(*, failure_threshold: int = 3) -> AiRuntimeConfigVersion:
    components = RuntimeComponentVersions(*(f"component-{index}-v1" for index in range(10)))
    return AiRuntimeConfigVersion(
        runtime_config_version_id=CONFIG_ID,
        version_number=1,
        display_name="合成运行配置",
        content_hash="a" * 64,
        system_prompt_template="合成系统提示",
        system_prompt_hash="b" * 64,
        components=components,
        policy=GatewayPolicy(
            attempt_timeout_ms=500,
            total_timeout_ms=2_000,
            max_attempts_per_route=2,
            max_prompt_characters=2_000,
            max_output_tokens=256,
            max_response_characters=4_000,
            circuit_failure_threshold=failure_threshold,
            circuit_recovery_ms=10_000,
            max_estimated_cost_microunits=5_000_000,
        ),
        routes=(
            RuntimeRouteSnapshot(
                UUID("60000000-0000-4000-8000-000000000606"),
                PRIMARY_ID,
                4,
                1,
                "synthetic-primary",
                "external",
                frozenset({"generation"}),
                20_000_000,
                40_000_000,
            ),
            RuntimeRouteSnapshot(
                UUID("60000000-0000-4000-8000-000000000607"),
                BACKUP_ID,
                4,
                2,
                "synthetic-backup",
                "external",
                frozenset({"generation"}),
                10_000_000,
                20_000_000,
            ),
        ),
        created_by_account_id=ADMIN_ID,
        created_at=NOW,
    )


def request(invocation_id: UUID | None = None) -> ModelRequest:
    return ModelRequest(
        invocation_id=invocation_id or uuid4(),
        workspace_id=WORKSPACE_ID,
        trace_id="6" * 32,
        traceparent="00-" + "6" * 32 + "-" + "7" * 16 + "-01",
        task_type="chat.answer",
        messages=(ModelMessage("user", "请回答合成问题"),),
        required_capabilities=frozenset({"generation"}),
        max_output_tokens=32,
        external_data_allowed=True,
        security_level="INTERNAL",
    )


def service(
    providers: dict[str, MockProvider],
    *,
    denied: frozenset[UUID] = frozenset(),
    failure_threshold: int = 3,
) -> tuple[RuntimeModelGatewayService, MemoryInvocationStore]:
    store = MemoryInvocationStore()
    return (
        RuntimeModelGatewayService(
            FixedConfigurationReader(configuration(failure_threshold=failure_threshold)),
            store,
            FixedAccessResolver(denied),
            MockFactory(providers),
        ),
        store,
    )


def test_runtime_gateway_persists_version_attempts_usage_cost_and_credential_version() -> None:
    primary = MockProvider(str(PRIMARY_ID), outcomes=["timeout", "timeout"])
    backup = MockProvider(str(BACKUP_ID), response_prefix="备用模型")
    runtime, store = service({str(PRIMARY_ID): primary, str(BACKUP_ID): backup})

    result = runtime.invoke(request())

    assert result.runtime_config_version_id == CONFIG_ID
    assert result.provider_id == str(BACKUP_ID)
    assert [attempt.failure_kind for attempt in result.attempts] == ["timeout", "timeout", None]
    assert store.outcomes[0].status == "succeeded"
    assert store.outcomes[0].credential_versions == {
        str(PRIMARY_ID): 7,
        str(BACKUP_ID): 7,
    }
    assert store.outcomes[0].result is not None
    assert store.outcomes[0].result.estimated_cost_microunits > 0


def test_invocation_id_is_reserved_before_provider_call() -> None:
    invocation_id = uuid4()
    primary = MockProvider(str(PRIMARY_ID))
    backup = MockProvider(str(BACKUP_ID))
    runtime, _ = service({str(PRIMARY_ID): primary, str(BACKUP_ID): backup})

    runtime.invoke(request(invocation_id))
    with pytest.raises(ModelInvocationConflictError):
        runtime.invoke(request(invocation_id))
    assert len(primary.calls) == 1


def test_all_policy_denied_routes_fail_closed_and_persist_attempts() -> None:
    runtime, store = service(
        {
            str(PRIMARY_ID): MockProvider(str(PRIMARY_ID)),
            str(BACKUP_ID): MockProvider(str(BACKUP_ID)),
        },
        denied=frozenset({PRIMARY_ID, BACKUP_ID}),
    )

    with pytest.raises(ModelDataBoundaryDeniedError):
        runtime.invoke(request())
    assert store.outcomes[0].status == "failed"
    assert store.outcomes[0].error_code == "MODEL_DATA_BOUNDARY_DENIED"
    assert [attempt.failure_kind for attempt in store.outcomes[0].attempts] == [
        "data_boundary",
        "data_boundary",
    ]


def test_shared_circuit_skips_provider_on_next_invocation() -> None:
    primary = MockProvider(str(PRIMARY_ID), outcomes=["unavailable"])
    backup = MockProvider(str(BACKUP_ID), outcomes=["unavailable"])
    runtime, store = service(
        {str(PRIMARY_ID): primary, str(BACKUP_ID): backup},
        failure_threshold=1,
    )

    with pytest.raises(ModelGatewayUnavailableError):
        runtime.invoke(request())
    with pytest.raises(ModelGatewayUnavailableError):
        runtime.invoke(request())
    assert len(primary.calls) == 1
    assert len(backup.calls) == 1
    assert [attempt.status for attempt in store.outcomes[1].attempts] == [
        "circuit_open",
        "circuit_open",
    ]


class FixedTargetPolicy:
    def resolve(self, value: str) -> ValidatedProviderTarget:
        assert value == "https://api.synthetic.example/v1"
        return ValidatedProviderTarget(
            value,
            "api.synthetic.example",
            443,
            "/v1",
            ("93.184.216.34",),
        )


class FakeHttpResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self, size: int) -> bytes:
        assert size == 4_194_305
        return self._body

    def getheader(self, name: str) -> str | None:
        return "synthetic-request-id" if name == "x-request-id" else None


class FakeHttpsConnection:
    response = FakeHttpResponse(
        200,
        json.dumps(
            {
                "choices": [{"message": {"content": "合成回答"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            }
        ).encode(),
    )
    last_headers: ClassVar[dict[str, str]] = {}

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> None:
        assert method == "POST"
        assert path == "/v1/chat/completions"
        assert b"synthetic-primary-model" in body
        type(self).last_headers = headers

    def getresponse(self) -> FakeHttpResponse:
        return self.response

    def close(self) -> None:
        return None


def test_openai_compatible_runtime_adapter_parses_usage_and_rejects_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_http, "_PinnedHttpsConnection", FakeHttpsConnection)
    access = RuntimeProviderAccess(provider(PRIMARY_ID), "synthetic-secret", 7)
    adapter = OpenAiCompatibleRuntimeProvider(access, FixedTargetPolicy())

    response = adapter.invoke(request(), "synthetic-primary-model", 500)

    assert response.content == "合成回答"
    assert response.usage is not None and response.usage.total_tokens == 16
    assert FakeHttpsConnection.last_headers["Authorization"] == "Bearer synthetic-secret"

    FakeHttpsConnection.response = FakeHttpResponse(302, b"")
    with pytest.raises(ProviderInvocationError) as captured:
        adapter.invoke(request(), "synthetic-primary-model", 500)
    assert captured.value.kind == "invalid_response"
    assert captured.value.fallback_allowed is True
