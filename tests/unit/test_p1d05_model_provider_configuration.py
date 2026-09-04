"""验证 P1D-05 供应商配置、凭证、数据政策和探测状态机。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import PlatformRequestContext
from ai_platform_api.common.security import EnvelopeSecretCipher, MasterKeyFile
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.config import Settings
from ai_platform_api.modules.model_gateway.application.configurations import (
    ModelProviderConfigurationService,
)
from ai_platform_api.modules.model_gateway.domain.configuration import (
    CapabilityProbeResult,
    ModelProviderConfiguration,
    ModelProviderCredential,
)
from ai_platform_api.modules.model_gateway.domain.configuration_errors import (
    ModelProviderConfigurationInvalidError,
    ModelProviderDataPolicyDeniedError,
    PlatformAdministratorRequiredError,
)
from ai_platform_api.modules.model_gateway.domain.models import ModelCapability
from ai_platform_api.modules.model_gateway.infrastructure import provider_http
from ai_platform_api.modules.model_gateway.infrastructure.provider_http import (
    OpenAiCompatibleCapabilityProbe,
    StrictProviderBaseUrlPolicy,
)

ADMIN_ID = UUID("10000000-0000-4000-8000-000000000505")
MEMBER_ID = UUID("10000000-0000-4000-8000-000000000506")


class MemoryProviders:
    def __init__(self) -> None:
        self.administrators = {ADMIN_ID}
        self.configurations: dict[UUID, ModelProviderConfiguration] = {}
        self.credentials: dict[UUID, list[ModelProviderCredential]] = {}

    def is_platform_administrator(self, account_id: UUID) -> bool:
        return account_id in self.administrators

    def list_configurations(self) -> tuple[ModelProviderConfiguration, ...]:
        return tuple(sorted(self.configurations.values(), key=lambda item: item.provider_key))

    def get_configuration(
        self, provider_id: UUID, *, for_update: bool = False
    ) -> ModelProviderConfiguration | None:
        del for_update
        return self.configurations.get(provider_id)

    def add_configuration(self, configuration: ModelProviderConfiguration) -> None:
        self.configurations[configuration.provider_id] = configuration

    def save_configuration(self, configuration: ModelProviderConfiguration) -> None:
        self.configurations[configuration.provider_id] = configuration

    def next_credential_version(self, provider_id: UUID) -> int:
        return len(self.credentials.get(provider_id, [])) + 1

    def get_active_credential(
        self, provider_id: UUID, *, for_update: bool = False
    ) -> ModelProviderCredential | None:
        del for_update
        return next(
            (
                item
                for item in reversed(self.credentials.get(provider_id, []))
                if item.status == "active"
            ),
            None,
        )

    def replace_active_credential(self, credential: ModelProviderCredential) -> None:
        existing = [
            replace(item, status="revoked", revoked_at=credential.created_at)
            if item.status == "active"
            else item
            for item in self.credentials.get(credential.provider_id, [])
        ]
        existing.append(credential)
        self.credentials[credential.provider_id] = existing


class MemoryAudit:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def add(self, **values: object) -> None:
        self.actions.append(str(values["action"]))


class MemoryUnitOfWork:
    def __init__(self) -> None:
        self.providers = MemoryProviders()
        self.audit = MemoryAudit()

    def __enter__(self) -> MemoryUnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def commit(self) -> None:
        return None


class FixedUrlPolicy:
    def __init__(self) -> None:
        self.call_count = 0

    def normalize_and_validate(self, value: str) -> str:
        self.call_count += 1
        assert value == "https://api.synthetic.example/v1"
        return value


class PassingProbe:
    def probe(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str,
        wire_api: str,
        capabilities: frozenset[ModelCapability],
    ) -> CapabilityProbeResult:
        assert base_url == "https://api.synthetic.example/v1"
        assert api_key.startswith("synthetic-")
        assert model_id == "synthetic-chat"
        assert wire_api == "chat_completions"
        return CapabilityProbeResult("passed", capabilities)


def context(account_id: UUID) -> PlatformRequestContext:
    return PlatformRequestContext(
        request_id=UUID("90000000-0000-4000-8000-000000000505"),
        trace=TraceContext("1" * 32, "2" * 16),
        actor_id=account_id,
        account_id=account_id,
    )


def service(
    tmp_path: Path,
) -> tuple[ModelProviderConfigurationService, MemoryUnitOfWork, FixedUrlPolicy]:
    key_path = tmp_path / "master.key"
    key_path.write_bytes(b"k" * 32)
    key_path.chmod(0o600)
    unit_of_work = MemoryUnitOfWork()
    url_policy = FixedUrlPolicy()
    return (
        ModelProviderConfigurationService(
            unit_of_work,
            EnvelopeSecretCipher(MasterKeyFile(str(key_path))),
            url_policy,
            PassingProbe(),
        ),
        unit_of_work,
        url_policy,
    )


def test_non_platform_administrator_cannot_trigger_url_or_secret_processing(tmp_path: Path) -> None:
    configuration_service, _, url_policy = service(tmp_path)

    with pytest.raises(PlatformAdministratorRequiredError):
        configuration_service.create(
            context(MEMBER_ID),
            provider_key="synthetic_gateway",
            display_name="合成 GPT 中转",
            adapter_kind="openai_compatible",
            base_url="https://api.synthetic.example/v1",
            probe_model_id="synthetic-chat",
            location="external",
            declared_capabilities=frozenset({"generation"}),
            api_key="synthetic-secret-value",
        )

    assert url_policy.call_count == 0


def test_administration_qualification_returns_boolean_without_listing_business_data(
    tmp_path: Path,
) -> None:
    """资格查询对管理员和普通账号均成功，管理员业务列表仍失败关闭。"""

    configuration_service, _, _ = service(tmp_path)

    assert configuration_service.is_platform_administrator(context(ADMIN_ID)) is True
    assert configuration_service.is_platform_administrator(context(MEMBER_ID)) is False
    with pytest.raises(PlatformAdministratorRequiredError):
        configuration_service.list_configurations(context(MEMBER_ID))


def test_provider_requires_policy_and_probe_then_rotates_without_plaintext(tmp_path: Path) -> None:
    configuration_service, unit_of_work, _ = service(tmp_path)
    created = configuration_service.create(
        context(ADMIN_ID),
        provider_key="synthetic_gateway",
        display_name="合成 GPT 中转",
        adapter_kind="openai_compatible",
        base_url="https://api.synthetic.example/v1",
        probe_model_id="synthetic-chat",
        location="external",
        declared_capabilities=frozenset({"generation", "structured_output"}),
        api_key="synthetic-secret-value-v1",
    )

    with pytest.raises(ModelProviderDataPolicyDeniedError):
        configuration_service.activate(context(ADMIN_ID), created.provider_id)

    reviewed = configuration_service.review_data_policy(
        context(ADMIN_ID),
        created.provider_id,
        approved=True,
        max_security_level="CONFIDENTIAL",
        retention_days=0,
        training_usage_allowed=False,
        policy_url="https://policy.synthetic.example/privacy",
        policy_version="synthetic-policy-v1",
    )
    assert reviewed.policy_review_status == "approved"
    probed = configuration_service.probe(context(ADMIN_ID), created.provider_id)
    assert probed.probe_status == "passed"
    activated = configuration_service.activate(context(ADMIN_ID), created.provider_id)
    assert activated.status == "active"
    runtime_access = configuration_service.resolve_runtime_access(
        created.provider_id, security_level="INTERNAL"
    )
    assert runtime_access.api_key == "synthetic-secret-value-v1"
    assert runtime_access.credential_version == 1
    assert (
        configuration_service.require_export_allowed(
            created.provider_id, security_level="CONFIDENTIAL"
        ).provider_id
        == created.provider_id
    )
    with pytest.raises(ModelProviderDataPolicyDeniedError):
        configuration_service.require_export_allowed(
            created.provider_id, security_level="RESTRICTED"
        )

    active_credential = unit_of_work.providers.get_active_credential(created.provider_id)
    assert active_credential is not None
    assert b"synthetic-secret-value-v1" not in active_credential.envelope.ciphertext
    rotated = configuration_service.rotate_credential(
        context(ADMIN_ID),
        created.provider_id,
        api_key="synthetic-secret-value-v2",
    )
    assert rotated.status == "draft"
    assert rotated.probe_status == "not_run"
    assert [item.status for item in unit_of_work.providers.credentials[created.provider_id]] == [
        "revoked",
        "active",
    ]
    assert unit_of_work.audit.actions == [
        "model_provider.created",
        "model_provider.data_policy_reviewed",
        "model_provider.capability_probed",
        "model_provider.activated",
        "model_provider.credential_rotated",
    ]


def test_provider_preserves_responses_wire_api_for_codex_compatible_gateway(
    tmp_path: Path,
) -> None:
    """Codex 类中转必须显式保留 Responses 协议，避免后续探测误用 Chat Completions。"""

    configuration_service, _, _ = service(tmp_path)

    created = configuration_service.create(
        context(ADMIN_ID),
        provider_key="synthetic_codex",
        display_name="合成 Codex 中转",
        adapter_kind="openai_compatible",
        wire_api="responses",
        base_url="https://api.synthetic.example/v1",
        probe_model_id="synthetic-codex",
        location="external",
        declared_capabilities=frozenset({"generation", "streaming"}),
        api_key="synthetic-secret-value",
    )

    assert created.wire_api == "responses"


def public_resolver(*args: object, **kwargs: object) -> list[tuple[Any, ...]]:
    del args, kwargs
    return [(2, 1, 6, "", ("93.184.216.34", 443))]


def mixed_resolver(*args: object, **kwargs: object) -> list[tuple[Any, ...]]:
    del args, kwargs
    return [
        (2, 1, 6, "", ("93.184.216.34", 443)),
        (2, 1, 6, "", ("127.0.0.1", 443)),
    ]


def fake_ip_resolver(*args: object, **kwargs: object) -> list[tuple[Any, ...]]:
    del args, kwargs
    return [(2, 1, 6, "", ("198.18.0.93", 443))]


def test_strict_base_url_policy_rejects_unapproved_private_and_redirect_style_urls() -> None:
    policy = StrictProviderBaseUrlPolicy(("api.synthetic.example",), resolver=public_resolver)

    assert (
        policy.normalize_and_validate("https://api.synthetic.example/v1/")
        == "https://api.synthetic.example/v1"
    )
    for value in (
        "http://api.synthetic.example/v1",
        "https://user@api.synthetic.example/v1",
        "https://api.synthetic.example:8443/v1",
        "https://unapproved.synthetic.example/v1",
        "https://api.synthetic.example/v1?target=internal",
    ):
        with pytest.raises(ModelProviderConfigurationInvalidError):
            policy.normalize_and_validate(value)

    rebinding_policy = StrictProviderBaseUrlPolicy(
        ("api.synthetic.example",), resolver=mixed_resolver
    )
    with pytest.raises(ModelProviderConfigurationInvalidError):
        rebinding_policy.normalize_and_validate("https://api.synthetic.example/v1")


def test_fake_ip_network_requires_explicit_local_exception() -> None:
    """Clash Fake-IP 默认仍被拒绝，只有显式本地例外才能进入地址钉住流程。"""

    default_policy = StrictProviderBaseUrlPolicy(
        ("api.synthetic.example",), resolver=fake_ip_resolver
    )
    with pytest.raises(ModelProviderConfigurationInvalidError):
        default_policy.resolve("https://api.synthetic.example/v1")

    local_policy = StrictProviderBaseUrlPolicy(
        ("api.synthetic.example",),
        allowed_resolved_networks=("198.18.0.0/15",),
        resolver=fake_ip_resolver,
    )
    target = local_policy.resolve("https://api.synthetic.example/v1")

    assert target.addresses == ("198.18.0.93",)


def test_fake_ip_exception_is_limited_to_local_benchmark_network() -> None:
    """例外只能覆盖 RFC 2544 基准网段，生产环境和其他保留网段必须失败关闭。"""

    local = Settings(
        environment="local",
        model_provider_allowed_resolved_networks=("198.18.0.0/16",),
    )
    assert local.model_provider_allowed_resolved_networks == ("198.18.0.0/16",)

    with pytest.raises(ValueError, match="只能在 local 或 test 环境启用"):
        Settings(
            environment="production",
            session_cookie_secure=True,
            model_provider_allowed_resolved_networks=("198.18.0.0/15",),
        )
    with pytest.raises(ValueError, match=r"只能包含 198\.18\.0\.0/15"):
        Settings(
            environment="test",
            model_provider_allowed_resolved_networks=("10.0.0.0/8",),
        )
    with pytest.raises(ValueError, match=r"只能包含 198\.18\.0\.0/15"):
        StrictProviderBaseUrlPolicy(
            ("api.synthetic.example",),
            allowed_resolved_networks=("0.0.0.0/0",),
            resolver=fake_ip_resolver,
        )


class ResponsesProbeHttpResponse:
    def __init__(self, content_type: str) -> None:
        self.status = 200
        self._content_type = content_type

    def read(self, size: int) -> bytes:
        assert size == 262_145
        return b""

    def getheader(self, name: str, default: str = "") -> str:
        return self._content_type if name == "Content-Type" else default


class ResponsesProbeHttpsConnection:
    requests: ClassVar[list[dict[str, object]]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self._streaming = False

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> None:
        assert method == "POST"
        assert path == "/responses"
        document = json.loads(body)
        assert document["store"] is False
        assert "messages" not in document
        self._streaming = document.get("stream") is True
        type(self).requests.append(document)
        assert headers["Authorization"] == "Bearer synthetic-secret"

    def getresponse(self) -> ResponsesProbeHttpResponse:
        return ResponsesProbeHttpResponse(
            "text/event-stream" if self._streaming else "application/json"
        )

    def close(self) -> None:
        return None


def test_responses_capability_probe_uses_responses_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Responses 供应商的生成和流式探测必须使用相同协议端点。"""

    ResponsesProbeHttpsConnection.requests = []
    monkeypatch.setattr(
        provider_http,
        "_PinnedHttpsConnection",
        ResponsesProbeHttpsConnection,
    )
    policy = StrictProviderBaseUrlPolicy(
        ("api.synthetic.example",),
        resolver=public_resolver,
    )
    probe = OpenAiCompatibleCapabilityProbe(policy)

    result = probe.probe(
        base_url="https://api.synthetic.example",
        api_key="synthetic-secret",
        model_id="synthetic-codex",
        wire_api="responses",
        capabilities=frozenset({"generation", "streaming"}),
    )

    assert result.status == "passed"
    assert result.capabilities == frozenset({"generation", "streaming"})
    assert len(ResponsesProbeHttpsConnection.requests) == 2
