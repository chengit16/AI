"""实现企业大脑会话与每次提问重新鉴权的应用用例。"""

from __future__ import annotations

import hashlib
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
    AssistantFeedbackConflictError,
    AssistantIdempotencyConflictError,
    AssistantNotFoundError,
    AssistantValidationError,
)
from ai_platform_api.modules.assistant.application.service import (
    current_runtime_config,
    message_request_hash,
    new_submission,
    normalize_texts,
    normalize_title,
    record_run_queued,
    require_idempotency_key,
)
from ai_platform_api.modules.assistant.application.sources import (
    CurrentEvidenceCitationCounter,
)
from ai_platform_api.modules.assistant.domain.models import (
    AssistantRun,
    AssistantUnitOfWork,
    AssistantWriteConflictError,
    Conversation,
    ConversationAttachment,
    EnterpriseBrainOverview,
    EnterpriseBrainReport,
    FeedbackIssueCode,
    FeedbackRating,
    Message,
    MessageFeedback,
    MessagePart,
    MessageSubmission,
    RuntimeConfigurationBootstrap,
)
from ai_platform_api.modules.authorization.application.policy import (
    AuthorizationPolicyUnavailableError,
)
from ai_platform_api.modules.authorization.domain.policy import (
    PolicyDecision,
    PolicyDecisionPoint,
)
from ai_platform_api.modules.enterprise_knowledge.domain.models import (
    ResolvedKnowledgeDomainScope,
)
from ai_platform_api.modules.integration.domain.events import IntegrationEvent
from ai_platform_api.modules.retrieval.application.authorization import (
    retrieval_policy_request,
)
from ai_platform_api.modules.service_governance.application.system_assistant import (
    SystemServiceRouteSync,
    ensure_system_service_route,
)
from ai_platform_api.modules.service_governance.domain.models import CurrentRouteInvalidator

__all__ = [
    "AssistantRun",
    "Conversation",
    "EnterpriseBrainConversationService",
    "EnterpriseBrainReport",
    "Message",
    "MessageFeedback",
]


class EnterpriseBrainConversationService:
    """维护独立企业大脑会话，并在每次提问前重新收敛知识域范围。"""

    def __init__(
        self,
        unit_of_work: AssistantUnitOfWork,
        policy: PolicyDecisionPoint,
        current_evidence: CurrentEvidenceCitationCounter,
        runtime_bootstrap: RuntimeConfigurationBootstrap | None = None,
        current_route_invalidator: CurrentRouteInvalidator | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy = policy
        self._current_evidence = current_evidence
        self._runtime_bootstrap = runtime_bootstrap
        self._current_route_invalidator = current_route_invalidator

    def create_conversation(
        self,
        context: RequestContext,
        *,
        knowledge_domain_id: UUID,
        title: str | None,
    ) -> Conversation:
        """选择当前获权知识域创建会话，空范围不创建任何会话事实。"""

        # 1. 在进入写事务前固定浏览器身份和 PDP 授权上下文，随后复核成员与知识域有效范围。
        account_id = _browser_account(context)
        authorized = self._retrieval_authorized_context(context)
        normalized_title = normalize_title(title)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            scope = _resolved_domain_scope(
                unit_of_work,
                authorized,
                knowledge_domain_id,
                account_id,
            )
            runtime_config = current_runtime_config(
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
            # 2. 只有模型路由与知识域范围都可用时才创建会话，并将业务事实、审计和事件原子提交。
            conversation = Conversation(
                conversation_id=uuid4(),
                workspace_id=context.workspace_id,
                created_by_account_id=account_id,
                conversation_kind="enterprise_brain",
                title=normalized_title,
                status="active",
                scope_mode="workspace",
                knowledge_base_ids=(),
                tag_ids=(),
                created_at=now,
                updated_at=now,
                version=1,
                knowledge_domain_id=knowledge_domain_id,
                knowledge_domain_policy_version=scope.policy_version,
            )
            unit_of_work.assistant.add_conversation(conversation)
            _record_conversation_event(
                unit_of_work,
                context,
                conversation,
                "created",
                now,
            )
            unit_of_work.commit()
        self._invalidate_route(context, route_sync)
        return conversation

    def list_conversations(
        self,
        context: RequestContext,
        *,
        limit: int,
    ) -> tuple[Conversation, ...]:
        """只返回当前账号自己的企业大脑会话，不混入普通私聊。"""

        account_id = _browser_account(context)
        if not 1 <= limit <= 200:
            raise AssistantNotFoundError
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            return unit_of_work.assistant.list_conversations(
                context.workspace_id,
                account_id,
                limit=limit,
                conversation_kind="enterprise_brain",
            )

    def create_user_message(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        texts: tuple[str, ...],
        idempotency_key: str,
    ) -> MessageSubmission:
        """重新解析知识域当前授权交集，并原子冻结到排队 Run。"""

        # 1. 固定可信账号、PDP 上下文和请求摘要，使后续幂等比较不受输入表现形式影响。
        account_id = _browser_account(context)
        authorized = self._retrieval_authorized_context(context)
        normalized_texts = normalize_texts(texts)
        require_idempotency_key(idempotency_key)
        request_hash = message_request_hash(conversation_id, normalized_texts)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                # 2. 先在当前空间重验成员并处理幂等重放，冲突请求不能复用既有 Run。
                _require_active_member(unit_of_work, context.workspace_id, account_id)
                existing = unit_of_work.assistant.get_submission(
                    context.workspace_id,
                    account_id,
                    idempotency_key,
                )
                if existing is not None:
                    if (
                        existing.run.request_hash != request_hash
                        or existing.run.knowledge_domain_id is None
                    ):
                        raise AssistantIdempotencyConflictError
                    return existing

                # 3. 锁定会话后重新计算知识域交集，再把范围、策略版本和路由快照冻结到同一 Run。
                conversation = _owned_conversation(
                    unit_of_work,
                    context,
                    conversation_id,
                    account_id,
                    for_update=True,
                )
                if conversation.status != "active":
                    raise AssistantNotFoundError
                if unit_of_work.assistant.has_active_run(
                    context.workspace_id,
                    conversation_id,
                ):
                    raise AssistantConversationBusyError
                if conversation.knowledge_domain_id is None:
                    raise AssistantNotFoundError
                scope = _resolved_domain_scope(
                    unit_of_work,
                    authorized,
                    conversation.knowledge_domain_id,
                    account_id,
                )
                runtime_config = current_runtime_config(
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
                submission = new_submission(
                    context=context,
                    account_id=account_id,
                    actor_id=context.actor_id,
                    conversation_id=conversation_id,
                    texts=normalized_texts,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    knowledge_base_ids=frozenset(scope.effective_knowledge_base_ids),
                    document_ids=None,
                    attachment_ids=(),
                    agent_release_id=release.release_id,
                    runtime_config_version_id=release.runtime_config_version_id,
                    service_id=route_sync.deployment.service.service_id,
                    service_route_id=route_sync.deployment.route.route_id,
                    service_route_version=route_sync.deployment.route.route_version,
                    now=now,
                    knowledge_domain_id=scope.domain_id,
                    knowledge_domain_policy_version=scope.policy_version,
                )
                unit_of_work.assistant.add_submission(submission)
                record_run_queued(unit_of_work, context, submission, now)
                unit_of_work.commit()
            self._invalidate_route(context, route_sync)
            return submission
        except AssistantWriteConflictError as error:
            if error.reason == "conversation_busy":
                raise AssistantConversationBusyError from error
            raise AssistantIdempotencyConflictError from error

    def list_messages(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        limit: int,
    ) -> tuple[Message, ...]:
        """返回自己的企业大脑消息，管理员权限不会扩大到他人会话。"""

        account_id = _browser_account(context)
        if not 1 <= limit <= 200:
            raise AssistantNotFoundError
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
        """返回自己的企业大脑 Run 事实，供页面恢复流状态。"""

        account_id = _browser_account(context)
        if not 1 <= limit <= 200:
            raise AssistantNotFoundError
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            _owned_conversation(unit_of_work, context, conversation_id, account_id)
            return unit_of_work.assistant.list_runs(
                context.workspace_id,
                conversation_id,
                account_id,
                limit=limit,
            )

    def create_report(
        self,
        context: RequestContext,
        *,
        message_id: UUID,
        template: Literal["briefing", "risk_review", "comparison"],
        title: str,
        idempotency_key: str,
    ) -> EnterpriseBrainReport:
        """从当前账号已完成的企业回答生成不可变 Markdown 报告。"""

        # 1. 先规范模板、标题和幂等键，拒绝无法形成稳定报告语义的请求。
        account_id = _browser_account(context)
        require_idempotency_key(idempotency_key)
        normalized_title = normalize_title(title)
        if normalized_title is None or template not in {"briefing", "risk_review", "comparison"}:
            raise AssistantValidationError
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                # 2. 在空间和账号边界内处理重放，并只接受当前企业大脑会话的已完成回答。
                _require_active_member(unit_of_work, context.workspace_id, account_id)
                existing = unit_of_work.assistant.get_enterprise_brain_report_by_key(
                    context.workspace_id,
                    account_id,
                    idempotency_key,
                )
                if existing is not None:
                    if existing.message_id != message_id or existing.template != template:
                        raise AssistantIdempotencyConflictError
                    return existing
                run = _owned_enterprise_run_by_message(
                    unit_of_work,
                    context,
                    _conversation_id_for_message(unit_of_work, context, message_id, account_id),
                    message_id,
                    account_id,
                )
                if run.status != "completed" or run.assistant_message_id != message_id:
                    raise AssistantNotFoundError
                conversation = _owned_conversation(
                    unit_of_work,
                    context,
                    run.conversation_id,
                    account_id,
                )
                messages = unit_of_work.assistant.list_messages(
                    context.workspace_id,
                    conversation.conversation_id,
                    limit=200,
                )
                answer = next(
                    (
                        _message_text(message)
                        for message in messages
                        if message.message_id == message_id and message.role == "assistant"
                    ),
                    None,
                )
                if answer is None or not answer.strip():
                    raise AssistantNotFoundError
                # 3. 引用数量来自当前仍获授权的不可变检索证据；模型正文中的编号不是
                # 可信引用事实。来源后续撤权不改写已经形成的历史报告。
                citation_count = self._current_evidence.count_current_citations(
                    context,
                    run_id=run.run_id,
                )
                # 4. 从已落库答案生成内容摘要，报告、审计与 Outbox 一次提交，正文落库后
                # 不再改写。
                content = _report_markdown(normalized_title, template, answer)
                report = EnterpriseBrainReport(
                    report_id=uuid4(),
                    workspace_id=context.workspace_id,
                    created_by_account_id=account_id,
                    conversation_id=conversation.conversation_id,
                    message_id=message_id,
                    run_id=run.run_id,
                    template=template,
                    title=normalized_title,
                    content=content,
                    content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    citation_count=citation_count,
                    idempotency_key=idempotency_key,
                    created_at=now,
                )
                unit_of_work.assistant.add_enterprise_brain_report(report)
                _record_report_event(unit_of_work, context, report, now)
                unit_of_work.commit()
                return report
        except AssistantWriteConflictError as error:
            raise AssistantIdempotencyConflictError from error

    def get_report(
        self,
        context: RequestContext,
        *,
        report_id: UUID,
    ) -> EnterpriseBrainReport:
        """读取当前账号自己的报告；来源撤权不影响报告正文历史事实。"""

        account_id = _browser_account(context)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            report = unit_of_work.assistant.get_enterprise_brain_report(
                context.workspace_id,
                report_id,
                account_id,
            )
            if report is None:
                raise AssistantNotFoundError
            return report

    def list_reports(
        self,
        context: RequestContext,
        *,
        limit: int,
    ) -> tuple[EnterpriseBrainReport, ...]:
        """按时间倒序返回当前账号创建的报告元数据与正文。"""

        account_id = _browser_account(context)
        if not 1 <= limit <= 200:
            raise AssistantValidationError
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            return unit_of_work.assistant.list_enterprise_brain_reports(
                context.workspace_id,
                account_id,
                limit=limit,
            )

    def get_overview(self, context: RequestContext) -> EnterpriseBrainOverview:
        """返回企业大脑低敏统计；个人空间和未登录调用均失败关闭。"""

        account_id = _browser_account(context)
        generated_at = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            if not unit_of_work.enterprise_knowledge.require_active_enterprise_member(
                context.workspace_id, account_id
            ):
                raise AssistantNotFoundError
            overview = unit_of_work.assistant.get_enterprise_brain_overview(
                context.workspace_id,
                account_id,
                generated_at=generated_at,
            )
            if overview is None:
                raise AssistantNotFoundError
            return overview

    def archive_conversation(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
    ) -> Conversation:
        """归档自己的企业大脑会话，活动 Run 存在时拒绝变更。"""

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

    def get_run_for_stream(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        run_id: UUID,
    ) -> AssistantRun:
        """授权自己的企业大脑 Run，供既有 SSE 协议复用。"""

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
            if run is None or run.knowledge_domain_id is None:
                raise AssistantNotFoundError
            return run

    def get_run_for_message(
        self,
        context: RequestContext,
        *,
        conversation_id: UUID,
        message_id: UUID,
    ) -> AssistantRun:
        """验证企业大脑会话和助手消息归属，来源不得按裸消息标识查询。"""

        account_id = _browser_account(context)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            _owned_conversation(unit_of_work, context, conversation_id, account_id)
            return _owned_enterprise_run_by_message(
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
        """条件取消自己的企业大脑 Run，并原子关闭流式占位消息。"""

        # 1. 复核成员与会话归属后锁定 Run；终态请求直接幂等返回，不能误改其他会话。
        account_id = _browser_account(context)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            _owned_conversation(unit_of_work, context, conversation_id, account_id)
            run = _owned_enterprise_run(
                unit_of_work,
                context,
                run_id,
                account_id,
                for_update=True,
            )
            if run.conversation_id != conversation_id:
                raise AssistantNotFoundError
            if run.status not in {"queued", "running"}:
                return run
            if run.assistant_message_id is None:
                raise AssistantConversationBusyError
            # 2. 先关闭流式占位消息，再以原状态作条件转换并提交取消审计，避免消息与 Run 终态分裂。
            message = _finished_message(run, account_id, "", now, "failed")
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
        """读取当前账号对企业大脑回答的反馈，管理员不能读取成员反馈。"""

        account_id = _browser_account(context)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            _owned_conversation(unit_of_work, context, conversation_id, account_id)
            _owned_enterprise_run_by_message(
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
        """新增或修订企业大脑回答反馈，自由文本不进入审计和 Outbox。"""

        # 1. 规范反馈字段并复核成员、私有会话和企业大脑回答终态，避免裸消息标识越权写入。
        normalized_issues, normalized_comment = _normalize_feedback(
            rating,
            issue_codes,
            comment,
        )
        account_id = _browser_account(context)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                _require_active_member(unit_of_work, context.workspace_id, account_id)
                _owned_conversation(unit_of_work, context, conversation_id, account_id)
                run = _owned_enterprise_run_by_message(
                    unit_of_work,
                    context,
                    conversation_id,
                    message_id,
                    account_id,
                )
                if run.status not in {"completed", "failed"}:
                    raise AssistantConversationBusyError
                # 2. 锁定当前反馈后按版本新增或修订，并让反馈事实、脱敏审计和事件保持同事务。
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
                _record_feedback(unit_of_work, context, feedback, now)
                unit_of_work.commit()
                return feedback
        except AssistantWriteConflictError as error:
            raise AssistantFeedbackConflictError from error

    def get_run_attachments(
        self,
        context: RequestContext,
        *,
        run_id: UUID,
    ) -> tuple[ConversationAttachment, ...]:
        """企业大脑第一版不支持临时附件，异常历史引用必须失败关闭。"""

        account_id = _browser_account(context)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            run = _owned_enterprise_run(
                unit_of_work,
                context,
                run_id,
                account_id,
                for_update=False,
            )
            attachments = unit_of_work.assistant.get_run_attachments(run)
            if run.attachment_ids or attachments:
                raise AssistantNotFoundError
            return ()

    def claim_run(self, context: RequestContext, *, run_id: UUID) -> AssistantRun | None:
        """只认领当前账号自己的排队企业大脑 Run，重复后台任务直接退出。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work, context.workspace_id, account_id)
            run = _owned_enterprise_run(
                unit_of_work,
                context,
                run_id,
                account_id,
                for_update=True,
            )
            if run.status != "queued":
                return None
            if run.assistant_message_id is None:
                message = _new_assistant_message(run, account_id, now)
                unit_of_work.assistant.add_assistant_message(message)
                run = replace(run, assistant_message_id=message.message_id)
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
        """原子保存企业大脑回答并结束运行。"""

        normalized_text = text.strip()
        if not normalized_text or len(normalized_text) > 200_000:
            raise AssistantValidationError
        return self._finish_run(
            context,
            run_id=run_id,
            text=normalized_text,
            status="completed",
            error_code=None,
        )

    def fail_run(
        self,
        context: RequestContext,
        *,
        run_id: UUID,
        error_code: str,
    ) -> AssistantRun:
        """以稳定错误码结束企业大脑运行，不保存供应商异常正文。"""

        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", error_code):
            raise AssistantValidationError
        return self._finish_run(
            context,
            run_id=run_id,
            text="",
            status="failed",
            error_code=error_code,
        )

    def _finish_run(
        self,
        context: RequestContext,
        *,
        run_id: UUID,
        text: str,
        status: Literal["completed", "failed"],
        error_code: str | None,
    ) -> AssistantRun:
        """在一个事务中收敛消息和 Run，避免部分完成。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            run = _owned_enterprise_run(
                unit_of_work,
                context,
                run_id,
                account_id,
                for_update=True,
            )
            if run.status != "running" or run.assistant_message_id is None:
                raise AssistantConversationBusyError
            message = _finished_message(run, account_id, text, now, status)
            if not unit_of_work.assistant.finish_assistant_message(message):
                raise AssistantConversationBusyError
            finished = replace(
                run,
                status=status,
                updated_at=now,
                completed_at=now,
                error_code=error_code,
            )
            if not unit_of_work.assistant.transition_run(finished, expected_status="running"):
                raise AssistantConversationBusyError
            _record_run_finished(unit_of_work, context, finished, status, now)
            unit_of_work.commit()
            return finished

    def _retrieval_authorized_context(self, context: RequestContext) -> RequestContext:
        """每次操作重新执行文档读取 PDP，失败原因只区分可重试依赖异常。"""

        decision = self._policy.decide(retrieval_policy_request(context))
        if not decision.allowed:
            if decision.reason == "policy_unavailable":
                raise AuthorizationPolicyUnavailableError
            raise AssistantDeniedError
        return _context_with_decision(context, decision)

    def _invalidate_route(
        self,
        context: RequestContext,
        route_sync: SystemServiceRouteSync,
    ) -> None:
        if route_sync.changed and self._current_route_invalidator is not None:
            self._current_route_invalidator.invalidate_current(
                context.workspace_id,
                route_sync.deployment.service.service_id,
            )


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
        conversation_kind="enterprise_brain",
    )
    if conversation is None:
        raise AssistantNotFoundError
    return conversation


def _owned_enterprise_run(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    run_id: UUID,
    account_id: UUID,
    *,
    for_update: bool,
) -> AssistantRun:
    """按空间、账号和知识域关系收敛企业大脑 Run。"""

    run = unit_of_work.assistant.get_run(
        context.workspace_id,
        None,
        run_id,
        account_id,
        for_update=for_update,
    )
    if run is None or run.knowledge_domain_id is None:
        raise AssistantNotFoundError
    return run


def _owned_enterprise_run_by_message(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    conversation_id: UUID,
    message_id: UUID,
    account_id: UUID,
) -> AssistantRun:
    run = unit_of_work.assistant.get_run_by_assistant_message(
        context.workspace_id,
        conversation_id,
        message_id,
        account_id,
    )
    if run is None or run.knowledge_domain_id is None:
        raise AssistantNotFoundError
    return run


def _new_assistant_message(run: AssistantRun, account_id: UUID, now: datetime) -> Message:
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


def _finished_message(
    run: AssistantRun,
    account_id: UUID,
    text: str,
    now: datetime,
    status: Literal["completed", "failed"],
) -> Message:
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


def _normalize_feedback(
    rating: FeedbackRating,
    issue_codes: tuple[FeedbackIssueCode, ...],
    comment: str | None,
) -> tuple[tuple[FeedbackIssueCode, ...], str | None]:
    valid_issues = frozenset({"incorrect", "missing_source", "source_mismatch", "unsafe", "other"})
    issues = tuple(dict.fromkeys(issue_codes))
    normalized_comment = comment.strip() if comment is not None else None
    if normalized_comment == "":
        normalized_comment = None
    if (
        any(issue not in valid_issues for issue in issues)
        or (rating == "helpful" and issues)
        or (rating == "unhelpful" and not issues)
        or len(issues) > 5
        or (normalized_comment is not None and len(normalized_comment) > 1000)
    ):
        raise AssistantValidationError
    return issues, normalized_comment


def _record_run_finished(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    run: AssistantRun,
    outcome: Literal["completed", "failed", "cancelled"],
    occurred_at: datetime,
) -> None:
    """记录低敏企业大脑终态，不传播问题或回答正文。"""

    attributes: dict[str, object] = {
        "conversation_id": str(run.conversation_id),
        "assistant_message_id": str(run.assistant_message_id),
        "knowledge_domain_id": str(run.knowledge_domain_id),
        "knowledge_domain_policy_version": run.knowledge_domain_policy_version,
        "status": run.status,
    }
    if run.error_code is not None:
        attributes["error_code"] = run.error_code
    _record_fact(
        unit_of_work,
        context,
        action=f"enterprise.brain.run.{outcome}",
        resource_type="assistant_run",
        resource_id=run.run_id,
        aggregate_version=2,
        occurred_at=occurred_at,
        outcome="failed" if outcome == "failed" else "succeeded",
        attributes=attributes,
    )


def _record_feedback(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    feedback: MessageFeedback,
    occurred_at: datetime,
) -> None:
    """反馈事件只记录结构化标签，用户自由文本不进入审计或 Outbox。"""

    _record_fact(
        unit_of_work,
        context,
        action="enterprise.brain.feedback.submitted",
        resource_type="message_feedback",
        resource_id=feedback.feedback_id,
        aggregate_version=feedback.version,
        occurred_at=occurred_at,
        outcome="succeeded",
        attributes={
            "conversation_id": str(feedback.conversation_id),
            "message_id": str(feedback.message_id),
            "run_id": str(feedback.run_id),
            "rating": feedback.rating,
            "issue_codes": list(feedback.issue_codes),
            "version": feedback.version,
        },
    )


def _conversation_id_for_message(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    message_id: UUID,
    account_id: UUID,
) -> UUID:
    """在不新增裸消息读取端口的情况下，从当前账号会话集合定位消息归属。"""

    conversations = unit_of_work.assistant.list_conversations(
        context.workspace_id,
        account_id,
        limit=200,
        conversation_kind="enterprise_brain",
    )
    for conversation in conversations:
        messages = unit_of_work.assistant.list_messages(
            context.workspace_id,
            conversation.conversation_id,
            limit=200,
        )
        if any(message.message_id == message_id for message in messages):
            return conversation.conversation_id
    raise AssistantNotFoundError


def _message_text(message: Message) -> str:
    """合并文本 Part 作为报告输入，保持消息顺序和不可变内容。"""

    return "\n".join(part.text for part in message.parts).strip()


def _report_markdown(
    title: str,
    template: Literal["briefing", "risk_review", "comparison"],
    answer: str,
) -> str:
    """以固定模板生成可审计 Markdown，不引入在线编辑或 AI 写回语义。"""

    headings = {
        "briefing": "企业简报",
        "risk_review": "风险审阅",
        "comparison": "差异分析",
    }
    return f"# {title}\n\n## {headings[template]}\n\n{answer}\n"


def _record_report_event(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    report: EnterpriseBrainReport,
    occurred_at: datetime,
) -> None:
    """报告正文不写入审计与 Outbox，仅记录结构化摘要和内容摘要。"""

    _record_fact(
        unit_of_work,
        context,
        action="enterprise.brain.report.created",
        resource_type="enterprise_brain_report",
        resource_id=report.report_id,
        aggregate_version=1,
        occurred_at=occurred_at,
        outcome="succeeded",
        attributes={
            "conversation_id": str(report.conversation_id),
            "message_id": str(report.message_id),
            "run_id": str(report.run_id),
            "template": report.template,
            "content_sha256": report.content_sha256,
            "citation_count": report.citation_count,
        },
    )


def _record_fact(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    *,
    action: str,
    resource_type: str,
    resource_id: UUID,
    aggregate_version: int,
    occurred_at: datetime,
    outcome: Literal["succeeded", "failed"],
    attributes: dict[str, object],
) -> None:
    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
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
            aggregate_id=resource_id,
            aggregate_version=aggregate_version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=attributes,
        )
    )


def _resolved_domain_scope(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    knowledge_domain_id: UUID,
    account_id: UUID,
) -> ResolvedKnowledgeDomainScope:
    """把当前成员、知识域声明和 PDP 知识库范围收敛为非空交集。"""

    scope = unit_of_work.enterprise_knowledge.resolve_domain_scope(
        workspace_id=context.workspace_id,
        domain_id=knowledge_domain_id,
        account_id=account_id,
        authorized_workspace=context.authorized_workspace,
        authorized_department_ids=context.authorized_department_ids,
        authorized_account_ids=context.authorized_account_ids,
        authorized_document_ids=context.authorized_resource_ids,
        maximum_security_level=context.authorized_maximum_security_level,
    )
    if scope is None or scope.empty_reason != "none" or not scope.effective_knowledge_base_ids:
        raise AssistantNotFoundError
    return scope


def _context_with_decision(
    context: RequestContext,
    decision: PolicyDecision,
) -> RequestContext:
    return replace(
        context,
        authorized_permission_code=decision.permission_code,
        authorized_policy_decision_id=decision.decision_id,
        authorized_policy_version=decision.policy_version,
        authorized_workspace=decision.resource_scope.workspace,
        authorized_department_ids=decision.resource_scope.department_ids,
        authorized_account_ids=decision.resource_scope.account_ids,
        authorized_resource_ids=decision.resource_scope.resource_ids,
        authorized_field_mask=decision.field_mask,
        authorized_maximum_security_level=decision.maximum_security_level,
    )


def _record_conversation_event(
    unit_of_work: AssistantUnitOfWork,
    context: RequestContext,
    conversation: Conversation,
    transition: Literal["created", "archived"],
    occurred_at: datetime,
) -> None:
    """记录低敏知识域关系，不传播问题、回答或知识内容。"""

    action = f"enterprise.brain.conversation.{transition}"
    attributes: dict[str, object] = {
        "status": conversation.status,
        "version": conversation.version,
        "knowledge_domain_id": str(conversation.knowledge_domain_id),
        "knowledge_domain_policy_version": conversation.knowledge_domain_policy_version,
    }
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
            attributes=attributes,
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
            payload={"conversation_id": str(conversation.conversation_id), **attributes},
        )
    )
