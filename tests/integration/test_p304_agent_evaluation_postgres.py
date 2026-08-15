"""验证 P3-04 Agent 固定测试、候选门禁和数据库不可变约束。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.agent_control.application.service import (
    AgentControlService,
    AgentNotFoundError,
    AgentTestGateFailedError,
    AgentValidationError,
)
from ai_platform_api.modules.agent_control.domain.evaluation import (
    REQUIRED_EVALUATION_CHECKS,
    AgentEvaluationDatasetVersion,
    AgentEvaluationObservation,
    AgentEvaluationRequest,
)
from ai_platform_api.modules.agent_control.domain.models import AgentReleaseCandidate
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
    agent_evaluation_case_results,
    agent_evaluation_dataset_versions,
    agent_evaluation_runs,
    agent_release_candidates,
    ai_runtime_config_publication,
    ai_runtime_config_versions,
    outbox_events,
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
TRACE = TraceContext("b" * 32, "c" * 16)
RUNTIME_ID = UUID("a4000000-0000-4000-8000-000000000304")
SAFETY_POLICY_ID = UUID("a9000000-0000-4000-8000-000000000001")


@dataclass(frozen=True)
class RegisteredAccount:
    """保存测试账号和默认个人空间标识。"""

    account_id: UUID
    workspace_id: UUID


@dataclass(frozen=True)
class EvaluationHarness:
    """集中持有临时 Schema 的注册服务和数据库入口。"""

    engine: Engine
    sessions: sessionmaker[Session]
    registration: RegistrationService


class SyntheticEvaluationExecutor:
    """按固定模式返回确定性合成观测，不调用模型或外部服务。"""

    evaluator_version = "synthetic-rules-v1"

    def __init__(self, mode: str = "passed") -> None:
        self.mode = mode
        self.calls = 0

    def evaluate(
        self,
        request: AgentEvaluationRequest,
    ) -> tuple[AgentEvaluationObservation, ...]:
        self.calls += 1
        observations = []
        for case in request.cases:
            if self.mode == "failed" and case.check_code == "output_contract":
                continue
            if self.mode == "failed" and case.check_code == "authorization":
                observations.append(
                    AgentEvaluationObservation(
                        case.case_id,
                        "failed",
                        0,
                        8,
                        {"evidence_code": "synthetic_authorization_denied"},
                    )
                )
                continue
            observations.append(
                AgentEvaluationObservation(
                    case.case_id,
                    "passed",
                    9_000 if case.check_code == "functional" else 10_000,
                    8,
                    {"evidence_code": f"synthetic_{case.check_code}_passed"},
                )
            )
        return tuple(observations)


@pytest.fixture(scope="module")
def evaluation_database() -> Iterator[EvaluationHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p304_test_{uuid4().hex}"
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
        yield EvaluationHarness(
            engine,
            sessions,
            RegistrationService(
                SqlAlchemyIdentityReader(sessions),
                SqlAlchemyRegistrationUnitOfWork(sessions),
                Argon2idPasswordAdapter(),
            ),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def register(harness: EvaluationHarness, identity: str) -> RegisteredAccount:
    result = harness.registration.register(
        login_name=f"synthetic.agent.evaluation.{identity}.{uuid4().hex}@example.com",
        display_name=f"合成 Agent 评估用户 {identity}",
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


def service(
    harness: EvaluationHarness,
    executor: SyntheticEvaluationExecutor | None = None,
) -> AgentControlService:
    return AgentControlService(
        SqlAlchemyAgentControlUnitOfWork(harness.sessions),
        executor,
    )


def test_dataset_is_idempotent_and_version_content_cannot_drift(
    evaluation_database: EvaluationHarness,
) -> None:
    owner = register(evaluation_database, "dataset")
    agents = service(evaluation_database)
    first = agents.create_evaluation_dataset(
        context(owner),
        name="合成发布测试集",
        dataset_version="p304-release-gate-v1",
        cases=dataset_cases(),
    )
    repeated = agents.create_evaluation_dataset(
        context(owner),
        name="合成发布测试集",
        dataset_version="p304-release-gate-v1",
        cases=tuple(reversed(dataset_cases())),
    )

    assert repeated == first
    changed = list(dataset_cases())
    changed[0] = {**changed[0], "timeout_ms": 3_000}
    with pytest.raises(AgentValidationError):
        agents.create_evaluation_dataset(
            context(owner),
            name="合成发布测试集",
            dataset_version="p304-release-gate-v1",
            cases=tuple(changed),
        )


def test_passing_evaluation_is_replayable_and_advances_candidate(
    evaluation_database: EvaluationHarness,
) -> None:
    owner = register(evaluation_database, "passed")
    executor = SyntheticEvaluationExecutor()
    agents = service(evaluation_database, executor)
    candidate, dataset = create_candidate_and_dataset(evaluation_database, agents, owner, "passed")

    report = agents.run_evaluation(
        context(owner),
        candidate_id=candidate.candidate_id,
        dataset_version_id=dataset.dataset_version_id,
    )
    repeated = agents.run_evaluation(
        context(owner),
        candidate_id=candidate.candidate_id,
        dataset_version_id=dataset.dataset_version_id,
    )

    assert repeated == report
    assert executor.calls == 1
    assert report.run.status == "passed"
    assert report.run.evidence_level == "core_functional"
    assert {item.status for item in report.checks} == {"passed"}
    assert (
        agents.require_passing_evaluation(
            context(owner),
            candidate_id=candidate.candidate_id,
        )
        == report
    )
    with evaluation_database.sessions() as session:
        stored_status = session.scalar(
            select(agent_release_candidates.c.status).where(
                agent_release_candidates.c.candidate_id == candidate.candidate_id
            )
        )
        assert stored_status == "ready_for_approval"
        assert (
            session.scalar(
                select(func.count())
                .select_from(outbox_events)
                .where(outbox_events.c.event_type == "agent.test.completed")
            )
            or 0
        ) >= 1


def test_failed_and_skipped_checks_block_the_release_gate(
    evaluation_database: EvaluationHarness,
) -> None:
    owner = register(evaluation_database, "failed")
    agents = service(evaluation_database, SyntheticEvaluationExecutor("failed"))
    candidate, dataset = create_candidate_and_dataset(evaluation_database, agents, owner, "failed")

    report = agents.run_evaluation(
        context(owner),
        candidate_id=candidate.candidate_id,
        dataset_version_id=dataset.dataset_version_id,
    )

    assert report.run.status == "failed"
    assert report.run.failed_cases == 1
    assert report.run.skipped_cases == 1
    with pytest.raises(AgentTestGateFailedError):
        agents.require_passing_evaluation(
            context(owner),
            candidate_id=candidate.candidate_id,
        )
    with evaluation_database.sessions() as session:
        assert (
            session.scalar(
                select(agent_release_candidates.c.status).where(
                    agent_release_candidates.c.candidate_id == candidate.candidate_id
                )
            )
            == "test_failed"
        )


def test_reports_are_workspace_isolated_and_database_immutable(
    evaluation_database: EvaluationHarness,
) -> None:
    owner = register(evaluation_database, "immutable-owner")
    outsider = register(evaluation_database, "immutable-outsider")
    agents = service(evaluation_database, SyntheticEvaluationExecutor())
    candidate, dataset = create_candidate_and_dataset(
        evaluation_database,
        agents,
        owner,
        "immutable",
    )
    report = agents.run_evaluation(
        context(owner),
        candidate_id=candidate.candidate_id,
        dataset_version_id=dataset.dataset_version_id,
    )

    with pytest.raises(AgentNotFoundError):
        agents.get_evaluation_report(
            context(outsider),
            candidate_id=candidate.candidate_id,
            evaluation_run_id=report.run.evaluation_run_id,
        )
    mutation_statements = (
        update(agent_evaluation_dataset_versions)
        .where(agent_evaluation_dataset_versions.c.dataset_version_id == dataset.dataset_version_id)
        .values(name="禁止修改"),
        update(agent_evaluation_runs)
        .where(agent_evaluation_runs.c.evaluation_run_id == report.run.evaluation_run_id)
        .values(status="failed"),
        delete(agent_evaluation_case_results).where(
            agent_evaluation_case_results.c.evaluation_run_id == report.run.evaluation_run_id
        ),
    )
    for statement in mutation_statements:
        with evaluation_database.sessions() as session:
            with pytest.raises(DBAPIError):
                session.execute(statement)
            session.rollback()


def test_database_rejects_ready_status_without_passing_evidence(
    evaluation_database: EvaluationHarness,
) -> None:
    owner = register(evaluation_database, "database-gate")
    agents = service(evaluation_database)
    candidate, _ = create_candidate_and_dataset(
        evaluation_database,
        agents,
        owner,
        "database-gate",
    )

    with evaluation_database.sessions() as session:
        with pytest.raises(DBAPIError):
            session.execute(
                update(agent_release_candidates)
                .where(agent_release_candidates.c.candidate_id == candidate.candidate_id)
                .values(
                    status="ready_for_approval",
                    version=candidate.version + 1,
                    updated_at=datetime.now(UTC),
                )
            )
        session.rollback()


def create_candidate_and_dataset(
    harness: EvaluationHarness,
    agents: AgentControlService,
    owner: RegisteredAccount,
    suffix: str,
) -> tuple[AgentReleaseCandidate, AgentEvaluationDatasetVersion]:
    """创建评估所需的模型配置、Agent 候选和固定测试集。"""

    owner_context = context(owner)
    ensure_runtime_configuration(harness.sessions, owner.account_id)
    prompt = agents.create_prompt_version(
        owner_context,
        name=f"合成评估 Prompt {suffix}",
        template=f"只依据合成资料回答, 场景 {suffix}。",
    )
    scope = agents.create_knowledge_scope_version(
        owner_context,
        name=f"合成评估知识范围 {suffix}",
        knowledge_base_ids=(),
    )
    output_schema = agents.create_output_schema_version(
        owner_context,
        name=f"合成评估输出 {suffix}",
        schema_document=output_schema_document(),
    )
    configuration: dict[str, object] = {
        "prompt_version_id": str(prompt.prompt_version_id),
        "runtime_config_version_id": str(RUNTIME_ID),
        "knowledge_scope_version_ids": [str(scope.knowledge_scope_version_id)],
        "workflow_release_id": None,
        "read_only_tools": [],
        "output_schema_version_id": str(output_schema.output_schema_version_id),
        "safety_policy_version_id": str(SAFETY_POLICY_ID),
        "limits": {
            "max_input_tokens": 8192,
            "max_output_tokens": 2048,
            "max_execution_seconds": 60,
            "max_cost_microunits": 500_000,
        },
    }
    agent, draft = agents.create_agent(
        owner_context,
        name=f"合成评估 Agent {suffix}",
        description="仅用于 P3-04 PostgreSQL 测试",
        configuration=configuration,
        idempotency_key=f"synthetic-agent-evaluation-create-{suffix}",
    )
    candidate = agents.request_release_candidate(
        owner_context,
        agent_id=agent.agent_id,
        expected_revision=draft.revision,
        idempotency_key=f"synthetic-agent-evaluation-candidate-{suffix}",
    )
    dataset = agents.create_evaluation_dataset(
        owner_context,
        name=f"合成评估集 {suffix}",
        dataset_version=f"p304-{suffix}-v1",
        cases=dataset_cases(),
    )
    return candidate, dataset


def ensure_runtime_configuration(
    sessions: sessionmaker[Session],
    account_id: UUID,
) -> None:
    """为临时 Schema 建立唯一当前 Mock 运行配置。"""

    with sessions.begin() as session:
        if (session.scalar(select(func.count()).select_from(ai_runtime_config_versions)) or 0) > 0:
            return
        now = datetime.now(UTC)
        session.execute(
            insert(ai_runtime_config_versions).values(
                runtime_config_version_id=RUNTIME_ID,
                version_number=1,
                display_name="合成 P3-04 运行配置",
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
                created_by_account_id=account_id,
                created_at=now,
            )
        )
        session.execute(
            insert(ai_runtime_config_publication).values(
                publication_key="current",
                runtime_config_version_id=RUNTIME_ID,
                generation=1,
                published_by_account_id=account_id,
                published_at=now,
            )
        )


def dataset_cases() -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "case_key": f"synthetic.{check_code}",
            "check_code": check_code,
            "input_fixture": {"question": f"SYNTHETIC_{check_code.upper()}"},
            "expected_fixture": {"decision": "allow"},
            "timeout_ms": 2_000,
            "minimum_score_bps": 8_000 if check_code == "functional" else 10_000,
        }
        for check_code in REQUIRED_EVALUATION_CHECKS
    )


def output_schema_document() -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
    }
