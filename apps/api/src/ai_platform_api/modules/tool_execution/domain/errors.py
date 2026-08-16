"""定义工具注册、授权和任务状态可跨层稳定识别的领域错误。"""

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


class ToolRunConflictError(PlatformError):
    """Run、Step、Attempt 或租约代际已被其他写入方推进。"""

    error_code = "TOOL_RUN_CONFLICT"


class ToolRunTerminalError(PlatformError):
    """父级已终止，当前请求不能覆盖稳定终态。"""

    error_code = "TOOL_RUN_TERMINAL"


class ToolRunBudgetExceededError(PlatformError):
    """任务已经超过冻结的步骤、尝试、时限或成本预算。"""

    error_code = "TOOL_RUN_BUDGET_EXCEEDED"
