"""定义会话、不可变消息 Part、系统助手发布快照和运行事实。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter

from ai_platform_api.common.runtime import RuntimeConfigSnapshot
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.enterprise_knowledge.domain.models import (
    ResolvedKnowledgeDomainScope,
)
from ai_platform_api.modules.identity.domain.entitlements import UsageRepository
from ai_platform_api.modules.integration.domain.events import OutboxWriter
from ai_platform_api.modules.service_governance.domain.models import ServiceRepository

ConversationStatus = Literal["active", "archived"]
ConversationKind = Literal["private", "service_invocation", "enterprise_brain"]
ConversationScopeMode = Literal["workspace", "selected"]
MessageRole = Literal["system", "user", "assistant", "tool"]
MessageStatus = Literal["streaming", "completed", "failed"]
AssistantRunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
FeedbackRating = Literal["helpful", "unhelpful"]
ReportTemplate = Literal["briefing", "risk_review", "comparison"]
AttachmentMediaType = Literal["text/plain", "text/markdown", "text/csv", "application/json"]
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


class EnterpriseKnowledgeScopeResolver(Protocol):
    """在助手事务内解析团队知识域，不暴露企业知识模块的写入接口。"""

    def require_active_enterprise_member(self, workspace_id: UUID, account_id: UUID) -> bool: ...

    def resolve_domain_scope(
        self,
        *,
        workspace_id: UUID,
        domain_id: UUID,
        account_id: UUID,
        authorized_workspace: bool,
        authorized_department_ids: frozenset[UUID],
        authorized_account_ids: frozenset[UUID],
        authorized_document_ids: frozenset[UUID],
        maximum_security_level: SecurityLevel,
    ) -> ResolvedKnowledgeDomainScope | None: ...


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
    conversation_kind: ConversationKind
    title: str | None
    status: ConversationStatus
    scope_mode: ConversationScopeMode
    knowledge_base_ids: tuple[UUID, ...]
    tag_ids: tuple[UUID, ...]
    created_at: datetime
    updated_at: datetime
    version: int
    knowledge_domain_id: UUID | None = None
    knowledge_domain_policy_version: int | None = None


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
class ConversationAttachment:
    """表示只在单个私有会话中使用的临时文本附件。"""

    attachment_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    created_by_account_id: UUID
    file_name: str
    media_type: AttachmentMediaType
    content: str
    size_bytes: int
    content_hash: str
    created_at: datetime


@dataclass(frozen=True)
class AssistantRun:
    """记录一次问答运行冻结的消息、助手发布与模型配置版本。"""

    run_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID | None
    service_id: UUID | None
    service_route_id: UUID | None
    service_route_version: int | None
    agent_release_id: UUID
    runtime_config_version_id: UUID
    requested_by_account_id: UUID
    requested_by_actor_id: UUID
    status: AssistantRunStatus
    idempotency_key: str
    request_hash: str
    trace_id: str
    traceparent: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    error_code: str | None
    knowledge_base_ids: frozenset[UUID] | None = None
    document_ids: frozenset[UUID] | None = None
    attachment_ids: tuple[UUID, ...] = ()
    knowledge_domain_id: UUID | None = None
    knowledge_domain_policy_version: int | None = None


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


@dataclass(frozen=True)
class EnterpriseBrainReport:
    """表示从企业大脑已完成回答生成的不可变 Markdown 报告。"""

    report_id: UUID
    workspace_id: UUID
    created_by_account_id: UUID
    conversation_id: UUID
    message_id: UUID
    run_id: UUID
    template: ReportTemplate
    title: str
    content: str
    content_sha256: str
    citation_count: int
    idempotency_key: str
    created_at: datetime


@dataclass(frozen=True)
class EnterpriseBrainOverview:
    """提供企业大脑页面所需的低敏聚合统计，不包含问答正文。"""

    workspace_id: UUID
    workspace_name: str
    generated_at: datetime
    window_started_at: datetime
    window_ended_at: datetime
    active_conversation_count: int
    archived_conversation_count: int
    run_count_30d: int
    completed_run_count_30d: int
    failed_run_count_30d: int
    cancelled_run_count_30d: int
    token_count_30d: int
    estimated_cost_microunits_30d: int
    knowledge_domain_ids: tuple[UUID, ...]


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
        conversation_kind: ConversationKind = "private",
    ) -> tuple[Conversation, ...]: ...

    def get_conversation(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        account_id: UUID,
        *,
        for_update: bool = False,
        conversation_kind: ConversationKind = "private",
    ) -> Conversation | None: ...

    def save_conversation(self, conversation: Conversation) -> None: ...

    def resolve_conversation_scope(
        self,
        workspace_id: UUID,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        tag_ids: tuple[UUID, ...],
    ) -> tuple[frozenset[UUID] | None, frozenset[UUID] | None] | None: ...

    def add_attachment(self, attachment: ConversationAttachment) -> None: ...

    def list_attachments(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        account_id: UUID,
    ) -> tuple[ConversationAttachment, ...]: ...

    def get_attachment(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        attachment_id: UUID,
        account_id: UUID,
    ) -> ConversationAttachment | None: ...

    def delete_attachment(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        attachment_id: UUID,
    ) -> None: ...

    def delete_conversation_attachments(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
    ) -> int: ...

    def get_run_attachments(self, run: AssistantRun) -> tuple[ConversationAttachment, ...]: ...

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
        actor_id: UUID,
        idempotency_key: str,
    ) -> MessageSubmission | None: ...

    def get_submission_by_run(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        run_id: UUID,
    ) -> MessageSubmission | None: ...

    def get_run(
        self,
        workspace_id: UUID,
        conversation_id: UUID | None,
        run_id: UUID,
        actor_id: UUID,
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

    def add_enterprise_brain_report(self, report: EnterpriseBrainReport) -> None: ...

    def get_enterprise_brain_report(
        self,
        workspace_id: UUID,
        report_id: UUID,
        account_id: UUID,
    ) -> EnterpriseBrainReport | None: ...

    def get_enterprise_brain_report_by_key(
        self,
        workspace_id: UUID,
        account_id: UUID,
        idempotency_key: str,
    ) -> EnterpriseBrainReport | None: ...

    def list_enterprise_brain_reports(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        limit: int,
    ) -> tuple[EnterpriseBrainReport, ...]: ...

    def get_enterprise_brain_overview(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        generated_at: datetime,
    ) -> EnterpriseBrainOverview | None: ...


class AssistantUnitOfWork(Protocol):
    """保证会话、消息、运行、审计和 Outbox 在同一事务内提交。"""

    @property
    def assistant(self) -> AssistantRepository: ...

    @property
    def services(self) -> ServiceRepository: ...

    @property
    def usage(self) -> UsageRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    @property
    def enterprise_knowledge(self) -> EnterpriseKnowledgeScopeResolver:
        """返回与助手事实共享事务的知识域只读端口。"""
        ...

    def __enter__(self) -> AssistantUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
