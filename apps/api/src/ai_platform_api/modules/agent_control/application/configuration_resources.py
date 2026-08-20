"""创建内容寻址的 Agent Prompt、知识范围和输出 Schema 版本。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid5

from ai_platform_backend.integration.domain import AuditRecord
from ai_platform_backend.safety import RAG_SAFETY_VERSION
from jsonschema import Draft202012Validator, SchemaError

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_control.application.configuration import (
    AgentConfigurationDocument,
    document_digest,
    knowledge_base_is_accessible,
    parse_agent_configuration,
    reject_credentials,
    scope_digest,
    text_digest,
)
from ai_platform_api.modules.agent_control.application.errors import (
    AgentConfigurationInvalidError,
)
from ai_platform_api.modules.agent_control.application.support import canonical_json
from ai_platform_api.modules.agent_control.domain.configuration import (
    AgentKnowledgeScopeVersion,
    AgentOutputSchemaVersion,
    AgentPromptVersion,
)
from ai_platform_api.modules.agent_control.domain.models import AgentControlUnitOfWork

CONFIGURATION_NAMESPACE = UUID("ac000000-0000-4000-8000-000000000303")
STARTER_PROMPT_NAME = "Agent 基础回答 Prompt"
STARTER_KNOWLEDGE_SCOPE_NAME = "Agent 基础空知识范围"
STARTER_OUTPUT_SCHEMA_NAME = "Agent 基础回答 Schema"
STARTER_PROMPT_TEMPLATE = (
    "你是工作空间中的基础 Agent。只使用当前配置明确授权的知识和工具回答问题。"
    "无法依据已授权信息确认时必须明确说明无法确认。输出必须符合已绑定的 JSON Schema。"
)


def create_prompt_version(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    name: str,
    template: str,
) -> AgentPromptVersion:
    """以内容寻址方式创建工作空间 Prompt 版本，重复输入返回原事实。"""

    # 1. 先在事务外完成凭证扫描和确定性身份计算，无效正文不会占用数据库锁。
    normalized_name = _resource_name(name)
    normalized_template = template.strip()
    if not normalized_template or len(normalized_template) > 32_000:
        raise AgentConfigurationInvalidError
    reject_credentials(normalized_template)
    content_hash = text_digest(normalized_template)
    version_id = _version_id("prompt", context.workspace_id, normalized_name, content_hash)
    now = datetime.now(UTC)
    version = AgentPromptVersion(
        prompt_version_id=version_id,
        workspace_id=context.workspace_id,
        name=normalized_name,
        template=normalized_template,
        prompt_hash=content_hash,
        created_by_account_id=_configuration_account(context),
        created_at=now,
    )
    # 2. 首次创建写入脱敏审计；同内容重试只读取原版本，不制造重复事实。
    with unit_of_work_factory as unit_of_work:
        if unit_of_work.configuration.add_prompt_version(version):
            _record_configuration_version(
                unit_of_work,
                context,
                action="agent.prompt_version.created",
                resource_type="agent_prompt_version",
                resource_id=version_id,
                content_hash=content_hash,
                occurred_at=now,
            )
            unit_of_work.commit()
            return version
        existing = unit_of_work.configuration.get_prompt_version(
            context.workspace_id,
            version_id,
        )
        if existing is None:
            raise AgentConfigurationInvalidError
        return existing


def create_knowledge_scope_version(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    name: str,
    knowledge_base_ids: tuple[UUID, ...],
) -> AgentKnowledgeScopeVersion:
    """冻结已授权知识库集合；空集合可表达明确不检索知识。"""

    version = prepare_knowledge_scope_version(
        context,
        name=name,
        knowledge_base_ids=knowledge_base_ids,
    )
    with unit_of_work_factory as unit_of_work:
        persisted = persist_knowledge_scope_version(unit_of_work, context, version)
        unit_of_work.commit()
        return persisted


def prepare_knowledge_scope_version(
    context: RequestContext,
    *,
    name: str,
    knowledge_base_ids: tuple[UUID, ...],
) -> AgentKnowledgeScopeVersion:
    """规范化知识库集合并生成内容寻址版本，尚不写入数据库。"""

    # 排序后计算集合身份，调用方提交顺序不会改变版本摘要。
    normalized_name = _resource_name(name)
    if len(knowledge_base_ids) > 50 or len(knowledge_base_ids) != len(set(knowledge_base_ids)):
        raise AgentConfigurationInvalidError
    normalized_ids = tuple(sorted(knowledge_base_ids, key=lambda value: value.int))
    content_hash = scope_digest(normalized_ids)
    version_id = _version_id("knowledge-scope", context.workspace_id, normalized_name, content_hash)
    now = datetime.now(UTC)
    version = AgentKnowledgeScopeVersion(
        knowledge_scope_version_id=version_id,
        workspace_id=context.workspace_id,
        name=normalized_name,
        knowledge_base_ids=normalized_ids,
        scope_hash=content_hash,
        created_by_account_id=_configuration_account(context),
        created_at=now,
    )
    return version


def persist_knowledge_scope_version(
    unit_of_work: AgentControlUnitOfWork,
    context: RequestContext,
    version: AgentKnowledgeScopeVersion,
) -> AgentKnowledgeScopeVersion:
    """在调用方事务中复核并写入范围，使草稿更新可以保持原子性。"""

    # 共享锁下复核每个知识库的状态、密级与可见范围，防止越权集合被固化。
    knowledge_bases = unit_of_work.configuration.get_knowledge_bases(
        context.workspace_id,
        version.knowledge_base_ids,
        for_share=True,
    )
    if len(knowledge_bases) != len(version.knowledge_base_ids) or any(
        not knowledge_base_is_accessible(context, value) for value in knowledge_bases
    ):
        raise AgentConfigurationInvalidError
    # 版本和条目一次插入；重复内容返回原版本并保持审计幂等。
    if unit_of_work.configuration.add_knowledge_scope_version(version):
        _record_configuration_version(
            unit_of_work,
            context,
            action="agent.knowledge_scope_version.created",
            resource_type="agent_knowledge_scope_version",
            resource_id=version.knowledge_scope_version_id,
            content_hash=version.scope_hash,
            occurred_at=version.created_at,
        )
        return version
    existing = unit_of_work.configuration.get_knowledge_scope_version(
        context.workspace_id,
        version.knowledge_scope_version_id,
    )
    if existing is None:
        raise AgentConfigurationInvalidError
    return existing


def create_output_schema_version(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    name: str,
    schema_document: dict[str, object],
) -> AgentOutputSchemaVersion:
    """校验并冻结 Draft 2020-12 输出 Schema，禁止凭证结构进入版本事实。"""

    # 1. 校验标准 Schema、大小和凭证边界后生成确定性版本身份。
    normalized_name = _resource_name(name)
    _validate_output_schema(schema_document)
    reject_credentials(schema_document)
    content_hash = document_digest(schema_document)
    version_id = _version_id("output-schema", context.workspace_id, normalized_name, content_hash)
    now = datetime.now(UTC)
    version = AgentOutputSchemaVersion(
        output_schema_version_id=version_id,
        workspace_id=context.workspace_id,
        name=normalized_name,
        schema_document=schema_document,
        schema_hash=content_hash,
        created_by_account_id=_configuration_account(context),
        created_at=now,
    )
    # 2. 首次插入保存脱敏审计，重复请求读取同一不可变版本。
    with unit_of_work_factory as unit_of_work:
        if unit_of_work.configuration.add_output_schema_version(version):
            _record_configuration_version(
                unit_of_work,
                context,
                action="agent.output_schema_version.created",
                resource_type="agent_output_schema_version",
                resource_id=version_id,
                content_hash=content_hash,
                occurred_at=now,
            )
            unit_of_work.commit()
            return version
        existing = unit_of_work.configuration.get_output_schema_version(
            context.workspace_id,
            version_id,
        )
        if existing is None:
            raise AgentConfigurationInvalidError
        return existing


def create_starter_configuration(
    unit_of_work: AgentControlUnitOfWork,
    context: RequestContext,
) -> tuple[AgentConfigurationDocument, dict[str, object], str]:
    """在 Agent 创建事务中生成可直接编辑、仍满足完整约束的基础配置。"""

    # 1. 基础配置只能绑定当前发布 Runtime 和当前实现匹配的安全策略，缺失时失败关闭。
    account_id = _configuration_account(context)
    runtime = unit_of_work.configuration.get_published_runtime_configuration(for_share=True)
    safety = unit_of_work.configuration.get_active_safety_policy_version(
        RAG_SAFETY_VERSION,
        for_share=True,
    )
    if runtime is None or safety is None or runtime.total_timeout_ms < 1_000:
        raise AgentConfigurationInvalidError

    # 2. 三类资源按内容寻址写入；重复请求复用原版本，且不在审计中保存正文或 Schema。
    now = datetime.now(UTC)
    prompt_hash = text_digest(STARTER_PROMPT_TEMPLATE)
    prompt = AgentPromptVersion(
        prompt_version_id=_version_id(
            "prompt",
            context.workspace_id,
            STARTER_PROMPT_NAME,
            prompt_hash,
        ),
        workspace_id=context.workspace_id,
        name=STARTER_PROMPT_NAME,
        template=STARTER_PROMPT_TEMPLATE,
        prompt_hash=prompt_hash,
        created_by_account_id=account_id,
        created_at=now,
    )
    scope_hash = scope_digest(())
    scope = AgentKnowledgeScopeVersion(
        knowledge_scope_version_id=_version_id(
            "knowledge-scope",
            context.workspace_id,
            STARTER_KNOWLEDGE_SCOPE_NAME,
            scope_hash,
        ),
        workspace_id=context.workspace_id,
        name=STARTER_KNOWLEDGE_SCOPE_NAME,
        knowledge_base_ids=(),
        scope_hash=scope_hash,
        created_by_account_id=account_id,
        created_at=now,
    )
    schema_document = _starter_output_schema()
    schema_hash = document_digest(schema_document)
    output_schema = AgentOutputSchemaVersion(
        output_schema_version_id=_version_id(
            "output-schema",
            context.workspace_id,
            STARTER_OUTPUT_SCHEMA_NAME,
            schema_hash,
        ),
        workspace_id=context.workspace_id,
        name=STARTER_OUTPUT_SCHEMA_NAME,
        schema_document=schema_document,
        schema_hash=schema_hash,
        created_by_account_id=account_id,
        created_at=now,
    )
    _add_starter_resource_versions(unit_of_work, context, prompt, scope, output_schema, now)

    # 3. 最终草稿保存普通完整配置，不保留 starter 标记，也不绕过 P3-03 的严格解析。
    configuration: dict[str, object] = {
        "prompt_version_id": str(prompt.prompt_version_id),
        "runtime_config_version_id": str(runtime.runtime_config_version_id),
        "knowledge_scope_version_ids": [str(scope.knowledge_scope_version_id)],
        "workflow_release_id": None,
        "read_only_tools": [],
        "output_schema_version_id": str(output_schema.output_schema_version_id),
        "safety_policy_version_id": str(safety.safety_policy_version_id),
        "limits": {
            "max_input_tokens": max(1, min(8_192, runtime.max_prompt_characters)),
            "max_output_tokens": min(1_024, runtime.max_output_tokens),
            "max_execution_seconds": min(60, runtime.total_timeout_ms // 1_000),
            "max_cost_microunits": min(500_000, runtime.max_estimated_cost_microunits),
        },
    }
    return parse_agent_configuration(configuration)


def _add_starter_resource_versions(
    unit_of_work: AgentControlUnitOfWork,
    context: RequestContext,
    prompt: AgentPromptVersion,
    scope: AgentKnowledgeScopeVersion,
    output_schema: AgentOutputSchemaVersion,
    occurred_at: datetime,
) -> None:
    """幂等写入基础资源，并仅为本事务首次插入的事实记录审计。"""

    resources = (
        (
            unit_of_work.configuration.add_prompt_version(prompt),
            "agent.prompt_version.created",
            "agent_prompt_version",
            prompt.prompt_version_id,
            prompt.prompt_hash,
        ),
        (
            unit_of_work.configuration.add_knowledge_scope_version(scope),
            "agent.knowledge_scope_version.created",
            "agent_knowledge_scope_version",
            scope.knowledge_scope_version_id,
            scope.scope_hash,
        ),
        (
            unit_of_work.configuration.add_output_schema_version(output_schema),
            "agent.output_schema_version.created",
            "agent_output_schema_version",
            output_schema.output_schema_version_id,
            output_schema.schema_hash,
        ),
    )
    for inserted, action, resource_type, resource_id, content_hash in resources:
        if inserted:
            _record_configuration_version(
                unit_of_work,
                context,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                content_hash=content_hash,
                occurred_at=occurred_at,
            )


def _starter_output_schema() -> dict[str, object]:
    """返回允许基础 Agent 输出答案文本的最小 Draft 2020-12 Schema。"""

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
    }


def _configuration_account(context: RequestContext) -> UUID:
    if (
        context.user_id is None
        or context.user_id != context.actor_id
        or context.authentication_method != "browser_session"
        or not context.authorized_workspace
    ):
        raise AgentConfigurationInvalidError
    return context.user_id


def _resource_name(value: str) -> str:
    normalized = value.strip()
    if not 1 <= len(normalized) <= 120:
        raise AgentConfigurationInvalidError
    return normalized


def _version_id(kind: str, workspace_id: UUID, name: str, content_hash: str) -> UUID:
    return uuid5(CONFIGURATION_NAMESPACE, f"{kind}:{workspace_id}:{name}:{content_hash}")


def _validate_output_schema(schema_document: dict[str, object]) -> None:
    if len(canonical_json(schema_document)) > 64 * 1024 or schema_document.get("type") != "object":
        raise AgentConfigurationInvalidError
    try:
        Draft202012Validator.check_schema(schema_document)
    except SchemaError as error:
        raise AgentConfigurationInvalidError from error


def _record_configuration_version(
    unit_of_work: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    action: str,
    resource_type: str,
    resource_id: UUID,
    content_hash: str,
    occurred_at: datetime,
) -> None:
    """只记录版本身份与摘要，Prompt、Schema 和知识集合不进入审计详情。"""

    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid5(CONFIGURATION_NAMESPACE, f"audit:{context.request_id}:{resource_id}"),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes={"content_hash": content_hash},
        )
    )
