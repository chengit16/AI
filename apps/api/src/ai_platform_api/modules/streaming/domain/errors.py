from ai_platform_api.common.errors import PlatformError


class ConversationBusyError(PlatformError):
    """同一会话已有活动生成 Run，不能重复启动。"""

    error_code = "CONVERSATION_BUSY"


class StreamRunNotFoundError(PlatformError):
    """请求的流式 Run 不存在或不属于当前工作空间。"""

    error_code = "RESOURCE_NOT_FOUND"


class StreamCursorNotFoundError(PlatformError):
    """Last-Event-ID 不属于当前 Run，不能猜测回放起点。"""

    error_code = "RESOURCE_NOT_FOUND"


class StreamEventExpiredError(PlatformError):
    """请求恢复的事件已超过保留期。"""

    error_code = "SSE_EVENT_EXPIRED"


class StreamReplayLimitExceededError(PlatformError):
    """回放事件超过数量或字节上限，应改读最终快照。"""

    error_code = "SSE_REPLAY_LIMIT_EXCEEDED"


class StreamRunClosedError(PlatformError):
    """已结束的 Run 不能继续追加增量事件。"""

    error_code = "STREAM_RUN_CLOSED"
