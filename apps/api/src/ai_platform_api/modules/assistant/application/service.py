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

    def get_run_for_stream(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        run_id: UUID,
    ) -> AssistantRun:
        """只向会话创建者返回目标 Run，供 SSE 在响应开始前完成授权。"""

        account_id = _browser_account(context)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            _owned_conversation(unit_of_work, context, conversation_id, account_id)
            run = unit_of_work.assistant.get_run(
                context.workspace_id,
                conversation_id,
                run_id,
                account_id,
            )
            if run is None:
                raise AssistantNotFoundError
            return run

    def claim_run(self, context: RequestContext, *, run_id: UUID) -> AssistantRun | None:
        """仅把当前账号的 queued Run 认领一次；重复后台任务直接退出。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            run = _owned_run(unit_of_work, context, run_id, account_id, for_update=True)
            if run.status != "queued":
                return None
            # 历史排队记录可能尚无助手消息；新请求已在提交事务中创建占位消息。
            if run.assistant_message_id is None:
                assistant_message = _new_assistant_message(run, account_id, now)
                unit_of_work.assistant.add_assistant_message(assistant_message)
                run = replace(run, assistant_message_id=assistant_message.message_id)
            running = replace(run, status="running", updated_at=now)
            if not unit_of_work.assistant.transition_run(running, expected_status="queued"):
                return None
            unit_of_work.commit()
            return running

    def complete_run(
        self,
        context: RequestContext,
        *,
        run_id: UUID,
        text: str,
    ) -> AssistantRun:
        """原子保存助手正文并结束运行，重复或越序完成一律失败关闭。"""

        normalized_text = text.strip()
        if not normalized_text or len(normalized_text) > 200_000:
            raise AssistantValidationError
        account_id = _browser_account(context)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            run = _owned_run(unit_of_work, context, run_id, account_id, for_update=True)
            if run.status != "running" or run.assistant_message_id is None:
                raise AssistantConversationBusyError
            message = _finished_assistant_message(
                run,
                account_id,
                normalized_text,
                now,
                "completed",
            )
            if not unit_of_work.assistant.finish_assistant_message(message):
                raise AssistantConversationBusyError
            completed = replace(
                run,
                status="completed",
                updated_at=now,
                completed_at=now,
                error_code=None,
            )
            if not unit_of_work.assistant.transition_run(completed, expected_status="running"):
                raise AssistantConversationBusyError
            _record_run_finished(unit_of_work, context, completed, "completed", now)
            unit_of_work.commit()
            return completed

    def fail_run(
        self,
        context: RequestContext,
        *,
        run_id: UUID,
        error_code: str,
    ) -> AssistantRun:
        """只保存脱敏错误码并结束运行，不把异常详情写入消息或事件。"""

        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", error_code):
            raise AssistantValidationError
        account_id = _browser_account(context)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            run = _owned_run(unit_of_work, context, run_id, account_id, for_update=True)
            if run.status != "running" or run.assistant_message_id is None:
                raise AssistantConversationBusyError
            message = _finished_assistant_message(run, account_id, "", now, "failed")
            if not unit_of_work.assistant.finish_assistant_message(message):
                raise AssistantConversationBusyError
            failed = replace(
                run,
                status="failed",
                updated_at=now,
                completed_at=now,
                error_code=error_code,
            )
            if not unit_of_work.assistant.transition_run(failed, expected_status="running"):
                raise AssistantConversationBusyError
            _record_run_finished(unit_of_work, context, failed, "failed", now)
            unit_of_work.commit()
            return failed


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


def _owned_run(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    run_id: UUID,
    account_id: UUID,
    *,
    for_update: bool,
) -> AssistantRun:
    run = unit_of_work.assistant.get_run(
        context.workspace_id,
        None,
        run_id,
        account_id,
        for_update=for_update,
    )
    if run is None:
        raise AssistantNotFoundError
    return run


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
    assistant_message_id = uuid4()
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
        assistant_message_id=assistant_message_id,
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
    assistant_message = Message(
        message_id=assistant_message_id,
        workspace_id=context.workspace_id,
        conversation_id=conversation_id,
        role="assistant",
        status="streaming",
        parts=(),
        created_by_account_id=account_id,
        created_at=now,
        updated_at=now,
        version=1,
    )
    return MessageSubmission(message, assistant_message, run)


def _new_assistant_message(
    run: AssistantRun,
    account_id: UUID,
    now: datetime,
) -> Message:
    """为升级前遗留的排队 Run 补建流式消息，不改写用户输入事实。"""

    return Message(
        message_id=uuid4(),
        workspace_id=run.workspace_id,
        conversation_id=run.conversation_id,
        role="assistant",
        status="streaming",
        parts=(),
        created_by_account_id=account_id,
        created_at=now,
        updated_at=now,
        version=1,
    )


def _finished_assistant_message(
    run: AssistantRun,
    account_id: UUID,
    text: str,
    now: datetime,
    status: Literal["completed", "failed"],
) -> Message:
    """构造助手终态消息；失败消息不保存供应商正文或内部异常。"""

    assert run.assistant_message_id is not None
    parts = (
        (MessagePart(uuid4(), run.assistant_message_id, 1, "text", text, now),)
        if status == "completed"
        else ()
    )
    return Message(
        message_id=run.assistant_message_id,
        workspace_id=run.workspace_id,
        conversation_id=run.conversation_id,
        role="assistant",
        status=status,
        parts=parts,
        created_by_account_id=account_id,
        created_at=run.created_at,
        updated_at=now,
        version=2,
    )


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


def _record_run_finished(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    run: AssistantRun,
    outcome: Literal["completed", "failed"],
    occurred_at: datetime,
) -> None:
    """用脱敏终态记录审计和 Outbox，模型正文只保存在助手消息中。"""

    attributes: dict[str, object] = {
        "conversation_id": str(run.conversation_id),
        "assistant_message_id": str(run.assistant_message_id),
        "status": run.status,
    }
    if run.error_code is not None:
        attributes["error_code"] = run.error_code
    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=f"assistant.run.{outcome}",
            resource_type="assistant_run",
            resource_id=run.run_id,
            outcome="succeeded" if outcome == "completed" else "failed",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            attributes=attributes,
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type=f"assistant.run.{outcome}",
            workspace_id=context.workspace_id,
            aggregate_id=run.run_id,
            aggregate_version=2,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=attributes,
        )
    )
