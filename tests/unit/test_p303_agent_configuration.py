"""验证 P3-03 Agent 配置规范化、凭证防护和资源引用校验。"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.agent_control.application.configuration import (
    parse_agent_configuration,
    validate_configuration_references,
)
from ai_platform_api.modules.agent_control.application.service import (
    AgentConfigurationInvalidError,
)
from ai_platform_api.modules.agent_control.application.support import canonical_json
from ai_platform_api.modules.agent_control.domain.configuration import (
    AgentKnowledgeScopeVersion,
    AgentOutputSchemaVersion,
    AgentPromptVersion,
    AgentSafetyPolicyVersion,
    AgentToolDefinition,
    KnowledgeBaseReference,
    RuntimeConfigurationReference,
    WorkflowReleaseReference,
)

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000303")
OTHER_WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000399")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000303")
PROMPT_ID = UUID("a3000000-0000-4000-8000-000000000303")
RUNTIME_ID = UUID("a4000000-0000-4000-8000-000000000303")
SCOPE_ID = UUID("a5000000-0000-4000-8000-000000000303")
SECOND_SCOPE_ID = UUID("a5000000-0000-4000-8000-000000000304")
WORKFLOW_VERSION_ID = UUID("a6000000-0000-4000-8000-000000000303")
WORKFLOW_ID = UUID("a6100000-0000-4000-8000-000000000303")
TOOL_ID = UUID("a7000000-0000-4000-8000-000000000303")
SECOND_TOOL_ID = UUID("a7000000-0000-4000-8000-000000000304")
OUTPUT_SCHEMA_ID = UUID("a8000000-0000-4000-8000-000000000303")
SAFETY_POLICY_ID = UUID("a9000000-0000-4000-8000-000000000001")
KNOWLEDGE_BASE_ID = UUID("d1000000-0000-4000-8000-000000000303")
CREATED_AT = datetime(2026, 8, 16, tzinfo=UTC)
TRACE = TraceContext("7" * 32, "8" * 16)


class StubConfigurationRepository:
    """保存可按场景替换的配置引用事实，精确模拟工作空间过滤。"""

    def __init__(self) -> None:
        prompt = "仅依据已授权的合成知识回答。"
        output_schema: dict[str, object] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        }
        scope_hash = _scope_hash((KNOWLEDGE_BASE_ID,))
        self.prompt = AgentPromptVersion(
            PROMPT_ID,
            WORKSPACE_ID,
            "合成 Prompt",
            prompt,
            hashlib.sha256(prompt.encode()).hexdigest(),
            ACCOUNT_ID,
            CREATED_AT,
        )
        self.scopes = {
            SCOPE_ID: AgentKnowledgeScopeVersion(
                SCOPE_ID,
                WORKSPACE_ID,
                "合成知识范围 A",
                (KNOWLEDGE_BASE_ID,),
                scope_hash,
                ACCOUNT_ID,
                CREATED_AT,
            ),
            SECOND_SCOPE_ID: AgentKnowledgeScopeVersion(
                SECOND_SCOPE_ID,
                WORKSPACE_ID,
                "合成知识范围 B",
                (),
                _scope_hash(()),
                ACCOUNT_ID,
                CREATED_AT,
            ),
        }
        self.output_schema = AgentOutputSchemaVersion(
            OUTPUT_SCHEMA_ID,
            WORKSPACE_ID,
            "合成输出",
            output_schema,
            hashlib.sha256(canonical_json(output_schema)).hexdigest(),
            ACCOUNT_ID,
            CREATED_AT,
        )
        self.knowledge_base = KnowledgeBaseReference(
            KNOWLEDGE_BASE_ID,
            WORKSPACE_ID,
            "active",
            "workspace",
            frozenset(),
            "INTERNAL",
            ACCOUNT_ID,
        )
        self.safety = AgentSafetyPolicyVersion(
            SAFETY_POLICY_ID,
            "rag-safety",
            1,
            "rag-safety-v2",
            "4b7a460fa0a2ce10283b7f5affe3e04bd69c0a9b40238669a7c266bb63f83738",
            "active",
        )
        self.tools = {
            (TOOL_ID, 1): AgentToolDefinition(
                TOOL_ID,
                1,
                "knowledge.search",
                "read",
                "knowledge.document.read",
            ),
            (SECOND_TOOL_ID, 1): AgentToolDefinition(
                SECOND_TOOL_ID,
                1,
                "quota.get_usage",
                "read",
                "workspace.entitlement.read",
            ),
        }
        self.runtime: RuntimeConfigurationReference | None = RuntimeConfigurationReference(
            RUNTIME_ID,
            3,
            100_000,
            4096,
            120_000,
            1_000_000,
        )
        self.workflow: WorkflowReleaseReference | None = WorkflowReleaseReference(
            WORKFLOW_VERSION_ID,
            WORKFLOW_ID,
            WORKSPACE_ID,
            "active",
            2,
        )

    def add_prompt_version(self, version: AgentPromptVersion) -> bool:
        self.prompt = version
        return True

    def get_prompt_version(
        self,
        workspace_id: UUID,
        prompt_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentPromptVersion | None:
        del for_share
        if (
            self.prompt.workspace_id == workspace_id
            and self.prompt.prompt_version_id == prompt_version_id
        ):
            return self.prompt
        return None

    def add_knowledge_scope_version(self, version: AgentKnowledgeScopeVersion) -> bool:
        self.scopes[version.knowledge_scope_version_id] = version
        return True

    def get_knowledge_scope_version(
        self,
        workspace_id: UUID,
        knowledge_scope_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentKnowledgeScopeVersion | None:
        del for_share
        value = self.scopes.get(knowledge_scope_version_id)
        return value if value is not None and value.workspace_id == workspace_id else None

    def get_knowledge_scope_versions(
        self,
        workspace_id: UUID,
        knowledge_scope_version_ids: tuple[UUID, ...],
        *,
        for_share: bool = False,
    ) -> tuple[AgentKnowledgeScopeVersion, ...]:
        del for_share
        return tuple(
            value
            for scope_id in knowledge_scope_version_ids
            if (value := self.scopes.get(scope_id)) is not None
            and value.workspace_id == workspace_id
        )

    def get_knowledge_bases(
        self,
        workspace_id: UUID,
        knowledge_base_ids: tuple[UUID, ...],
        *,
        for_share: bool = False,
    ) -> tuple[KnowledgeBaseReference, ...]:
        del for_share
        if (
            workspace_id == self.knowledge_base.workspace_id
            and self.knowledge_base.knowledge_base_id in knowledge_base_ids
        ):
            return (self.knowledge_base,)
        return ()

    def add_output_schema_version(self, version: AgentOutputSchemaVersion) -> bool:
        self.output_schema = version
        return True

    def get_output_schema_version(
        self,
        workspace_id: UUID,
        output_schema_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentOutputSchemaVersion | None:
        del for_share
        if (
            self.output_schema.workspace_id == workspace_id
            and self.output_schema.output_schema_version_id == output_schema_version_id
        ):
            return self.output_schema
        return None

    def get_safety_policy_version(
        self,
        safety_policy_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentSafetyPolicyVersion | None:
        del for_share
        return self.safety if safety_policy_version_id == SAFETY_POLICY_ID else None

    def get_active_safety_policy_version(
        self,
        implementation_version: str,
        *,
        for_share: bool = False,
    ) -> AgentSafetyPolicyVersion | None:
        del for_share
        return self.safety if implementation_version == self.safety.implementation_version else None

    def get_tool_definition(
        self,
        tool_id: UUID,
        tool_version: int,
        *,
        for_share: bool = False,
    ) -> AgentToolDefinition | None:
        del for_share
        return self.tools.get((tool_id, tool_version))

    def get_current_runtime_configuration(
        self,
        runtime_config_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> RuntimeConfigurationReference | None:
        del for_share
        if (
            self.runtime is not None
            and self.runtime.runtime_config_version_id == runtime_config_version_id
        ):
            return self.runtime
        return None

    def get_published_runtime_configuration(
        self,
        *,
        for_share: bool = False,
    ) -> RuntimeConfigurationReference | None:
        del for_share
        return self.runtime

    def get_current_workflow_release(
        self,
        workspace_id: UUID,
        workflow_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> WorkflowReleaseReference | None:
        del for_share
        if (
            self.workflow is not None
            and self.workflow.workspace_id == workspace_id
            and self.workflow.workflow_version_id == workflow_version_id
        ):
            return self.workflow
        return None


def context() -> RequestContext:
    """构造具备内部密级的工作空间级合成授权上下文。"""

    return replace(
        RequestContext.trusted(
            actor_id=ACCOUNT_ID,
            user_id=ACCOUNT_ID,
            workspace_id=WORKSPACE_ID,
            trace=TRACE,
            authentication_method="browser_session",
        ),
        authorized_workspace=True,
        authorized_maximum_security_level="INTERNAL",
    )


def configuration() -> dict[str, object]:
    """返回同时覆盖知识、工作流、工具、安全和预算的严格配置。"""

    return {
        "prompt_version_id": str(PROMPT_ID),
        "runtime_config_version_id": str(RUNTIME_ID),
        "knowledge_scope_version_ids": [str(SCOPE_ID), str(SECOND_SCOPE_ID)],
        "workflow_release_id": str(WORKFLOW_VERSION_ID),
        "read_only_tools": [
            {
                "tool_id": str(TOOL_ID),
                "tool_version": 1,
                "access_mode": "read",
                "permission_code": "knowledge.document.read",
            },
            {
                "tool_id": str(SECOND_TOOL_ID),
                "tool_version": 1,
                "access_mode": "read",
                "permission_code": "workspace.entitlement.read",
            },
        ],
        "output_schema_version_id": str(OUTPUT_SCHEMA_ID),
        "safety_policy_version_id": str(SAFETY_POLICY_ID),
        "limits": {
            "max_input_tokens": 8192,
            "max_output_tokens": 2048,
            "max_execution_seconds": 60,
            "max_cost_microunits": 500_000,
        },
    }


def test_configuration_order_is_normalized_before_hashing() -> None:
    first = configuration()
    reversed_configuration = configuration()
    reversed_configuration["knowledge_scope_version_ids"] = list(
        reversed(cast(list[str], reversed_configuration["knowledge_scope_version_ids"]))
    )
    reversed_configuration["read_only_tools"] = list(
        reversed(cast(list[dict[str, object]], reversed_configuration["read_only_tools"]))
    )

    _, first_document, first_hash = parse_agent_configuration(first)
    _, second_document, second_hash = parse_agent_configuration(reversed_configuration)

    assert second_document == first_document
    assert second_hash == first_hash


@pytest.mark.parametrize("mutation", ["unknown", "missing", "duplicate", "credential"])
def test_invalid_structure_and_credentials_are_rejected(mutation: str) -> None:
    document = configuration()
    if mutation == "unknown":
        document["api_key"] = "synthetic-secret-value-123456"
    elif mutation == "missing":
        del document["limits"]
    elif mutation == "duplicate":
        document["knowledge_scope_version_ids"] = [str(SCOPE_ID), str(SCOPE_ID)]
    else:
        document["read_only_tools"] = [
            {
                "tool_id": str(TOOL_ID),
                "tool_version": 1,
                "access_mode": "read",
                "permission_code": "secret.value.token",
                "secret": "api_key=synthetic-secret-value-123456",
            }
        ]

    with pytest.raises(AgentConfigurationInvalidError):
        parse_agent_configuration(document)


def test_valid_published_and_authorized_references_pass() -> None:
    repository = StubConfigurationRepository()
    parsed, _, _ = parse_agent_configuration(configuration())

    validate_configuration_references(repository, context(), parsed)


@pytest.mark.parametrize(
    "invalid_case",
    ["credential_prompt", "cross_workspace", "deleted_knowledge", "old_runtime", "write_tool"],
)
def test_invalid_or_unauthorized_references_fail_closed(invalid_case: str) -> None:
    repository = StubConfigurationRepository()
    if invalid_case == "credential_prompt":
        repository.prompt = replace(
            repository.prompt,
            template="Bearer synthetic-secret-value-123456",
            prompt_hash=hashlib.sha256(b"Bearer synthetic-secret-value-123456").hexdigest(),
        )
    elif invalid_case == "cross_workspace":
        repository.prompt = replace(repository.prompt, workspace_id=OTHER_WORKSPACE_ID)
    elif invalid_case == "deleted_knowledge":
        repository.knowledge_base = replace(repository.knowledge_base, status="deleted")
    elif invalid_case == "old_runtime":
        repository.runtime = None
    else:
        repository.tools[(TOOL_ID, 1)] = replace(
            repository.tools[(TOOL_ID, 1)],
            access_mode="write",
        )
    parsed, _, _ = parse_agent_configuration(configuration())

    with pytest.raises(AgentConfigurationInvalidError):
        validate_configuration_references(repository, context(), parsed)


def _scope_hash(knowledge_base_ids: tuple[UUID, ...]) -> str:
    return hashlib.sha256(canonical_json([str(value) for value in knowledge_base_ids])).hexdigest()
