"""定义 Runtime 发布装载的稳定失败语义。"""

from ai_platform_api.common.errors import PlatformError


class AgentRuntimeReleaseRequiredError(PlatformError):
    """表示执行输入不是完整、有效且与 Route 一致的不可变 Release。"""

    error_code = "AGENT_RUNTIME_RELEASE_REQUIRED"


class RuntimeServiceRouteUnavailableError(PlatformError):
    """表示发布 Source 与可信缓存都不能提供可执行 Route。"""

    error_code = "SERVICE_ROUTE_UNAVAILABLE"
