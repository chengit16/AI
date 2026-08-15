"""实现发布候选来源冻结和并发幂等恢复，不提前执行测试或审批。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_control.application.configuration import (
    parse_agent_configuration,
    validate_configuration_references,
)
from ai_platform_api.modules.agent_control.application.errors import (
    AgentLifecycleConflictError,
    AgentValidationError,
)
from ai_platform_api.modules.agent_control.application.support import (
    REQUEST_RELEASE_OPERATION,
    browser_account,
    control_request,
    raise_write_conflict,
    record_change,
    replay_candidate,
    request_digest,
    require_custom_agent,
    require_draft,
    require_idempotency_key,
    require_request_hash,
    require_resource_scope,
)
from ai_platform_api.modules.agent_control.domain.models import (
    AgentControlUnitOfWork,
    AgentReleaseCandidate,
    AgentWriteConflictError,
)


def request_release_candidate(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    agent_id: UUID,
    expected_revision: int,
    idempotency_key: str,
) -> AgentReleaseCandidate:
    """冻结候选来源；本节点不伪造测试、审批或已发布结论。"""

    # 长函数保留原因: 幂等检查、行锁、配置复核、候选与审计提交必须共享一个事务视图。
    # 1. 候选请求绑定 revision；真正配置语义校验、测试和审批由后续节点推进。
    account_id = browser_account(context)
    require_resource_scope(context, agent_id)
    if expected_revision < 1:
        raise AgentValidationError
    require_idempotency_key(idempotency_key)
    request_hash = request_digest(
        {
            "operation": REQUEST_RELEASE_OPERATION,
            "agent_id": str(agent_id),
            "expected_revision": expected_revision,
        }
    )
    now = datetime.now(UTC)
    try:
        with unit_of_work_factory as unit_of_work:
            request = unit_of_work.agents.get_request(
                context.workspace_id,
                context.actor_id,
                REQUEST_RELEASE_OPERATION,
                idempotency_key,
            )
            if request is not None:
                require_request_hash(request, request_hash)
                return replay_candidate(unit_of_work, context.workspace_id, request)

            # 2. 锁内确认 Agent 仍可编辑且 revision 未变化，再冻结不含配置正文的候选摘要。
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
            if agent.status != "active" or draft.revision != expected_revision:
                raise AgentLifecycleConflictError
            parsed, normalized_configuration, config_hash = parse_agent_configuration(
                draft.configuration
            )
            validate_configuration_references(unit_of_work.configuration, context, parsed)
            # 历史正文、规范化结果和摘要必须一致，避免绕过应用层写入的脏数据进入候选。
            if normalized_configuration != draft.configuration or config_hash != draft.config_hash:
                raise AgentLifecycleConflictError
            candidate_id = uuid4()
            candidate_hash = request_digest(
                {
                    "schema_version": 1,
                    "candidate_id": str(candidate_id),
                    "agent_id": str(agent_id),
                    "draft_id": str(draft.draft_id),
                    "draft_revision": draft.revision,
                    "config_hash": draft.config_hash,
                }
            )
            candidate = AgentReleaseCandidate(
                candidate_id=candidate_id,
                agent_id=agent_id,
                draft_id=draft.draft_id,
                workspace_id=context.workspace_id,
                draft_revision=draft.revision,
                candidate_hash=candidate_hash,
                config_hash=draft.config_hash,
                status="created",
                created_by_account_id=account_id,
                created_at=now,
                updated_at=now,
                version=1,
            )
            unit_of_work.agents.add_candidate(candidate)
            unit_of_work.agents.add_request(
                control_request(
                    context,
                    REQUEST_RELEASE_OPERATION,
                    idempotency_key,
                    request_hash,
                    "candidate",
                    candidate_id,
                    1,
                    now,
                )
            )
            # 3. `created` 仅表示来源冻结，不代表测试、审批或发布已通过。
            record_change(
                unit_of_work,
                context,
                action="agent.release.requested",
                event_type="agent.release.requested",
                resource_id=agent_id,
                aggregate_version=draft.revision,
                occurred_at=now,
                attributes={
                    "candidate_id": str(candidate_id),
                    "draft_id": str(draft.draft_id),
                    "draft_revision": draft.revision,
                    "candidate_hash": candidate_hash,
                    "config_hash": draft.config_hash,
                    "status": candidate.status,
                },
            )
            unit_of_work.commit()
            return candidate
    except AgentLifecycleConflictError:
        replayed = _recover_candidate(
            unit_of_work_factory,
            context,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replayed is not None:
            return replayed
        raise
    except AgentWriteConflictError as error:
        replayed = _recover_candidate(
            unit_of_work_factory,
            context,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replayed is not None:
            return replayed
        raise_write_conflict(error)


def _recover_candidate(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    idempotency_key: str,
    request_hash: str,
) -> AgentReleaseCandidate | None:
    """在候选来源竞争后恢复已经冻结的同一请求结果。"""

    with unit_of_work_factory as unit_of_work:
        request = unit_of_work.agents.get_request(
            context.workspace_id,
            context.actor_id,
            REQUEST_RELEASE_OPERATION,
            idempotency_key,
        )
        if request is None:
            return None
        require_request_hash(request, request_hash)
        return replay_candidate(unit_of_work, context.workspace_id, request)
