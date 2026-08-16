"""为应用调用方统一再导出工具执行领域错误。"""

from ai_platform_api.modules.tool_execution.domain.errors import (
    ToolAdapterNotAllowedError,
    ToolAdapterUnavailableError,
    ToolConfirmationRequiredError,
    ToolConfirmationStaleError,
    ToolCredentialExposureDetectedError,
    ToolCredentialUnavailableError,
    ToolDefinitionInvalidError,
    ToolExecutionDeniedError,
    ToolIdempotencyConflictError,
    ToolOutcomeUnknownError,
    ToolResultRejectedError,
    ToolRunBudgetExceededError,
    ToolRunConflictError,
    ToolRunTerminalError,
    ToolVersionNotAvailableError,
)

__all__ = [
    "ToolAdapterNotAllowedError",
    "ToolAdapterUnavailableError",
    "ToolConfirmationRequiredError",
    "ToolConfirmationStaleError",
    "ToolCredentialExposureDetectedError",
    "ToolCredentialUnavailableError",
    "ToolDefinitionInvalidError",
    "ToolExecutionDeniedError",
    "ToolIdempotencyConflictError",
    "ToolOutcomeUnknownError",
    "ToolResultRejectedError",
    "ToolRunBudgetExceededError",
    "ToolRunConflictError",
    "ToolRunTerminalError",
    "ToolVersionNotAvailableError",
]
