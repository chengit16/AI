class ConversationBusyError(Exception):
    """同一会话已有活动生成 Run，不能重复启动。"""


class StreamRunNotFoundError(Exception):
    """请求的流式 Run 不存在或不属于当前工作空间。"""


class StreamCursorNotFoundError(Exception):
    """Last-Event-ID 不属于当前 Run，不能猜测回放起点。"""


class StreamEventExpiredError(Exception):
    """请求恢复的事件已超过保留期。"""


class StreamReplayLimitExceededError(Exception):
    """回放事件超过数量或字节上限，应改读最终快照。"""


class StreamRunClosedError(Exception):
    """已结束的 Run 不能继续追加增量事件。"""
