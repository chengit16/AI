"""验证 P3-03 Agent 配置资源、跨模块引用与数据库不可变约束。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.agent_control.application.service import (
    AgentConfigurationInvalidError,
    AgentControlService,
)
from ai_platform_api.modules.agent_control.infrastructure.sqlalchemy import (
    SqlAlchemyAgentControlUnitOfWork,
)
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    agent_drafts,
    agent_knowledge_scope_versions,
    agent_output_schema_versions,
    agent_prompt_versions,
    agent_release_candidates,
    agent_safety_policy_versions,
    agent_tool_definitions,
    ai_runtime_config_publication,
    ai_runtime_config_versions,
    knowledge_bases,
    workflow_publications,
    workflow_versions,
    workflows,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext("9" * 32, "a" * 16)
RUNTIME_ID = UUID("a4000000-0000-4000-8000-000000000303")
FACT_NAMESPACE = UUID("ac000000-0000-4000-8000-000000000333")
TOOL_ID = UUID("a7000000-0000-4000-8000-000000000001")
SAFETY_POLICY_ID = UUID("a9000000-0000-4000-8000-000000000001")


@dataclass(frozen=True)
class RegisteredAccount:
    """保存测试账号和默认个人空间标识。"""

    account_id: UUID
    workspace_id: UUID


@dataclass(frozen=True)
class ConfigurationHarness:
    """集中持有临时 Schema 的注册、Agent 服务和数据库入口。"""

    engine: Engine
    sessions: sessionmaker[Session]
    registration: RegistrationService
    agents: AgentControlService


@dataclass(frozen=True)
class CrossModuleFacts:
    """保存一个测试空间的知识库和当前工作流版本身份。"""

    knowledge_base_id: UUID
    workflow_id: UUID
    workflow_version_id: UUID


@pytest.fixture(scope="module")
def configuration_database() -> Iterator[ConfigurationHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p303_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    migration = Config(str(ROOT / "alembic.ini"))
    migration.set_main_option("script_location", str(ROOT / "infra/migrations"))
    migration.set_main_option(
        "prepend_sys_path",
        f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
    )
    migration.set_main_option("sqlalchemy.url", database_url)
    migration.set_main_option("ai_platform_schema", schema)
    command.upgrade(migration, "head")

    engine = create_platform_engine(database_url, schema)
    sessions = create_session_factory(engine)
    try:
        yield ConfigurationHarness(
            engine,
            sessions,
            RegistrationService(
                SqlAlchemyIdentityReader(sessions),
                SqlAlchemyRegistrationUnitOfWork(sessions),
                Argon2idPasswordAdapter(),
            ),
            AgentControlService(SqlAlchemyAgentControlUnitOfWork(sessions)),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def register(harness: ConfigurationHarness, identity: str) -> RegisteredAccount:
    result = harness.registration.register(
        login_name=f"synthetic.agent.config.{identity}.{uuid4().hex}@example.com",
        display_name=f"合成 Agent 配置用户 {identity}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return RegisteredAccount(result.account_id, result.personal_workspace_id)


def context(account: RegisteredAccount) -> RequestContext:
    return replace(
        RequestContext.trusted(
            actor_id=account.account_id,
            user_id=account.account_id,
            workspace_id=account.workspace_id,
            trace=TRACE,
            authentication_method="browser_session",
        ),
        authorized_workspace=True,
        authorized_maximum_security_level="INTERNAL",
    )


def test_version_resources_are_naturally_idempotent_and_immutable(
    configuration_database: ConfigurationHarness,
) -> None:
    owner = register(configuration_database, "version-owner")
    owner_context = context(owner)
    first_prompt = configuration_database.agents.create_prompt_version(
        owner_context,
        name="合成客服 Prompt",
        template="仅依据当前空间的合成资料回答。",
    )
    repeated_prompt = configuration_database.agents.create_prompt_version(
        owner_context,
        name="合成客服 Prompt",
        template="仅依据当前空间的合成资料回答。",
    )
    output_schema = configuration_database.agents.create_output_schema_version(
        owner_context,
        name="合成客服输出",
        schema_document=_output_schema(),
    )
    scope = configuration_database.agents.create_knowledge_scope_version(
        owner_context,
        name="合成空知识范围",
        knowledge_base_ids=(),
    )

    assert repeated_prompt == first_prompt
    with configuration_database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(agent_prompt_versions)
                .where(agent_prompt_versions.c.workspace_id == owner.workspace_id)
            )
            == 1
        )
    # 版本事实、固定安全目录和工具目录都由数据库拒绝原地修改或删除。
    mutation_statements = (
        update(agent_prompt_versions)
        .where(agent_prompt_versions.c.prompt_version_id == first_prompt.prompt_version_id)
        .values(name="被篡改的 Prompt"),
        delete(agent_output_schema_versions).where(
            agent_output_schema_versions.c.output_schema_version_id
            == output_schema.output_schema_version_id
        ),
        update(agent_knowledge_scope_versions)
        .where(
            agent_knowledge_scope_versions.c.knowledge_scope_version_id
            == scope.knowledge_scope_version_id
        )
        .values(scope_hash="f" * 64),
        update(agent_safety_policy_versions)
        .where(agent_safety_policy_versions.c.safety_policy_version_id == SAFETY_POLICY_ID)
        .values(status="retired"),
        delete(agent_tool_definitions).where(agent_tool_definitions.c.tool_id == TOOL_ID),
    )
    for statement in mutation_statements:
        with pytest.raises(DBAPIError), configuration_database.sessions.begin() as session:
            session.execute(statement)


def test_valid_configuration_checks_all_published_references(
    configuration_database: ConfigurationHarness,
) -> None:
    owner = register(configuration_database, "valid-owner")
    owner_context = context(owner)
    facts = _seed_cross_module_facts(configuration_database.sessions, owner)
    agent_configuration = _configuration(configuration_database, owner_context, facts, "valid")

    agent, draft = configuration_database.agents.create_agent(
        owner_context,
        name="合成完整配置 Agent",
        description="覆盖已发布模型、知识、工作流、安全和只读工具",
        configuration=agent_configuration,
        idempotency_key="synthetic-agent-config-valid-create-0303",
    )
    candidate = configuration_database.agents.request_release_candidate(
        owner_context,
        agent_id=agent.agent_id,
        expected_revision=draft.revision,
        idempotency_key="synthetic-agent-config-valid-candidate-0303",
    )

    assert candidate.config_hash == draft.config_hash
    assert draft.configuration["workflow_release_id"] == str(facts.workflow_version_id)
    assert draft.configuration["read_only_tools"] == [
        {
            "access_mode": "read",
            "permission_code": "knowledge.document.read",
            "tool_id": str(TOOL_ID),
            "tool_version": 1,
        }
    ]


def test_draft_update_atomically_freezes_visible_knowledge_scope(
    configuration_database: ConfigurationHarness,
) -> None:
    """正式草稿入口保存可见范围，跨空间成员失败时不留下半成品版本。"""

    owner = register(configuration_database, "scope-binding-owner")
    outsider = register(configuration_database, "scope-binding-outsider")
    owner_context = context(owner)
    owner_facts = _seed_cross_module_facts(configuration_database.sessions, owner)
    outsider_facts = _seed_cross_module_facts(configuration_database.sessions, outsider)
    configuration = _configuration(
        configuration_database,
        owner_context,
        owner_facts,
        "scope-binding",
    )
    agent, draft = configuration_database.agents.create_agent(
        owner_context,
        name="合成知识范围绑定 Agent",
        description=None,
        configuration=configuration,
        idempotency_key="synthetic-agent-scope-create-0303",
    )

    updated = configuration_database.agents.update_draft(
        owner_context,
        agent_id=agent.agent_id,
        expected_revision=draft.revision,
        configuration=configuration,
        knowledge_scope_name="合成正式知识范围",
        knowledge_base_ids=(owner_facts.knowledge_base_id,),
        idempotency_key="synthetic-agent-scope-bind-0303",
    )
    listed = configuration_database.agents.list_agents(owner_context)

    assert updated.revision == 2
    assert len(listed) == 1
    assert listed[0][2][0].knowledge_base_ids == (owner_facts.knowledge_base_id,)
    assert updated.configuration["knowledge_scope_version_ids"] == [
        str(listed[0][2][0].knowledge_scope_version_id)
    ]

    with configuration_database.sessions() as session:
        version_count = session.scalar(
            select(func.count())
            .select_from(agent_knowledge_scope_versions)
            .where(agent_knowledge_scope_versions.c.workspace_id == owner.workspace_id)
        )
    with pytest.raises(AgentConfigurationInvalidError):
        configuration_database.agents.update_draft(
            owner_context,
            agent_id=agent.agent_id,
            expected_revision=updated.revision,
            configuration=updated.configuration,
            knowledge_scope_name="合成跨空间知识范围",
            knowledge_base_ids=(outsider_facts.knowledge_base_id,),
            idempotency_key="synthetic-agent-scope-cross-workspace-0303",
        )
    with configuration_database.sessions() as session:
        assert (
            session.scalar(
                select(agent_drafts.c.revision).where(agent_drafts.c.agent_id == agent.agent_id)
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(agent_knowledge_scope_versions)
                .where(agent_knowledge_scope_versions.c.workspace_id == owner.workspace_id)
            )
            == version_count
        )


def test_invalid_update_and_stale_candidate_do_not_advance_facts(
    configuration_database: ConfigurationHarness,
) -> None:
    owner = register(configuration_database, "failure-owner")
    owner_context = context(owner)
    facts = _seed_cross_module_facts(configuration_database.sessions, owner)
    valid_configuration = _configuration(
        configuration_database,
        owner_context,
        facts,
        "failure",
    )
    agent, draft = configuration_database.agents.create_agent(
        owner_context,
        name="合成失败关闭 Agent",
        description=None,
        configuration=valid_configuration,
        idempotency_key="synthetic-agent-config-failure-create-0303",
    )
    invalid_tool_configuration = {**valid_configuration}
    invalid_tool_configuration["read_only_tools"] = [
        {
            "tool_id": str(TOOL_ID),
            "tool_version": 1,
            "access_mode": "write",
            "permission_code": "knowledge.document.read",
        }
    ]

    with pytest.raises(AgentConfigurationInvalidError):
        configuration_database.agents.update_draft(
            owner_context,
            agent_id=agent.agent_id,
            expected_revision=draft.revision,
            configuration=invalid_tool_configuration,
            idempotency_key="synthetic-agent-config-write-tool-0303",
        )
    with configuration_database.sessions.begin() as session:
        session.execute(
            update(knowledge_bases)
            .where(knowledge_bases.c.knowledge_base_id == facts.knowledge_base_id)
            .values(status="deleted", deleted_at=datetime.now(UTC))
        )
    with pytest.raises(AgentConfigurationInvalidError):
        configuration_database.agents.request_release_candidate(
            owner_context,
            agent_id=agent.agent_id,
            expected_revision=draft.revision,
            idempotency_key="synthetic-agent-config-deleted-knowledge-0303",
        )

    with configuration_database.sessions() as session:
        assert (
            session.scalar(
                select(agent_drafts.c.revision).where(agent_drafts.c.agent_id == agent.agent_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(agent_release_candidates)
                .where(agent_release_candidates.c.agent_id == agent.agent_id)
            )
            == 0
        )


def test_cross_workspace_and_unpublished_runtime_references_are_rejected(
    configuration_database: ConfigurationHarness,
) -> None:
    owner = register(configuration_database, "boundary-owner")
    outsider = register(configuration_database, "boundary-outsider")
    owner_context = context(owner)
    outsider_context = context(outsider)
    facts = _seed_cross_module_facts(configuration_database.sessions, owner)
    owner_configuration = _configuration(
        configuration_database,
        owner_context,
        facts,
        "boundary",
    )
    outsider_prompt = configuration_database.agents.create_prompt_version(
        outsider_context,
        name="外部空间 Prompt",
        template="只使用另一个合成空间的资料。",
    )
    cross_workspace = {**owner_configuration}
    cross_workspace["prompt_version_id"] = str(outsider_prompt.prompt_version_id)

    with pytest.raises(AgentConfigurationInvalidError):
        configuration_database.agents.create_agent(
            owner_context,
            name="跨空间配置 Agent",
            description=None,
            configuration=cross_workspace,
            idempotency_key="synthetic-agent-config-cross-workspace-0303",
        )
    unpublished = {**owner_configuration}
    unpublished["runtime_config_version_id"] = str(uuid4())
    with pytest.raises(AgentConfigurationInvalidError):
        configuration_database.agents.create_agent(
            owner_context,
            name="未发布模型配置 Agent",
            description=None,
            configuration=unpublished,
            idempotency_key="synthetic-agent-config-unpublished-runtime-0303",
        )


def _configuration(
    harness: ConfigurationHarness,
    owner_context: RequestContext,
    facts: CrossModuleFacts,
    suffix: str,
) -> dict[str, object]:
    """创建工作空间版本资源并组装覆盖全部 P3-03 引用的配置。"""

    prompt = harness.agents.create_prompt_version(
        owner_context,
        name=f"合成 Agent Prompt {suffix}",
        template=f"仅使用已授权合成资料回答, 场景 {suffix}。",
    )
    scope = harness.agents.create_knowledge_scope_version(
        owner_context,
        name=f"合成知识范围 {suffix}",
        knowledge_base_ids=(facts.knowledge_base_id,),
    )
    output_schema = harness.agents.create_output_schema_version(
        owner_context,
        name=f"合成输出 {suffix}",
        schema_document=_output_schema(),
    )
    return {
        "prompt_version_id": str(prompt.prompt_version_id),
        "runtime_config_version_id": str(RUNTIME_ID),
        "knowledge_scope_version_ids": [str(scope.knowledge_scope_version_id)],
        "workflow_release_id": str(facts.workflow_version_id),
        "read_only_tools": [
            {
                "tool_id": str(TOOL_ID),
                "tool_version": 1,
                "access_mode": "read",
                "permission_code": "knowledge.document.read",
            }
        ],
        "output_schema_version_id": str(output_schema.output_schema_version_id),
        "safety_policy_version_id": str(SAFETY_POLICY_ID),
        "limits": {
            "max_input_tokens": 8192,
            "max_output_tokens": 2048,
            "max_execution_seconds": 60,
            "max_cost_microunits": 500_000,
        },
    }


def _seed_cross_module_facts(
    sessions: sessionmaker[Session],
    owner: RegisteredAccount,
) -> CrossModuleFacts:
    """为一个工作空间创建当前模型、知识库和当前工作流发布事实。"""

    now = datetime.now(UTC)
    knowledge_base_id = uuid5(FACT_NAMESPACE, f"knowledge:{owner.workspace_id}")
    workflow_id = uuid5(FACT_NAMESPACE, f"workflow:{owner.workspace_id}")
    workflow_version_id = uuid5(FACT_NAMESPACE, f"workflow-version:{owner.workspace_id}")
    with sessions.begin() as session:
        # 每个测试使用独立临时 Schema，但全局发布事实只需写入一次并绑定首个合成账号。
        if session.scalar(select(func.count()).select_from(ai_runtime_config_versions)) == 0:
            session.execute(
                insert(ai_runtime_config_versions).values(
                    runtime_config_version_id=RUNTIME_ID,
                    version_number=1,
                    display_name="合成 P3-03 运行配置",
                    content_hash="c" * 64,
                    system_prompt_template="只使用合成资料。",
                    system_prompt_hash="d" * 64,
                    component_versions={"safety": "rag-safety-v2"},
                    attempt_timeout_ms=1_000,
                    total_timeout_ms=120_000,
                    max_attempts_per_route=1,
                    max_prompt_characters=100_000,
                    max_output_tokens=4096,
                    max_response_characters=100_000,
                    circuit_failure_threshold=3,
                    circuit_recovery_ms=30_000,
                    rule_degradation_message=None,
                    max_estimated_cost_microunits=1_000_000,
                    created_by_account_id=owner.account_id,
                    created_at=now,
                )
            )
            session.execute(
                insert(ai_runtime_config_publication).values(
                    publication_key="current",
                    runtime_config_version_id=RUNTIME_ID,
                    generation=1,
                    published_by_account_id=owner.account_id,
                    published_at=now,
                )
            )
        session.execute(
            insert(knowledge_bases).values(
                knowledge_base_id=knowledge_base_id,
                workspace_id=owner.workspace_id,
                name=f"合成知识库 {owner.workspace_id}",
                description="只包含合成数据",
                default_visibility="workspace",
                department_ids=[],
                default_security_level="INTERNAL",
                status="active",
                created_by_account_id=owner.account_id,
                created_at=now,
                updated_at=now,
                deleted_at=None,
                version=1,
            )
        )
        session.execute(
            insert(workflows).values(
                workflow_id=workflow_id,
                workspace_id=owner.workspace_id,
                name=f"合成工作流 {owner.workspace_id}",
                description=None,
                status="active",
                created_by_account_id=owner.account_id,
                created_at=now,
                updated_at=now,
                version=1,
            )
        )
        session.execute(
            insert(workflow_versions).values(
                workflow_version_id=workflow_version_id,
                workflow_id=workflow_id,
                workspace_id=owner.workspace_id,
                version_number=1,
                source_draft_revision=1,
                graph={"schema_version": 1, "entry_node_id": "start", "nodes": [], "edges": []},
                graph_digest="e" * 64,
                published_by_account_id=owner.account_id,
                published_at=now,
            )
        )
        session.execute(
            insert(workflow_publications).values(
                workflow_id=workflow_id,
                workspace_id=owner.workspace_id,
                workflow_version_id=workflow_version_id,
                generation=1,
                published_by_account_id=owner.account_id,
                published_at=now,
            )
        )
    return CrossModuleFacts(knowledge_base_id, workflow_id, workflow_version_id)


def _output_schema() -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
    }
