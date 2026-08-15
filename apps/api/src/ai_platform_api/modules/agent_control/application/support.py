"""集中 Agent 控制面用例共享的校验、重放和脱敏事实写入规则。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import NoReturn
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_control.application.errors import (
    AgentDeniedError,
    AgentIdempotencyConflictError,
    AgentLifecycleConflictError,
    AgentNotFoundError,
    AgentValidationError,
)
from ai_platform_api.modules.agent_control.domain.models import (
    Agent,
    AgentControlRequest,
    AgentControlResultType,
    AgentControlUnitOfWork,
    AgentDraft,
    AgentDraftRevision,
    AgentRelease,
    AgentReleaseCandidate,
    AgentWriteConflictError,
)
from ai_platform_api.modules.integration.domain.events import IntegrationEvent

IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
MAX_CONFIGURATION_BYTES = 256 * 1024

CREATE_OPERATION = "agent.create"
UPDATE_DRAFT_OPERATION = "agent.draft.update"
REQUEST_RELEASE_OPERATION = "agent.release.request"
PUBLISH_RELEASE_OPERATION = "agent.release.publish"
ARCHIVE_OPERATION = "agent.archive"


def configuration_digest(configuration: dict[str, object]) -> str:
    """计算有限大小规范 JSON 的 SHA-256，作为 revision 和候选的稳定配置身份。"""

    payload = canonical_json(configuration)
    if len(payload) > MAX_CONFIGURATION_BYTES:
        raise AgentValidationError
    return hashlib.sha256(payload).hexdigest()


def canonical_json(document: object) -> bytes:
    """将受支持文档编码为稳定 JSON；不可序列化值和 NaN 一律失败关闭。"""

    try:
        return json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError) as error:
        raise AgentValidationError from error


def request_digest(document: dict[str, object]) -> str:
    """计算幂等请求或候选身份的稳定摘要。"""

    return hashlib.sha256(canonical_json(document)).hexdigest()


def normalize_name(value: str) -> str:
    """规范 Agent 名称并执行数据库列一致的长度边界。"""

    normalized = value.strip()
    if not 1 <= len(normalized) <= 120:
        raise AgentValidationError
    return normalized


def normalize_description(value: str | None) -> str | None:
    """将空描述统一为缺失值，并限制可持久化长度。"""

    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 1000:
        raise AgentValidationError
    return normalized


def require_idempotency_key(value: str) -> None:
    """拒绝不满足稳定字符集与长度约束的幂等键。"""

    if IDEMPOTENCY_PATTERN.fullmatch(value) is None:
        raise AgentValidationError


def browser_account(context: RequestContext) -> UUID:
    """只接受服务端认证链构建的浏览器人类主体。"""

    if (
        context.user_id is None
        or context.user_id != context.actor_id
        or context.authentication_method != "browser_session"
    ):
        raise AgentDeniedError
    return context.user_id


def require_resource_scope(context: RequestContext, agent_id: UUID) -> None:
    """要求工作空间全域授权或目标 Agent 的显式资源授权。"""

    if not context.authorized_workspace and agent_id not in context.authorized_resource_ids:
        raise AgentDeniedError


def require_custom_agent(
    unit_of_work: AgentControlUnitOfWork,
    workspace_id: UUID,
    agent_id: UUID,
    *,
    for_update: bool = False,
) -> Agent:
    """按空间读取自定义 Agent，并以不存在语义隐藏系统与跨空间事实。"""

    agent = unit_of_work.agents.get_agent(
        workspace_id,
        agent_id,
        for_update=for_update,
    )
    # 系统助手由 assistant 模块独占写入；控制面用不存在语义避免暴露跨边界资源。
    if agent is None or agent.agent_kind != "custom":
        raise AgentNotFoundError
    return agent


def require_draft(
    unit_of_work: AgentControlUnitOfWork,
    workspace_id: UUID,
    agent_id: UUID,
    *,
    for_update: bool = False,
) -> AgentDraft:
    """按空间和 Agent 身份读取唯一当前草稿。"""

    draft = unit_of_work.agents.get_draft(
        workspace_id,
        agent_id,
        for_update=for_update,
    )
    if draft is None:
        raise AgentNotFoundError
    return draft


def revision_from_draft(draft: AgentDraft) -> AgentDraftRevision:
    """复制当前草稿为不可变修订，避免历史依赖后续当前态。"""

    return AgentDraftRevision(
        draft_id=draft.draft_id,
        agent_id=draft.agent_id,
        workspace_id=draft.workspace_id,
        revision=draft.revision,
        status=draft.status,
        configuration=draft.configuration,
        config_hash=draft.config_hash,
        updated_by_account_id=draft.updated_by_account_id,
        updated_at=draft.updated_at,
    )


def draft_from_revision(revision: AgentDraftRevision) -> AgentDraft:
    """从不可变修订恢复幂等响应，不返回可能继续变化的当前草稿。"""

    return AgentDraft(
        draft_id=revision.draft_id,
        agent_id=revision.agent_id,
        workspace_id=revision.workspace_id,
        revision=revision.revision,
        status=revision.status,
        configuration=revision.configuration,
        config_hash=revision.config_hash,
        updated_by_account_id=revision.updated_by_account_id,
        updated_at=revision.updated_at,
    )


def control_request(
    context: RequestContext,
    operation: str,
    idempotency_key: str,
    request_hash: str,
    result_type: AgentControlResultType,
    result_id: UUID,
    result_revision: int | None,
    created_at: datetime,
) -> AgentControlRequest:
    """创建与业务提交同事务保存的不可变幂等请求事实。"""

    return AgentControlRequest(
        request_id=uuid4(),
        workspace_id=context.workspace_id,
        actor_id=context.actor_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result_type=result_type,
        result_id=result_id,
        result_revision=result_revision,
        created_at=created_at,
    )


def require_request_hash(request: AgentControlRequest, expected_hash: str) -> None:
    """拒绝用相同幂等键提交不同业务参数。"""

    if request.request_hash != expected_hash:
        raise AgentIdempotencyConflictError


def replay_created_agent(
    unit_of_work: AgentControlUnitOfWork,
    workspace_id: UUID,
    request: AgentControlRequest,
) -> tuple[Agent, AgentDraft]:
    """重放创建请求绑定的 Agent 和首个不可变草稿修订。"""

    agent = require_custom_agent(unit_of_work, workspace_id, request.result_id)
    current = require_draft(unit_of_work, workspace_id, agent.agent_id)
    revision_number = request.result_revision
    if revision_number is None:
        raise AgentLifecycleConflictError
    revision = unit_of_work.agents.get_draft_revision(
        workspace_id,
        current.draft_id,
        revision_number,
    )
    if revision is None:
        raise AgentLifecycleConflictError
    return agent, draft_from_revision(revision)


def replay_draft(
    unit_of_work: AgentControlUnitOfWork,
    workspace_id: UUID,
    request: AgentControlRequest,
) -> AgentDraft:
    """重放更新请求绑定的不可变草稿修订。"""

    revision_number = request.result_revision
    if revision_number is None:
        raise AgentLifecycleConflictError
    revision = unit_of_work.agents.get_draft_revision(
        workspace_id,
        request.result_id,
        revision_number,
    )
    if revision is None:
        raise AgentLifecycleConflictError
    return draft_from_revision(revision)


def replay_candidate(
    unit_of_work: AgentControlUnitOfWork,
    workspace_id: UUID,
    request: AgentControlRequest,
) -> AgentReleaseCandidate:
    """重放候选请求绑定的不可变来源事实。"""

    candidate = unit_of_work.agents.get_candidate(workspace_id, request.result_id)
    if candidate is None:
        raise AgentLifecycleConflictError
    return candidate


def replay_release(
    unit_of_work: AgentControlUnitOfWork,
    workspace_id: UUID,
    request: AgentControlRequest,
) -> AgentRelease:
    """重放发布请求绑定的不可变 Release，并拒绝损坏的结果类型。"""

    if request.result_type != "release":
        raise AgentLifecycleConflictError
    release = unit_of_work.agents.get_release(workspace_id, request.result_id)
    if release is None or release.release_kind != "custom":
        raise AgentLifecycleConflictError
    return release


def replay_archived_agent(
    unit_of_work: AgentControlUnitOfWork,
    workspace_id: UUID,
    request: AgentControlRequest,
) -> Agent:
    """重放归档请求绑定的 Agent 终态。"""

    agent = require_custom_agent(unit_of_work, workspace_id, request.result_id)
    if agent.status != "archived":
        raise AgentLifecycleConflictError
    return agent


def raise_write_conflict(error: AgentWriteConflictError) -> NoReturn:
    """把数据库竞争映射为稳定应用错误，隐藏约束与 SQL 细节。"""

    if error.reason == "idempotency":
        raise AgentIdempotencyConflictError from error
    raise AgentLifecycleConflictError from error


def record_change(
    unit_of_work: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    action: str,
    event_type: str,
    resource_id: UUID,
    aggregate_version: int,
    occurred_at: datetime,
    attributes: dict[str, object],
) -> None:
    """同步写入脱敏审计与 Outbox；配置正文和 Prompt 不得进入运营表面。"""

    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type="agent_definition",
            resource_id=resource_id,
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
            event_type=event_type,
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
