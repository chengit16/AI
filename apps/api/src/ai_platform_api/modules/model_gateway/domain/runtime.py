"""定义不可变运行配置、组件版本、发布指针和调用记录领域模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_api.modules.model_gateway.domain.configuration import (
    ModelProviderConfiguration,
)
from ai_platform_api.modules.model_gateway.domain.models import (
    GatewayPolicy,
    ModelAttempt,
    ModelCapability,
    ModelRequest,
    ModelResult,
    ProviderLocation,
)

InvocationStatus = Literal["running", "succeeded", "degraded", "failed", "rejected"]


@dataclass(frozen=True)
class RuntimeComponentVersions:
    """冻结一次 AI 运行所依赖的检索、安全及后置接口版本。"""

    chunking: str
    embedding: str
    index_schema: str
    reranker: str
    retrieval: str
    source_ranking: str
    safety: str
    data_source_interface: str
    relevance_grader_interface: str
    multimodal_router_interface: str


@dataclass(frozen=True)
class RuntimeRouteDraft:
    """描述创建运行配置时尚未绑定供应商配置版本的路由。"""

    provider_id: UUID
    priority: int
    model_id: str
    capabilities: frozenset[ModelCapability]
    input_price_microunits_per_million_tokens: int
    output_price_microunits_per_million_tokens: int


@dataclass(frozen=True)
class RuntimeRouteSnapshot:
    """冻结供应商版本、模型能力、优先级和成本的运行路由。"""

    route_id: UUID
    provider_id: UUID
    provider_configuration_version: int
    priority: int
    model_id: str
    location: ProviderLocation
    capabilities: frozenset[ModelCapability]
    input_price_microunits_per_million_tokens: int
    output_price_microunits_per_million_tokens: int
    currency: Literal["CNY"] = "CNY"


@dataclass(frozen=True)
class AiRuntimeConfigVersion:
    """表示内容寻址且发布后不可变的 AI 运行配置版本。"""

    runtime_config_version_id: UUID
    version_number: int
    display_name: str
    content_hash: str
    system_prompt_template: str
    system_prompt_hash: str
    components: RuntimeComponentVersions
    policy: GatewayPolicy
    routes: tuple[RuntimeRouteSnapshot, ...]
    created_by_account_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class AiRuntimeConfigPublication:
    """记录当前发布配置及用于缓存失效的递增代次。"""

    runtime_config_version_id: UUID
    generation: int
    published_by_account_id: UUID
    published_at: datetime


@dataclass(frozen=True)
class RuntimeInvocationOutcome:
    """汇总一次模型调用的配置、尝试链、凭据版本和最终状态。"""

    request: ModelRequest
    runtime_config_version_id: UUID
    status: InvocationStatus
    result: ModelResult | None
    attempts: tuple[ModelAttempt, ...]
    credential_versions: dict[str, int]
    error_code: str | None
    completed_at: datetime


class RuntimeConfigurationRepository(Protocol):
    """管理不可变运行配置版本及唯一当前发布指针。"""

    def is_platform_administrator(self, account_id: UUID) -> bool: ...

    def list_configurations(self) -> tuple[AiRuntimeConfigVersion, ...]: ...

    def get_configuration(
        self, runtime_config_version_id: UUID, *, for_update: bool = False
    ) -> AiRuntimeConfigVersion | None: ...

    def get_current_publication(
        self, *, for_update: bool = False
    ) -> AiRuntimeConfigPublication | None: ...

    def get_provider_configuration(
        self, provider_id: UUID
    ) -> ModelProviderConfiguration | None: ...

    def next_version_number(self) -> int: ...

    def add_configuration(self, configuration: AiRuntimeConfigVersion) -> None: ...

    def publish(self, publication: AiRuntimeConfigPublication) -> None: ...


class RuntimeConfigAuditWriter(Protocol):
    """记录运行配置创建和发布的操作者与追踪上下文。"""

    def add(
        self,
        *,
        account_id: UUID,
        runtime_config_version_id: UUID,
        action: str,
        request_id: UUID,
        trace_id: str,
        occurred_at: datetime,
        details: dict[str, object],
    ) -> None: ...


class RuntimeConfigurationUnitOfWork(Protocol):
    """保证运行配置、发布指针和平台审计原子提交。"""

    @property
    def runtime_configs(self) -> RuntimeConfigurationRepository: ...

    @property
    def audit(self) -> RuntimeConfigAuditWriter: ...

    def __enter__(self) -> RuntimeConfigurationUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class RuntimeConfigurationReader(Protocol):
    """向调用路径提供当前发布配置和关联供应商凭据。"""

    def current(self) -> AiRuntimeConfigVersion | None: ...


class RuntimeInvocationStore(Protocol):
    """持久化模型调用的开始事实与最终结果，供审计和成本追踪。"""

    def reserve(self, request: ModelRequest, runtime_config_version_id: UUID) -> None: ...

    def complete(self, outcome: RuntimeInvocationOutcome) -> None: ...
