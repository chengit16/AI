"""实现自定义 Agent 归档、草稿终止及并发幂等恢复。"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_control.application.errors import (
    AgentLifecycleConflictError,
    AgentValidationError,
)
from ai_platform_api.modules.agent_control.application.support import (
    ARCHIVE_OPERATION,
    browser_account,
    control_request,
    raise_write_conflict,
    record_change,
    replay_archived_agent,
    request_digest,
    require_custom_agent,
    require_draft,
    require_idempotency_key,
    require_request_hash,
    require_resource_scope,
    revision_from_draft,
)
from ai_platform_api.modules.agent_control.domain.models import (
    Agent,
    AgentControlUnitOfWork,
    AgentWriteConflictError,
)


def archive_agent(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    agent_id: UUID,
    expected_version: int,
    idempotency_key: str,
) -> Agent:
    """归档自定义 Agent 并终止当前草稿，历史候选和 Release 保持可追溯。"""

    # 1. Agent version 与幂等摘要共同阻止旧页面覆盖新的定义状态。
    account_id = browser_account(context)
    require_resource_scope(context, agent_id)
    if expected_version < 1:
        raise AgentValidationError
    require_idempotency_key(idempotency_key)
    request_hash = request_digest(
        {
            "operation": ARCHIVE_OPERATION,
            "agent_id": str(agent_id),
            "expected_version": expected_version,
        }
    )
    now = datetime.now(UTC)
    try:
        with unit_of_work_factory as unit_of_work:
            request = unit_of_work.agents.get_request(
                context.workspace_id,
                context.actor_id,
                ARCHIVE_OPERATION,
                idempotency_key,
            )
            if request is not None:
                require_request_hash(request, request_hash)
                return replay_archived_agent(unit_of_work, context.workspace_id, request)

            # 2. 定义和当前草稿同时锁定；归档不会更新或删除历史 Release。
            agent = require_custom_agent(
                unit_of_work,
                context.workspace_id,
                agent_id,
                for_update=True,
            )
            draft = require_draft(
                unit_of_work,
                context.workspace_id,
                agent_id,
                for_update=True,
            )
            if agent.status != "active" or agent.version != expected_version:
                raise AgentLifecycleConflictError
            archived = replace(
                agent,
                status="archived",
                updated_at=now,
                version=agent.version + 1,
            )
            superseded = replace(
                draft,
                revision=draft.revision + 1,
                status="superseded",
                updated_by_account_id=account_id,
                updated_at=now,
            )
            if not unit_of_work.agents.save_agent(
                archived,
                expected_version=agent.version,
            ) or not unit_of_work.agents.save_draft(
                superseded,
                expected_revision=draft.revision,
            ):
                raise AgentLifecycleConflictError
            unit_of_work.agents.add_draft_revision(revision_from_draft(superseded))
            unit_of_work.agents.add_request(
                control_request(
                    context,
                    ARCHIVE_OPERATION,
                    idempotency_key,
                    request_hash,
                    "agent",
                    agent_id,
                    archived.version,
                    now,
                )
            )
            # 3. 归档和草稿终止作为一个事务提交，避免出现已归档定义仍保留可编辑草稿。
            record_change(
                unit_of_work,
                context,
                action="agent.definition.archived",
                event_type="agent.draft.changed",
                resource_id=agent_id,
                aggregate_version=archived.version,
                occurred_at=now,
                attributes={
                    "agent_status": archived.status,
                    "agent_version": archived.version,
                    "draft_id": str(superseded.draft_id),
                    "draft_revision": superseded.revision,
                    "draft_status": superseded.status,
                },
            )
            unit_of_work.commit()
            return archived
    except AgentLifecycleConflictError:
        replayed = _recover_archived_agent(
            unit_of_work_factory,
            context,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replayed is not None:
            return replayed
        raise
    except AgentWriteConflictError as error:
        replayed = _recover_archived_agent(
            unit_of_work_factory,
            context,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replayed is not None:
            return replayed
        raise_write_conflict(error)


def _recover_archived_agent(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    idempotency_key: str,
    request_hash: str,
) -> Agent | None:
    """在定义 version 竞争后恢复同一归档请求的终态。"""

    with unit_of_work_factory as unit_of_work:
        request = unit_of_work.agents.get_request(
            context.workspace_id,
            context.actor_id,
            ARCHIVE_OPERATION,
            idempotency_key,
        )
        if request is None:
            return None
        require_request_hash(request, request_hash)
        return replay_archived_agent(unit_of_work, context.workspace_id, request)
