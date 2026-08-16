"""持久化 Agent 配置资源，并投影跨模块已发布事实用于同事务校验。"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, insert, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from ai_platform_api.modules.agent_control.domain.configuration import (
    AgentConfigurationRepository,
    AgentKnowledgeScopeVersion,
    AgentOutputSchemaVersion,
    AgentPromptVersion,
    AgentSafetyPolicyVersion,
    AgentToolDefinition,
    KnowledgeBaseReference,
    RuntimeConfigurationReference,
    WorkflowReleaseReference,
)
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.persistence.tables import (
    agent_knowledge_scope_items,
    agent_knowledge_scope_versions,
    agent_output_schema_versions,
    agent_prompt_versions,
    agent_safety_policy_versions,
    agent_tool_definitions,
    ai_runtime_config_publication,
    ai_runtime_config_versions,
    knowledge_bases,
    workflow_publications,
    workflow_versions,
    workflows,
)


class SqlAlchemyAgentConfigurationRepository(AgentConfigurationRepository):
    """在 Agent 事务中维护版本事实并读取其他模块的最小公开投影。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_prompt_version(self, version: AgentPromptVersion) -> bool:
        result = self._session.execute(
            postgresql_insert(agent_prompt_versions)
            .values(
                prompt_version_id=version.prompt_version_id,
                workspace_id=version.workspace_id,
                name=version.name,
                template=version.template,
                prompt_hash=version.prompt_hash,
                created_by_account_id=version.created_by_account_id,
                created_at=version.created_at,
            )
            .on_conflict_do_nothing(index_elements=[agent_prompt_versions.c.prompt_version_id])
            .returning(agent_prompt_versions.c.prompt_version_id)
        )
        return result.scalar_one_or_none() is not None

    def get_prompt_version(
        self,
        workspace_id: UUID,
        prompt_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentPromptVersion | None:
        statement = select(agent_prompt_versions).where(
            agent_prompt_versions.c.workspace_id == workspace_id,
            agent_prompt_versions.c.prompt_version_id == prompt_version_id,
        )
        row = self._session.execute(_share(statement, for_share)).one_or_none()
        if row is None:
            return None
        return AgentPromptVersion(
            row.prompt_version_id,
            row.workspace_id,
            row.name,
            row.template,
            row.prompt_hash,
            row.created_by_account_id,
            row.created_at,
        )

    def add_knowledge_scope_version(self, version: AgentKnowledgeScopeVersion) -> bool:
        inserted = self._session.execute(
            postgresql_insert(agent_knowledge_scope_versions)
            .values(
                knowledge_scope_version_id=version.knowledge_scope_version_id,
                workspace_id=version.workspace_id,
                name=version.name,
                scope_hash=version.scope_hash,
                created_by_account_id=version.created_by_account_id,
                created_at=version.created_at,
            )
            .on_conflict_do_nothing(
                index_elements=[agent_knowledge_scope_versions.c.knowledge_scope_version_id]
            )
            .returning(agent_knowledge_scope_versions.c.knowledge_scope_version_id)
        ).scalar_one_or_none()
        if inserted is None:
            return False
        if version.knowledge_base_ids:
            self._session.execute(
                insert(agent_knowledge_scope_items),
                [
                    {
                        "knowledge_scope_version_id": version.knowledge_scope_version_id,
                        "workspace_id": version.workspace_id,
                        "knowledge_base_id": knowledge_base_id,
                        "position": position,
                    }
                    for position, knowledge_base_id in enumerate(
                        version.knowledge_base_ids,
                        start=1,
                    )
                ],
            )
        return True

    def get_knowledge_scope_version(
        self,
        workspace_id: UUID,
        knowledge_scope_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentKnowledgeScopeVersion | None:
        statement = select(agent_knowledge_scope_versions).where(
            agent_knowledge_scope_versions.c.workspace_id == workspace_id,
            agent_knowledge_scope_versions.c.knowledge_scope_version_id
            == knowledge_scope_version_id,
        )
        row = self._session.execute(_share(statement, for_share)).one_or_none()
        if row is None:
            return None
        item_statement = (
            select(agent_knowledge_scope_items.c.knowledge_base_id)
            .where(
                agent_knowledge_scope_items.c.workspace_id == workspace_id,
                agent_knowledge_scope_items.c.knowledge_scope_version_id
                == knowledge_scope_version_id,
            )
            .order_by(agent_knowledge_scope_items.c.position)
        )
        knowledge_base_ids = tuple(self._session.scalars(_share(item_statement, for_share)).all())
        return AgentKnowledgeScopeVersion(
            row.knowledge_scope_version_id,
            row.workspace_id,
            row.name,
            knowledge_base_ids,
            row.scope_hash,
            row.created_by_account_id,
            row.created_at,
        )

    def get_knowledge_bases(
        self,
        workspace_id: UUID,
        knowledge_base_ids: tuple[UUID, ...],
        *,
        for_share: bool = False,
    ) -> tuple[KnowledgeBaseReference, ...]:
        if not knowledge_base_ids:
            return ()
        statement = select(knowledge_bases).where(
            knowledge_bases.c.workspace_id == workspace_id,
            knowledge_bases.c.knowledge_base_id.in_(knowledge_base_ids),
        )
        rows = self._session.execute(_share(statement, for_share))
        return tuple(_knowledge_base(row) for row in rows)

    def add_output_schema_version(self, version: AgentOutputSchemaVersion) -> bool:
        result = self._session.execute(
            postgresql_insert(agent_output_schema_versions)
            .values(
                output_schema_version_id=version.output_schema_version_id,
                workspace_id=version.workspace_id,
                name=version.name,
                schema_document=version.schema_document,
                schema_hash=version.schema_hash,
                created_by_account_id=version.created_by_account_id,
                created_at=version.created_at,
            )
            .on_conflict_do_nothing(
                index_elements=[agent_output_schema_versions.c.output_schema_version_id]
            )
            .returning(agent_output_schema_versions.c.output_schema_version_id)
        )
        return result.scalar_one_or_none() is not None

    def get_output_schema_version(
        self,
        workspace_id: UUID,
        output_schema_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentOutputSchemaVersion | None:
        statement = select(agent_output_schema_versions).where(
            agent_output_schema_versions.c.workspace_id == workspace_id,
            agent_output_schema_versions.c.output_schema_version_id == output_schema_version_id,
        )
        row = self._session.execute(_share(statement, for_share)).one_or_none()
        if row is None:
            return None
        return AgentOutputSchemaVersion(
            row.output_schema_version_id,
            row.workspace_id,
            row.name,
            cast(dict[str, object], row.schema_document),
            row.schema_hash,
            row.created_by_account_id,
            row.created_at,
        )

    def get_safety_policy_version(
        self,
        safety_policy_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentSafetyPolicyVersion | None:
        statement = select(agent_safety_policy_versions).where(
            agent_safety_policy_versions.c.safety_policy_version_id == safety_policy_version_id
        )
        row = self._session.execute(_share(statement, for_share)).one_or_none()
        if row is None:
            return None
        return AgentSafetyPolicyVersion(
            row.safety_policy_version_id,
            row.policy_key,
            row.version_number,
            row.implementation_version,
            row.policy_hash,
            row.status,
        )

    def get_active_safety_policy_version(
        self,
        implementation_version: str,
        *,
        for_share: bool = False,
    ) -> AgentSafetyPolicyVersion | None:
        """返回与当前代码实现匹配的最高活动安全策略版本。"""

        statement = (
            select(agent_safety_policy_versions)
            .where(
                agent_safety_policy_versions.c.policy_key == "rag-safety",
                agent_safety_policy_versions.c.implementation_version == implementation_version,
                agent_safety_policy_versions.c.status == "active",
            )
            .order_by(agent_safety_policy_versions.c.version_number.desc())
            .limit(1)
        )
        row = self._session.execute(_share(statement, for_share)).one_or_none()
        if row is None:
            return None
        return AgentSafetyPolicyVersion(
            row.safety_policy_version_id,
            row.policy_key,
            row.version_number,
            row.implementation_version,
            row.policy_hash,
            row.status,
        )

    def get_tool_definition(
        self,
        tool_id: UUID,
        tool_version: int,
        *,
        for_share: bool = False,
    ) -> AgentToolDefinition | None:
        statement = select(agent_tool_definitions).where(
            agent_tool_definitions.c.tool_id == tool_id,
            agent_tool_definitions.c.tool_version == tool_version,
        )
        row = self._session.execute(_share(statement, for_share)).one_or_none()
        if row is None:
            return None
        return AgentToolDefinition(
            row.tool_id,
            row.tool_version,
            row.tool_key,
            row.access_mode,
            row.permission_code,
        )

    def get_current_runtime_configuration(
        self,
        runtime_config_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> RuntimeConfigurationReference | None:
        statement = (
            select(
                ai_runtime_config_versions.c.runtime_config_version_id,
                ai_runtime_config_publication.c.generation,
                ai_runtime_config_versions.c.max_prompt_characters,
                ai_runtime_config_versions.c.max_output_tokens,
                ai_runtime_config_versions.c.total_timeout_ms,
                ai_runtime_config_versions.c.max_estimated_cost_microunits,
            )
            .join(
                ai_runtime_config_publication,
                ai_runtime_config_publication.c.runtime_config_version_id
                == ai_runtime_config_versions.c.runtime_config_version_id,
            )
            .where(
                ai_runtime_config_publication.c.publication_key == "current",
                ai_runtime_config_versions.c.runtime_config_version_id == runtime_config_version_id,
            )
        )
        row = self._session.execute(_share(statement, for_share)).one_or_none()
        if row is None:
            return None
        return RuntimeConfigurationReference(
            row.runtime_config_version_id,
            row.generation,
            row.max_prompt_characters,
            row.max_output_tokens,
            row.total_timeout_ms,
            row.max_estimated_cost_microunits,
        )

    def get_published_runtime_configuration(
        self,
        *,
        for_share: bool = False,
    ) -> RuntimeConfigurationReference | None:
        """读取唯一的当前 Runtime 发布指针及其 Agent 预算上限。"""

        statement = (
            select(
                ai_runtime_config_versions.c.runtime_config_version_id,
                ai_runtime_config_publication.c.generation,
                ai_runtime_config_versions.c.max_prompt_characters,
                ai_runtime_config_versions.c.max_output_tokens,
                ai_runtime_config_versions.c.total_timeout_ms,
                ai_runtime_config_versions.c.max_estimated_cost_microunits,
            )
            .join(
                ai_runtime_config_publication,
                ai_runtime_config_publication.c.runtime_config_version_id
                == ai_runtime_config_versions.c.runtime_config_version_id,
            )
            .where(ai_runtime_config_publication.c.publication_key == "current")
        )
        row = self._session.execute(_share(statement, for_share)).one_or_none()
        if row is None:
            return None
        return RuntimeConfigurationReference(
            row.runtime_config_version_id,
            row.generation,
            row.max_prompt_characters,
            row.max_output_tokens,
            row.total_timeout_ms,
            row.max_estimated_cost_microunits,
        )

    def get_current_workflow_release(
        self,
        workspace_id: UUID,
        workflow_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> WorkflowReleaseReference | None:
        # 1. 版本必须同时命中定义和当前发布指针，历史版本不会被误判为可用。
        statement = (
            select(
                workflow_versions.c.workflow_version_id,
                workflow_versions.c.workflow_id,
                workflow_versions.c.workspace_id,
                workflows.c.status,
                workflow_publications.c.generation,
            )
            .join(
                workflows,
                (workflows.c.workflow_id == workflow_versions.c.workflow_id)
                & (workflows.c.workspace_id == workflow_versions.c.workspace_id),
            )
            .join(
                workflow_publications,
                (workflow_publications.c.workflow_id == workflow_versions.c.workflow_id)
                & (workflow_publications.c.workspace_id == workflow_versions.c.workspace_id)
                & (
                    workflow_publications.c.workflow_version_id
                    == workflow_versions.c.workflow_version_id
                ),
            )
            .where(
                workflow_versions.c.workspace_id == workspace_id,
                workflow_versions.c.workflow_version_id == workflow_version_id,
            )
        )
        row = self._session.execute(_share(statement, for_share)).one_or_none()
        if row is None:
            return None
        # 2. 只投影 Agent 校验所需字段，工作流图和私有持久化模型不跨模块泄漏。
        return WorkflowReleaseReference(
            row.workflow_version_id,
            row.workflow_id,
            row.workspace_id,
            row.status,
            row.generation,
        )


def _share(statement: Select[Any], enabled: bool) -> Select[Any]:
    """在引用校验事务中获取共享行锁，避免校验后资源立即失效。"""

    return statement.with_for_update(read=True) if enabled else statement


def _knowledge_base(row: Row[Any]) -> KnowledgeBaseReference:
    return KnowledgeBaseReference(
        row.knowledge_base_id,
        row.workspace_id,
        row.status,
        row.default_visibility,
        frozenset(row.department_ids),
        cast(SecurityLevel, row.default_security_level),
        row.created_by_account_id,
    )
