"""验证 P1E-06 本地 Mock Adapter 的身份边界和安全配置。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from ai_platform_api.app.local_mock import (
    LOCAL_MOCK_PROVIDER_ID,
    LOCAL_MOCK_PROVIDER_KEY,
)
from ai_platform_api.config import Settings
from ai_platform_api.modules.model_gateway.domain.configuration import (
    ModelProviderConfiguration,
    RuntimeProviderAccess,
)
from ai_platform_api.modules.model_gateway.domain.models import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ProviderResponse,
    TokenUsage,
)
from ai_platform_api.modules.model_gateway.infrastructure.local_mock import (
    LocalMockRuntimeProviderFactory,
)


class SentinelProvider:
    """标识真实供应商回退工厂是否被选择，不执行任何外部网络请求。"""

    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id

    def invoke(
        self,
        request: ModelRequest,
        model_id: str,
        timeout_ms: int,
    ) -> ProviderResponse:
        del request, model_id, timeout_ms
        return ProviderResponse("合成真实供应商结果", "stop", TokenUsage(1, 1))


class SentinelFactory:
    """收集回退调用，证明名称相似的普通配置不能冒充内置 Provider。"""

    def __init__(self) -> None:
        self.created: list[UUID] = []

    def create(self, access: RuntimeProviderAccess) -> ModelProvider:
        self.created.append(access.configuration.provider_id)
        return SentinelProvider(str(access.configuration.provider_id))


def provider(provider_id: UUID, provider_key: str) -> ModelProviderConfiguration:
    """构造不包含真实凭据或客户数据的活动供应商配置。"""

    now = datetime.now(UTC)
    return ModelProviderConfiguration(
        provider_id=provider_id,
        provider_key=provider_key,
        display_name="合成供应商",
        adapter_kind="openai_compatible",
        base_url="https://synthetic.example/v1",
        probe_model_id="synthetic-model",
        location="private",
        declared_capabilities=frozenset({"generation"}),
        policy_review_status="approved",
        max_security_level="RESTRICTED",
        retention_days=0,
        training_usage_allowed=False,
        policy_url=None,
        policy_version="synthetic-v1",
        policy_reviewed_by_account_id=uuid4(),
        policy_reviewed_at=now,
        probe_status="passed",
        probed_capabilities=frozenset({"generation"}),
        last_probe_error_code=None,
        last_probed_at=now,
        status="active",
        created_by_account_id=uuid4(),
        created_at=now,
        updated_by_account_id=uuid4(),
        updated_at=now,
        version=1,
    )


def request() -> ModelRequest:
    """构造同时含合成证据和用户问题的最小模型请求。"""

    return ModelRequest(
        invocation_id=uuid4(),
        workspace_id=uuid4(),
        trace_id="a" * 32,
        traceparent=f"00-{'a' * 32}-{'b' * 16}-01",
        task_type="assistant.answer",
        messages=(
            ModelMessage("system", "合成系统约束"),
            ModelMessage("user", "<untrusted_evidence>合成证据正文</untrusted_evidence>"),
            ModelMessage("user", "差旅额度是多少?"),
        ),
        required_capabilities=frozenset({"generation"}),
        max_output_tokens=128,
        external_data_allowed=True,
        security_level="INTERNAL",
    )


def test_only_fixed_builtin_identity_uses_local_provider() -> None:
    fallback = SentinelFactory()
    factory = LocalMockRuntimeProviderFactory(
        fallback,
        provider_id=LOCAL_MOCK_PROVIDER_ID,
        provider_key=LOCAL_MOCK_PROVIDER_KEY,
    )
    access = RuntimeProviderAccess(
        provider(LOCAL_MOCK_PROVIDER_ID, LOCAL_MOCK_PROVIDER_KEY),
        "synthetic-local-secret",
        1,
    )

    result = factory.create(access).invoke(request(), "local-mock-v1", 500)

    assert result.content.startswith("本地 Mock 回答:")
    assert "差旅额度是多少?" in result.content
    assert "合成证据正文" not in result.content
    assert fallback.created == []


@pytest.mark.parametrize(
    ("provider_id", "provider_key"),
    [
        (uuid4(), LOCAL_MOCK_PROVIDER_KEY),
        (LOCAL_MOCK_PROVIDER_ID, "local_mock_lookalike"),
    ],
)
def test_similar_provider_identity_still_uses_real_factory(
    provider_id: UUID,
    provider_key: str,
) -> None:
    fallback = SentinelFactory()
    factory = LocalMockRuntimeProviderFactory(
        fallback,
        provider_id=LOCAL_MOCK_PROVIDER_ID,
        provider_key=LOCAL_MOCK_PROVIDER_KEY,
    )

    created = factory.create(
        RuntimeProviderAccess(provider(provider_id, provider_key), "synthetic-secret", 1)
    )

    assert isinstance(created, SentinelProvider)
    assert fallback.created == [provider_id]


def test_non_local_environment_rejects_builtin_bootstrap() -> None:
    with pytest.raises(ValueError, match="内置 Mock"):
        Settings(
            environment="production",
            session_cookie_secure=True,
            local_mock_bootstrap_enabled=True,
        )
