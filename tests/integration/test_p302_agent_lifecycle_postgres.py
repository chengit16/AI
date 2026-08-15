"""验证 P3-02 Agent 生命周期在真实 PostgreSQL 中的隔离、幂等和不可变约束。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.agent_control.application.service import (
    AgentControlService,
    AgentNotFoundError,
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
    agent_control_requests,
    agent_draft_revisions,
    agent_release_candidates,
    agent_releases,
    ai_runtime_config_publication,
    ai_runtime_config_versions,
    audit_records,
    outbox_events,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext("5" * 32, "6" * 16)


@dataclass(frozen=True)
class RegisteredAccount:
    """保存测试账号和默认个人空间标识。"""

    account_id: UUID
    workspace_id: UUID


@dataclass(frozen=True)
class AgentHarness:
    """集中持有临时 Schema 的注册、Agent 服务和数据库入口。"""

    engine: Engine
    sessions: sessionmaker[Session]
    registration: RegistrationService
    agents: AgentControlService


@pytest.fixture(scope="module")
def agent_database() -> Iterator[AgentHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p302_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "infra/migrations"))
    config.set_main_option(
        "prepend_sys_path",
        f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
    )
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("ai_platform_schema", schema)
    command.upgrade(config, "head")

    engine = create_platform_engine(database_url, schema)
    sessions = create_session_factory(engine)
    try:
        yield AgentHarness(
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


def register(harness: AgentHarness, identity: str) -> RegisteredAccount:
    result = harness.registration.register(
        login_name=f"synthetic.agent.{identity}.{uuid4().hex}@example.com",
        display_name=f"合成 Agent 用户 {identity}",
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
    )


def configuration(
    harness: AgentHarness,
    owner_context: RequestContext,
    version: str,
) -> dict[str, object]:
    """创建真实不可变资源版本，并返回通过 P3-03 校验的合成配置。"""

    runtime_config_id = _ensure_runtime_config(harness.sessions, owner_context.actor_id)
    prompt = harness.agents.create_prompt_version(
        owner_context,
        name=f"合成 Prompt {version}",
        template=f"仅使用授权的合成资料回答, 版本 {version}。",
    )
    scope = harness.agents.create_knowledge_scope_version(
        owner_context,
        name="合成空知识范围",
        knowledge_base_ids=(),
    )
    output_schema = harness.agents.create_output_schema_version(
        owner_context,
        name="合成回答输出",
        schema_document={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "properties": {"answer": {"type": "string"}},
        },
    )
    return {
        "prompt_version_id": str(prompt.prompt_version_id),
        "runtime_config_version_id": str(runtime_config_id),
        "knowledge_scope_version_ids": [str(scope.knowledge_scope_version_id)],
        "workflow_release_id": None,
        "read_only_tools": [],
        "output_schema_version_id": str(output_schema.output_schema_version_id),
        "safety_policy_version_id": "a9000000-0000-4000-8000-000000000001",
        "limits": {
            "max_input_tokens": 8192,
            "max_output_tokens": 2048,
            "max_execution_seconds": 60,
            "max_cost_microunits": 500_000,
        },
    }


def test_lifecycle_history_idempotency_and_transactional_evidence(
    agent_database: AgentHarness,
) -> None:
    owner = register(agent_database, "owner")
    owner_context = context(owner)
    first_configuration = configuration(agent_database, owner_context, "v1")
    second_configuration = configuration(agent_database, owner_context, "v2")
    agent, draft = agent_database.agents.create_agent(
        owner_context,
        name="合成 PostgreSQL Agent",
        description="只使用合成数据",
        configuration=first_configuration,
        idempotency_key="synthetic-postgres-agent-create-0302",
    )
    repeated_agent, repeated_draft = agent_database.agents.create_agent(
        owner_context,
        name="合成 PostgreSQL Agent",
        description="只使用合成数据",
        configuration=first_configuration,
        idempotency_key="synthetic-postgres-agent-create-0302",
    )
    updated = agent_database.agents.update_draft(
        owner_context,
        agent_id=agent.agent_id,
        expected_revision=draft.revision,
        configuration=second_configuration,
        idempotency_key="synthetic-postgres-agent-update-0302",
    )
    repeated_updated = agent_database.agents.update_draft(
        owner_context,
        agent_id=agent.agent_id,
        expected_revision=draft.revision,
        configuration=second_configuration,
        idempotency_key="synthetic-postgres-agent-update-0302",
    )
    candidate = agent_database.agents.request_release_candidate(
        owner_context,
        agent_id=agent.agent_id,
        expected_revision=updated.revision,
        idempotency_key="synthetic-postgres-agent-candidate-0302",
    )

    assert (repeated_agent, repeated_draft) == (agent, draft)
    assert repeated_updated == updated
    assert candidate.draft_revision == updated.revision
    with agent_database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(agent_draft_revisions)) == 2
        assert session.scalar(select(func.count()).select_from(agent_release_candidates)) == 1
        assert session.scalar(select(func.count()).select_from(agent_control_requests)) == 3
        assert (
            session.scalar(
                select(func.count())
                .select_from(audit_records)
                .where(audit_records.c.resource_type == "agent_definition")
            )
            == 3
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(outbox_events)
                .where(
                    outbox_events.c.event_type.in_(
                        ("agent.draft.changed", "agent.release.requested")
                    )
                )
            )
            == 3
        )


def test_cross_workspace_reads_fail_closed(agent_database: AgentHarness) -> None:
    owner = register(agent_database, "isolation-owner")
    outsider = register(agent_database, "isolation-outsider")
    owner_context = context(owner)
    agent, _ = agent_database.agents.create_agent(
        owner_context,
        name="合成隔离 Agent",
        description=None,
        configuration=configuration(agent_database, owner_context, "isolation-v1"),
        idempotency_key="synthetic-postgres-agent-isolation-0302",
    )

    with pytest.raises(AgentNotFoundError):
        agent_database.agents.get_agent(context(outsider), agent_id=agent.agent_id)


def test_concurrent_create_replays_one_committed_fact(agent_database: AgentHarness) -> None:
    owner = register(agent_database, "concurrent-owner")
    owner_context = context(owner)
    agent_configuration = configuration(agent_database, owner_context, "concurrent-v1")

    def create() -> tuple[object, object]:
        return agent_database.agents.create_agent(
            owner_context,
            name="合成并发 Agent",
            description=None,
            configuration=agent_configuration,
            idempotency_key="synthetic-postgres-agent-concurrent-0302",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: create(), range(2)))

    assert results[0] == results[1]
    with agent_database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(agent_control_requests)
                .where(
                    agent_control_requests.c.workspace_id == owner.workspace_id,
                    agent_control_requests.c.idempotency_key
                    == "synthetic-postgres-agent-concurrent-0302",
                )
            )
            == 1
        )


def test_direct_release_and_history_mutation_are_rejected_by_database(
    agent_database: AgentHarness,
) -> None:
    owner = register(agent_database, "immutable-owner")
    owner_context = context(owner)
    agent_configuration = configuration(agent_database, owner_context, "immutable-v1")
    agent, draft = agent_database.agents.create_agent(
        owner_context,
        name="合成不可变 Agent",
        description=None,
        configuration=agent_configuration,
        idempotency_key="synthetic-postgres-agent-immutable-0302",
    )
    candidate = agent_database.agents.request_release_candidate(
        owner_context,
        agent_id=agent.agent_id,
        expected_revision=draft.revision,
        idempotency_key="synthetic-postgres-agent-immutable-candidate-0302",
    )
    runtime_config_id = _ensure_runtime_config(agent_database.sessions, owner.account_id)
    snapshot = {
        "snapshot_schema_version": 1,
        "source_draft_id": str(draft.draft_id),
        "source_draft_revision": draft.revision,
        "configuration": draft.configuration,
    }
    # P3-06 起自定义 Release 必须绑定完整评估和审批证据，早期直接插入路径应失败关闭。
    with pytest.raises(DBAPIError), agent_database.sessions.begin() as session:
        session.execute(
            insert(agent_releases).values(
                release_id=uuid4(),
                agent_id=agent.agent_id,
                workspace_id=owner.workspace_id,
                version=1,
                status="released",
                runtime_config_version_id=runtime_config_id,
                config_hash=draft.config_hash,
                released_by_account_id=owner.account_id,
                released_at=draft.updated_at,
                release_kind="custom",
                candidate_id=candidate.candidate_id,
                candidate_hash=candidate.candidate_hash,
                snapshot=snapshot,
                snapshot_hash="8" * 64,
            )
        )
    with pytest.raises(DBAPIError), agent_database.sessions.begin() as session:
        session.execute(
            update(agent_draft_revisions)
            .where(
                agent_draft_revisions.c.draft_id == draft.draft_id,
                agent_draft_revisions.c.revision == 1,
            )
            .values(config_hash="9" * 64)
        )
    with pytest.raises(DBAPIError), agent_database.sessions.begin() as session:
        session.execute(
            update(agent_release_candidates)
            .where(agent_release_candidates.c.candidate_id == candidate.candidate_id)
            .values(draft_revision=2)
        )


def _ensure_runtime_config(sessions: sessionmaker[Session], account_id: UUID) -> UUID:
    """创建并发布模块级唯一合成运行配置，重复调用返回当前版本。"""

    with sessions() as session:
        current = session.scalar(
            select(ai_runtime_config_publication.c.runtime_config_version_id).where(
                ai_runtime_config_publication.c.publication_key == "current"
            )
        )
        if current is not None:
            return cast(UUID, current)
    runtime_config_version_id = UUID("a4000000-0000-4000-8000-000000000302")
    now = datetime.now(UTC)
    with sessions.begin() as session:
        session.execute(
            insert(ai_runtime_config_versions).values(
                runtime_config_version_id=runtime_config_version_id,
                version_number=1,
                display_name="合成 P3-02 运行配置",
                content_hash="a" * 64,
                system_prompt_template="只使用合成资料。",
                system_prompt_hash="b" * 64,
                component_versions={"retrieval": "synthetic-v1"},
                attempt_timeout_ms=500,
                total_timeout_ms=120_000,
                max_attempts_per_route=1,
                max_prompt_characters=4_000,
                max_output_tokens=4096,
                max_response_characters=8_000,
                circuit_failure_threshold=3,
                circuit_recovery_ms=30_000,
                rule_degradation_message=None,
                max_estimated_cost_microunits=5_000_000,
                created_by_account_id=account_id,
                created_at=now,
            )
        )
        session.execute(
            insert(ai_runtime_config_publication).values(
                publication_key="current",
                runtime_config_version_id=runtime_config_version_id,
                generation=1,
                published_by_account_id=account_id,
                published_at=now,
            )
        )
    return runtime_config_version_id
