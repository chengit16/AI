"""定义会话、不可变消息 Part、系统助手发布快照和运行事实。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter

from ai_platform_api.common.runtime import RuntimeConfigSnapshot
from ai_platform_api.modules.integration.domain.events import OutboxWriter
from ai_platform_api.modules.service_governance.domain.models import ServiceRepository

ConversationStatus = Literal["active", "archived"]
MessageRole = Literal["system", "user", "assistant", "tool"]
MessageStatus = Literal["streaming", "completed", "failed"]
AssistantRunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
FeedbackRating = Literal["helpful", "unhelpful"]
FeedbackIssueCode = Literal[
    "incorrect",
    "missing_source",
    "source_mismatch",
    "unsafe",
    "other",
]


class AssistantWriteConflictError(Exception):
    """数据库并发或唯一约束拒绝助手事实写入。"""

    def __init__(self, reason: Literal["idempotency", "conversation_busy", "write"]) -> None:
        self.reason = reason
        super().__init__(reason)


class RuntimeConfigurationBootstrap(Protocol):
    """在本地零配置模式下创建当前 AI 运行配置，不改变真实配置发布流程。"""

    def ensure(self, account_id: UUID) -> RuntimeConfigSnapshot: ...


@dataclass(frozen=True)
class AgentRelease:
    """冻结系统知识助手与运行配置的绑定，历史 Run 永不回读当前配置。"""

    release_id: UUID
    agent_id: UUID
    workspace_id: UUID
    version: int
    runtime_config_version_id: UUID
    config_hash: str
    released_at: datetime


@dataclass(frozen=True)
class Conversation:
    """表示仅创建者可见的工作空间问答会话。"""

    conversation_id: UUID
    workspace_id: UUID
    created_by_account_id: UUID
    title: str | None
    status: ConversationStatus
    created_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class MessagePart:
    """表示消息中按稳定顺序保存的不可变文本片段。"""

    part_id: UUID
    message_id: UUID
    sequence_no: int
    part_type: Literal["text"]
    text: str
    created_at: datetime


@dataclass(frozen=True)
class Message:
    """表示会话消息头及其不可变内容 Part。"""

    message_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    role: MessageRole
    status: MessageStatus
    parts: tuple[MessagePart, ...]
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class AssistantRun:
    """记录一次问答运行冻结的消息、助手发布与模型配置版本。"""

    run_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID | None
    agent_release_id: UUID
    runtime_config_version_id: UUID
    requested_by_account_id: UUID
    status: AssistantRunStatus
    idempotency_key: str
    request_hash: str
    trace_id: str
    traceparent: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    error_code: str | None


@dataclass(frozen=True)
class MessageSubmission:
    """聚合幂等消息创建返回所需的用户消息与排队 Run。"""

    message: Message
    assistant_message: Message | None
    run: AssistantRun


@dataclass(frozen=True)
class MessageFeedback:
    """保存当前账号对单条助手消息的可修订人工反馈。"""

    feedback_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    message_id: UUID
    run_id: UUID
    account_id: UUID
    rating: FeedbackRating
    issue_codes: tuple[FeedbackIssueCode, ...]
    comment: str | None
    created_at: datetime
    updated_at: datetime
    version: int


class AssistantRepository(Protocol):
    """按工作空间和会话创建者边界读写助手事实。"""

    def get_active_member(self, workspace_id: UUID, account_id: UUID) -> bool: ...

    def get_current_runtime_config(self) -> RuntimeConfigSnapshot | None: ...

    def get_or_create_system_release(
        self,
        *,
        workspace_id: UUID,
        account_id: UUID,
        runtime_config: RuntimeConfigSnapshot,
        released_at: datetime,
    ) -> AgentRelease: ...

    def add_conversation(self, conversation: Conversation) -> None: ...

    def list_conversations(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        limit: int,
    ) -> tuple[Conversation, ...]: ...

    def get_conversation(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> Conversation | None: ...

    def save_conversation(self, conversation: Conversation) -> None: ...

    def list_messages(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        *,
        limit: int,
    ) -> tuple[Message, ...]: ...

    def list_runs(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        account_id: UUID,
        *,
        limit: int,
    ) -> tuple[AssistantRun, ...]: ...

    def get_submission(
        self,
        workspace_id: UUID,
        account_id: UUID,
        idempotency_key: str,
    ) -> MessageSubmission | None: ...

    def get_run(
        self,
        workspace_id: UUID,
        conversation_id: UUID | None,
        run_id: UUID,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> AssistantRun | None: ...

    def get_run_by_assistant_message(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        account_id: UUID,
    ) -> AssistantRun | None: ...

    def has_active_run(self, workspace_id: UUID, conversation_id: UUID) -> bool: ...

    def add_submission(self, submission: MessageSubmission) -> None: ...

    def add_assistant_message(self, message: Message) -> None: ...

    def transition_run(
        self,
        run: AssistantRun,
        *,
        expected_status: AssistantRunStatus,
    ) -> bool: ...

    def finish_assistant_message(self, message: Message) -> bool: ...

    def get_feedback(
        self,
        workspace_id: UUID,
        message_id: UUID,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> MessageFeedback | None: ...

    def save_feedback(self, feedback: MessageFeedback) -> None: ...


class AssistantUnitOfWork(Protocol):
    """保证会话、消息、运行、审计和 Outbox 在同一事务内提交。"""

    @property
    def assistant(self) -> AssistantRepository: ...

    @property
    def services(self) -> ServiceRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> AssistantUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
