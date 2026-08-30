"""实现私有会话、不可变消息与助手运行排队用例。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.runtime import RuntimeConfigSnapshot
from ai_platform_api.modules.assistant.application.errors import (
    AssistantConversationBusyError,
    AssistantDeniedError,
    AssistantFeedbackConflictError,
    AssistantIdempotencyConflictError,
    AssistantNotFoundError,
    AssistantValidationError,
)
from ai_platform_api.modules.assistant.domain.models import (
    AssistantRun,
    AssistantUnitOfWork,
    AssistantWriteConflictError,
    AttachmentMediaType,
    Conversation,
    ConversationAttachment,
    ConversationScopeMode,
    FeedbackIssueCode,
    FeedbackRating,
    Message,
    MessageFeedback,
    MessagePart,
    MessageSubmission,
    RuntimeConfigurationBootstrap,
)
from ai_platform_api.modules.integration.domain.events import IntegrationEvent
from ai_platform_api.modules.model_gateway.domain.runtime_errors import (
    AiRuntimeConfigNotActiveError,
)
from ai_platform_api.modules.service_governance.application.system_assistant import (
    SystemServiceRouteSync,
    ensure_system_service_route,
)
from ai_platform_api.modules.service_governance.domain.models import CurrentRouteInvalidator

IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
FEEDBACK_RATINGS = frozenset({"helpful", "unhelpful"})
FEEDBACK_ISSUE_CODES = frozenset(
    {"incorrect", "missing_source", "source_mismatch", "unsafe", "other"}
)
ATTACHMENT_MEDIA_TYPES = frozenset({"text/plain", "text/markdown", "text/csv", "application/json"})
MAX_ATTACHMENT_COUNT = 3
MAX_ATTACHMENT_BYTES = 20_000

__all__ = [
    "AssistantConversationService",
    "AssistantRun",
    "Conversation",
    "ConversationAttachment",
    "Message",
    "MessageFeedback",
]


class AssistantConversationService:
    """维护创建者私有会话，并把每次用户消息排入可追溯运行队列。"""

    def __init__(
        self,
        unit_of_work: AssistantUnitOfWork,
        runtime_bootstrap: RuntimeConfigurationBootstrap | None = None,
        current_route_invalidator: CurrentRouteInvalidator | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._runtime_bootstrap = runtime_bootstrap
        self._current_route_invalidator = current_route_invalidator

    def create_conversation(
        self,
        context: RequestContext,
        *,
        title: str | None,
    ) -> Conversation:
        """创建私有会话，并确保空间已有匹配当前模型配置的系统助手发布。"""

        # 1. 先验证成员并同步系统 Release 与 Service Route，避免创建不可运行的空会话。
        account_id = _browser_account(context)
        normalized_title = _normalize_title(title)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            runtime_config = _current_runtime_config(
                unit_of_work,
                self._runtime_bootstrap,
                account_id,
            )
            release = unit_of_work.assistant.get_or_create_system_release(
                workspace_id=context.workspace_id,
                account_id=account_id,
                runtime_config=runtime_config,
                released_at=now,
            )
            route_sync = ensure_system_service_route(
                unit_of_work,
                context,
                agent_id=release.agent_id,
                release_id=release.release_id,
                occurred_at=now,
            )
            # 2. 服务事实就绪后再创建会话，并与审计和 Outbox 一次提交。
            conversation = Conversation(
                conversation_id=uuid4(),
                workspace_id=context.workspace_id,
                created_by_account_id=account_id,
                conversation_kind="private",
                title=normalized_title,
                status="active",
                scope_mode="workspace",
                knowledge_base_ids=(),
                tag_ids=(),
                created_at=now,
                updated_at=now,
                version=1,
            )
            unit_of_work.assistant.add_conversation(conversation)
            _record_conversation_event(unit_of_work, context, conversation, "created", now)
            unit_of_work.commit()
        self._invalidate_synced_route(context, route_sync)
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

    def list_runs(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        limit: int,
    ) -> tuple[AssistantRun, ...]:
        """返回会话运行事实，供页面刷新后恢复活动 SSE 游标和终态。"""

        account_id = _browser_account(context)
        _require_limit(limit)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            _owned_conversation(unit_of_work, context, conversation_id, account_id)
            return unit_of_work.assistant.list_runs(
                context.workspace_id,
                conversation_id,
                account_id,
                limit=limit,
            )

    def archive_conversation(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
    ) -> Conversation:
        """归档当前账号创建的活动会话，已归档请求保持幂等。"""

        # 1. 先锁定并校验会话归属，活动 Run 存在时拒绝改变生命周期。
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
            # 2. 归档同时清理临时附件并写入审计与清理意图，所有事实在一个事务提交。
            archived = replace(
                current,
                status="archived",
                updated_at=now,
                version=current.version + 1,
            )
            unit_of_work.assistant.save_conversation(archived)
            removed_attachment_count = unit_of_work.assistant.delete_conversation_attachments(
                context.workspace_id,
                conversation_id,
            )
            _record_conversation_event(unit_of_work, context, archived, "archived", now)
            _record_attachment_cleanup(
                unit_of_work,
                context,
                archived,
                removed_attachment_count,
                now,
            )
            unit_of_work.commit()
            return archived

    def update_conversation_scope(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        scope_mode: ConversationScopeMode,
        knowledge_base_ids: tuple[UUID, ...],
        tag_ids: tuple[UUID, ...],
    ) -> Conversation:
        """更新活动会话的知识选择；Run 会在消息提交时另行冻结解析结果。"""

        # 1. 规范化选择并校验空范围，避免客户端顺序或重复值影响权限判断。
        account_id = _browser_account(context)
        normalized_bases, normalized_tags = _normalize_scope_selection(
            scope_mode,
            knowledge_base_ids,
            tag_ids,
        )
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
            if current.status != "active" or unit_of_work.assistant.has_active_run(
                context.workspace_id, conversation_id
            ):
                raise AssistantConversationBusyError
            if (
                scope_mode == "selected"
                and unit_of_work.assistant.resolve_conversation_scope(
                    context.workspace_id,
                    knowledge_base_ids=normalized_bases,
                    tag_ids=normalized_tags,
                )
                is None
            ):
                raise AssistantNotFoundError
            # 2. 仅保存已解析且可授权的范围，消息提交时再复制为不可变 Run 快照。
            updated = replace(
                current,
                scope_mode=scope_mode,
                knowledge_base_ids=normalized_bases,
                tag_ids=normalized_tags,
                updated_at=now,
                version=current.version + 1,
            )
            unit_of_work.assistant.save_conversation(updated)
            _record_conversation_event(unit_of_work, context, updated, "scope_updated", now)
            unit_of_work.commit()
            return updated

    def list_attachments(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
    ) -> tuple[ConversationAttachment, ...]:
        """列出当前账号私有会话的临时附件，不跨会话复用。"""

        account_id = _browser_account(context)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            _owned_conversation(unit_of_work, context, conversation_id, account_id)
            return unit_of_work.assistant.list_attachments(
                context.workspace_id,
                conversation_id,
                account_id,
            )

    def upload_attachment(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        file_name: str,
        media_type: str,
        content: bytes,
    ) -> ConversationAttachment:
        """保存小型 UTF-8 文本附件；它不进入永久知识库、索引或在线正文模型。"""

        # 1. 在事务外完成大小、编码和媒体类型校验，拒绝二进制内容进入模型上下文。
        account_id = _browser_account(context)
        normalized_name, normalized_type, text_content = _normalize_attachment(
            file_name,
            media_type,
            content,
        )
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            # 2. 事务内再次锁定会话并限制数量，保证活动 Run 与附件写入不会竞态。
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            conversation = _owned_conversation(
                unit_of_work,
                context,
                conversation_id,
                account_id,
                for_update=True,
            )
            if conversation.status != "active" or unit_of_work.assistant.has_active_run(
                context.workspace_id, conversation_id
            ):
                raise AssistantConversationBusyError
            current = unit_of_work.assistant.list_attachments(
                context.workspace_id,
                conversation_id,
                account_id,
            )
            if len(current) >= MAX_ATTACHMENT_COUNT:
                raise AssistantValidationError
            attachment = ConversationAttachment(
                attachment_id=uuid4(),
                workspace_id=context.workspace_id,
                conversation_id=conversation_id,
                created_by_account_id=account_id,
                file_name=normalized_name,
                media_type=normalized_type,
                content=text_content,
                size_bytes=len(content),
                content_hash=hashlib.sha256(content).hexdigest(),
                created_at=now,
            )
            unit_of_work.assistant.add_attachment(attachment)
            _record_attachment_event(unit_of_work, context, attachment, "uploaded", now)
            unit_of_work.commit()
            return attachment

    def delete_attachment(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        attachment_id: UUID,
    ) -> None:
        """删除临时附件；活动 Run 期间拒绝删除，避免模型上下文在执行中漂移。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            conversation = _owned_conversation(
                unit_of_work,
                context,
                conversation_id,
                account_id,
                for_update=True,
            )
            if conversation.status != "active" or unit_of_work.assistant.has_active_run(
                context.workspace_id, conversation_id
            ):
                raise AssistantConversationBusyError
            attachment = unit_of_work.assistant.get_attachment(
                context.workspace_id,
                conversation_id,
                attachment_id,
                account_id,
            )
            if attachment is None:
                raise AssistantNotFoundError
            unit_of_work.assistant.delete_attachment(
                context.workspace_id,
                conversation_id,
                attachment_id,
            )
            _record_attachment_event(unit_of_work, context, attachment, "deleted", now)
            unit_of_work.commit()

    def get_run_attachments(
        self,
        context: RequestContext,
        *,
        run_id: UUID,
    ) -> tuple[ConversationAttachment, ...]:
        """为执行器读取 Run 冻结附件；任一附件已缺失时失败关闭。"""

        account_id, actor_id = _request_principal(context)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            run = _owned_run(unit_of_work, context, run_id, actor_id, for_update=False)
            attachments = unit_of_work.assistant.get_run_attachments(run)
            if tuple(item.attachment_id for item in attachments) != run.attachment_ids:
                raise AssistantNotFoundError
            return attachments

    def create_user_message(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        texts: tuple[str, ...],
        idempotency_key: str,
        attachment_ids: tuple[UUID, ...] = (),
    ) -> MessageSubmission:
        """原子创建用户消息和 queued Run，并冻结助手及运行配置版本。"""

        # 1. 先验证稳定输入并计算请求摘要，重试可以在任何会话状态检查之前返回原结果。
        account_id = _browser_account(context)
        normalized_texts = normalize_texts(texts)
        normalized_attachment_ids = _normalize_attachment_ids(attachment_ids)
        require_idempotency_key(idempotency_key)
        request_hash = _request_hash(
            conversation_id,
            normalized_texts,
            normalized_attachment_ids,
        )
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
                scope = _resolved_conversation_scope(unit_of_work, context, conversation)
                attachments = tuple(
                    unit_of_work.assistant.get_attachment(
                        context.workspace_id,
                        conversation_id,
                        attachment_id,
                        account_id,
                    )
                    for attachment_id in normalized_attachment_ids
                )
                if any(item is None for item in attachments):
                    raise AssistantNotFoundError
                runtime_config = _current_runtime_config(
                    unit_of_work,
                    self._runtime_bootstrap,
                    account_id,
                )
                release = unit_of_work.assistant.get_or_create_system_release(
                    workspace_id=context.workspace_id,
                    account_id=account_id,
                    runtime_config=runtime_config,
                    released_at=now,
                )
                route_sync = ensure_system_service_route(
                    unit_of_work,
                    context,
                    agent_id=release.agent_id,
                    release_id=release.release_id,
                    occurred_at=now,
                )

                # 3. 用户消息、不可变 Part、排队 Run、审计和 Outbox 同事务提交。
                submission = new_submission(
                    context=context,
                    account_id=account_id,
                    actor_id=context.actor_id,
                    conversation_id=conversation_id,
                    texts=normalized_texts,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    knowledge_base_ids=scope[0],
                    document_ids=scope[1],
                    attachment_ids=normalized_attachment_ids,
                    agent_release_id=release.release_id,
                    runtime_config_version_id=release.runtime_config_version_id,
                    service_id=route_sync.deployment.service.service_id,
                    service_route_id=route_sync.deployment.route.route_id,
                    service_route_version=route_sync.deployment.route.route_version,
                    now=now,
                )
                unit_of_work.assistant.add_submission(submission)
                record_run_queued(unit_of_work, context, submission, now)
                unit_of_work.commit()
            self._invalidate_synced_route(context, route_sync)
            return submission
        except AssistantWriteConflictError as error:
            if error.reason == "conversation_busy":
                raise AssistantConversationBusyError from error
            raise AssistantIdempotencyConflictError from error

    def _invalidate_synced_route(
        self,
        context: RequestContext,
        route_sync: SystemServiceRouteSync,
    ) -> None:
        """仅在系统 Route 已提交变化后清除派生缓存，避免无变化请求重复删除。"""

        if route_sync.changed and self._current_route_invalidator is not None:
            self._current_route_invalidator.invalidate_current(
                context.workspace_id,
                route_sync.deployment.service.service_id,
            )

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

    def get_run_for_message(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        message_id: UUID,
    ) -> AssistantRun:
        """验证私有会话和助手消息归属，来源与反馈不得按裸消息 ID 查询。"""

        account_id = _browser_account(context)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            _owned_conversation(unit_of_work, context, conversation_id, account_id)
            return _owned_run_by_message(
                unit_of_work,
                context,
                conversation_id,
                message_id,
                account_id,
            )

    def cancel_run(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        run_id: UUID,
    ) -> AssistantRun:
        """条件取消 queued/running Run，并把流式助手消息收敛为失败展示状态。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            _owned_conversation(unit_of_work, context, conversation_id, account_id)
            run = _owned_run(unit_of_work, context, run_id, account_id, for_update=True)
            if run.conversation_id != conversation_id:
                raise AssistantNotFoundError
            if run.status not in {"queued", "running"}:
                return run
            if run.assistant_message_id is None:
                raise AssistantConversationBusyError

            # 先把占位消息置为无正文失败态，再以原状态作为条件关闭 Run，避免完成与取消互相覆盖。
            message = _finished_assistant_message(run, account_id, "", now, "failed")
            if not unit_of_work.assistant.finish_assistant_message(message):
                raise AssistantConversationBusyError
            cancelled = replace(
                run,
                status="cancelled",
                updated_at=now,
                completed_at=now,
                error_code="RUN_CANCELLED",
            )
            if not unit_of_work.assistant.transition_run(cancelled, expected_status=run.status):
                raise AssistantConversationBusyError
            _record_run_finished(unit_of_work, context, cancelled, "cancelled", now)
            unit_of_work.commit()
            return cancelled

    def get_feedback(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        message_id: UUID,
    ) -> MessageFeedback | None:
        """读取当前账号对助手消息的反馈；没有提交时返回空事实。"""

        account_id = _browser_account(context)
        with self._unit_of_work as unit_of_work:
            # 成员、私有会话和消息归属必须与反馈读取位于同一事务，避免撤权后读取旧检查结果。
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            _owned_conversation(unit_of_work, context, conversation_id, account_id)
            _owned_run_by_message(
                unit_of_work,
                context,
                conversation_id,
                message_id,
                account_id,
            )
            return unit_of_work.assistant.get_feedback(
                context.workspace_id,
                message_id,
                account_id,
            )

    def submit_feedback(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        message_id: UUID,
        rating: FeedbackRating,
        issue_codes: tuple[FeedbackIssueCode, ...],
        comment: str | None,
    ) -> MessageFeedback:
        """新增或修订单账号单消息反馈，并与审计和 Outbox 同事务提交。"""

        normalized_issues, normalized_comment = _normalize_feedback(rating, issue_codes, comment)
        account_id = _browser_account(context)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                # 1. 在反馈写事务内重新验证成员、私有会话及消息终态，撤权或运行变化立即失败关闭。
                _require_active_member(unit_of_work, context.workspace_id, account_id)
                _owned_conversation(unit_of_work, context, conversation_id, account_id)
                run = _owned_run_by_message(
                    unit_of_work,
                    context,
                    conversation_id,
                    message_id,
                    account_id,
                )
                if run.status not in {"completed", "failed"}:
                    raise AssistantConversationBusyError

                # 2. 单账号单消息反馈按版本修订；自由文本只留在反馈事实，不进入审计和 Outbox。
                current = unit_of_work.assistant.get_feedback(
                    context.workspace_id,
                    message_id,
                    account_id,
                    for_update=True,
                )
                feedback = MessageFeedback(
                    feedback_id=current.feedback_id if current is not None else uuid4(),
                    workspace_id=context.workspace_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    run_id=run.run_id,
                    account_id=account_id,
                    rating=rating,
                    issue_codes=normalized_issues,
                    comment=normalized_comment,
                    created_at=current.created_at if current is not None else now,
                    updated_at=now,
                    version=(current.version + 1) if current is not None else 1,
                )
                unit_of_work.assistant.save_feedback(feedback)
                _record_feedback_event(unit_of_work, context, feedback, now)
                unit_of_work.commit()
                return feedback
        except AssistantWriteConflictError as error:
            raise AssistantFeedbackConflictError from error

    def claim_run(self, context: RequestContext, *, run_id: UUID) -> AssistantRun | None:
        """仅把当前 Actor 的 queued Run 认领一次；重复后台任务直接退出。"""

        account_id, actor_id = _request_principal(context)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            run = _owned_run(unit_of_work, context, run_id, actor_id, for_update=True)
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
        account_id, actor_id = _request_principal(context)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            run = _owned_run(unit_of_work, context, run_id, actor_id, for_update=True)
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
        account_id, actor_id = _request_principal(context)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            run = _owned_run(unit_of_work, context, run_id, actor_id, for_update=True)
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


def _request_principal(context: RequestContext) -> tuple[UUID, UUID]:
    """恢复运行所需的人类账号和独立 Actor，拒绝未知认证方式。"""

    if context.user_id is None or context.authentication_method not in {
        "browser_session",
        "open_api_key",
    }:
        raise AssistantDeniedError
    if context.authentication_method == "browser_session" and context.user_id != context.actor_id:
        raise AssistantDeniedError
    return context.user_id, context.actor_id


def _require_active_member(
    unit_of_work: AssistantUnitOfWork,
    workspace_id: UUID,
    account_id: UUID,
) -> None:
    if not unit_of_work.assistant.get_active_member(workspace_id, account_id):
        raise AssistantDeniedError


def _current_runtime_config(
    unit_of_work: AssistantUnitOfWork,
    bootstrap: RuntimeConfigurationBootstrap | None,
    account_id: UUID,
) -> RuntimeConfigSnapshot:
    if bootstrap is not None:
        # 显式本地模式每次都做幂等复核，使误停用的内置 Provider 在下一次交互前恢复。
        return bootstrap.ensure(account_id)
    current = unit_of_work.assistant.get_current_runtime_config()
    if current is not None:
        return current
    raise AiRuntimeConfigNotActiveError


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


def _owned_run_by_message(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    conversation_id: UUID,
    message_id: UUID,
    account_id: UUID,
) -> AssistantRun:
    """按工作空间、私有会话、助手消息和请求账号共同收敛 Run 读取。"""

    run = unit_of_work.assistant.get_run_by_assistant_message(
        context.workspace_id,
        conversation_id,
        message_id,
        account_id,
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


def _normalize_scope_selection(
    scope_mode: ConversationScopeMode,
    knowledge_base_ids: tuple[UUID, ...],
    tag_ids: tuple[UUID, ...],
) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
    """规范会话范围并保留稳定顺序，防止重复标识制造不同语义的版本。"""

    bases = tuple(dict.fromkeys(knowledge_base_ids))
    tags = tuple(dict.fromkeys(tag_ids))
    if (
        scope_mode not in {"workspace", "selected"}
        or len(bases) != len(knowledge_base_ids)
        or len(tags) != len(tag_ids)
        or len(bases) > 20
        or len(tags) > 20
    ):
        raise AssistantValidationError
    if scope_mode == "workspace":
        if bases or tags:
            raise AssistantValidationError
        return (), ()
    if not bases and not tags:
        raise AssistantValidationError
    return bases, tags


def _resolved_conversation_scope(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    conversation: Conversation,
) -> tuple[frozenset[UUID] | None, frozenset[UUID] | None]:
    """在排队事务内解析活动资源；失效选择不能退回工作空间全量检索。"""

    if conversation.scope_mode == "workspace":
        return None, None
    resolved = unit_of_work.assistant.resolve_conversation_scope(
        context.workspace_id,
        knowledge_base_ids=conversation.knowledge_base_ids,
        tag_ids=conversation.tag_ids,
    )
    if resolved is None:
        raise AssistantNotFoundError
    return resolved


def _normalize_attachment(
    file_name: str,
    media_type: str,
    content: bytes,
) -> tuple[str, AttachmentMediaType, str]:
    """限制临时附件为小型 UTF-8 文本，并拒绝路径名、空内容和二进制控制字符。"""

    normalized_name = file_name.strip()
    normalized_type = media_type.split(";", maxsplit=1)[0].strip().casefold()
    if (
        not normalized_name
        or len(normalized_name) > 255
        or "/" in normalized_name
        or "\\" in normalized_name
        or normalized_type not in ATTACHMENT_MEDIA_TYPES
        or not 1 <= len(content) <= MAX_ATTACHMENT_BYTES
    ):
        raise AssistantValidationError
    try:
        text_content = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AssistantValidationError from error
    if (
        not text_content.strip()
        or len(text_content) > MAX_ATTACHMENT_BYTES
        or any(ord(character) < 32 and character not in "\n\r\t" for character in text_content)
    ):
        raise AssistantValidationError
    return normalized_name, cast("AttachmentMediaType", normalized_type), text_content


def _normalize_attachment_ids(attachment_ids: tuple[UUID, ...]) -> tuple[UUID, ...]:
    normalized = tuple(dict.fromkeys(attachment_ids))
    if len(normalized) != len(attachment_ids) or len(normalized) > MAX_ATTACHMENT_COUNT:
        raise AssistantValidationError
    return normalized


def normalize_texts(texts: tuple[str, ...]) -> tuple[str, ...]:
    """规范消息文本并统一执行 Part 数量、单项长度和总长度边界。"""

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


def require_idempotency_key(idempotency_key: str) -> None:
    """校验助手与服务出口共享的幂等键格式，拒绝不可稳定持久化的键。"""

    if IDEMPOTENCY_PATTERN.fullmatch(idempotency_key) is None:
        raise AssistantValidationError


def _normalize_feedback(
    rating: FeedbackRating,
    issue_codes: tuple[FeedbackIssueCode, ...],
    comment: str | None,
) -> tuple[tuple[FeedbackIssueCode, ...], str | None]:
    issues = tuple(dict.fromkeys(issue_codes))
    normalized_comment = comment.strip() if comment is not None else None
    if normalized_comment == "":
        normalized_comment = None
    if (
        rating not in FEEDBACK_RATINGS
        or any(issue not in FEEDBACK_ISSUE_CODES for issue in issues)
        or (rating == "helpful" and issues)
        or (rating == "unhelpful" and not issues)
        or len(issues) > 5
        or (normalized_comment is not None and len(normalized_comment) > 1000)
    ):
        raise AssistantValidationError
    return issues, normalized_comment


def _request_hash(
    conversation_id: UUID,
    texts: tuple[str, ...],
    attachment_ids: tuple[UUID, ...] = (),
) -> str:
    canonical = json.dumps(
        {
            "attachment_ids": [str(value) for value in attachment_ids],
            "conversation_id": str(conversation_id),
            "parts": list(texts),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def new_submission(
    *,
    context: RequestContext,
    account_id: UUID,
    actor_id: UUID,
    conversation_id: UUID,
    texts: tuple[str, ...],
    idempotency_key: str,
    request_hash: str,
    knowledge_base_ids: frozenset[UUID] | None,
    document_ids: frozenset[UUID] | None,
    attachment_ids: tuple[UUID, ...],
    agent_release_id: UUID,
    runtime_config_version_id: UUID,
    service_id: UUID,
    service_route_id: UUID,
    service_route_version: int,
    now: datetime,
) -> MessageSubmission:
    """构造冻结服务路由与发布版本的排队 Run；持久化和提交由调用方负责。"""

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
        service_id=service_id,
        service_route_id=service_route_id,
        service_route_version=service_route_version,
        agent_release_id=agent_release_id,
        runtime_config_version_id=runtime_config_version_id,
        requested_by_account_id=account_id,
        requested_by_actor_id=actor_id,
        knowledge_base_ids=knowledge_base_ids,
        document_ids=document_ids,
        attachment_ids=attachment_ids,
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
    transition: Literal["created", "archived", "scope_updated"],
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
            authorization=context.audit_authorization,
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
                "scope_mode": conversation.scope_mode,
                "knowledge_base_count": len(conversation.knowledge_base_ids),
                "tag_count": len(conversation.tag_ids),
            },
        )
    )


def _record_attachment_event(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    attachment: ConversationAttachment,
    transition: Literal["uploaded", "deleted"],
    occurred_at: datetime,
) -> None:
    """记录附件元数据与摘要，不把临时正文写入审计或 Outbox。"""

    action = f"assistant.attachment.{transition}"
    attributes: dict[str, object] = {
        "conversation_id": str(attachment.conversation_id),
        "file_name": attachment.file_name,
        "media_type": attachment.media_type,
        "size_bytes": attachment.size_bytes,
        "content_hash": attachment.content_hash,
    }
    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type="conversation_attachment",
            resource_id=attachment.attachment_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes=attributes,
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type=action,
            workspace_id=context.workspace_id,
            aggregate_id=attachment.attachment_id,
            aggregate_version=1,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=attributes,
        )
    )


def _record_attachment_cleanup(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    conversation: Conversation,
    removed_count: int,
    occurred_at: datetime,
) -> None:
    """归档清理只记录数量，已删除附件的名称和正文不继续传播。"""

    if removed_count == 0:
        return
    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action="assistant.attachment.cleaned",
            resource_type="conversation",
            resource_id=conversation.conversation_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes={"removed_count": removed_count},
        )
    )


def record_run_queued(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    submission: MessageSubmission,
    occurred_at: datetime,
) -> None:
    """追加不含消息正文的排队审计与 Outbox，并由调用方纳入同一事务提交。"""

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
            authorization=context.audit_authorization,
            attributes={
                "conversation_id": str(run.conversation_id),
                "message_id": str(run.user_message_id),
                "service_id": str(run.service_id),
                "service_route_id": str(run.service_route_id),
                "service_route_version": run.service_route_version,
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
                "service_id": str(run.service_id),
                "service_route_id": str(run.service_route_id),
                "service_route_version": run.service_route_version,
                "agent_release_id": str(run.agent_release_id),
                "runtime_config_version_id": str(run.runtime_config_version_id),
            },
        )
    )


def _record_run_finished(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    run: AssistantRun,
    outcome: Literal["completed", "failed", "cancelled"],
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
            outcome="succeeded" if outcome in {"completed", "cancelled"} else "failed",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
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


def _record_feedback_event(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    feedback: MessageFeedback,
    occurred_at: datetime,
) -> None:
    """只记录反馈类型和问题标签，用户自由文本不进入审计或集成事件。"""

    attributes: dict[str, object] = {
        "conversation_id": str(feedback.conversation_id),
        "message_id": str(feedback.message_id),
        "run_id": str(feedback.run_id),
        "rating": feedback.rating,
        "issue_codes": list(feedback.issue_codes),
        "version": feedback.version,
    }
    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action="assistant.feedback.submitted",
            resource_type="message_feedback",
            resource_id=feedback.feedback_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes=attributes,
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type="assistant.feedback.submitted",
            workspace_id=context.workspace_id,
            aggregate_id=feedback.feedback_id,
            aggregate_version=feedback.version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=attributes,
        )
    )
