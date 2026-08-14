"""定义助手问答事实的稳定应用错误。"""

from ai_platform_api.common.errors import PlatformError


class AssistantDeniedError(PlatformError):
    """当前主体不能访问目标空间或他人私有会话。"""

    error_code = "POLICY_DENIED"


class AssistantNotFoundError(PlatformError):
    """会话不存在、已不可见或不属于当前创建者。"""

    error_code = "RESOURCE_NOT_FOUND"


class AssistantValidationError(PlatformError):
    """会话标题、消息 Part、列表上限或幂等键不满足约束。"""

    error_code = "VALIDATION_ERROR"


class AssistantIdempotencyConflictError(PlatformError):
    """幂等键已经绑定到不同的消息请求。"""

    error_code = "IDEMPOTENCY_CONFLICT"


class AssistantConversationBusyError(PlatformError):
    """会话已有排队或运行中的生成任务。"""

    error_code = "CONVERSATION_BUSY"


class AssistantFeedbackConflictError(PlatformError):
    """反馈已被并发修改，调用方应刷新后重试。"""

    error_code = "ASSISTANT_FEEDBACK_CONFLICT"
