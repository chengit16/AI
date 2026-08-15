"""提供稳定的 Agent 控制面应用接口，并把具体用例委托给职责模块。"""

from __future__ import annotations

from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_control.application.candidates import (
    request_release_candidate,
)
from ai_platform_api.modules.agent_control.application.configuration_resources import (
    create_knowledge_scope_version,
    create_output_schema_version,
    create_prompt_version,
)
from ai_platform_api.modules.agent_control.application.creation import create_agent
from ai_platform_api.modules.agent_control.application.drafts import (
    list_draft_revisions,
    update_draft,
)
from ai_platform_api.modules.agent_control.application.errors import (
    AgentConfigurationInvalidError,
    AgentDeniedError,
    AgentIdempotencyConflictError,
    AgentLifecycleConflictError,
    AgentNotFoundError,
    AgentValidationError,
)
from ai_platform_api.modules.agent_control.application.lifecycle import archive_agent
from ai_platform_api.modules.agent_control.application.queries import get_agent, get_release
from ai_platform_api.modules.agent_control.application.support import configuration_digest
from ai_platform_api.modules.agent_control.domain.configuration import (
    AgentKnowledgeScopeVersion,
    AgentOutputSchemaVersion,
    AgentPromptVersion,
)
from ai_platform_api.modules.agent_control.domain.models import (
    Agent,
    AgentControlUnitOfWork,
    AgentDraft,
    AgentDraftRevision,
    AgentRelease,
    AgentReleaseCandidate,
)

__all__ = [
    "AgentConfigurationInvalidError",
    "AgentControlService",
    "AgentDeniedError",
    "AgentIdempotencyConflictError",
    "AgentLifecycleConflictError",
    "AgentNotFoundError",
    "AgentValidationError",
    "configuration_digest",
]


class AgentControlService:
    """管理自定义 Agent 生命周期，并阻止控制面写入系统助手事实。"""

    def __init__(self, unit_of_work: AgentControlUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def create_prompt_version(
        self,
        context: RequestContext,
        *,
        name: str,
        template: str,
    ) -> AgentPromptVersion:
        """创建内容寻址的不可变 Prompt 版本，明文凭证不会进入事实库。"""

        return create_prompt_version(
            self._unit_of_work,
            context,
            name=name,
            template=template,
        )

    def create_knowledge_scope_version(
        self,
        context: RequestContext,
        *,
        name: str,
        knowledge_base_ids: tuple[UUID, ...],
    ) -> AgentKnowledgeScopeVersion:
        """冻结当前主体可访问的知识库集合，后续仍会复核状态与权限。"""

        return create_knowledge_scope_version(
            self._unit_of_work,
            context,
            name=name,
            knowledge_base_ids=knowledge_base_ids,
        )

    def create_output_schema_version(
        self,
        context: RequestContext,
        *,
        name: str,
        schema_document: dict[str, object],
    ) -> AgentOutputSchemaVersion:
        """校验并冻结结构化输出 Schema，供草稿只按版本引用。"""

        return create_output_schema_version(
            self._unit_of_work,
            context,
            name=name,
            schema_document=schema_document,
        )

    def create_agent(
        self,
        context: RequestContext,
        *,
        name: str,
        description: str | None,
        configuration: dict[str, object],
        idempotency_key: str,
    ) -> tuple[Agent, AgentDraft]:
        """原子创建自定义 Agent、首个草稿修订及可靠变更事件。"""

        return create_agent(
            self._unit_of_work,
            context,
            name=name,
            description=description,
            configuration=configuration,
            idempotency_key=idempotency_key,
        )

    def get_agent(
        self,
        context: RequestContext,
        *,
        agent_id: UUID,
    ) -> tuple[Agent, AgentDraft]:
        """读取当前定义和草稿；系统 Agent 对控制面保持不可见。"""

        return get_agent(self._unit_of_work, context, agent_id=agent_id)

    def update_draft(
        self,
        context: RequestContext,
        *,
        agent_id: UUID,
        expected_revision: int,
        configuration: dict[str, object],
        idempotency_key: str,
    ) -> AgentDraft:
        """用乐观锁写入新草稿 revision，并保留旧修订的完整可追溯事实。"""

        return update_draft(
            self._unit_of_work,
            context,
            agent_id=agent_id,
            expected_revision=expected_revision,
            configuration=configuration,
            idempotency_key=idempotency_key,
        )

    def list_draft_revisions(
        self,
        context: RequestContext,
        *,
        agent_id: UUID,
        limit: int,
    ) -> tuple[AgentDraftRevision, ...]:
        """按 revision 倒序返回 Agent 草稿历史，不跨工作空间或资源范围。"""

        return list_draft_revisions(
            self._unit_of_work,
            context,
            agent_id=agent_id,
            limit=limit,
        )

    def request_release_candidate(
        self,
        context: RequestContext,
        *,
        agent_id: UUID,
        expected_revision: int,
        idempotency_key: str,
    ) -> AgentReleaseCandidate:
        """冻结候选来源；本节点不伪造测试、审批或已发布结论。"""

        return request_release_candidate(
            self._unit_of_work,
            context,
            agent_id=agent_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

    def archive_agent(
        self,
        context: RequestContext,
        *,
        agent_id: UUID,
        expected_version: int,
        idempotency_key: str,
    ) -> Agent:
        """归档自定义 Agent 并终止当前草稿，历史候选和 Release 保持可追溯。"""

        return archive_agent(
            self._unit_of_work,
            context,
            agent_id=agent_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )

    def get_release(
        self,
        context: RequestContext,
        *,
        agent_id: UUID,
        release_id: UUID,
    ) -> AgentRelease:
        """读取自定义 Agent 的不可变 Release，不回读当前草稿补齐快照。"""

        return get_release(
            self._unit_of_work,
            context,
            agent_id=agent_id,
            release_id=release_id,
        )
