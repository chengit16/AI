"""导出服务调用入口所需的限流端口。"""

from ai_platform_api.modules.service_delivery.domain.models import InvocationRateLimiter

__all__ = ["InvocationRateLimiter"]
