"""实现自定义 Agent 与首个草稿修订的原子创建用例。"""

from datetime import UTC, datetime
from uuid import uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_control.application.errors import AgentDeniedError
from ai_platform_api.modules.agent_control.application.support import (
    CREATE_OPERATION,
    browser_account,
    configuration_digest,
    control_request,
    normalize_description,
    normalize_name,
    raise_write_conflict,
    record_change,
    replay_created_agent,
    request_digest,
    require_idempotency_key,
    require_request_hash,
    revision_from_draft,
)
from ai_platform_api.modules.agent_control.domain.models import (
    Agent,
    AgentControlUnitOfWork,
    AgentDraft,
    AgentWriteConflictError,
)


def create_agent(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    name: str,
    description: str | None,
    configuration: dict[str, object],
    idempotency_key: str,
) -> tuple[Agent, AgentDraft]:
    """原子创建自定义 Agent、首个草稿修订及可靠变更事件。"""

    # 1. 在事务外规范化公开字段和配置摘要，超限或非 JSON 配置不会持有数据库锁。
    account_id = browser_account(context)
    if not context.authorized_workspace:
        raise AgentDeniedError
    normalized_name = normalize_name(name)
    normalized_description = normalize_description(description)
    config_hash = configuration_digest(configuration)
    require_idempotency_key(idempotency_key)
    request_hash = request_digest(
        {
            "operation": CREATE_OPERATION,
            "name": normalized_name,
            "description": normalized_description,
            "config_hash": config_hash,
        }
    )
    now = datetime.now(UTC)
    try:
        with unit_of_work_factory as unit_of_work:
            # 2. 已提交的相同请求直接返回首个修订；同键异参必须失败关闭。
            request = unit_of_work.agents.get_request(
                context.workspace_id,
                context.actor_id,
                CREATE_OPERATION,
                idempotency_key,
            )
            if request is not None:
                require_request_hash(request, request_hash)
                return replay_created_agent(unit_of_work, context.workspace_id, request)

            agent_id = uuid4()
            draft_id = uuid4()
            agent = Agent(
                agent_id=agent_id,
                workspace_id=context.workspace_id,
                agent_key=f"custom-{agent_id.hex}",
                agent_kind="custom",
                name=normalized_name,
                description=normalized_description,
                status="active",
                created_by_account_id=account_id,
                created_at=now,
                updated_at=now,
                version=1,
            )
            draft = AgentDraft(
                draft_id=draft_id,
                agent_id=agent_id,
                workspace_id=context.workspace_id,
                revision=1,
                status="editing",
                configuration=configuration,
                config_hash=config_hash,
                updated_by_account_id=account_id,
                updated_at=now,
            )
            unit_of_work.agents.add_agent(agent, draft, revision_from_draft(draft))
            unit_of_work.agents.add_request(
                control_request(
                    context,
                    CREATE_OPERATION,
                    idempotency_key,
                    request_hash,
                    "agent",
                    agent_id,
                    1,
                    now,
                )
            )
            # 3. 草稿配置不进入审计或 Outbox，只传播资源身份、revision 和摘要。
            record_change(
                unit_of_work,
                context,
                action="agent.definition.created",
                event_type="agent.draft.changed",
                resource_id=agent_id,
                aggregate_version=1,
                occurred_at=now,
                attributes={
                    "draft_id": str(draft_id),
                    "revision": 1,
                    "config_hash": config_hash,
                },
            )
            unit_of_work.commit()
            return agent, draft
    except AgentWriteConflictError as error:
        replayed = _recover_created_agent(
            unit_of_work_factory,
            context,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replayed is not None:
            return replayed
        raise_write_conflict(error)


def _recover_created_agent(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    idempotency_key: str,
    request_hash: str,
) -> tuple[Agent, AgentDraft] | None:
    """在并发唯一键竞争回滚后，从已提交请求恢复创建结果。"""

    with unit_of_work_factory as unit_of_work:
        request = unit_of_work.agents.get_request(
            context.workspace_id,
            context.actor_id,
            CREATE_OPERATION,
            idempotency_key,
        )
        if request is None:
            return None
        require_request_hash(request, request_hash)
        return replay_created_agent(unit_of_work, context.workspace_id, request)
