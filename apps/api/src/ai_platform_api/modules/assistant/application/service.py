"""实现私有会话、不可变消息与助手运行排队用例。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.assistant.application.errors import (
    AssistantConversationBusyError,
    AssistantDeniedError,
    AssistantIdempotencyConflictError,
    AssistantNotFoundError,
    AssistantValidationError,
)
from ai_platform_api.modules.assistant.domain.models import (
    AgentRelease,
    AssistantRun,
    AssistantUnitOfWork,
    AssistantWriteConflictError,
    Conversation,
    Message,
    MessagePart,
    MessageSubmission,
    RuntimeConfigSnapshot,
)
from ai_platform_api.modules.integration.domain.events import IntegrationEvent
from ai_platform_api.modules.model_gateway.domain.runtime_errors import (
    AiRuntimeConfigNotActiveError,
)

IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")

__all__ = [
    "AssistantConversationService",
    "AssistantRun",
    "Conversation",
    "Message",
]


class AssistantConversationService:
    """维护创建者私有会话，并把每次用户消息排入可追溯运行队列。"""

    def __init__(self, unit_of_work: AssistantUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def create_conversation(
        self,
        context: RequestContext,
        *,
        title: str | None,
    ) -> Conversation:
        """创建私有会话，并确保空间已有匹配当前模型配置的系统助手发布。"""

        account_id = _browser_account(context)
        normalized_title = _normalize_title(title)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            runtime_config = _current_runtime_config(unit_of_work)
            unit_of_work.assistant.get_or_create_system_release(
                workspace_id=context.workspace_id,
                account_id=account_id,
                runtime_config=runtime_config,
                released_at=now,
            )
            conversation = Conversation(
                conversation_id=uuid4(),
                workspace_id=context.workspace_id,
                created_by_account_id=account_id,
                title=normalized_title,
                status="active",
                created_at=now,
                updated_at=now,
                version=1,
            )
            unit_of_work.assistant.add_conversation(conversation)
            _record_conversation_event(unit_of_work, context, conversation, "created", now)
            unit_of_work.commit()
            return conversation

    def list_conversations(
        self,
        context: RequestContext,
        *,
        limit: int,
    ) -> tuple[Conversation, ...]:
        """只列出当前账号创建的会话，企业管理员也不能默认读取成员私聊。"""

        account_id = _browser_account(context)
        _require_limit(limit)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            return unit_of_work.assistant.list_conversations(
                context.workspace_id,
                account_id,
                limit=limit,
            )

    def list_messages(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        limit: int,
    ) -> tuple[Message, ...]:
        """验证会话创建者后按时间顺序返回不可变消息内容。"""

        account_id = _browser_account(context)
        _require_limit(limit)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            _owned_conversation(unit_of_work, context, conversation_id, account_id)
            return unit_of_work.assistant.list_messages(
                context.workspace_id,
                conversation_id,
                limit=limit,
            )

    def archive_conversation(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
    ) -> Conversation:
        """归档当前账号创建的活动会话，已归档请求保持幂等。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            current = _owned_conversation(
                unit_of_work,
                context,
                conversation_id,
                account_id,
                for_update=True,
            )
            if current.status == "archived":
                return current
            if unit_of_work.assistant.has_active_run(context.workspace_id, conversation_id):
                raise AssistantConversationBusyError
            archived = replace(
                current,
                status="archived",
                updated_at=now,
                version=current.version + 1,
            )
            unit_of_work.assistant.save_conversation(archived)
            _record_conversation_event(unit_of_work, context, archived, "archived", now)
            unit_of_work.commit()
            return archived

    def create_user_message(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        texts: tuple[str, ...],
        idempotency_key: str,
    ) -> MessageSubmission:
        """原子创建用户消息和 queued Run，并冻结助手及运行配置版本。"""

        # 1. 先验证稳定输入并计算请求摘要，重试可以在任何会话状态检查之前返回原结果。
        account_id = _browser_account(context)
        normalized_texts = _normalize_texts(texts)
        _require_idempotency_key(idempotency_key)
        request_hash = _request_hash(conversation_id, normalized_texts)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                _require_active_member(unit_of_work, context.workspace_id, account_id)
                existing = unit_of_work.assistant.get_submission(
                    context.workspace_id,
                    account_id,
                    idempotency_key,
                )
                if existing is not None:
                    if existing.run.request_hash != request_hash:
                        raise AssistantIdempotencyConflictError
                    return existing

                # 2. 锁定会话后阻止并行生成，再冻结当前运行配置对应的系统助手发布。
                conversation = _owned_conversation(
                    unit_of_work,
                    context,
                    conversation_id,
                    account_id,
                    for_update=True,
                )
                if conversation.status != "active":
                    raise AssistantNotFoundError
                if unit_of_work.assistant.has_active_run(context.workspace_id, conversation_id):
                    raise AssistantConversationBusyError
                runtime_config = _current_runtime_config(unit_of_work)
                release = unit_of_work.assistant.get_or_create_system_release(
                    workspace_id=context.workspace_id,
                    account_id=account_id,
                    runtime_config=runtime_config,
                    released_at=now,
                )

                # 3. 用户消息、不可变 Part、排队 Run、审计和 Outbox 同事务提交。
                submission = _new_submission(
                    context=context,
                    account_id=account_id,
                    conversation_id=conversation_id,
                    texts=normalized_texts,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    release=release,
                    now=now,
                )
                unit_of_work.assistant.add_submission(submission)
                _record_run_queued(unit_of_work, context, submission, now)
                unit_of_work.commit()
                return submission
        except AssistantWriteConflictError as error:
            if error.reason == "conversation_busy":
                raise AssistantConversationBusyError from error
            raise AssistantIdempotencyConflictError from error


def _browser_account(context: RequestContext) -> UUID:
    if (
        context.user_id is None
        or context.user_id != context.actor_id
        or context.authentication_method != "browser_session"
    ):
        raise AssistantDeniedError
    return context.user_id


def _require_active_member(
    unit_of_work: AssistantUnitOfWork,
    workspace_id: UUID,
    account_id: UUID,
) -> None:
    if not unit_of_work.assistant.get_active_member(workspace_id, account_id):
        raise AssistantDeniedError


def _current_runtime_config(unit_of_work: AssistantUnitOfWork) -> RuntimeConfigSnapshot:
    current = unit_of_work.assistant.get_current_runtime_config()
    if current is None:
        raise AiRuntimeConfigNotActiveError
    return current


def _owned_conversation(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    conversation_id: UUID,
    account_id: UUID,
    *,
    for_update: bool = False,
) -> Conversation:
    conversation = unit_of_work.assistant.get_conversation(
        context.workspace_id,
        conversation_id,
        account_id,
        for_update=for_update,
    )
    if conversation is None:
        raise AssistantNotFoundError
    return conversation


def _normalize_title(title: str | None) -> str | None:
    if title is None:
        return None
    normalized = title.strip()
    if not normalized or len(normalized) > 200:
        raise AssistantValidationError
    return normalized


def _normalize_texts(texts: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(text.strip() for text in texts)
    if not normalized or len(normalized) > 16:
        raise AssistantValidationError
    if any(not text or len(text) > 100_000 for text in normalized):
        raise AssistantValidationError
    if sum(len(text) for text in normalized) > 200_000:
        raise AssistantValidationError
    return normalized


def _require_limit(limit: int) -> None:
    if not 1 <= limit <= 200:
        raise AssistantValidationError


def _require_idempotency_key(idempotency_key: str) -> None:
    if IDEMPOTENCY_PATTERN.fullmatch(idempotency_key) is None:
        raise AssistantValidationError


def _request_hash(conversation_id: UUID, texts: tuple[str, ...]) -> str:
    canonical = json.dumps(
        {"conversation_id": str(conversation_id), "parts": list(texts)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _new_submission(
    *,
    context: RequestContext,
    account_id: UUID,
    conversation_id: UUID,
    texts: tuple[str, ...],
    idempotency_key: str,
    request_hash: str,
    release: AgentRelease,
    now: datetime,
) -> MessageSubmission:
    message_id = uuid4()
    message = Message(
        message_id=message_id,
        workspace_id=context.workspace_id,
        conversation_id=conversation_id,
        role="user",
        status="completed",
        parts=tuple(
            MessagePart(uuid4(), message_id, index, "text", text, now)
            for index, text in enumerate(texts, start=1)
        ),
        created_by_account_id=account_id,
        created_at=now,
        updated_at=now,
        version=1,
    )
    run = AssistantRun(
        run_id=uuid4(),
        workspace_id=context.workspace_id,
        conversation_id=conversation_id,
        user_message_id=message_id,
        assistant_message_id=None,
        agent_release_id=release.release_id,
        runtime_config_version_id=release.runtime_config_version_id,
        requested_by_account_id=account_id,
        status="queued",
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        trace_id=context.trace.trace_id,
        traceparent=context.trace.traceparent,
        created_at=now,
        updated_at=now,
        completed_at=None,
        error_code=None,
    )
    return MessageSubmission(message, run)


def _record_conversation_event(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    conversation: Conversation,
    transition: Literal["created", "archived"],
    occurred_at: datetime,
) -> None:
    action = f"assistant.conversation.{transition}"
    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type="conversation",
            resource_id=conversation.conversation_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            attributes={"status": conversation.status, "version": conversation.version},
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type=action,
            workspace_id=context.workspace_id,
            aggregate_id=conversation.conversation_id,
            aggregate_version=conversation.version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload={
                "conversation_id": str(conversation.conversation_id),
                "status": conversation.status,
            },
        )
    )


def _record_run_queued(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    submission: MessageSubmission,
    occurred_at: datetime,
) -> None:
    run = submission.run
    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action="assistant.message.create",
            resource_type="assistant_run",
            resource_id=run.run_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            attributes={
                "conversation_id": str(run.conversation_id),
                "message_id": str(run.user_message_id),
                "agent_release_id": str(run.agent_release_id),
                "runtime_config_version_id": str(run.runtime_config_version_id),
            },
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type="assistant.run.queued",
            workspace_id=context.workspace_id,
            aggregate_id=run.run_id,
            aggregate_version=1,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload={
                "run_id": str(run.run_id),
                "conversation_id": str(run.conversation_id),
                "user_message_id": str(run.user_message_id),
                "agent_release_id": str(run.agent_release_id),
                "runtime_config_version_id": str(run.runtime_config_version_id),
            },
        )
    )
