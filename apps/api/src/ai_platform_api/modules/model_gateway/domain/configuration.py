"""定义供应商配置、凭证版本、政策审核和能力探测领域模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_api.common.security import EncryptedSecret
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.model_gateway.domain.models import ModelCapability, ProviderLocation

ProviderAdapterKind = Literal["openai_compatible"]
ProviderWireApi = Literal["chat_completions", "responses"]
ProviderStatus = Literal["draft", "active", "disabled"]
PolicyReviewStatus = Literal["pending", "approved", "rejected"]
ProbeStatus = Literal["not_run", "passed", "failed"]
CredentialStatus = Literal["active", "revoked"]


@dataclass(frozen=True)
class ModelProviderConfiguration:
    """记录模型供应商地址、合规审核、能力探测和启用状态。"""

    provider_id: UUID
    provider_key: str
    display_name: str
    adapter_kind: ProviderAdapterKind
    wire_api: ProviderWireApi
    base_url: str
    probe_model_id: str
    location: ProviderLocation
    declared_capabilities: frozenset[ModelCapability]
    policy_review_status: PolicyReviewStatus
    max_security_level: SecurityLevel
    retention_days: int | None
    training_usage_allowed: bool
    policy_url: str | None
    policy_version: str | None
    policy_reviewed_by_account_id: UUID | None
    policy_reviewed_at: datetime | None
    probe_status: ProbeStatus
    probed_capabilities: frozenset[ModelCapability]
    last_probe_error_code: str | None
    last_probed_at: datetime | None
    status: ProviderStatus
    created_by_account_id: UUID
    created_at: datetime
    updated_by_account_id: UUID
    updated_at: datetime
    version: int

    @property
    def ready_to_activate(self) -> bool:
        return (
            self.policy_review_status == "approved"
            and self.probe_status == "passed"
            and self.declared_capabilities.issubset(self.probed_capabilities)
        )


@dataclass(frozen=True)
class ModelProviderCredential:
    """保存供应商凭据的信封密文、轮换版本和撤销状态。"""

    credential_id: UUID
    provider_id: UUID
    credential_version: int
    envelope: EncryptedSecret
    status: CredentialStatus
    created_by_account_id: UUID
    created_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True)
class CapabilityProbeResult:
    """描述供应商连通性探测后的可用能力或稳定错误码。"""

    status: Literal["passed", "failed"]
    capabilities: frozenset[ModelCapability]
    error_code: str | None = None


@dataclass(frozen=True)
class RuntimeProviderAccess:
    """仅供模型网关装配层短暂持有，禁止进入 HTTP 响应、日志或持久化事件。"""

    configuration: ModelProviderConfiguration
    api_key: str
    credential_version: int


class ModelProviderRepository(Protocol):
    """维护平台级供应商配置及唯一活动凭据版本。"""

    def is_platform_administrator(self, account_id: UUID) -> bool: ...

    def list_configurations(self) -> tuple[ModelProviderConfiguration, ...]: ...

    def get_configuration(
        self, provider_id: UUID, *, for_update: bool = False
    ) -> ModelProviderConfiguration | None: ...

    def add_configuration(self, configuration: ModelProviderConfiguration) -> None: ...

    def save_configuration(self, configuration: ModelProviderConfiguration) -> None: ...

    def next_credential_version(self, provider_id: UUID) -> int: ...

    def get_active_credential(
        self, provider_id: UUID, *, for_update: bool = False
    ) -> ModelProviderCredential | None: ...

    def replace_active_credential(self, credential: ModelProviderCredential) -> None: ...


class PlatformAuditWriter(Protocol):
    """记录不隶属于单个工作空间的供应商管理审计。"""

    def add(
        self,
        *,
        account_id: UUID,
        provider_id: UUID,
        action: str,
        request_id: UUID,
        trace_id: str,
        occurred_at: datetime,
        details: dict[str, object],
    ) -> None: ...


class ModelProviderUnitOfWork(Protocol):
    """保证供应商配置、凭据轮换和平台审计原子提交。"""

    @property
    def providers(self) -> ModelProviderRepository: ...

    @property
    def audit(self) -> PlatformAuditWriter: ...

    def __enter__(self) -> ModelProviderUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class CapabilityProbe(Protocol):
    """通过供应商公开接口验证声明能力，失败时返回稳定错误码。"""

    def probe(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str,
        wire_api: ProviderWireApi,
        capabilities: frozenset[ModelCapability],
    ) -> CapabilityProbeResult: ...


class CredentialCipher(Protocol):
    """使用关联数据加解密供应商凭据，防止密文跨供应商替换。"""

    def encrypt(self, plaintext: str, *, associated_data: bytes) -> EncryptedSecret: ...

    def decrypt(self, secret: EncryptedSecret, *, associated_data: bytes) -> str: ...


class ProviderBaseUrlPolicy(Protocol):
    """规范化供应商地址并拒绝本地或私有网络的 SSRF 风险。"""

    def normalize_and_validate(self, value: str) -> str: ...
