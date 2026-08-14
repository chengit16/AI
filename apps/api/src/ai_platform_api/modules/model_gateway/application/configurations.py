from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import PlatformRequestContext
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.model_gateway.domain.configuration import (
    CapabilityProbe,
    CredentialCipher,
    ModelProviderConfiguration,
    ModelProviderCredential,
    ModelProviderUnitOfWork,
    ProviderAdapterKind,
    ProviderBaseUrlPolicy,
    RuntimeProviderAccess,
)
from ai_platform_api.modules.model_gateway.domain.configuration_errors import (
    ModelProviderConfigurationInvalidError,
    ModelProviderConflictError,
    ModelProviderCredentialUnavailableError,
    ModelProviderDataPolicyDeniedError,
    ModelProviderNotFoundError,
    ModelProviderProbeFailedError,
    PlatformAdministratorRequiredError,
)
from ai_platform_api.modules.model_gateway.domain.models import (
    ModelCapability,
    ProviderLocation,
)

PROVIDER_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
SECURITY_RANK: dict[SecurityLevel, int] = {
    "PUBLIC": 0,
    "INTERNAL": 1,
    "CONFIDENTIAL": 2,
    "RESTRICTED": 3,
}

__all__ = ["ModelProviderConfiguration", "ModelProviderConfigurationService"]


class ModelProviderConfigurationService:
    """集中执行平台管理员、密钥版本、能力探测和数据外发审批门禁。"""

    def __init__(
        self,
        unit_of_work: ModelProviderUnitOfWork,
        cipher: CredentialCipher,
        base_url_policy: ProviderBaseUrlPolicy,
        capability_probe: CapabilityProbe,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._cipher = cipher
        self._base_url_policy = base_url_policy
        self._capability_probe = capability_probe

    def list_configurations(
        self, context: PlatformRequestContext
    ) -> tuple[ModelProviderConfiguration, ...]:
        with self._unit_of_work as unit_of_work:
            self._require_administrator(unit_of_work, context.account_id)
            return unit_of_work.providers.list_configurations()

    def create(
        self,
        context: PlatformRequestContext,
        *,
        provider_key: str,
        display_name: str,
        adapter_kind: ProviderAdapterKind,
        base_url: str,
        probe_model_id: str,
        location: ProviderLocation,
        declared_capabilities: frozenset[ModelCapability],
        api_key: str,
    ) -> ModelProviderConfiguration:
        normalized_key = provider_key.strip().casefold()
        normalized_name = display_name.strip()
        normalized_model = probe_model_id.strip()
        if (
            PROVIDER_KEY_PATTERN.fullmatch(normalized_key) is None
            or not normalized_name
            or len(normalized_name) > 120
            or adapter_kind != "openai_compatible"
            or not normalized_model
            or len(normalized_model) > 255
            or not api_key.strip()
            or len(api_key) > 4096
            or "generation" not in declared_capabilities
        ):
            raise ModelProviderConfigurationInvalidError
        # 管理员校验先于 DNS 和主密钥读取，未授权账号不能借配置接口消耗外部资源。
        with self._unit_of_work as unit_of_work:
            self._require_administrator(unit_of_work, context.account_id)
        normalized_url = self._base_url_policy.normalize_and_validate(base_url)
        now = datetime.now(UTC)
        provider_id = uuid4()
        configuration = ModelProviderConfiguration(
            provider_id=provider_id,
            provider_key=normalized_key,
            display_name=normalized_name,
            adapter_kind=adapter_kind,
            base_url=normalized_url,
            probe_model_id=normalized_model,
            location=location,
            declared_capabilities=declared_capabilities,
            policy_review_status="pending",
            max_security_level="PUBLIC",
            retention_days=None,
            training_usage_allowed=False,
            policy_url=None,
            policy_version=None,
            policy_reviewed_by_account_id=None,
            policy_reviewed_at=None,
            probe_status="not_run",
            probed_capabilities=frozenset(),
            last_probe_error_code=None,
            last_probed_at=None,
            status="draft",
            created_by_account_id=context.account_id,
            created_at=now,
            updated_by_account_id=context.account_id,
            updated_at=now,
            version=1,
        )
        credential = self._new_credential(
            provider_id,
            credential_version=1,
            api_key=api_key,
            account_id=context.account_id,
            occurred_at=now,
        )
        try:
            with self._unit_of_work as unit_of_work:
                self._require_administrator(unit_of_work, context.account_id)
                unit_of_work.providers.add_configuration(configuration)
                unit_of_work.providers.replace_active_credential(credential)
                self._audit(unit_of_work, context, provider_id, "model_provider.created", now)
                unit_of_work.commit()
        except ModelProviderConflictError:
            raise
        return configuration

    def rotate_credential(
        self,
        context: PlatformRequestContext,
        provider_id: UUID,
        *,
        api_key: str,
    ) -> ModelProviderConfiguration:
        if not api_key.strip() or len(api_key) > 4096:
            raise ModelProviderConfigurationInvalidError
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            self._require_administrator(unit_of_work, context.account_id)
            current = self._require_configuration(unit_of_work, provider_id, for_update=True)
            credential_version = unit_of_work.providers.next_credential_version(provider_id)
            credential = self._new_credential(
                provider_id,
                credential_version=credential_version,
                api_key=api_key,
                account_id=context.account_id,
                occurred_at=now,
            )
            updated = replace(
                current,
                probe_status="not_run",
                probed_capabilities=frozenset(),
                last_probe_error_code=None,
                last_probed_at=None,
                status="draft",
                updated_by_account_id=context.account_id,
                updated_at=now,
                version=current.version + 1,
            )
            unit_of_work.providers.replace_active_credential(credential)
            unit_of_work.providers.save_configuration(updated)
            self._audit(
                unit_of_work,
                context,
                provider_id,
                "model_provider.credential_rotated",
                now,
                {"credential_version": credential_version},
            )
            unit_of_work.commit()
        return updated

    def review_data_policy(
        self,
        context: PlatformRequestContext,
        provider_id: UUID,
        *,
        approved: bool,
        max_security_level: SecurityLevel,
        retention_days: int | None,
        training_usage_allowed: bool,
        policy_url: str | None,
        policy_version: str | None,
    ) -> ModelProviderConfiguration:
        normalized_policy_url = _validated_policy_url(policy_url)
        normalized_policy_version = policy_version.strip() if policy_version else None
        if (
            (retention_days is not None and not 0 <= retention_days <= 3650)
            or (normalized_policy_version is not None and len(normalized_policy_version) > 128)
            or (
                approved
                and (
                    retention_days is None
                    or normalized_policy_version is None
                    or (training_usage_allowed and max_security_level != "PUBLIC")
                )
            )
        ):
            raise ModelProviderConfigurationInvalidError
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            self._require_administrator(unit_of_work, context.account_id)
            current = self._require_configuration(unit_of_work, provider_id, for_update=True)
            if approved and current.location == "external" and normalized_policy_url is None:
                raise ModelProviderConfigurationInvalidError
            updated = replace(
                current,
                policy_review_status="approved" if approved else "rejected",
                max_security_level=max_security_level,
                retention_days=retention_days,
                training_usage_allowed=training_usage_allowed,
                policy_url=normalized_policy_url,
                policy_version=normalized_policy_version,
                policy_reviewed_by_account_id=context.account_id,
                policy_reviewed_at=now,
                status="draft" if current.status == "active" else current.status,
                updated_by_account_id=context.account_id,
                updated_at=now,
                version=current.version + 1,
            )
            unit_of_work.providers.save_configuration(updated)
            self._audit(
                unit_of_work,
                context,
                provider_id,
                "model_provider.data_policy_reviewed",
                now,
                {
                    "decision": updated.policy_review_status,
                    "max_security_level": max_security_level,
                    "policy_version": normalized_policy_version or "not_set",
                },
            )
            unit_of_work.commit()
        return updated

    def probe(
        self,
        context: PlatformRequestContext,
        provider_id: UUID,
    ) -> ModelProviderConfiguration:
        with self._unit_of_work as unit_of_work:
            self._require_administrator(unit_of_work, context.account_id)
            current = self._require_configuration(unit_of_work, provider_id)
            credential = unit_of_work.providers.get_active_credential(provider_id)
            if credential is None:
                raise ModelProviderCredentialUnavailableError
        api_key = self._cipher.decrypt(
            credential.envelope,
            associated_data=_credential_associated_data(credential),
        )
        result = self._capability_probe.probe(
            base_url=current.base_url,
            api_key=api_key,
            model_id=current.probe_model_id,
            capabilities=current.declared_capabilities,
        )
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            self._require_administrator(unit_of_work, context.account_id)
            latest = self._require_configuration(unit_of_work, provider_id, for_update=True)
            latest_credential = unit_of_work.providers.get_active_credential(
                provider_id, for_update=True
            )
            if latest.version != current.version or latest_credential != credential:
                raise ModelProviderConflictError
            updated = replace(
                latest,
                probe_status=result.status,
                probed_capabilities=result.capabilities,
                last_probe_error_code=result.error_code,
                last_probed_at=now,
                updated_by_account_id=context.account_id,
                updated_at=now,
                version=latest.version + 1,
            )
            unit_of_work.providers.save_configuration(updated)
            self._audit(
                unit_of_work,
                context,
                provider_id,
                "model_provider.capability_probed",
                now,
                {
                    "status": result.status,
                    "capabilities": sorted(result.capabilities),
                    "error_code": result.error_code or "none",
                },
            )
            unit_of_work.commit()
        if result.status == "failed":
            raise ModelProviderProbeFailedError
        return updated

    def activate(
        self, context: PlatformRequestContext, provider_id: UUID
    ) -> ModelProviderConfiguration:
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            self._require_administrator(unit_of_work, context.account_id)
            current = self._require_configuration(unit_of_work, provider_id, for_update=True)
            if (
                not current.ready_to_activate
                or unit_of_work.providers.get_active_credential(provider_id) is None
            ):
                raise ModelProviderDataPolicyDeniedError
            updated = replace(
                current,
                status="active",
                updated_by_account_id=context.account_id,
                updated_at=now,
                version=current.version + 1,
            )
            unit_of_work.providers.save_configuration(updated)
            self._audit(unit_of_work, context, provider_id, "model_provider.activated", now)
            unit_of_work.commit()
        return updated

    def disable(
        self, context: PlatformRequestContext, provider_id: UUID
    ) -> ModelProviderConfiguration:
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            self._require_administrator(unit_of_work, context.account_id)
            current = self._require_configuration(unit_of_work, provider_id, for_update=True)
            updated = replace(
                current,
                status="disabled",
                updated_by_account_id=context.account_id,
                updated_at=now,
                version=current.version + 1,
            )
            unit_of_work.providers.save_configuration(updated)
            self._audit(unit_of_work, context, provider_id, "model_provider.disabled", now)
            unit_of_work.commit()
        return updated

    def require_export_allowed(
        self,
        provider_id: UUID,
        *,
        security_level: SecurityLevel,
    ) -> ModelProviderConfiguration:
        """P1D-06 运行层只能通过本方法取得已激活且可外发的配置。"""

        with self._unit_of_work as unit_of_work:
            configuration = unit_of_work.providers.get_configuration(provider_id)
        if configuration is None or not _export_allowed(configuration, security_level):
            raise ModelProviderDataPolicyDeniedError
        return configuration

    def resolve_runtime_access(
        self,
        provider_id: UUID,
        *,
        security_level: SecurityLevel,
    ) -> RuntimeProviderAccess:
        """只在模型调用边缘恢复明文 Key，业务模块无法直接读取凭证表。"""

        with self._unit_of_work as unit_of_work:
            configuration = unit_of_work.providers.get_configuration(provider_id)
            credential = unit_of_work.providers.get_active_credential(provider_id)
        if configuration is None or not _export_allowed(configuration, security_level):
            raise ModelProviderDataPolicyDeniedError
        if credential is None:
            raise ModelProviderCredentialUnavailableError
        api_key = self._cipher.decrypt(
            credential.envelope,
            associated_data=_credential_associated_data(credential),
        )
        return RuntimeProviderAccess(configuration, api_key, credential.credential_version)

    @staticmethod
    def _require_administrator(unit_of_work: ModelProviderUnitOfWork, account_id: UUID) -> None:
        if not unit_of_work.providers.is_platform_administrator(account_id):
            raise PlatformAdministratorRequiredError

    @staticmethod
    def _require_configuration(
        unit_of_work: ModelProviderUnitOfWork,
        provider_id: UUID,
        *,
        for_update: bool = False,
    ) -> ModelProviderConfiguration:
        configuration = unit_of_work.providers.get_configuration(provider_id, for_update=for_update)
        if configuration is None:
            raise ModelProviderNotFoundError
        return configuration

    def _new_credential(
        self,
        provider_id: UUID,
        *,
        credential_version: int,
        api_key: str,
        account_id: UUID,
        occurred_at: datetime,
    ) -> ModelProviderCredential:
        credential_id = uuid4()
        stub = ModelProviderCredential(
            credential_id,
            provider_id,
            credential_version,
            # 加密关联数据包含凭证主键和版本，密文不能被复制到其他记录继续解密。
            self._cipher.encrypt(
                api_key,
                associated_data=_credential_associated_data_values(
                    provider_id, credential_id, credential_version
                ),
            ),
            "active",
            account_id,
            occurred_at,
            None,
        )
        return stub

    @staticmethod
    def _audit(
        unit_of_work: ModelProviderUnitOfWork,
        context: PlatformRequestContext,
        provider_id: UUID,
        action: str,
        occurred_at: datetime,
        details: dict[str, object] | None = None,
    ) -> None:
        unit_of_work.audit.add(
            account_id=context.account_id,
            provider_id=provider_id,
            action=action,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            occurred_at=occurred_at,
            details=details or {},
        )


def _credential_associated_data(credential: ModelProviderCredential) -> bytes:
    return _credential_associated_data_values(
        credential.provider_id,
        credential.credential_id,
        credential.credential_version,
    )


def _credential_associated_data_values(
    provider_id: UUID,
    credential_id: UUID,
    credential_version: int,
) -> bytes:
    return (
        f"model-provider-credential:v1:{provider_id}:{credential_id}:{credential_version}"
    ).encode()


def _validated_policy_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.hostname
        or parsed.fragment
        or len(value) > 2048
    ):
        raise ModelProviderConfigurationInvalidError
    return parsed.geturl()


def _export_allowed(
    configuration: ModelProviderConfiguration,
    security_level: SecurityLevel,
) -> bool:
    return bool(
        configuration.status == "active"
        and configuration.policy_review_status == "approved"
        and SECURITY_RANK[security_level] <= SECURITY_RANK[configuration.max_security_level]
        and (not configuration.training_usage_allowed or security_level == "PUBLIC")
    )
