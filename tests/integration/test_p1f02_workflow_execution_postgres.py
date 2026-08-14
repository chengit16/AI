"""验证 P1F-02 执行认领、Step/Attempt、分支和审批等待的 PostgreSQL 闭环。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.application.field_registry import (
    load_field_policy_registry,
)
from ai_platform_api.modules.authorization.application.policy import RbacPolicyDecisionPoint
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.infrastructure.sqlalchemy import (
    SqlAlchemyPolicyGrantRepository,
)
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.modules.workflow.application.executor import WorkflowRunExecutor
from ai_platform_api.modules.workflow.application.service import WorkflowDefinitionService
from ai_platform_api.modules.workflow.domain.execution import (
    WorkflowKnowledgeResult,
    WorkflowModelResult,
)
from ai_platform_api.modules.workflow.domain.models import (
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
)
from ai_platform_api.modules.workflow.infrastructure.execution_sqlalchemy import (
    SqlAlchemyWorkflowExecutionStore,
)
from ai_platform_api.modules.workflow.infrastructure.sqlalchemy import (
    SqlAlchemyWorkflowUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    audit_records,
    outbox_events,
    workflow_node_attempts,
    workflow_run_steps,
    workflow_runs,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext("e" * 32, "f" * 16)


@dataclass(frozen=True)
class RegisteredAccount:
    """保留测试账号和默认个人空间标识。"""

    account_id: UUID
    workspace_id: UUID


@dataclass(frozen=True)
class ExecutionHarness:
    """集中持有临时 Schema 的注册、定义、执行和数据库入口。"""

    engine: Engine
    sessions: sessionmaker[Session]
    registration: RegistrationService
    workflows: WorkflowDefinitionService
    executor: WorkflowRunExecutor


class UnexpectedKnowledge:
    """本测试图不包含检索节点，调用即表示执行器走错路径。"""

    def retrieve(
        self,
        context: RequestContext,
        *,
        query: str,
        knowledge_base_ids: frozenset[UUID] | None,
        limit: int,
    ) -> WorkflowKnowledgeResult:
        del context, query, knowledge_base_ids, limit
        raise AssertionError("合成分支图不应执行知识检索")


class UnexpectedModels:
    """本测试图不包含模型节点，调用即表示执行器走错路径。"""

    def invoke(self, *args: object, **kwargs: object) -> WorkflowModelResult:
        del args, kwargs
        raise AssertionError("合成分支图不应调用模型")


@pytest.fixture(scope="module")
def execution_database() -> Iterator[ExecutionHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1f02_test_{uuid4().hex}"
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
    registry = load_resource_registry(ROOT / "contracts/authorization/resource-registry.v1.json")
    field_registry = load_field_policy_registry(
        ROOT / "contracts/authorization/field-policy-registry.v1.json"
    )
    policy = RbacPolicyDecisionPoint(
        registry,
        SqlAlchemyPolicyGrantRepository(sessions),
        field_registry,
    )
    try:
        yield ExecutionHarness(
            engine,
            sessions,
            RegistrationService(
                SqlAlchemyIdentityReader(sessions),
                SqlAlchemyRegistrationUnitOfWork(sessions),
                Argon2idPasswordAdapter(),
            ),
            WorkflowDefinitionService(SqlAlchemyWorkflowUnitOfWork(sessions)),
            WorkflowRunExecutor(
                SqlAlchemyWorkflowExecutionStore(sessions),
                policy,
                UnexpectedKnowledge(),
                UnexpectedModels(),
            ),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def register(harness: ExecutionHarness, identity: str) -> RegisteredAccount:
    result = harness.registration.register(
        login_name=f"synthetic.workflow.execution.{identity}.{uuid4().hex}@example.com",
        display_name=f"合成执行用户 {identity}",
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


def branch_graph() -> WorkflowGraph:
    return WorkflowGraph(
        1,
        "trigger",
        (
            WorkflowNode("trigger", "trigger", "触发", {}),
            WorkflowNode(
                "condition",
                "condition",
                "金额条件",
                {"source_path": "input.amount", "operator": "gte", "expected": 1000},
            ),
            WorkflowNode("manual", "result", "人工路径", {"source_path": "input.amount"}),
            WorkflowNode("auto", "result", "自动路径", {"source_path": "input.amount"}),
        ),
        (
            WorkflowEdge("e1", "trigger", "condition"),
            WorkflowEdge("e2", "condition", "manual", "true"),
            WorkflowEdge("e3", "condition", "auto", "false"),
        ),
    )


def approval_graph() -> WorkflowGraph:
    return WorkflowGraph(
        1,
        "trigger",
        (
            WorkflowNode("trigger", "trigger", "触发", {}),
            WorkflowNode(
                "approval",
                "approval",
                "高风险审批",
                {"risk_level": "high", "subject_path": "input.request_id"},
            ),
            WorkflowNode("result", "result", "审批结果", {}),
        ),
        (
            WorkflowEdge("e1", "trigger", "approval"),
            WorkflowEdge("e2", "approval", "result"),
        ),
    )


def create_run(
    harness: ExecutionHarness,
    request_context: RequestContext,
    graph: WorkflowGraph,
    *,
    identity: str,
    input_payload: dict[str, object],
) -> UUID:
    workflow, draft = harness.workflows.create(
        request_context,
        name=f"合成执行工作流 {identity}",
        description="仅用于 P1F-02 PostgreSQL 验收",
        graph=graph,
    )
    _, version, _ = harness.workflows.publish(
        request_context,
        workflow_id=workflow.workflow_id,
        expected_revision=draft.revision,
    )
    run = harness.workflows.create_run(
        request_context,
        workflow_id=workflow.workflow_id,
        workflow_version_id=version.workflow_version_id,
        idempotency_key=f"synthetic-p1f02-{identity}",
        input_payload=input_payload,
    )
    return run.workflow_run_id


def test_branch_execution_persists_steps_attempts_and_terminal_event(
    execution_database: ExecutionHarness,
) -> None:
    account = register(execution_database, "branch")
    request_context = context(account)
    run_id = create_run(
        execution_database,
        request_context,
        branch_graph(),
        identity="branch",
        input_payload={"amount": 1800},
    )

    result = execution_database.executor.execute(request_context, run_id)

    assert result.claimed is True
    assert result.status == "succeeded"
    assert execution_database.executor.execute(request_context, run_id).claimed is False
    with execution_database.sessions() as session:
        run = session.execute(
            select(workflow_runs).where(workflow_runs.c.workflow_run_id == run_id)
        ).one()
        steps = list(
            session.execute(
                select(workflow_run_steps)
                .where(workflow_run_steps.c.workflow_run_id == run_id)
                .order_by(workflow_run_steps.c.sequence_no)
            )
        )
        attempts = list(
            session.execute(
                select(workflow_node_attempts).where(
                    workflow_node_attempts.c.workflow_run_id == run_id
                )
            )
        )
        events = list(
            session.execute(
                select(outbox_events.c.event_type).where(outbox_events.c.aggregate_id == run_id)
            ).scalars()
        )
        audits = list(
            session.execute(
                select(audit_records.c.action).where(audit_records.c.resource_id == run_id)
            ).scalars()
        )

    assert run.status == "succeeded"
    assert run.output_payload == {"results": {"manual": 1800}}
    assert run.executor_version == "workflow-executor-v1"
    assert run.steps_executed == 3
    assert [step.status for step in steps] == ["succeeded", "succeeded", "skipped", "succeeded"]
    assert len(attempts) == 3
    assert all(attempt.status == "succeeded" for attempt in attempts)
    assert "workflow.run.succeeded" in events
    assert "workflow.run.succeeded" in audits

    terminal_attempt = attempts[0]
    with pytest.raises(DBAPIError), execution_database.sessions.begin() as session:
        session.execute(
            update(workflow_node_attempts)
            .where(
                workflow_node_attempts.c.workflow_attempt_id == terminal_attempt.workflow_attempt_id
            )
            .values(status="running")
        )


def test_approval_node_stops_run_in_waiting_state(
    execution_database: ExecutionHarness,
) -> None:
    account = register(execution_database, "approval")
    request_context = context(account)
    run_id = create_run(
        execution_database,
        request_context,
        approval_graph(),
        identity="approval",
        input_payload={"request_id": "synthetic-approval-001"},
    )

    result = execution_database.executor.execute(request_context, run_id)

    assert result.status == "waiting_approval"
    with execution_database.sessions() as session:
        run = session.execute(
            select(workflow_runs).where(workflow_runs.c.workflow_run_id == run_id)
        ).one()
        steps = list(
            session.execute(
                select(workflow_run_steps)
                .where(workflow_run_steps.c.workflow_run_id == run_id)
                .order_by(workflow_run_steps.c.sequence_no)
            )
        )
    assert run.status == "waiting_approval"
    assert run.completed_at is None
    assert [step.status for step in steps] == ["succeeded", "waiting_approval"]
    assert steps[-1].output_payload["subject"] == "synthetic-approval-001"
