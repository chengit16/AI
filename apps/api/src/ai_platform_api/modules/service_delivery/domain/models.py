"""定义服务调用接入层依赖的最小限流端口。"""

from typing import Protocol
from uuid import UUID

from ai_platform_api.common.errors import PlatformError


class ServiceInvocationRateLimitedError(PlatformError):
    """当前 Actor 在固定窗口内发起的新服务调用超过限制。"""

    error_code = "SERVICE_RATE_LIMITED"


class ServiceInvocationRateLimitUnavailableError(PlatformError):
    """限流事实暂时不可用，服务入口按失败关闭处理。"""

    error_code = "SERVICE_RATE_LIMIT_UNAVAILABLE"


class InvocationRateLimiter(Protocol):
    """按工作空间、服务和 Actor 原子限制新调用，幂等重试不重复计数。"""

    def consume(
        self,
        workspace_id: UUID,
        service_id: UUID,
        actor_id: UUID,
        idempotency_key: str,
    ) -> None: ...

    def close(self) -> None: ...
