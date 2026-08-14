from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import PlatformRequestContext
from ai_platform_api.common.security import EnvelopeSecretCipher, MasterKeyFile
from ai_platform_api.common.trace import TraceContext
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
from ai_platform_api.modules.model_gateway.infrastructure.provider_http import (
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
        capabilities: frozenset[ModelCapability],
    ) -> CapabilityProbeResult:
        assert base_url == "https://api.synthetic.example/v1"
        assert api_key.startswith("synthetic-")
        assert model_id == "synthetic-chat"
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


def public_resolver(*args: object, **kwargs: object) -> list[tuple[Any, ...]]:
    del args, kwargs
    return [(2, 1, 6, "", ("93.184.216.34", 443))]


def mixed_resolver(*args: object, **kwargs: object) -> list[tuple[Any, ...]]:
    del args, kwargs
    return [
        (2, 1, 6, "", ("93.184.216.34", 443)),
        (2, 1, 6, "", ("127.0.0.1", 443)),
    ]


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
