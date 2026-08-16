"""实现自定义 Agent 与首个草稿修订的原子创建用例。"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_control.application.configuration import (
    AgentConfigurationDocument,
    parse_agent_configuration,
    validate_configuration_references,
)
from ai_platform_api.modules.agent_control.application.configuration_resources import (
    create_starter_configuration,
)
from ai_platform_api.modules.agent_control.application.errors import (
    AgentConfigurationInvalidError,
    AgentDeniedError,
)
from ai_platform_api.modules.agent_control.application.support import (
    CREATE_OPERATION,
    browser_account,
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

ParsedConfiguration = tuple[AgentConfigurationDocument, dict[str, object], str]


@dataclass(frozen=True)
class _PreparedAgentCreation:
    """保存事务外已规范化的 Agent 创建输入与稳定幂等摘要。"""

    account_id: UUID
    name: str
    description: str | None
    configuration: ParsedConfiguration | None
    request_hash: str


def create_agent(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    name: str,
    description: str | None,
    configuration: dict[str, object] | None,
    use_starter_configuration: bool = False,
    runtime_bootstrap: Callable[[UUID], object] | None = None,
    idempotency_key: str,
) -> tuple[Agent, AgentDraft]:
    """原子创建自定义 Agent、首个草稿修订及可靠变更事件。"""

    # 1. 显式配置沿用原摘要语义；基础模式按请求意图做幂等，不受后续 Runtime 切换影响。
    prepared = _prepare_creation(
        context,
        name=name,
        description=description,
        configuration=configuration,
        use_starter_configuration=use_starter_configuration,
        idempotency_key=idempotency_key,
    )

    # 2. 本地模式允许在首次基础创建前自举 Runtime；已提交幂等请求无需依赖当前发布指针。
    if use_starter_configuration and runtime_bootstrap is not None:
        replayed = _recover_created_agent(
            unit_of_work_factory,
            context,
            idempotency_key=idempotency_key,
            request_hash=prepared.request_hash,
        )
        if replayed is not None:
            return replayed
        runtime_bootstrap(prepared.account_id)

    now = datetime.now(UTC)
    try:
        with unit_of_work_factory as unit_of_work:
            # 3. 自举与创建之间仍需复核幂等事实，避免并发请求生成两个 Agent。
            request = unit_of_work.agents.get_request(
                context.workspace_id,
                context.actor_id,
                CREATE_OPERATION,
                idempotency_key,
            )
            if request is not None:
                require_request_hash(request, prepared.request_hash)
                return replay_created_agent(unit_of_work, context.workspace_id, request)

            resolved_configuration: ParsedConfiguration | None
            if use_starter_configuration:
                resolved_configuration = create_starter_configuration(unit_of_work, context)
            else:
                resolved_configuration = prepared.configuration
            if resolved_configuration is None:
                raise AgentConfigurationInvalidError
            parsed, normalized_configuration, config_hash = resolved_configuration
            validate_configuration_references(unit_of_work.configuration, context, parsed)

            agent, draft = _new_agent_and_draft(
                context,
                prepared,
                normalized_configuration=normalized_configuration,
                config_hash=config_hash,
                created_at=now,
            )
            unit_of_work.agents.add_agent(agent, draft, revision_from_draft(draft))
            unit_of_work.agents.add_request(
                control_request(
                    context,
                    CREATE_OPERATION,
                    idempotency_key,
                    prepared.request_hash,
                    "agent",
                    agent.agent_id,
                    1,
                    now,
                )
            )
            # 4. 草稿配置不进入审计或 Outbox，只传播资源身份、revision 和摘要。
            record_change(
                unit_of_work,
                context,
                action="agent.definition.created",
                event_type="agent.draft.changed",
                resource_id=agent.agent_id,
                aggregate_version=1,
                occurred_at=now,
                attributes={
                    "draft_id": str(draft.draft_id),
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
            request_hash=prepared.request_hash,
        )
        if replayed is not None:
            return replayed
        raise_write_conflict(error)


def _prepare_creation(
    context: RequestContext,
    *,
    name: str,
    description: str | None,
    configuration: dict[str, object] | None,
    use_starter_configuration: bool,
    idempotency_key: str,
) -> _PreparedAgentCreation:
    """规范化创建输入，并保持显式配置已有的幂等摘要兼容性。"""

    # 1. 先校验可信浏览器主体和公共字段，使两种配置模式共享完全一致的定义语义。
    account_id = browser_account(context)
    if not context.authorized_workspace:
        raise AgentDeniedError
    normalized_name = normalize_name(name)
    normalized_description = normalize_description(description)
    require_idempotency_key(idempotency_key)
    # 2. 基础模式按稳定意图计算摘要；显式模式保留既有 config_hash 摘要，兼容历史幂等事实。
    if use_starter_configuration:
        if configuration is not None:
            raise AgentConfigurationInvalidError
        parsed_configuration = None
        request_document: dict[str, object] = {
            "operation": CREATE_OPERATION,
            "name": normalized_name,
            "description": normalized_description,
            "configuration_mode": "starter",
        }
    else:
        if configuration is None:
            raise AgentConfigurationInvalidError
        parsed_configuration = parse_agent_configuration(configuration)
        request_document = {
            "operation": CREATE_OPERATION,
            "name": normalized_name,
            "description": normalized_description,
            "config_hash": parsed_configuration[2],
        }
    return _PreparedAgentCreation(
        account_id,
        normalized_name,
        normalized_description,
        parsed_configuration,
        request_digest(request_document),
    )


def _new_agent_and_draft(
    context: RequestContext,
    prepared: _PreparedAgentCreation,
    *,
    normalized_configuration: dict[str, object],
    config_hash: str,
    created_at: datetime,
) -> tuple[Agent, AgentDraft]:
    """构造共享身份和时间戳的新 Agent 与首个可编辑草稿。"""

    agent_id = uuid4()
    return (
        Agent(
            agent_id=agent_id,
            workspace_id=context.workspace_id,
            agent_key=f"custom-{agent_id.hex}",
            agent_kind="custom",
            name=prepared.name,
            description=prepared.description,
            status="active",
            created_by_account_id=prepared.account_id,
            created_at=created_at,
            updated_at=created_at,
            version=1,
        ),
        AgentDraft(
            draft_id=uuid4(),
            agent_id=agent_id,
            workspace_id=context.workspace_id,
            revision=1,
            status="editing",
            configuration=normalized_configuration,
            config_hash=config_hash,
            updated_by_account_id=prepared.account_id,
            updated_at=created_at,
        ),
    )


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
