"""实现会话、消息、系统助手发布和运行事实的 PostgreSQL Adapter。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime
from types import TracebackType
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import CursorResult, case, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.common.runtime import RuntimeConfigSnapshot
from ai_platform_api.modules.assistant.domain.models import (
    AgentRelease,
    AssistantRepository,
    AssistantRun,
    AssistantRunStatus,
    AssistantUnitOfWork,
    AssistantWriteConflictError,
    Conversation,
    FeedbackIssueCode,
    FeedbackRating,
    Message,
    MessageFeedback,
    MessagePart,
    MessageSubmission,
)
from ai_platform_api.modules.identity.domain.entitlements import UsageRepository
from ai_platform_api.modules.service_governance.domain.models import ServiceRepository
from ai_platform_api.persistence.tables import (
    agent_publications,
    agent_releases,
    agents,
    ai_runtime_config_publication,
    ai_runtime_config_versions,
    assistant_runs,
    conversations,
    message_feedbacks,
    message_parts,
    messages,
    workspace_memberships,
    workspaces,
)

SessionFactory = Callable[[], Session]
UsageRepositoryFactory = Callable[[Session], UsageRepository]
ServiceRepositoryFactory = Callable[[Session], ServiceRepository]
SYSTEM_AGENT_KEY = "system_knowledge"
SYSTEM_AGENT_NAME = "系统知识助手"


class SqlAlchemyAssistantRepository(AssistantRepository):
    """在单个事务中维护创建者私有会话和不可变运行快照。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_active_member(self, workspace_id: UUID, account_id: UUID) -> bool:
        return bool(
            self._session.scalar(
                select(func.count())
                .select_from(workspace_memberships)
                .join(workspaces, workspaces.c.workspace_id == workspace_memberships.c.workspace_id)
                .where(
                    workspace_memberships.c.workspace_id == workspace_id,
                    workspace_memberships.c.account_id == account_id,
                    workspace_memberships.c.status == "active",
                    workspaces.c.status == "active",
                )
            )
        )

    def get_current_runtime_config(self) -> RuntimeConfigSnapshot | None:
        row = self._session.execute(
            select(
                ai_runtime_config_versions.c.runtime_config_version_id,
                ai_runtime_config_versions.c.content_hash,
            )
            .join(
                ai_runtime_config_publication,
                ai_runtime_config_publication.c.runtime_config_version_id
                == ai_runtime_config_versions.c.runtime_config_version_id,
            )
            .where(ai_runtime_config_publication.c.publication_key == "current")
        ).one_or_none()
        if row is None:
            return None
        return RuntimeConfigSnapshot(row.runtime_config_version_id, row.content_hash)

    def get_or_create_system_release(
        self,
        *,
        workspace_id: UUID,
        account_id: UUID,
        runtime_config: RuntimeConfigSnapshot,
        released_at: datetime,
    ) -> AgentRelease:
        """按空间串行创建系统助手，并在运行配置变化时发布新快照。"""

        # 1. 空间级事务锁覆盖首次 Agent 与 Release 创建，避免空表场景无法使用行锁。
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:workspace_id), 58101)"),
            {"workspace_id": str(workspace_id)},
        )
        agent_id = self._session.scalar(
            select(agents.c.agent_id).where(
                agents.c.workspace_id == workspace_id,
                agents.c.agent_key == SYSTEM_AGENT_KEY,
                agents.c.status == "active",
            )
        )
        if agent_id is None:
            agent_id = uuid4()
            self._session.execute(
                insert(agents).values(
                    agent_id=agent_id,
                    workspace_id=workspace_id,
                    agent_key=SYSTEM_AGENT_KEY,
                    name=SYSTEM_AGENT_NAME,
                    status="active",
                    created_by_account_id=account_id,
                    created_at=released_at,
                    updated_at=released_at,
                    version=1,
                )
            )

        # 2. 当前发布已绑定相同运行配置时直接复用；旧 Release 永不更新。
        current_row = self._session.execute(
            select(agent_releases)
            .join(
                agent_publications,
                agent_publications.c.release_id == agent_releases.c.release_id,
            )
            .where(agent_publications.c.agent_id == agent_id)
        ).one_or_none()
        if (
            current_row is not None
            and current_row.runtime_config_version_id == runtime_config.runtime_config_version_id
        ):
            return _release(current_row)

        # 3. 新 Release 摘要同时绑定 Agent 身份、版本、运行配置 ID 和配置内容摘要。
        version = (
            int(
                self._session.scalar(
                    select(func.max(agent_releases.c.version)).where(
                        agent_releases.c.agent_id == agent_id
                    )
                )
                or 0
            )
            + 1
        )
        release_id = uuid4()
        config_hash = _release_hash(
            agent_id=agent_id,
            version=version,
            runtime_config=runtime_config,
        )
        self._session.execute(
            insert(agent_releases).values(
                release_id=release_id,
                agent_id=agent_id,
                workspace_id=workspace_id,
                version=version,
                status="released",
                runtime_config_version_id=runtime_config.runtime_config_version_id,
                config_hash=config_hash,
                released_by_account_id=account_id,
                released_at=released_at,
            )
        )
        generation = int(current_row.version if current_row is not None else 0) + 1
        statement = postgresql_insert(agent_publications).values(
            agent_id=agent_id,
            workspace_id=workspace_id,
            release_id=release_id,
            generation=generation,
            published_by_account_id=account_id,
            published_at=released_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[agent_publications.c.agent_id],
            set_={
                "release_id": release_id,
                "generation": generation,
                "published_by_account_id": account_id,
                "published_at": released_at,
            },
        )
        self._session.execute(statement)
        return AgentRelease(
            release_id,
            agent_id,
            workspace_id,
            version,
            runtime_config.runtime_config_version_id,
            config_hash,
            released_at,
        )

    def add_conversation(self, conversation: Conversation) -> None:
        try:
            self._session.execute(
                insert(conversations).values(
                    conversation_id=conversation.conversation_id,
                    workspace_id=conversation.workspace_id,
                    created_by_account_id=conversation.created_by_account_id,
                    conversation_kind=conversation.conversation_kind,
                    title=conversation.title,
                    status=conversation.status,
                    created_at=conversation.created_at,
                    updated_at=conversation.updated_at,
                    version=conversation.version,
                )
            )
        except IntegrityError as error:
            raise AssistantWriteConflictError("write") from error

    def list_conversations(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        limit: int,
    ) -> tuple[Conversation, ...]:
        rows = self._session.execute(
            select(conversations)
            .where(
                conversations.c.workspace_id == workspace_id,
                conversations.c.created_by_account_id == account_id,
                conversations.c.conversation_kind == "private",
            )
            .order_by(conversations.c.updated_at.desc(), conversations.c.conversation_id)
            .limit(limit)
        )
        return tuple(_conversation(row) for row in rows)

    def get_conversation(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> Conversation | None:
        statement = select(conversations).where(
            conversations.c.workspace_id == workspace_id,
            conversations.c.conversation_id == conversation_id,
            conversations.c.created_by_account_id == account_id,
            conversations.c.conversation_kind == "private",
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _conversation(row) if row is not None else None

    def save_conversation(self, conversation: Conversation) -> None:
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(conversations)
                .where(
                    conversations.c.workspace_id == conversation.workspace_id,
                    conversations.c.conversation_id == conversation.conversation_id,
                    conversations.c.version == conversation.version - 1,
                )
                .values(
                    status=conversation.status,
                    title=conversation.title,
                    updated_at=conversation.updated_at,
                    version=conversation.version,
                )
            ),
        )
        if result.rowcount != 1:
            raise AssistantWriteConflictError("write")

    def list_messages(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        *,
        limit: int,
    ) -> tuple[Message, ...]:
        # 同一轮用户与助手消息共享创建时间，角色序保证因果顺序，UUID 仅处理同角色并列。
        role_order = case(
            (messages.c.role == "system", 0),
            (messages.c.role == "user", 1),
            (messages.c.role == "assistant", 2),
            else_=3,
        )
        rows = self._session.execute(
            select(messages)
            .where(
                messages.c.workspace_id == workspace_id,
                messages.c.conversation_id == conversation_id,
            )
            .order_by(messages.c.created_at, role_order, messages.c.message_id)
            .limit(limit)
        )
        return tuple(self._message(row) for row in rows)

    def list_runs(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        account_id: UUID,
        *,
        limit: int,
    ) -> tuple[AssistantRun, ...]:
        rows = self._session.execute(
            select(assistant_runs)
            .where(
                assistant_runs.c.workspace_id == workspace_id,
                assistant_runs.c.conversation_id == conversation_id,
                assistant_runs.c.requested_by_actor_id == account_id,
            )
            .order_by(assistant_runs.c.created_at, assistant_runs.c.run_id)
            .limit(limit)
        )
        return tuple(_run(row) for row in rows)

    def get_submission(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        idempotency_key: str,
    ) -> MessageSubmission | None:
        run_row = self._session.execute(
            select(assistant_runs).where(
                assistant_runs.c.workspace_id == workspace_id,
                assistant_runs.c.requested_by_actor_id == actor_id,
                assistant_runs.c.idempotency_key == idempotency_key,
            )
        ).one_or_none()
        if run_row is None:
            return None
        return self._submission(run_row)

    def get_submission_by_run(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        run_id: UUID,
    ) -> MessageSubmission | None:
        """按可信 Actor 读取服务调用结果，API Key 不能读取同账号其他 Key 的 Run。"""

        run_row = self._session.execute(
            select(assistant_runs).where(
                assistant_runs.c.workspace_id == workspace_id,
                assistant_runs.c.requested_by_actor_id == actor_id,
                assistant_runs.c.run_id == run_id,
            )
        ).one_or_none()
        return self._submission(run_row) if run_row is not None else None

    def _submission(self, run_row: Row[Any]) -> MessageSubmission:
        """聚合 Run 的输入和助手消息，调用方已完成工作空间与 Actor 过滤。"""

        message_row = self._session.execute(
            select(messages).where(messages.c.message_id == run_row.user_message_id)
        ).one()
        assistant_row = (
            self._session.execute(
                select(messages).where(messages.c.message_id == run_row.assistant_message_id)
            ).one()
            if run_row.assistant_message_id is not None
            else None
        )
        return MessageSubmission(
            self._message(message_row),
            self._message(assistant_row) if assistant_row is not None else None,
            _run(run_row),
        )

    def get_run(
        self,
        workspace_id: UUID,
        conversation_id: UUID | None,
        run_id: UUID,
        actor_id: UUID,
        *,
        for_update: bool = False,
    ) -> AssistantRun | None:
        statement = select(assistant_runs).where(
            assistant_runs.c.workspace_id == workspace_id,
            assistant_runs.c.run_id == run_id,
            assistant_runs.c.requested_by_actor_id == actor_id,
        )
        if conversation_id is not None:
            statement = statement.where(assistant_runs.c.conversation_id == conversation_id)
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _run(row) if row is not None else None

    def get_run_by_assistant_message(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        account_id: UUID,
    ) -> AssistantRun | None:
        row = self._session.execute(
            select(assistant_runs).where(
                assistant_runs.c.workspace_id == workspace_id,
                assistant_runs.c.conversation_id == conversation_id,
                assistant_runs.c.assistant_message_id == message_id,
                assistant_runs.c.requested_by_actor_id == account_id,
            )
        ).one_or_none()
        return _run(row) if row is not None else None

    def has_active_run(self, workspace_id: UUID, conversation_id: UUID) -> bool:
        return bool(
            self._session.scalar(
                select(func.count())
                .select_from(assistant_runs)
                .where(
                    assistant_runs.c.workspace_id == workspace_id,
                    assistant_runs.c.conversation_id == conversation_id,
                    assistant_runs.c.status.in_(("queued", "running")),
                )
            )
        )

    def add_submission(self, submission: MessageSubmission) -> None:
        message = submission.message
        assistant_message = submission.assistant_message
        run = submission.run
        if assistant_message is None:
            raise AssistantWriteConflictError("write")
        try:
            # 1. 同时写用户输入和空的助手流式消息，SSE 从一开始即可绑定稳定 message_id。
            self._insert_message(message)
            self._insert_message(assistant_message)
            # 2. 最后写入冻结发布与运行配置的排队 Run；任一步冲突都由外层事务整体回滚。
            self._session.execute(
                insert(assistant_runs).values(
                    run_id=run.run_id,
                    workspace_id=run.workspace_id,
                    conversation_id=run.conversation_id,
                    user_message_id=run.user_message_id,
                    assistant_message_id=run.assistant_message_id,
                    service_id=run.service_id,
                    service_route_id=run.service_route_id,
                    service_route_version=run.service_route_version,
                    agent_release_id=run.agent_release_id,
                    runtime_config_version_id=run.runtime_config_version_id,
                    requested_by_account_id=run.requested_by_account_id,
                    requested_by_actor_id=run.requested_by_actor_id,
                    status=run.status,
                    idempotency_key=run.idempotency_key,
                    request_hash=run.request_hash,
                    trace_id=run.trace_id,
                    traceparent=run.traceparent,
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                    completed_at=run.completed_at,
                    error_code=run.error_code,
                )
            )
        except IntegrityError as error:
            constraint_name = _constraint_name(error)
            reason: Literal["idempotency", "conversation_busy"] = (
                "conversation_busy"
                if constraint_name == "uq_assistant_runs_active_conversation"
                else "idempotency"
            )
            raise AssistantWriteConflictError(reason) from error

    def add_assistant_message(self, message: Message) -> None:
        """只为升级前缺失占位消息的 Run 补建助手消息。"""

        try:
            self._insert_message(message)
        except IntegrityError as error:
            raise AssistantWriteConflictError("write") from error

    def transition_run(
        self,
        run: AssistantRun,
        *,
        expected_status: AssistantRunStatus,
    ) -> bool:
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(assistant_runs)
                .where(
                    assistant_runs.c.run_id == run.run_id,
                    assistant_runs.c.workspace_id == run.workspace_id,
                    assistant_runs.c.requested_by_actor_id == run.requested_by_actor_id,
                    assistant_runs.c.status == expected_status,
                )
                .values(
                    assistant_message_id=run.assistant_message_id,
                    status=run.status,
                    updated_at=run.updated_at,
                    completed_at=run.completed_at,
                    error_code=run.error_code,
                )
            ),
        )
        return result.rowcount == 1

    def finish_assistant_message(self, message: Message) -> bool:
        """在消息仍为 streaming 时追加一次终态 Part，失败消息保持无正文。"""

        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(messages)
                .where(
                    messages.c.message_id == message.message_id,
                    messages.c.workspace_id == message.workspace_id,
                    messages.c.status == "streaming",
                )
                .values(
                    status=message.status,
                    updated_at=message.updated_at,
                    version=message.version,
                )
            ),
        )
        if result.rowcount != 1:
            return False
        if message.parts:
            self._insert_parts(message)
        return True

    def get_feedback(
        self,
        workspace_id: UUID,
        message_id: UUID,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> MessageFeedback | None:
        statement = select(message_feedbacks).where(
            message_feedbacks.c.workspace_id == workspace_id,
            message_feedbacks.c.message_id == message_id,
            message_feedbacks.c.account_id == account_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _feedback(row) if row is not None else None

    def save_feedback(self, feedback: MessageFeedback) -> None:
        """首次插入或按版本更新反馈，避免并发覆盖用户刚提交的选择。"""

        # 1. 首次反馈依赖消息唯一约束，竞争插入统一映射为领域写冲突。
        values = {
            "rating": feedback.rating,
            "issue_codes": list(feedback.issue_codes),
            "comment": feedback.comment,
            "updated_at": feedback.updated_at,
            "version": feedback.version,
        }
        current_version = feedback.version - 1
        if current_version == 0:
            try:
                self._session.execute(
                    insert(message_feedbacks).values(
                        feedback_id=feedback.feedback_id,
                        workspace_id=feedback.workspace_id,
                        conversation_id=feedback.conversation_id,
                        message_id=feedback.message_id,
                        run_id=feedback.run_id,
                        account_id=feedback.account_id,
                        created_at=feedback.created_at,
                        **values,
                    )
                )
            except IntegrityError as error:
                raise AssistantWriteConflictError("write") from error
            return
        # 2. 修订只接受调用方已读取版本的下一版，行数不匹配代表并发修改。
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(message_feedbacks)
                .where(
                    message_feedbacks.c.feedback_id == feedback.feedback_id,
                    message_feedbacks.c.workspace_id == feedback.workspace_id,
                    message_feedbacks.c.version == current_version,
                )
                .values(**values)
            ),
        )
        if result.rowcount != 1:
            raise AssistantWriteConflictError("write")

    def _insert_message(self, message: Message) -> None:
        self._session.execute(
            insert(messages).values(
                message_id=message.message_id,
                workspace_id=message.workspace_id,
                conversation_id=message.conversation_id,
                role=message.role,
                status=message.status,
                created_by_account_id=message.created_by_account_id,
                created_at=message.created_at,
                updated_at=message.updated_at,
                version=message.version,
            )
        )
        if message.parts:
            self._insert_parts(message)

    def _insert_parts(self, message: Message) -> None:
        self._session.execute(
            insert(message_parts),
            [
                {
                    "part_id": part.part_id,
                    "workspace_id": message.workspace_id,
                    "message_id": part.message_id,
                    "sequence_no": part.sequence_no,
                    "part_type": part.part_type,
                    "text_content": part.text,
                    "object_ref": None,
                    "media_type": None,
                    "created_at": part.created_at,
                }
                for part in message.parts
            ],
        )

    def _message(self, row: Row[Any]) -> Message:
        part_rows = self._session.execute(
            select(message_parts)
            .where(message_parts.c.message_id == row.message_id)
            .order_by(message_parts.c.sequence_no)
        )
        parts = tuple(
            MessagePart(
                part.part_id,
                part.message_id,
                part.sequence_no,
                "text",
                cast(str, part.text_content),
                part.created_at,
            )
            for part in part_rows
        )
        return Message(
            row.message_id,
            row.workspace_id,
            row.conversation_id,
            row.role,
            row.status,
            parts,
            row.created_by_account_id,
            row.created_at,
            row.updated_at,
            row.version,
        )


class SqlAlchemyAssistantUnitOfWork(AssistantUnitOfWork):
    """为助手事实提供不可嵌套的显式 SQLAlchemy 事务边界。"""

    def __init__(
        self,
        session_factory: SessionFactory,
        service_repository_factory: ServiceRepositoryFactory,
        usage_repository_factory: UsageRepositoryFactory | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._service_repository_factory = service_repository_factory
        self._usage_repository_factory = usage_repository_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyAssistantRepository,
                ServiceRepository,
                UsageRepository | None,
                SqlAlchemyAuditWriter,
                SqlAlchemyOutboxWriter,
            ]
            | None
        ] = ContextVar("assistant_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyAssistantUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Assistant Unit of Work 不允许重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyAssistantRepository(session),
                self._service_repository_factory(session),
                (
                    self._usage_repository_factory(session)
                    if self._usage_repository_factory is not None
                    else None
                ),
                SqlAlchemyAuditWriter(session),
                SqlAlchemyOutboxWriter(session),
            )
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        state = self._state.get()
        if state is not None:
            if exc_type is not None:
                state[0].rollback()
            state[0].close()
            self._state.set(None)

    @property
    def assistant(self) -> SqlAlchemyAssistantRepository:
        return self._require_state()[1]

    @property
    def services(self) -> ServiceRepository:
        """向助手应用暴露服务治理端口，不允许直接访问服务表。"""

        return self._require_state()[2]

    @property
    def usage(self) -> UsageRepository:
        """向服务调用用例暴露同事务用量端口，普通助手测试可不装配该能力。"""

        repository = self._require_state()[3]
        if repository is None:
            raise RuntimeError("Assistant Unit of Work 未装配用量 Repository")
        return repository

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        return self._require_state()[4]

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        return self._require_state()[5]

    def commit(self) -> None:
        self._require_state()[0].commit()

    def _require_state(
        self,
    ) -> tuple[
        Session,
        SqlAlchemyAssistantRepository,
        ServiceRepository,
        UsageRepository | None,
        SqlAlchemyAuditWriter,
        SqlAlchemyOutboxWriter,
    ]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Assistant Unit of Work 尚未进入事务范围")
        return state


def _release(row: Row[Any]) -> AgentRelease:
    return AgentRelease(
        row.release_id,
        row.agent_id,
        row.workspace_id,
        row.version,
        row.runtime_config_version_id,
        row.config_hash,
        row.released_at,
    )


def _conversation(row: Row[Any]) -> Conversation:
    return Conversation(
        row.conversation_id,
        row.workspace_id,
        row.created_by_account_id,
        row.conversation_kind,
        row.title,
        row.status,
        row.created_at,
        row.updated_at,
        row.version,
    )


def _run(row: Row[Any]) -> AssistantRun:
    return AssistantRun(
        row.run_id,
        row.workspace_id,
        row.conversation_id,
        row.user_message_id,
        row.assistant_message_id,
        row.service_id,
        row.service_route_id,
        row.service_route_version,
        row.agent_release_id,
        row.runtime_config_version_id,
        row.requested_by_account_id,
        row.requested_by_actor_id,
        row.status,
        row.idempotency_key,
        row.request_hash,
        row.trace_id,
        row.traceparent,
        row.created_at,
        row.updated_at,
        row.completed_at,
        row.error_code,
    )


def _feedback(row: Row[Any]) -> MessageFeedback:
    return MessageFeedback(
        row.feedback_id,
        row.workspace_id,
        row.conversation_id,
        row.message_id,
        row.run_id,
        row.account_id,
        cast("FeedbackRating", row.rating),
        tuple(cast("list[FeedbackIssueCode]", row.issue_codes)),
        row.comment,
        row.created_at,
        row.updated_at,
        row.version,
    )


def _release_hash(
    *,
    agent_id: UUID,
    version: int,
    runtime_config: RuntimeConfigSnapshot,
) -> str:
    canonical = json.dumps(
        {
            "agent_id": str(agent_id),
            "agent_key": SYSTEM_AGENT_KEY,
            "release_schema_version": 1,
            "runtime_config_content_hash": runtime_config.content_hash,
            "runtime_config_version_id": str(runtime_config.runtime_config_version_id),
            "version": version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    value = getattr(diagnostic, "constraint_name", None)
    return value if isinstance(value, str) else None
