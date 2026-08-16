"""定义三类已发布服务共用的 HTTP 与 SSE 启动响应。"""

from pydantic import BaseModel, ConfigDict, Field

from ai_platform_api.modules.assistant.api.schemas import (
    AssistantRunResponse,
    CreateMessagePartRequest,
    MessageResponse,
)


class InvokePublishedServiceRequest(BaseModel):
    """表示统一服务出口首期接受的纯文本请求。"""

    model_config = ConfigDict(extra="forbid")

    parts: list[CreateMessagePartRequest] = Field(min_length=1, max_length=16)


class PublishedServiceInvocationResponse(BaseModel):
    """返回同一 Run 的 HTTP 快照和可恢复 SSE 地址。"""

    model_config = ConfigDict(extra="forbid")

    input_message: MessageResponse
    output_message: MessageResponse | None
    run: AssistantRunResponse
    event_stream_path: str
