"""定义工作空间隔离治理的稳定应用错误。"""

from ai_platform_api.common.errors import PlatformError


class IsolationDeniedError(PlatformError):
    """主体、空间、套餐或合规资格不满足时统一拒绝。"""

    error_code = "POLICY_DENIED"


class IsolationNotConfiguredError(PlatformError):
    """真实合规策略或目标隔离环境尚未配置。"""

    error_code = "RUNTIME_NOT_CONFIGURED"


class IsolationNotFoundError(PlatformError):
    """目标迁移计划不属于当前工作空间或不存在。"""

    error_code = "RESOURCE_NOT_FOUND"


class IsolationConflictError(PlatformError):
    """套餐、路由或迁移状态发生并发漂移。"""

    error_code = "IDEMPOTENCY_CONFLICT"


class IsolationValidationError(PlatformError):
    """请求等级、状态转换或证据不符合冻结契约。"""

    error_code = "VALIDATION_ERROR"
