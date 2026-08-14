"""从激活配置构建可追溯模型请求并记录调用事实。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.model_gateway.application.gateway import (
    CircuitState,
    ModelGateway,
)
from ai_platform_api.modules.model_gateway.domain.configuration import RuntimeProviderAccess
from ai_platform_api.modules.model_gateway.domain.configuration_errors import (
    ModelProviderCredentialUnavailableError,
    ModelProviderDataPolicyDeniedError,
)
from ai_platform_api.modules.model_gateway.domain.errors import (
    ModelDataBoundaryDeniedError,
    ModelGatewayUnavailableError,
    ProviderInvocationError,
)
from ai_platform_api.modules.model_gateway.domain.models import (
    ModelProvider,
    ModelRequest,
    ModelResult,
    ModelRoute,
    ProviderResponse,
)
from ai_platform_api.modules.model_gateway.domain.runtime import (
    InvocationStatus,
    RuntimeConfigurationReader,
    RuntimeInvocationOutcome,
    RuntimeInvocationStore,
)
from ai_platform_api.modules.model_gateway.domain.runtime_errors import (
    AiRuntimeConfigNotActiveError,
)


class RuntimeProviderFactory(Protocol):
    """按活动配置和短暂凭据创建单次调用使用的供应商 Adapter。"""

    def create(self, access: RuntimeProviderAccess) -> ModelProvider: ...


class RuntimeProviderAccessResolver(Protocol):
    """解析指定供应商版本的活动配置与凭据，版本不一致时失败关闭。"""

    def resolve_runtime_access(
        self,
        provider_id: UUID,
        *,
        security_level: SecurityLevel,
    ) -> RuntimeProviderAccess: ...


@dataclass
class _GovernedProvider:
    """每次调用都重新执行数据政策和凭证复核，避免缓存已撤销的明文 Key。"""

    provider_uuid: UUID
    provider_id: str
    access_service: RuntimeProviderAccessResolver
    factory: RuntimeProviderFactory
    credential_versions: dict[str, int]

    def invoke(
        self,
        request: ModelRequest,
        model_id: str,
        timeout_ms: int,
    ) -> ProviderResponse:
        try:
            access = self.access_service.resolve_runtime_access(
                self.provider_uuid,
                security_level=request.security_level,
            )
        except ModelProviderDataPolicyDeniedError as error:
            raise ProviderInvocationError(
                kind="data_boundary",
                retryable=False,
                fallback_allowed=True,
            ) from error
        except ModelProviderCredentialUnavailableError as error:
            raise ProviderInvocationError(
                kind="unavailable",
                retryable=False,
                fallback_allowed=True,
            ) from error
        self.credential_versions[self.provider_id] = access.credential_version
        return self.factory.create(access).invoke(request, model_id, timeout_ms)


class RuntimeModelGatewayService:
    """以当前不可变配置执行模型调用，并在返回前固化版本、用量和尝试事实。"""

    def __init__(
        self,
        configurations: RuntimeConfigurationReader,
        invocations: RuntimeInvocationStore,
        provider_access: RuntimeProviderAccessResolver,
        provider_factory: RuntimeProviderFactory,
    ) -> None:
        self._configurations = configurations
        self._invocations = invocations
        self._provider_access = provider_access
        self._provider_factory = provider_factory
        # 首期本地单 API 进程共享熔断状态；多实例共享状态在容量阶段按实测再引入。
        self._circuit_states: dict[str, CircuitState] = {}

    def invoke(self, request: ModelRequest) -> ModelResult:
        """读取当前发布配置和短暂凭据后调用模型，并持久化最终调用结果。"""

        # 1. 固定本次调用使用的发布配置，并先预留调用记录防止进程失败后无迹可查。
        configuration = self._configurations.current()
        if configuration is None:
            raise AiRuntimeConfigNotActiveError
        self._invocations.reserve(request, configuration.runtime_config_version_id)

        # 2. 为快照路由装配受治理供应商；凭据在实际调用时短暂解析并记录版本。
        credential_versions: dict[str, int] = {}
        providers: dict[str, ModelProvider] = {
            str(route.provider_id): _GovernedProvider(
                provider_uuid=route.provider_id,
                provider_id=str(route.provider_id),
                access_service=self._provider_access,
                factory=self._provider_factory,
                credential_versions=credential_versions,
            )
            for route in configuration.routes
        }
        routes = tuple(
            ModelRoute(
                route_id=str(route.route_id),
                provider_id=str(route.provider_id),
                model_id=route.model_id,
                location=route.location,
                capabilities=route.capabilities,
                input_price_microunits_per_million_tokens=(
                    route.input_price_microunits_per_million_tokens
                ),
                output_price_microunits_per_million_tokens=(
                    route.output_price_microunits_per_million_tokens
                ),
            )
            for route in sorted(configuration.routes, key=lambda item: item.priority)
        )
        gateway = ModelGateway(
            routes,
            providers,
            configuration.policy,
            circuit_states=self._circuit_states,
        )
        # 3. 调用失败也必须写入尝试链、稳定错误码和已使用凭据版本，再向上抛出。
        try:
            result = replace(
                gateway.invoke(request),
                runtime_config_version_id=configuration.runtime_config_version_id,
            )
        except PlatformError as error:
            attempts = (
                error.attempts
                if isinstance(
                    error,
                    (ModelGatewayUnavailableError, ModelDataBoundaryDeniedError),
                )
                else ()
            )
            status: InvocationStatus = (
                "rejected" if error.error_code == "MODEL_REQUEST_REJECTED" else "failed"
            )
            self._invocations.complete(
                RuntimeInvocationOutcome(
                    request=request,
                    runtime_config_version_id=configuration.runtime_config_version_id,
                    status=status,
                    result=None,
                    attempts=attempts,
                    credential_versions=credential_versions,
                    error_code=error.error_code,
                    completed_at=datetime.now(UTC),
                )
            )
            raise

        # 4. 成功或规则降级统一完成调用记录，结果绑定不可变运行配置版本。
        self._invocations.complete(
            RuntimeInvocationOutcome(
                request=request,
                runtime_config_version_id=configuration.runtime_config_version_id,
                status="degraded" if result.degraded else "succeeded",
                result=result,
                attempts=result.attempts,
                credential_versions=credential_versions,
                error_code=None,
                completed_at=datetime.now(UTC),
            )
        )
        return result
