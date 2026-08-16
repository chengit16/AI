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


class ToolAdapterNotAllowedError(PlatformError):
    """当前工具定义不是阶段 4 允许的内部只读 Adapter。"""

    error_code = "TOOL_ADAPTER_NOT_ALLOWED"


class ToolAdapterUnavailableError(PlatformError):
    """已登记的内部只读 Adapter 暂时不可用。"""

    error_code = "TOOL_ADAPTER_UNAVAILABLE"


class ToolCredentialUnavailableError(PlatformError):
    """工具调用缺少当前活动、精确绑定且可解密的凭证。"""

    error_code = "TOOL_CREDENTIAL_UNAVAILABLE"


class ToolCredentialExposureDetectedError(PlatformError):
    """Adapter 异常携带凭证明文，调用结果必须隔离且不得继续传播。"""

    error_code = "TOOL_CREDENTIAL_EXPOSURE_DETECTED"


class ToolIdempotencyConflictError(PlatformError):
    """稳定幂等键已经绑定不同的副作用请求摘要。"""

    error_code = "IDEMPOTENCY_CONFLICT"


class ToolOutcomeUnknownError(PlatformError):
    """Adapter 可能已接收副作用，必须人工对账且禁止自动重放。"""

    error_code = "TOOL_OUTCOME_UNKNOWN"


class ToolResultRejectedError(PlatformError):
    """工具结果未通过结构、大小、敏感字段或 Prompt Injection 检查。"""

    error_code = "TOOL_RESULT_REJECTED"


class ToolConfirmationRequiredError(PlatformError):
    """副作用工具尚未获得与当前调用完全绑定的有效确认。"""

    error_code = "TOOL_CONFIRMATION_REQUIRED"


class ToolConfirmationStaleError(PlatformError):
    """工具确认已经过期、撤回或与当前调用及策略版本不一致。"""

    error_code = "TOOL_CONFIRMATION_STALE"
