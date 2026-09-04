"""串联 AssistantRun、RAG、安全模型上下文和可恢复 SSE 事件。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from ai_platform_backend.observability import observed_operation

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.assistant.domain.models import AssistantRun, ConversationAttachment
from ai_platform_api.modules.model_gateway.application.context import (
    AuthorizedModelContextBuilder,
)
from ai_platform_api.modules.model_gateway.application.runtime_gateway import (
    RuntimeModelGatewayService,
)
from ai_platform_api.modules.model_gateway.domain.models import ModelMessage, ModelRequest
from ai_platform_api.modules.model_gateway.domain.runtime import (
    AiRuntimeConfigVersion,
    RuntimeConfigurationReader,
)
from ai_platform_api.modules.model_gateway.domain.runtime_errors import (
    AiRuntimeConfigNotActiveError,
)
from ai_platform_api.modules.retrieval.application.evidence import RetrievalEvidenceService
from ai_platform_api.modules.retrieval.application.planning import (
    BoundedRetrievalPlanningService,
)
from ai_platform_api.modules.retrieval.domain.evidence import EvidenceSetSnapshot
from ai_platform_api.modules.retrieval.domain.models import SecurityLevel
from ai_platform_api.modules.retrieval.domain.planning import RetrievalPlanSnapshot
from ai_platform_api.modules.service_runtime.application import RuntimeReleaseLoader
from ai_platform_api.modules.service_runtime.application.errors import (
    AgentRuntimeReleaseRequiredError,
)
from ai_platform_api.modules.service_runtime.domain.models import RuntimeReleaseSnapshot
from ai_platform_api.modules.streaming.application.service import TransactionalStreamService

_DEGRADATION_MESSAGES = {
    "no_candidates": "当前知识范围内没有找到足够证据。无法可靠回答这个问题。",
    "no_current_evidence": "相关知识当前不可用或已发生版本变化。请稍后重试。",
    "insufficient_sources": "当前可用来源不足以支持可靠结论。请补充更具体的问题。",
    "conflicting_evidence": "当前授权来源之间存在冲突。暂时无法给出确定结论。",
}


@dataclass(frozen=True)
class AssistantRunExecutionResult:
    """返回后台任务是否取得执行权及最终状态，正文仍以消息和事件事实为准。"""

    claimed: bool
    status: str | None
    error_code: str | None = None


class AssistantRunLifecycle(Protocol):
    """抽象普通助手与企业大脑共用的运行生命周期窄接口。"""

    def claim_run(self, context: RequestContext, *, run_id: UUID) -> AssistantRun | None: ...

    def complete_run(
        self,
        context: RequestContext,
        *,
        run_id: UUID,
        text: str,
    ) -> AssistantRun: ...

    def fail_run(
        self,
        context: RequestContext,
        *,
        run_id: UUID,
        error_code: str,
    ) -> AssistantRun: ...

    def get_run_attachments(
        self,
        context: RequestContext,
        *,
        run_id: UUID,
    ) -> tuple[ConversationAttachment, ...]: ...


class AssistantRunExecutor:
    """认领一次排队 Run，并把 RAG 问答结果写入消息与可恢复事件。"""

    def __init__(
        self,
        conversations: AssistantRunLifecycle,
        retrieval_planning: BoundedRetrievalPlanningService,
        retrieval_evidence: RetrievalEvidenceService,
        runtime_releases: RuntimeReleaseLoader,
        runtime_configurations: RuntimeConfigurationReader,
        model_runtime: RuntimeModelGatewayService,
        model_context: AuthorizedModelContextBuilder,
        streams: TransactionalStreamService,
        *,
        delta_batch_characters: int,
    ) -> None:
        self._conversations = conversations
        self._retrieval_planning = retrieval_planning
        self._retrieval_evidence = retrieval_evidence
        self._runtime_releases = runtime_releases
        self._runtime_configurations = runtime_configurations
        self._model_runtime = model_runtime
        self._model_context = model_context
        self._streams = streams
        self._delta_batch_characters = delta_batch_characters

    @observed_operation(component="assistant", operation="execute")
    def execute(
        self,
        context: RequestContext,
        run_id: UUID,
    ) -> AssistantRunExecutionResult:
        """执行一次非持久调度任务；数据库状态条件保证重连和重复调度不会重新生成。"""

        run = self._conversations.claim_run(context, run_id=run_id)
        if run is None:
            return AssistantRunExecutionResult(False, None)
        if run.assistant_message_id is None:
            return self._fail_without_stream(context, run_id, "INTERNAL_ERROR")

        stream_started = False
        try:
            # 1. 先复核 Run 冻结的 Route 与 Release；任何草稿旁路或改绑都在检索前失败关闭。
            release = self._runtime_release(run)
            self._streams.start_run(
                run.workspace_id,
                run.conversation_id,
                run.assistant_message_id,
                run.run_id,
                now=datetime.now(UTC),
            )
            stream_started = True
            self._append_status(run.run_id, run.trace_id, run.traceparent, "retrieval", "running")
            retrieval_context = _retrieval_execution_context(context, run)
            plan = self._retrieval_planning.retrieve(retrieval_context, run.run_id)
            evidence = self._retrieval_evidence.prepare(retrieval_context, run.run_id)
            attachments = self._conversations.get_run_attachments(context, run_id=run.run_id)
            self._append_status(
                run.run_id,
                run.trace_id,
                run.traceparent,
                "retrieval",
                "completed",
                candidate_count=len(plan.candidates),
                evidence_count=len(evidence.items),
                attachment_count=len(attachments),
            )

            # 2. 证据不足时使用固定降级文案；证据充分时只调用 Release 冻结的模型配置。
            text = self._answer(run, release, plan, evidence, attachments)
            self._append_answer_events(run.run_id, run.trace_id, run.traceparent, text, evidence)

            # 3. 先固化消息正文，再关闭流恢复事实；正常路径不会把模型正文写入审计或日志。
            completed = self._conversations.complete_run(context, run_id=run.run_id, text=text)
            self._streams.finish(
                run.run_id,
                "completed",
                _final_payload(run.assistant_message_id, text, evidence),
                now=datetime.now(UTC),
            )
            return AssistantRunExecutionResult(True, completed.status)
        except Exception as error:
            error_code = _stable_error_code(error)
            return self._fail(
                context,
                run_id,
                run.trace_id,
                run.traceparent,
                error_code,
                stream_started,
            )

    def _answer(
        self,
        run: AssistantRun,
        release: RuntimeReleaseSnapshot,
        plan: RetrievalPlanSnapshot,
        evidence: EvidenceSetSnapshot,
        attachments: tuple[ConversationAttachment, ...],
    ) -> str:
        if evidence.status == "uncertain" and not attachments:
            reason = evidence.degradation_reason or "no_current_evidence"
            return _DEGRADATION_MESSAGES[reason]
        runtime_config_version_id = release.runtime_config_version_id
        configuration = self._runtime_configurations.get(runtime_config_version_id)
        if configuration is None:
            raise AiRuntimeConfigNotActiveError
        request = ModelRequest(
            invocation_id=uuid5(NAMESPACE_URL, f"assistant-run:{run.run_id}"),
            workspace_id=run.workspace_id,
            trace_id=run.trace_id,
            traceparent=run.traceparent,
            task_type="assistant.answer",
            messages=_model_messages(
                configuration,
                plan,
                evidence,
                attachments,
                self._model_context,
            ),
            required_capabilities=frozenset({"generation"}),
            max_output_tokens=configuration.policy.max_output_tokens,
            external_data_allowed=True,
            security_level=_maximum_context_security_level(evidence, attachments),
        )
        result = self._model_runtime.invoke(
            request,
            runtime_config_version_id=runtime_config_version_id,
        )
        return result.content.strip()

    def _runtime_release(self, run: AssistantRun) -> RuntimeReleaseSnapshot:
        """装载新 Run 的完整发布绑定；升级前无 Route 证据的遗留 Run 不允许重新执行。"""

        if (
            run.service_id is None
            or run.service_route_id is None
            or run.service_route_version is None
        ):
            raise AgentRuntimeReleaseRequiredError
        snapshot = self._runtime_releases.resolve_bound(
            run.workspace_id,
            run.service_id,
            run.service_route_id,
            run.service_route_version,
            run.agent_release_id,
        )
        if snapshot.runtime_config_version_id != run.runtime_config_version_id:
            raise AgentRuntimeReleaseRequiredError
        return snapshot

    def _append_status(
        self,
        run_id: UUID,
        trace_id: str,
        traceparent: str,
        stage: str,
        status: str,
        **metrics: object,
    ) -> None:
        payload: dict[str, object] = {"stage": stage, "status": status, **metrics}
        self._streams.append(
            run_id,
            "tool.status",
            trace_id,
            traceparent,
            payload,
            now=datetime.now(UTC),
        )

    def _append_answer_events(
        self,
        run_id: UUID,
        trace_id: str,
        traceparent: str,
        text: str,
        evidence: EvidenceSetSnapshot,
    ) -> None:
        # 同步模型网关只能在完整结果返回后切分；有限字符批次避免按 Token 高频写库。
        for offset in range(0, len(text), self._delta_batch_characters):
            delta = text[offset : offset + self._delta_batch_characters]
            self._streams.append(
                run_id,
                "message.delta",
                trace_id,
                traceparent,
                {"delta": delta, "offset": offset},
                now=datetime.now(UTC),
            )
        if evidence.items:
            self._streams.append(
                run_id,
                "message.citation",
                trace_id,
                traceparent,
                {"citations": _citations(evidence)},
                now=datetime.now(UTC),
            )
        self._streams.append(
            run_id,
            "message.completed",
            trace_id,
            traceparent,
            {"text": text, "citations": _citations(evidence)},
            now=datetime.now(UTC),
        )

    def _fail(
        self,
        context: RequestContext,
        run_id: UUID,
        trace_id: str,
        traceparent: str,
        error_code: str,
        stream_started: bool,
    ) -> AssistantRunExecutionResult:
        # 失败清理不回显异常详情；助手 Run 优先落终态，流事件失败不能阻止消息收敛。
        try:
            failed = self._conversations.fail_run(context, run_id=run_id, error_code=error_code)
        except PlatformError:
            failed = None
        if stream_started:
            try:
                self._streams.append(
                    run_id,
                    "message.failed",
                    trace_id,
                    traceparent,
                    {"error_code": error_code},
                    now=datetime.now(UTC),
                )
                self._streams.finish(
                    run_id,
                    "failed",
                    {"status": "failed", "error_code": error_code},
                    now=datetime.now(UTC),
                )
            except PlatformError:
                pass
        return AssistantRunExecutionResult(
            True,
            failed.status if failed is not None else "failed",
            error_code,
        )

    def _fail_without_stream(
        self,
        context: RequestContext,
        run_id: UUID,
        error_code: str,
    ) -> AssistantRunExecutionResult:
        failed = self._conversations.fail_run(context, run_id=run_id, error_code=error_code)
        return AssistantRunExecutionResult(True, failed.status, error_code)


def _maximum_evidence_security_level(evidence: EvidenceSetSnapshot) -> SecurityLevel:
    """按实际进入模型上下文的证据计算密级，不能用调用者授权上限替代。"""

    ranks: dict[SecurityLevel, int] = {
        "PUBLIC": 0,
        "INTERNAL": 1,
        "CONFIDENTIAL": 2,
        "RESTRICTED": 3,
    }
    if not evidence.items:
        # sufficient 证据集按领域规则必须包含证据；异常空集不能被降级成 PUBLIC 外发。
        return "RESTRICTED"
    return max((item.security_level for item in evidence.items), key=ranks.__getitem__)


def _maximum_context_security_level(
    evidence: EvidenceSetSnapshot,
    attachments: tuple[ConversationAttachment, ...],
) -> SecurityLevel:
    """临时附件按 INTERNAL 处理，并与永久知识证据取更高密级。"""

    if not attachments:
        return _maximum_evidence_security_level(evidence)
    if not evidence.items:
        return "INTERNAL"
    ranks: dict[SecurityLevel, int] = {
        "PUBLIC": 0,
        "INTERNAL": 1,
        "CONFIDENTIAL": 2,
        "RESTRICTED": 3,
    }
    evidence_level = _maximum_evidence_security_level(evidence)
    return max((evidence_level, "INTERNAL"), key=ranks.__getitem__)


def _model_messages(
    configuration: AiRuntimeConfigVersion,
    plan: RetrievalPlanSnapshot,
    evidence: EvidenceSetSnapshot,
    attachments: tuple[ConversationAttachment, ...],
    builder: AuthorizedModelContextBuilder,
) -> tuple[ModelMessage, ...]:
    system = ModelMessage("system", configuration.system_prompt_template)
    query = builder.build_user_message(plan.variants[0].text)
    messages: list[ModelMessage] = [system]
    used_characters = system.content.__len__() + query.content.__len__()
    # 临时附件优先进入上下文，确保用户显式选择的本轮材料不会被永久知识候选挤出预算。
    for attachment in attachments:
        candidate = builder.build_untrusted_evidence_message(
            resource_type="conversation_attachment",
            payload={"file_name": attachment.file_name, "content": attachment.content},
            field_mask=frozenset(),
        )
        if used_characters + len(candidate.content) > configuration.policy.max_prompt_characters:
            continue
        messages.append(candidate)
        used_characters += len(candidate.content)
    for item in evidence.items:
        candidate = builder.build_untrusted_evidence_message(
            resource_type="chunk",
            payload={
                "content": item.context_text,
                "source_position": item.source_position,
            },
            field_mask=plan.field_mask,
        )
        if used_characters + len(candidate.content) > configuration.policy.max_prompt_characters:
            break
        messages.append(candidate)
        used_characters += len(candidate.content)
    messages.append(query)
    if len(messages) < 3:
        raise ValueError("授权证据未能进入模型上下文")
    return tuple(messages)


def _citations(evidence: EvidenceSetSnapshot) -> list[dict[str, object]]:
    return [
        {
            "rank": item.rank,
            "document_id": str(item.document_id),
            "document_version_id": str(item.document_version_id),
            "chunk_id": str(item.chunk_id),
            "content_hash": item.content_hash,
            "quote": item.quote,
            "source_position": item.source_position,
            "document_title": item.document_title,
        }
        for item in evidence.items
    ]


def _final_payload(
    message_id: UUID,
    text: str,
    evidence: EvidenceSetSnapshot,
) -> dict[str, object]:
    return {
        "status": "completed",
        "message_id": str(message_id),
        "text": text,
        "citations": _citations(evidence),
    }


def _stable_error_code(error: Exception) -> str:
    return error.error_code if isinstance(error, PlatformError) else "INTERNAL_ERROR"


def _retrieval_execution_context(
    request_context: RequestContext,
    run: AssistantRun,
) -> RequestContext:
    """在已认领服务 Run 内恢复账号数据权限，避免 API Key Scope 被误当作文档权限。"""

    if request_context.authentication_method != "open_api_key":
        return request_context
    if (
        request_context.user_id is None
        or run.requested_by_actor_id != request_context.actor_id
        or run.requested_by_account_id != request_context.user_id
        or run.service_id is None
    ):
        raise AgentRuntimeReleaseRequiredError
    # 服务授权已由入口和冻结 Route 证明；内部检索仍重新执行账号 RBAC/ABAC，且不携带
    # API Key 的服务级 Scope，防止它错误拒绝知识权限或被误用于扩大账号权限。
    return replace(
        request_context,
        actor_id=request_context.user_id,
        authentication_method="browser_session",
        credential_scopes=None,
        authorized_permission_code=None,
        authorized_policy_decision_id=None,
        authorized_policy_version=None,
        authorized_workspace=False,
        authorized_department_ids=frozenset(),
        authorized_account_ids=frozenset(),
        authorized_resource_ids=frozenset(),
        authorized_field_mask=frozenset(),
        authorized_maximum_security_level="PUBLIC",
    )
