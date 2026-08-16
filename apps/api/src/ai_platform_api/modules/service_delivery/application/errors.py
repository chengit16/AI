"""定义统一服务出口的稳定失败语义。"""

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.modules.service_delivery.domain.models import (
    ServiceInvocationRateLimitedError,
    ServiceInvocationRateLimitUnavailableError,
)


class ServiceInvocationDeniedError(PlatformError):
    """当前主体、凭证 Scope 或服务访问策略不允许调用目标服务。"""

    error_code = "POLICY_DENIED"


__all__ = [
    "ServiceInvocationDeniedError",
    "ServiceInvocationRateLimitUnavailableError",
    "ServiceInvocationRateLimitedError",
]
