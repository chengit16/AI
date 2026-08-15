"""规范化 Agent 配置并执行发布前资源引用校验。"""

from __future__ import annotations

import hashlib
import re
from typing import Annotated, cast
from uuid import UUID

from ai_platform_backend.safety import RAG_SAFETY_VERSION
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_control.application.errors import (
    AgentConfigurationInvalidError,
    AgentValidationError,
)
from ai_platform_api.modules.agent_control.application.support import canonical_json
from ai_platform_api.modules.agent_control.domain.configuration import (
    AgentConfigurationRepository,
    AgentToolDefinition,
    KnowledgeBaseReference,
)
from ai_platform_api.modules.authorization.domain.fields import SECURITY_LEVEL_RANK

PERMISSION_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){2,}$")
CREDENTIAL_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(
        r"\b(?:api[_ -]?key|access[_ -]?token|secret)\s*[:=]\s*[A-Za-z0-9._~+/=-]{12,}",
        re.IGNORECASE,
    ),
)


class RuntimeLimitsDocument(BaseModel):
    """固定单次 Agent Run 可消耗的输入、输出、时间和成本上限。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_input_tokens: Annotated[int, Field(ge=1, le=2_000_000)]
    max_output_tokens: Annotated[int, Field(ge=1, le=65_536)]
    max_execution_seconds: Annotated[int, Field(ge=1, le=600)]
    max_cost_microunits: Annotated[int, Field(ge=0, le=1_000_000_000_000)]


class ReadOnlyToolDocument(BaseModel):
    """冻结草稿引用的工具身份，不携带参数 Schema 或执行信息。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: UUID
    tool_version: Annotated[int, Field(ge=1)]
    access_mode: Annotated[str, Field(pattern="^read$")]
    permission_code: Annotated[str, Field(pattern=PERMISSION_PATTERN.pattern)]


class AgentConfigurationDocument(BaseModel):
    """表示契约允许进入草稿的完整 Agent 配置。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_version_id: UUID
    runtime_config_version_id: UUID
    knowledge_scope_version_ids: Annotated[tuple[UUID, ...], Field(max_length=20)]
    workflow_release_id: UUID | None
    read_only_tools: Annotated[tuple[ReadOnlyToolDocument, ...], Field(max_length=20)]
    output_schema_version_id: UUID
    safety_policy_version_id: UUID
    limits: RuntimeLimitsDocument

    @model_validator(mode="after")
    def reject_duplicate_references(self) -> AgentConfigurationDocument:
        """重复引用会制造不稳定快照语义，因此在排序前直接拒绝。"""

        scope_ids = self.knowledge_scope_version_ids
        tool_keys = tuple((item.tool_id, item.tool_version) for item in self.read_only_tools)
        if len(scope_ids) != len(set(scope_ids)) or len(tool_keys) != len(set(tool_keys)):
            raise ValueError("Agent 配置存在重复引用")
        return self

    def normalized_document(self) -> dict[str, object]:
        """输出确定性字段与数组顺序，保证同一语义得到相同摘要。"""

        document = self.model_dump(mode="json")
        document["knowledge_scope_version_ids"] = sorted(
            cast(list[str], document["knowledge_scope_version_ids"])
        )
        document["read_only_tools"] = sorted(
            cast(list[dict[str, object]], document["read_only_tools"]),
            key=lambda item: (str(item["tool_id"]), int(cast(int, item["tool_version"]))),
        )
        return document


def parse_agent_configuration(
    configuration: dict[str, object],
) -> tuple[AgentConfigurationDocument, dict[str, object], str]:
    """严格解析并规范化外部 JSON 配置，未知字段和隐式类型转换失败关闭。"""

    try:
        model = AgentConfigurationDocument.model_validate_json(
            canonical_json(configuration),
            strict=True,
        )
    except (ValidationError, AgentValidationError) as error:
        raise AgentConfigurationInvalidError from error
    normalized = model.normalized_document()
    reject_credentials(normalized)
    digest = hashlib.sha256(canonical_json(normalized)).hexdigest()
    return model, normalized, digest


def validate_configuration_references(
    repository: AgentConfigurationRepository,
    context: RequestContext,
    configuration: AgentConfigurationDocument,
) -> None:
    """验证所有引用仍已发布、可见且未超过当前运行配置预算。"""

    # 1. 工作空间资源使用复合边界读取；不存在和跨空间统一映射为配置无效。
    prompt = repository.get_prompt_version(
        context.workspace_id,
        configuration.prompt_version_id,
        for_share=True,
    )
    output_schema = repository.get_output_schema_version(
        context.workspace_id,
        configuration.output_schema_version_id,
        for_share=True,
    )
    if (
        prompt is None
        or output_schema is None
        or prompt.prompt_hash != text_digest(prompt.template)
        or output_schema.schema_hash != document_digest(output_schema.schema_document)
    ):
        raise AgentConfigurationInvalidError
    reject_credentials(prompt.template)
    reject_credentials(output_schema.schema_document)

    # 2. 知识范围冻结资源集合，但每次写草稿和申请候选仍复核当前状态与调用者权限。
    for scope_id in configuration.knowledge_scope_version_ids:
        scope = repository.get_knowledge_scope_version(
            context.workspace_id,
            scope_id,
            for_share=True,
        )
        if scope is None or scope.scope_hash != scope_digest(scope.knowledge_base_ids):
            raise AgentConfigurationInvalidError
        knowledge_bases = repository.get_knowledge_bases(
            context.workspace_id,
            scope.knowledge_base_ids,
            for_share=True,
        )
        if len(knowledge_bases) != len(scope.knowledge_base_ids) or any(
            not knowledge_base_is_accessible(context, value) for value in knowledge_bases
        ):
            raise AgentConfigurationInvalidError

    # 3. 模型和工作流必须引用当前发布指针；历史版本存在也不能继续进入新候选。
    runtime = repository.get_current_runtime_configuration(
        configuration.runtime_config_version_id,
        for_share=True,
    )
    if runtime is None or (
        configuration.limits.max_output_tokens > runtime.max_output_tokens
        or configuration.limits.max_execution_seconds * 1000 > runtime.total_timeout_ms
        or configuration.limits.max_cost_microunits > runtime.max_estimated_cost_microunits
    ):
        raise AgentConfigurationInvalidError
    if configuration.workflow_release_id is not None:
        workflow = repository.get_current_workflow_release(
            context.workspace_id,
            configuration.workflow_release_id,
            for_share=True,
        )
        if workflow is None or workflow.workflow_status != "active":
            raise AgentConfigurationInvalidError

    # 4. 安全实现必须与当前代码一致；工具身份、版本、权限和只读模式必须完全匹配目录。
    safety = repository.get_safety_policy_version(
        configuration.safety_policy_version_id,
        for_share=True,
    )
    if (
        safety is None
        or safety.status != "active"
        or safety.implementation_version != RAG_SAFETY_VERSION
    ):
        raise AgentConfigurationInvalidError
    for reference in configuration.read_only_tools:
        tool = repository.get_tool_definition(
            reference.tool_id,
            reference.tool_version,
            for_share=True,
        )
        if not _tool_matches(reference, tool):
            raise AgentConfigurationInvalidError


def reject_credentials(document: object) -> None:
    """扫描 Prompt 与结构化配置中的常见明文凭证形态。"""

    try:
        text = document if isinstance(document, str) else canonical_json(document).decode("utf-8")
    except AgentValidationError as error:
        raise AgentConfigurationInvalidError from error
    if any(pattern.search(text) is not None for pattern in CREDENTIAL_PATTERNS):
        raise AgentConfigurationInvalidError


def text_digest(value: str) -> str:
    """计算 UTF-8 文本摘要，用于复核不可变 Prompt 版本。"""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def document_digest(value: dict[str, object]) -> str:
    """计算规范 JSON 摘要，避免对象键顺序改变版本身份。"""

    return hashlib.sha256(canonical_json(value)).hexdigest()


def scope_digest(knowledge_base_ids: tuple[UUID, ...]) -> str:
    """按已排序知识库身份计算范围摘要，不复制知识正文。"""

    return hashlib.sha256(canonical_json([str(value) for value in knowledge_base_ids])).hexdigest()


def knowledge_base_is_accessible(
    context: RequestContext,
    knowledge_base: KnowledgeBaseReference,
) -> bool:
    """同时执行密级与可见范围校验，任一边界不满足即拒绝引用。"""

    if knowledge_base.status != "active" or (
        SECURITY_LEVEL_RANK[knowledge_base.default_security_level]
        > SECURITY_LEVEL_RANK[context.authorized_maximum_security_level]
    ):
        return False
    if knowledge_base.knowledge_base_id in context.authorized_resource_ids:
        return True
    if knowledge_base.default_visibility == "private":
        return knowledge_base.created_by_account_id == context.actor_id
    if knowledge_base.default_visibility == "workspace":
        return context.authorized_workspace
    return bool(knowledge_base.department_ids & context.authorized_department_ids)


def _tool_matches(
    reference: ReadOnlyToolDocument,
    tool: AgentToolDefinition | None,
) -> bool:
    return (
        tool is not None
        and reference.access_mode == "read"
        and tool.access_mode == "read"
        and reference.permission_code == tool.permission_code
    )
