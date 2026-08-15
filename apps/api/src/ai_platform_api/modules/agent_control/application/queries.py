"""提供工作空间隔离的 Agent 当前事实和不可变 Release 查询。"""

from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_control.application.errors import AgentNotFoundError
from ai_platform_api.modules.agent_control.application.support import (
    browser_account,
    require_custom_agent,
    require_draft,
    require_resource_scope,
)
from ai_platform_api.modules.agent_control.domain.models import (
    Agent,
    AgentControlUnitOfWork,
    AgentDraft,
    AgentRelease,
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
        return release
