from ai_platform_api.common.errors import PlatformError


class AiRuntimeConfigInvalidError(PlatformError):
    """运行配置、组件版本、路由或预算不满足确定性约束。"""

    error_code = "AI_RUNTIME_CONFIG_INVALID"


class AiRuntimeConfigNotFoundError(PlatformError):
    """指定的不可变运行配置版本不存在。"""

    error_code = "AI_RUNTIME_CONFIG_NOT_FOUND"


class AiRuntimeConfigConflictError(PlatformError):
    """相同配置已存在，或发布指针发生并发冲突。"""

    error_code = "AI_RUNTIME_CONFIG_CONFLICT"


class AiRuntimeConfigNotActiveError(PlatformError):
    """平台尚未发布可用于模型调用的运行配置。"""

    error_code = "AI_RUNTIME_CONFIG_NOT_ACTIVE"


class ModelInvocationConflictError(PlatformError):
    """调用标识已经被占用，禁止重复触发供应商计费。"""

    error_code = "MODEL_INVOCATION_CONFLICT"
