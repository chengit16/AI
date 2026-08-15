"""实现 Agent 草稿乐观锁更新、不可变修订查询和幂等恢复。"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_control.application.errors import (
    AgentLifecycleConflictError,
    AgentValidationError,
)
from ai_platform_api.modules.agent_control.application.support import (
    UPDATE_DRAFT_OPERATION,
    browser_account,
    configuration_digest,
    control_request,
    raise_write_conflict,
    record_change,
    replay_draft,
    request_digest,
    require_custom_agent,
    require_draft,
    require_idempotency_key,
    require_request_hash,
    require_resource_scope,
    revision_from_draft,
)
from ai_platform_api.modules.agent_control.domain.models import (
    AgentControlUnitOfWork,
    AgentDraft,
    AgentDraftRevision,
    AgentWriteConflictError,
)


def update_draft(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    agent_id: UUID,
    expected_revision: int,
    configuration: dict[str, object],
    idempotency_key: str,
) -> AgentDraft:
    """用乐观锁写入新草稿 revision，并保留旧修订的完整可追溯事实。"""

    # 1. 先冻结请求摘要；expected_revision 进入摘要，防止幂等重放绕过并发语义。
    account_id = browser_account(context)
    require_resource_scope(context, agent_id)
    if expected_revision < 1:
        raise AgentValidationError
    config_hash = configuration_digest(configuration)
    require_idempotency_key(idempotency_key)
    request_hash = request_digest(
        {
            "operation": UPDATE_DRAFT_OPERATION,
            "agent_id": str(agent_id),
            "expected_revision": expected_revision,
            "config_hash": config_hash,
        }
    )
    now = datetime.now(UTC)
    try:
        with unit_of_work_factory as unit_of_work:
            # 2. 幂等命中读取不可变 revision，而不是返回可能已继续变化的当前草稿。
            request = unit_of_work.agents.get_request(
                context.workspace_id,
                context.actor_id,
                UPDATE_DRAFT_OPERATION,
                idempotency_key,
            )
            if request is not None:
                require_request_hash(request, request_hash)
                return replay_draft(unit_of_work, context.workspace_id, request)

            agent = require_custom_agent(
                unit_of_work,
                context.workspace_id,
                agent_id,
                for_update=True,
            )
            current = require_draft(
                unit_of_work,
                context.workspace_id,
                agent_id,
                for_update=True,
            )
            if agent.status != "active" or current.revision != expected_revision:
                raise AgentLifecycleConflictError
            updated = replace(
                current,
                revision=current.revision + 1,
                status="editing",
                configuration=configuration,
                config_hash=config_hash,
                updated_by_account_id=account_id,
                updated_at=now,
            )
            if not unit_of_work.agents.save_draft(
                updated,
                expected_revision=current.revision,
            ):
                raise AgentLifecycleConflictError
            unit_of_work.agents.add_draft_revision(revision_from_draft(updated))
            unit_of_work.agents.add_request(
                control_request(
                    context,
                    UPDATE_DRAFT_OPERATION,
                    idempotency_key,
                    request_hash,
                    "draft",
                    updated.draft_id,
                    updated.revision,
                    now,
                )
            )
            # 3. 当前草稿和不可变历史、幂等、审计及 Outbox 一次提交，失败不留半个 revision。
            record_change(
                unit_of_work,
                context,
                action="agent.draft.updated",
                event_type="agent.draft.changed",
                resource_id=agent_id,
                aggregate_version=updated.revision,
                occurred_at=now,
                attributes={
                    "draft_id": str(updated.draft_id),
                    "revision": updated.revision,
                    "config_hash": updated.config_hash,
                    "status": updated.status,
                },
            )
            unit_of_work.commit()
            return updated
    except AgentLifecycleConflictError:
        replayed = _recover_draft(
            unit_of_work_factory,
            context,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replayed is not None:
            return replayed
        raise
    except AgentWriteConflictError as error:
        replayed = _recover_draft(
            unit_of_work_factory,
            context,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replayed is not None:
            return replayed
        raise_write_conflict(error)


def list_draft_revisions(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    agent_id: UUID,
    limit: int,
) -> tuple[AgentDraftRevision, ...]:
    """按 revision 倒序返回 Agent 草稿历史，不跨工作空间或资源范围。"""

    browser_account(context)
    require_resource_scope(context, agent_id)
    if not 1 <= limit <= 200:
        raise AgentValidationError
    with unit_of_work_factory as unit_of_work:
        require_custom_agent(unit_of_work, context.workspace_id, agent_id)
        return unit_of_work.agents.list_draft_revisions(
            context.workspace_id,
            agent_id,
            limit=limit,
        )


def _recover_draft(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    idempotency_key: str,
    request_hash: str,
) -> AgentDraft | None:
    """在 revision 竞争后优先恢复同一幂等请求产生的不可变修订。"""

    with unit_of_work_factory as unit_of_work:
        request = unit_of_work.agents.get_request(
            context.workspace_id,
            context.actor_id,
            UPDATE_DRAFT_OPERATION,
            idempotency_key,
        )
        if request is None:
            return None
        require_request_hash(request, request_hash)
        return replay_draft(unit_of_work, context.workspace_id, request)
