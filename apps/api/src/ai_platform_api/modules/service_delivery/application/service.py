"""编排三类已发布服务出口的授权、限流、配额、路由和 Run 创建。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.assistant.application.errors import (
    AssistantIdempotencyConflictError,
    AssistantNotFoundError,
)
from ai_platform_api.modules.assistant.application.service import (
    new_submission,
    normalize_texts,
    record_run_queued,
    require_idempotency_key,
)
from ai_platform_api.modules.assistant.domain.models import (
    AssistantRun,
    AssistantUnitOfWork,
    AssistantWriteConflictError,
    Conversation,
    Message,
    MessageSubmission,
)
from ai_platform_api.modules.identity.application.entitlement_errors import (
    EntitlementConflictError,
)
from ai_platform_api.modules.identity.application.usage import consume_usage
from ai_platform_api.modules.identity.domain.entitlements import EntitlementWriteConflictError
from ai_platform_api.modules.service_delivery.application.errors import (
    ServiceInvocationDeniedError,
)
from ai_platform_api.modules.service_delivery.domain.models import InvocationRateLimiter
from ai_platform_api.modules.service_governance.application.support import require_deployment
from ai_platform_api.modules.service_governance.domain.models import ServiceDeployment
from ai_platform_api.modules.service_runtime.application.errors import (
    RuntimeServiceRouteUnavailableError,
)
from ai_platform_api.modules.service_runtime.application.loader import RuntimeReleaseLoader
from ai_platform_api.modules.service_runtime.domain.models import RuntimeReleaseSnapshot


@dataclass(frozen=True)
class ServiceInvocationMessagePartView:
    """向协议层投影消息 Part，避免 Router 依赖 Assistant 领域对象。"""

    part_id: UUID
    sequence_no: int
    part_type: Literal["text"]
    text: str


@dataclass(frozen=True)
class ServiceInvocationMessageView:
    """表示服务调用响应所需的最小消息字段集合。"""

    message_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    role: Literal["system", "user", "assistant", "tool"]
    status: Literal["streaming", "completed", "failed"]
    parts: tuple[ServiceInvocationMessagePartView, ...]
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class ServiceInvocationRunView:
    """表示服务调用响应公开的 Run 追溯字段，不暴露幂等键或请求摘要。"""

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
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    trace_id: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    error_code: str | None


@dataclass(frozen=True)
class ServiceInvocationView:
    """隔离统一服务出口与 Assistant 内部聚合的只读应用投影。"""

    input_message: ServiceInvocationMessageView
    output_message: ServiceInvocationMessageView | None
    run: ServiceInvocationRunView


class ServiceInvocationService:
    """让自定义 Agent、场景应用和 Open API 共享同一安全调用链。"""

    def __init__(
        self,
        unit_of_work: AssistantUnitOfWork,
        runtime_releases: RuntimeReleaseLoader,
        rate_limiter: InvocationRateLimiter,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._runtime_releases = runtime_releases
        self._rate_limiter = rate_limiter

    def invoke(
        self,
        context: RequestContext,
        *,
        service_id: UUID,
        texts: tuple[str, ...],
        idempotency_key: str,
    ) -> MessageSubmission:
        """在新 Run 产生前完成全部入口门禁，并原子保存配额与冻结路由。"""

        # 1. 原始正文和分配键只停留在本次请求内存；限流键仅使用不可逆摘要。
        account_id = _principal_account(context)
        _require_service_permission(context, service_id)
        normalized_texts = normalize_texts(texts)
        require_idempotency_key(idempotency_key)
        request_hash = _request_hash(service_id, normalized_texts)
        self._rate_limiter.consume(
            context.workspace_id,
            service_id,
            context.actor_id,
            idempotency_key,
        )
        snapshot = self._runtime_releases.resolve_current(
            context.workspace_id,
            service_id,
            context.actor_id.hex,
        )

        # 2. 在同一事务复核当前服务、访问策略和 Route，防止授权到写入之间发生漂移。
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                deployment = require_deployment(
                    unit_of_work.services,
                    context.workspace_id,
                    service_id,
                    for_update=True,
                )
                _require_surface(context, deployment.service.service_type)
                if not unit_of_work.services.account_can_invoke(
                    context.workspace_id,
                    service_id,
                    account_id,
                ):
                    raise ServiceInvocationDeniedError
                _require_snapshot_matches_current(snapshot, deployment)

                existing = unit_of_work.assistant.get_submission(
                    context.workspace_id,
                    context.actor_id,
                    idempotency_key,
                )
                if existing is not None:
                    if (
                        existing.run.request_hash != request_hash
                        or existing.run.service_id != service_id
                    ):
                        raise AssistantIdempotencyConflictError
                    return existing

                # 3. 月度问答配额、隐藏会话、消息、Run、审计和 Outbox 一次提交。
                usage = consume_usage(
                    unit_of_work.usage,
                    context=context,
                    workspace_id=context.workspace_id,
                    metric="questions_monthly",
                    delta_value=1,
                    idempotency_key=_usage_idempotency_key(context.actor_id, idempotency_key),
                    occurred_at=now,
                )
                conversation = Conversation(
                    conversation_id=uuid4(),
                    workspace_id=context.workspace_id,
                    created_by_account_id=account_id,
                    conversation_kind="service_invocation",
                    title=None,
                    status="active",
                    created_at=now,
                    updated_at=now,
                    version=1,
                )
                submission = new_submission(
                    context=context,
                    account_id=account_id,
                    actor_id=context.actor_id,
                    conversation_id=conversation.conversation_id,
                    texts=normalized_texts,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    agent_release_id=snapshot.agent_release_id,
                    runtime_config_version_id=snapshot.runtime_config_version_id,
                    service_id=service_id,
                    service_route_id=snapshot.route_id,
                    service_route_version=snapshot.service_route_version,
                    now=now,
                )
                unit_of_work.assistant.add_conversation(conversation)
                unit_of_work.assistant.add_submission(submission)
                if usage.created:
                    if usage.audit is None or usage.event is None:
                        raise RuntimeError("服务调用用量事实不完整")
                    unit_of_work.audit.add(usage.audit)
                    unit_of_work.outbox.add(usage.event)
                record_run_queued(unit_of_work, context, submission, now)
                unit_of_work.commit()
                return submission
        except AssistantWriteConflictError as error:
            raise AssistantIdempotencyConflictError from error
        except EntitlementWriteConflictError as error:
            raise EntitlementConflictError from error

    def get_invocation(
        self,
        context: RequestContext,
        *,
        service_id: UUID,
        run_id: UUID,
    ) -> MessageSubmission:
        """按当前 Actor 和最新服务访问策略读取调用终态或 SSE 恢复身份。"""

        account_id = _principal_account(context)
        _require_service_permission(context, service_id)
        with self._unit_of_work as unit_of_work:
            deployment = require_deployment(
                unit_of_work.services,
                context.workspace_id,
                service_id,
            )
            _require_surface(context, deployment.service.service_type)
            if not unit_of_work.services.account_can_invoke(
                context.workspace_id,
                service_id,
                account_id,
            ):
                raise ServiceInvocationDeniedError
            submission = unit_of_work.assistant.get_submission_by_run(
                context.workspace_id,
                context.actor_id,
                run_id,
            )
            if submission is None or submission.run.service_id != service_id:
                raise AssistantNotFoundError
            return submission

    def get_run(
        self,
        context: RequestContext,
        *,
        service_id: UUID,
        run_id: UUID,
    ) -> AssistantRun:
        """返回经过相同访问复核的 Run，供 SSE 等待期间重复确认终态。"""

        return self.get_invocation(context, service_id=service_id, run_id=run_id).run


def invocation_view(submission: MessageSubmission) -> ServiceInvocationView:
    """把内部消息聚合收敛为协议层可消费且不含敏感控制字段的投影。"""

    run = submission.run
    return ServiceInvocationView(
        input_message=_message_view(submission.message),
        output_message=(
            _message_view(submission.assistant_message)
            if submission.assistant_message is not None
            else None
        ),
        run=ServiceInvocationRunView(
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
            status=run.status,
            trace_id=run.trace_id,
            created_at=run.created_at,
            updated_at=run.updated_at,
            completed_at=run.completed_at,
            error_code=run.error_code,
        ),
    )


def _message_view(message: Message) -> ServiceInvocationMessageView:
    return ServiceInvocationMessageView(
        message_id=message.message_id,
        workspace_id=message.workspace_id,
        conversation_id=message.conversation_id,
        role=message.role,
        status=message.status,
        parts=tuple(
            ServiceInvocationMessagePartView(
                part_id=part.part_id,
                sequence_no=part.sequence_no,
                part_type=part.part_type,
                text=part.text,
            )
            for part in message.parts
        ),
        created_by_account_id=message.created_by_account_id,
        created_at=message.created_at,
        updated_at=message.updated_at,
        version=message.version,
    )


def _principal_account(context: RequestContext) -> UUID:
    if context.user_id is None or context.authentication_method not in {
        "browser_session",
        "open_api_key",
    }:
        raise ServiceInvocationDeniedError
    if context.authentication_method == "browser_session" and context.actor_id != context.user_id:
        raise ServiceInvocationDeniedError
    return context.user_id


def _require_service_permission(context: RequestContext, service_id: UUID) -> None:
    """应用层再次确认注册接口授权，不能只依赖 Router 正确装配。"""

    if (
        context.authorized_permission_code != "service.definition.read"
        or not context.credential_allows("service.definition.read", service_id)
        or (not context.authorized_workspace and service_id not in context.authorized_resource_ids)
    ):
        raise ServiceInvocationDeniedError


def _require_surface(context: RequestContext, service_type: str) -> None:
    """浏览器承载 Agent/场景页面，Open API Key 只能进入专用服务出口。"""

    allowed = (
        service_type in {"custom_knowledge_agent", "scenario_application"}
        if context.authentication_method == "browser_session"
        else service_type == "open_api"
    )
    if not allowed:
        raise ServiceInvocationDeniedError


def _require_snapshot_matches_current(
    snapshot: RuntimeReleaseSnapshot,
    deployment: ServiceDeployment,
) -> None:
    """确认 Runtime 选择仍是锁定事务内的当前 Route，发布竞争时让调用方安全重试。"""

    route = deployment.route
    service = deployment.service
    if (
        snapshot.service_id != service.service_id
        or snapshot.route_id != route.route_id
        or snapshot.service_route_version != route.route_version
        or snapshot.agent_release_id not in {route.primary_release_id, route.canary_release_id}
    ):
        raise RuntimeServiceRouteUnavailableError


def _request_hash(service_id: UUID, texts: tuple[str, ...]) -> str:
    document = json.dumps(
        {"service_id": str(service_id), "parts": list(texts)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def _usage_idempotency_key(actor_id: UUID, idempotency_key: str) -> str:
    digest = hashlib.sha256(actor_id.bytes + b"\x00" + idempotency_key.encode("utf-8")).hexdigest()
    return f"svcq:{digest}"
