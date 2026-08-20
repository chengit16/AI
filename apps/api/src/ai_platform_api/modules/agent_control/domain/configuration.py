"""定义 Agent 配置引用、不可变资源版本和跨模块只读投影端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_api.modules.authorization.domain.fields import SecurityLevel

ToolAccessMode = Literal["read", "write"]


@dataclass(frozen=True)
class AgentPromptVersion:
    """冻结工作空间 Prompt 正文及摘要，草稿只保存该版本标识。"""

    prompt_version_id: UUID
    workspace_id: UUID
    name: str
    template: str
    prompt_hash: str
    created_by_account_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class KnowledgeBaseReference:
    """提供知识范围校验所需的最小可见性和状态事实。"""

    knowledge_base_id: UUID
    workspace_id: UUID
    status: str
    default_visibility: str
    department_ids: frozenset[UUID]
    default_security_level: SecurityLevel
    created_by_account_id: UUID


@dataclass(frozen=True)
class AgentKnowledgeScopeVersion:
    """冻结 Agent 可检索的知识库集合，不复制文档正文或当前发布指针。"""

    knowledge_scope_version_id: UUID
    workspace_id: UUID
    name: str
    knowledge_base_ids: tuple[UUID, ...]
    scope_hash: str
    created_by_account_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class AgentOutputSchemaVersion:
    """冻结结构化输出 JSON Schema；运行时只按版本读取。"""

    output_schema_version_id: UUID
    workspace_id: UUID
    name: str
    schema_document: dict[str, object]
    schema_hash: str
    created_by_account_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class AgentSafetyPolicyVersion:
    """登记可被 Agent 配置引用的安全实现版本。"""

    safety_policy_version_id: UUID
    policy_key: str
    version_number: int
    implementation_version: str
    policy_hash: str
    status: Literal["active", "retired"]


@dataclass(frozen=True)
class AgentToolDefinition:
    """投影 Agent 配置校验所需的最小工具身份。

    完整 Schema、风险和 Adapter 治理由 ``tool_execution`` 模块拥有；Agent 控制面
    只验证 Release 草稿引用的稳定 ID、版本、读写类型与权限，避免复制工具事实源。
    """

    tool_id: UUID
    tool_version: int
    tool_key: str
    access_mode: ToolAccessMode
    permission_code: str


@dataclass(frozen=True)
class RuntimeConfigurationReference:
    """投影当前已发布模型配置及 Agent 预算校验所需上限。"""

    runtime_config_version_id: UUID
    publication_generation: int
    max_prompt_characters: int
    max_output_tokens: int
    total_timeout_ms: int
    max_estimated_cost_microunits: int


@dataclass(frozen=True)
class WorkflowReleaseReference:
    """投影当前工作流发布版本，避免 Agent 引用历史或草稿图。"""

    workflow_version_id: UUID
    workflow_id: UUID
    workspace_id: UUID
    workflow_status: str
    publication_generation: int


class AgentConfigurationRepository(Protocol):
    """维护配置资源版本，并以共享事务读取已发布跨模块事实。"""

    def add_prompt_version(self, version: AgentPromptVersion) -> bool: ...

    def get_prompt_version(
        self,
        workspace_id: UUID,
        prompt_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentPromptVersion | None: ...

    def add_knowledge_scope_version(self, version: AgentKnowledgeScopeVersion) -> bool: ...

    def get_knowledge_scope_version(
        self,
        workspace_id: UUID,
        knowledge_scope_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentKnowledgeScopeVersion | None: ...

    def get_knowledge_scope_versions(
        self,
        workspace_id: UUID,
        knowledge_scope_version_ids: tuple[UUID, ...],
        *,
        for_share: bool = False,
    ) -> tuple[AgentKnowledgeScopeVersion, ...]: ...

    def get_knowledge_bases(
        self,
        workspace_id: UUID,
        knowledge_base_ids: tuple[UUID, ...],
        *,
        for_share: bool = False,
    ) -> tuple[KnowledgeBaseReference, ...]: ...

    def add_output_schema_version(self, version: AgentOutputSchemaVersion) -> bool: ...

    def get_output_schema_version(
        self,
        workspace_id: UUID,
        output_schema_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentOutputSchemaVersion | None: ...

    def get_safety_policy_version(
        self,
        safety_policy_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentSafetyPolicyVersion | None: ...

    def get_active_safety_policy_version(
        self,
        implementation_version: str,
        *,
        for_share: bool = False,
    ) -> AgentSafetyPolicyVersion | None: ...

    def get_tool_definition(
        self,
        tool_id: UUID,
        tool_version: int,
        *,
        for_share: bool = False,
    ) -> AgentToolDefinition | None: ...

    def get_current_runtime_configuration(
        self,
        runtime_config_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> RuntimeConfigurationReference | None: ...

    def get_published_runtime_configuration(
        self,
        *,
        for_share: bool = False,
    ) -> RuntimeConfigurationReference | None: ...

    def get_current_workflow_release(
        self,
        workspace_id: UUID,
        workflow_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> WorkflowReleaseReference | None: ...
