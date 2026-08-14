"""定义助手会话、消息 Part 和运行事实的 HTTP Schema。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateConversationRequest(BaseModel):
    """表示可选标题的私有会话创建请求。"""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)


class ConversationResponse(BaseModel):
    """表示当前账号可见的会话事实。"""

    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    workspace_id: UUID
    created_by_account_id: UUID
    title: str | None
    status: Literal["active", "archived"]
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class ConversationListResponse(BaseModel):
    """表示当前账号私有会话列表。"""

    model_config = ConfigDict(extra="forbid")

    items: list[ConversationResponse]


class CreateMessagePartRequest(BaseModel):
    """表示首期可提交的文本消息 Part；图片问答保持后置。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["text"]
    text: str = Field(min_length=1, max_length=100_000)


class CreateUserMessageRequest(BaseModel):
    """表示由一个或多个文本 Part 组成的用户消息。"""

    model_config = ConfigDict(extra="forbid")

    parts: list[CreateMessagePartRequest] = Field(min_length=1, max_length=16)


class MessagePartResponse(BaseModel):
    """表示按稳定序号返回的不可变消息 Part。"""

    model_config = ConfigDict(extra="forbid")

    part_id: UUID
    sequence_no: int = Field(ge=1)
    type: Literal["text"]
    text: str


class MessageResponse(BaseModel):
    """表示消息头和不可变 Part 集合。"""

    model_config = ConfigDict(extra="forbid")

    message_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    role: Literal["system", "user", "assistant", "tool"]
    status: Literal["streaming", "completed", "failed"]
    parts: list[MessagePartResponse]
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class MessageListResponse(BaseModel):
    """表示会话内按时间正序返回的消息列表。"""

    model_config = ConfigDict(extra="forbid")

    items: list[MessageResponse]


class AssistantRunResponse(BaseModel):
    """表示一次问答运行冻结的版本关系和当前状态。"""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID | None
    agent_release_id: UUID
    runtime_config_version_id: UUID
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    trace_id: str = Field(min_length=16, max_length=64)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    error_code: str | None


class AssistantRunListResponse(BaseModel):
    """表示会话内按创建时间正序返回的运行事实。"""

    model_config = ConfigDict(extra="forbid")

    items: list[AssistantRunResponse]


class UserMessageCreatedResponse(BaseModel):
    """同时返回幂等创建的用户消息与排队运行。"""

    model_config = ConfigDict(extra="forbid")

    message: MessageResponse
    run: AssistantRunResponse


class AssistantSourceResponse(BaseModel):
    """表示当前仍获授权且版本有效的一条助手引用来源。"""

    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    quote: str = Field(min_length=1, max_length=1000)
    source_position: dict[str, object]
    document_title: str = Field(min_length=1, max_length=255)
    source_kind: Literal["manual", "upload", "web", "data_source"]
    source_name: str = Field(min_length=1, max_length=255)
    conflict_detected: bool


class AssistantSourceListResponse(BaseModel):
    """表示单条助手消息在当前授权边界内可查看的来源。"""

    model_config = ConfigDict(extra="forbid")

    items: list[AssistantSourceResponse]


class SubmitMessageFeedbackRequest(BaseModel):
    """表示用户对助手答案的帮助度、问题标签和可选补充说明。"""

    model_config = ConfigDict(extra="forbid")

    rating: Literal["helpful", "unhelpful"]
    issue_codes: list[
        Literal["incorrect", "missing_source", "source_mismatch", "unsafe", "other"]
    ] = Field(default_factory=list, max_length=5)
    comment: str | None = Field(default=None, min_length=1, max_length=1000)


class MessageFeedbackResponse(BaseModel):
    """表示当前账号对单条助手消息的最新反馈事实。"""

    model_config = ConfigDict(extra="forbid")

    feedback_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    message_id: UUID
    run_id: UUID
    rating: Literal["helpful", "unhelpful"]
    issue_codes: list[Literal["incorrect", "missing_source", "source_mismatch", "unsafe", "other"]]
    comment: str | None
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class CurrentMessageFeedbackResponse(BaseModel):
    """表示反馈可能尚未提交的稳定读取包装。"""

    model_config = ConfigDict(extra="forbid")

    item: MessageFeedbackResponse | None
