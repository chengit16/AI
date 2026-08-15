"""定义服务治理稳定错误并复用平台错误目录。"""

from ai_platform_api.common.errors import PlatformError


class ServiceDeniedError(PlatformError):
    """表示可信上下文没有目标服务或工作空间管理范围。"""

    error_code = "POLICY_DENIED"


class ServiceNotFoundError(PlatformError):
    """表示服务事实在当前工作空间内不可见。"""

    error_code = "RESOURCE_NOT_FOUND"


class ServiceValidationError(PlatformError):
    """表示服务字段或访问策略不满足冻结约束。"""

    error_code = "VALIDATION_ERROR"


class ServiceRouteConflictError(PlatformError):
    """表示服务或当前路由 generation 已经发生并发变化。"""

    error_code = "SERVICE_ROUTE_CONFLICT"


class ServiceRouteUnavailableError(PlatformError):
    """表示服务没有可安全使用的有效 Release 路由。"""

    error_code = "SERVICE_ROUTE_UNAVAILABLE"


class ServiceIdempotencyConflictError(PlatformError):
    """表示相同幂等键已经绑定不同服务管理请求。"""

    error_code = "IDEMPOTENCY_CONFLICT"
