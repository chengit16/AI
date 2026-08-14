"""装配平台管理员限定的模型供应商和运行配置服务。"""

from typing import Annotated
from uuid import UUID

from fastapi import Header, Request

from ai_platform_api.common.request_context import PlatformRequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.api.dependencies import authentication_service
from ai_platform_api.modules.model_gateway.application.configurations import (
    ModelProviderConfigurationService,
)
from ai_platform_api.modules.model_gateway.application.runtime_configurations import (
    AiRuntimeConfigurationService,
)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def model_provider_configuration_service(
    request: Request,
) -> ModelProviderConfigurationService:
    """从应用容器解析模型供应商配置服务，避免路由自行装配基础设施。"""

    service = getattr(request.app.state, "model_provider_configuration_service", None)
    if not isinstance(service, ModelProviderConfigurationService):
        raise RuntimeError("模型供应商配置服务尚未完成装配")
    return service


def ai_runtime_configuration_service(request: Request) -> AiRuntimeConfigurationService:
    """从应用容器解析AI运行时配置服务，避免路由自行装配基础设施。"""

    service = getattr(request.app.state, "ai_runtime_configuration_service", None)
    if not isinstance(service, AiRuntimeConfigurationService):
        raise RuntimeError("AI 运行配置服务尚未完成装配")
    return service


def trusted_platform_context(
    request: Request,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> PlatformRequestContext:
    """从应用容器解析可信平台上下文，避免路由自行装配基础设施。"""

    state = request.scope.get("state", {})
    request_id = state.get("request_id")
    trace = state.get("trace_context")
    if not isinstance(request_id, UUID) or not isinstance(trace, TraceContext):
        raise RuntimeError("可信请求标识尚未建立")
    service = authentication_service(request)
    return service.platform_browser_context(
        session_token=request.cookies.get("ai_platform_session"),
        csrf_token=csrf_token,
        require_csrf=request.method not in SAFE_METHODS,
        request_id=request_id,
        trace=trace,
    )
