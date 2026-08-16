"""定义工具注册与目录查询可稳定映射的应用错误。"""

from ai_platform_api.common.errors import PlatformError


class ToolDefinitionInvalidError(PlatformError):
    """工具定义、Schema、摘要或治理字段不满足冻结约束。"""

    error_code = "TOOL_DEFINITION_INVALID"


class ToolVersionNotAvailableError(PlatformError):
    """目标版本不存在、已退休或不属于工作空间当前套餐。"""

    error_code = "TOOL_VERSION_NOT_AVAILABLE"


class ToolExecutionDeniedError(PlatformError):
    """可信主体、工作空间、套餐或当前策略不允许访问工具。"""

    error_code = "TOOL_EXECUTION_DENIED"
