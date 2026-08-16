"""提供工作空间隔离的 Agent 当前事实、候选证据和不可变 Release 查询。"""

from dataclasses import dataclass
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_control.application.errors import (
    AgentNotFoundError,
    AgentValidationError,
)
from ai_platform_api.modules.agent_control.application.releases import require_valid_release
from ai_platform_api.modules.agent_control.application.support import (
    browser_account,
    require_custom_agent,
    require_draft,
    require_resource_scope,
)
from ai_platform_api.modules.agent_control.domain.approval import AgentApprovalDecision
from ai_platform_api.modules.agent_control.domain.evaluation import AgentEvaluationReport
from ai_platform_api.modules.agent_control.domain.models import (
    Agent,
    AgentControlUnitOfWork,
    AgentDraft,
    AgentRelease,
    AgentReleaseCandidate,
)


@dataclass(frozen=True)
class AgentCandidateControlView:
    """聚合候选、最近测试与审批结论，不返回测试输入、回答或 Prompt 正文。"""

    candidate: AgentReleaseCandidate
    evaluation: AgentEvaluationReport | None
    approval: AgentApprovalDecision | None


def list_agents(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    limit: int,
) -> tuple[tuple[Agent, AgentDraft], ...]:
    """列出当前空间自定义 Agent 及当前草稿，系统助手不会进入控制台。"""

    browser_account(context)
    if not context.authorized_workspace or not 1 <= limit <= 200:
        raise AgentValidationError
    with unit_of_work_factory as unit_of_work:
        result: list[tuple[Agent, AgentDraft]] = []
        for agent in unit_of_work.agents.list_agents(context.workspace_id, limit=limit):
            draft = require_draft(unit_of_work, context.workspace_id, agent.agent_id)
            result.append((agent, draft))
        return tuple(result)


def list_candidate_controls(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    agent_id: UUID,
    limit: int,
) -> tuple[AgentCandidateControlView, ...]:
    """读取候选流水及最近测试/审批摘要，失败证据不会因重试而被隐藏。"""

    browser_account(context)
    require_resource_scope(context, agent_id)
    if not 1 <= limit <= 100:
        raise AgentValidationError
    with unit_of_work_factory as unit_of_work:
        require_custom_agent(unit_of_work, context.workspace_id, agent_id)
        return tuple(
            AgentCandidateControlView(
                candidate,
                unit_of_work.evaluation.get_latest_report(
                    context.workspace_id,
                    candidate.candidate_id,
                ),
                unit_of_work.approval.get_decision_by_candidate(
                    context.workspace_id,
                    candidate.candidate_id,
                ),
            )
            for candidate in unit_of_work.agents.list_candidates(
                context.workspace_id,
                agent_id,
                limit=limit,
            )
        )


def list_releases(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    agent_id: UUID,
    limit: int,
) -> tuple[AgentRelease, ...]:
    """列出 Agent 的不可变 Release，并逐条复核快照摘要。"""

    browser_account(context)
    require_resource_scope(context, agent_id)
    if not 1 <= limit <= 100:
        raise AgentValidationError
    with unit_of_work_factory as unit_of_work:
        require_custom_agent(unit_of_work, context.workspace_id, agent_id)
        return tuple(
            require_valid_release(release)
            for release in unit_of_work.agents.list_releases(
                context.workspace_id,
                agent_id,
                limit=limit,
            )
        )


def get_agent(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    agent_id: UUID,
) -> tuple[Agent, AgentDraft]:
    """读取当前定义和草稿；系统 Agent 对控制面保持不可见。"""

    browser_account(context)
    require_resource_scope(context, agent_id)
    with unit_of_work_factory as unit_of_work:
        agent = require_custom_agent(unit_of_work, context.workspace_id, agent_id)
        draft = require_draft(unit_of_work, context.workspace_id, agent_id)
        return agent, draft


def get_release(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    agent_id: UUID,
    release_id: UUID,
) -> AgentRelease:
    """读取自定义 Agent 的不可变 Release，不回读当前草稿补齐快照。"""

    browser_account(context)
    require_resource_scope(context, agent_id)
    with unit_of_work_factory as unit_of_work:
        require_custom_agent(unit_of_work, context.workspace_id, agent_id)
        release = unit_of_work.agents.get_release(context.workspace_id, release_id)
        if release is None or release.agent_id != agent_id or release.release_kind != "custom":
            raise AgentNotFoundError
        return require_valid_release(release)
