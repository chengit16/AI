"""验证 P4-05 真实 Release 允许列表、策略证据和原子计划冻结。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.modules.authorization.application.policy import RbacPolicyDecisionPoint
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.infrastructure.sqlalchemy import (
    SqlAlchemyPolicyGrantRepository,
)
from ai_platform_api.modules.identity.infrastructure.entitlements_sqlalchemy import (
    SqlAlchemyEntitlementAccessReader,
)
from ai_platform_api.modules.service_governance.application.service import (
    ServiceGovernanceService,
)
from ai_platform_api.modules.service_governance.infrastructure.sqlalchemy import (
    SqlAlchemyServiceGovernanceUnitOfWork,
)
from ai_platform_api.modules.tool_execution.application.catalog import ToolCatalogService
from ai_platform_api.modules.tool_execution.application.errors import ToolRunConflictError
from ai_platform_api.modules.tool_execution.application.planning import (
    ToolExecutionPlanningService,
    parse_candidate_tool_intents,
)
from ai_platform_api.modules.tool_execution.application.tasks import ToolTaskService
from ai_platform_api.modules.tool_execution.domain.planning import FrozenToolPlanStep
from ai_platform_api.modules.tool_execution.domain.tasks import ToolRunBudget
from ai_platform_api.modules.tool_execution.infrastructure.planning_sqlalchemy import (
    SqlAlchemyToolReleasePlanSource,
)
from ai_platform_api.modules.tool_execution.infrastructure.sqlalchemy import (
    SqlAlchemyToolCatalogRepository,
)
from ai_platform_api.modules.tool_execution.infrastructure.tasks_sqlalchemy import (
    SqlAlchemyToolTaskStore,
)
from ai_platform_api.modules.workflow.domain.approval_runtime import ApprovalRuntimeCommand
from ai_platform_api.persistence.tables import (
    tool_policy_decisions,
    tool_progress_events,
    tool_runs,
    tool_steps,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.exc import DBAPIError

from tests.integration.test_p304_agent_evaluation_postgres import (
    RUNTIME_ID,
    SAFETY_POLICY_ID,
    RegisteredAccount,
    SyntheticEvaluationExecutor,
    context,
    dataset_cases,
    ensure_runtime_configuration,
    output_schema_document,
)
from tests.integration.test_p305_agent_approval_postgres import (
    ApprovalHarness,
    approval_harness,
    register,
)
from tests.integration.test_p305_agent_approval_postgres import (
    service as agent_service,
)

ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 8, 16, 10, 0, tzinfo=UTC)
KNOWLEDGE_TOOL_ID = UUID("a7000000-0000-4000-8000-000000000001")
BUDGET = ToolRunBudget(5, 2, 120, 0)


@pytest.fixture(scope="module")
def planning_database() -> Iterator[ApprovalHarness]:
    """复用真实 Agent 测试、审批和发布链，避免伪造自定义 Release。"""

    yield from approval_harness()


@dataclass(frozen=True)
class PlanningEnvironment:
    """集中保存同一工作空间的发布服务与工具计划组件。"""

    owner: RegisteredAccount
    service_id: UUID
    release_id: UUID
    tasks: ToolTaskService
    store: SqlAlchemyToolTaskStore
    source: SqlAlchemyToolReleasePlanSource
    planning: ToolExecutionPlanningService


def _environment(harness: ApprovalHarness, suffix: str) -> PlanningEnvironment:
    """通过正式配置、评估、审批、发布和服务治理入口准备工具计划事实。"""

    owner = register(harness, f"planning-{suffix}")
    owner_context = context(owner)
    ensure_runtime_configuration(harness.sessions, owner.account_id)
    agents = agent_service(harness, SyntheticEvaluationExecutor())
    prompt = agents.create_prompt_version(
        owner_context,
        name=f"合成计划 Prompt {suffix}",
        template="只允许使用发布快照中的合成只读工具。",
    )
    scope = agents.create_knowledge_scope_version(
        owner_context,
        name=f"合成计划知识范围 {suffix}",
        knowledge_base_ids=(),
    )
    output_schema = agents.create_output_schema_version(
        owner_context,
        name=f"合成计划输出 {suffix}",
        schema_document=output_schema_document(),
    )
    configuration: dict[str, object] = {
        "prompt_version_id": str(prompt.prompt_version_id),
        "runtime_config_version_id": str(RUNTIME_ID),
        "knowledge_scope_version_ids": [str(scope.knowledge_scope_version_id)],
        "workflow_release_id": None,
        "read_only_tools": [
            {
                "tool_id": str(KNOWLEDGE_TOOL_ID),
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
    agent, draft = agents.create_agent(
        owner_context,
        name=f"合成计划 Agent {suffix}",
        description="仅用于 P4-05 PostgreSQL 测试。",
        configuration=configuration,
        idempotency_key=f"synthetic-p405-agent-{suffix}",
    )
    candidate = agents.request_release_candidate(
        owner_context,
        agent_id=agent.agent_id,
        expected_revision=draft.revision,
        idempotency_key=f"synthetic-p405-candidate-{suffix}",
    )
    dataset = agents.create_evaluation_dataset(
        owner_context,
        name=f"合成计划测试集 {suffix}",
        dataset_version=f"p304-p405-{suffix}-v1",
        cases=dataset_cases(),
    )
    report = agents.run_evaluation(
        owner_context,
        candidate_id=candidate.candidate_id,
        dataset_version_id=dataset.dataset_version_id,
    )
    assert report.run.status == "passed"
    requested = agents.request_approval(
        owner_context,
        candidate_id=candidate.candidate_id,
        idempotency_key=f"synthetic-p405-approval-{suffix}",
    )
    approved = harness.approvals.act(
        owner_context,
        approval_instance_id=requested.state.instance.approval_instance_id,
        command=ApprovalRuntimeCommand(
            "approve",
            owner.account_id,
            f"synthetic-p405-approve-{suffix}",
        ),
    )
    assert approved.state.instance.status == "approved"
    release = agents.publish_release(
        owner_context,
        candidate_id=candidate.candidate_id,
        idempotency_key=f"synthetic-p405-release-{suffix}",
    )
    deployment = ServiceGovernanceService(
        SqlAlchemyServiceGovernanceUnitOfWork(harness.sessions)
    ).create_service(
        owner_context,
        name=f"合成计划服务 {suffix}",
        release_id=release.release_id,
        visibility="workspace",
        idempotency_key=f"synthetic-p405-service-{suffix}",
    )

    store = SqlAlchemyToolTaskStore(harness.sessions)
    tasks = ToolTaskService(store)
    source = SqlAlchemyToolReleasePlanSource(harness.sessions)
    catalog = ToolCatalogService(
        SqlAlchemyToolCatalogRepository(harness.sessions),
        SqlAlchemyEntitlementAccessReader(harness.sessions),
        RbacPolicyDecisionPoint(
            load_resource_registry(
                ROOT / "contracts" / "authorization" / "resource-registry.v1.json"
            ),
            SqlAlchemyPolicyGrantRepository(harness.sessions),
        ),
    )
    return PlanningEnvironment(
        owner,
        deployment.service.service_id,
        release.release_id,
        tasks,
        store,
        source,
        ToolExecutionPlanningService(tasks, store, catalog, source),
    )


def test_real_release_plan_freezes_budget_policy_and_blocks_database_bypass(
    planning_database: ApprovalHarness,
) -> None:
    environment = _environment(planning_database, "freeze")
    owner_context = context(environment.owner)
    release_plan = environment.source.get_bound(
        environment.owner.workspace_id,
        environment.service_id,
        environment.release_id,
    )
    assert release_plan is not None
    assert release_plan.release_snapshot_hash is not None
    assert [(item.tool_id, item.access_mode) for item in release_plan.tools] == [
        (KNOWLEDGE_TOOL_ID, "read")
    ]
    assert (
        environment.source.get_bound(
            environment.owner.workspace_id,
            uuid4(),
            environment.release_id,
        )
        is None
    )

    run = environment.tasks.create_run(
        owner_context,
        service_id=environment.service_id,
        agent_release_id=environment.release_id,
        idempotency_key="synthetic-p405-freeze-run",
        budget=BUDGET,
        created_at=NOW,
    )
    intents = parse_candidate_tool_intents(
        {
            "tool_calls": [
                {
                    "tool_key": "knowledge.search",
                    "tool_id": str(KNOWLEDGE_TOOL_ID),
                    "tool_version": 1,
                    "arguments": {"query": "合成计划"},
                }
            ]
        }
    )
    plan = environment.planning.freeze(owner_context, run.run_id, intents, evaluated_at=NOW)

    assert plan.run.state == "running"
    assert len(plan.steps) == len(plan.policies) == 1
    assert plan.steps[0].state == "ready"
    assert plan.steps[0].budget.max_attempts == 2
    assert plan.steps[0].budget.max_result_bytes == 262_144
    with planning_database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(tool_steps)) == 1
        assert session.scalar(select(func.count()).select_from(tool_policy_decisions)) == 1

    # 原始状态接口可以建立 policy_checking，但没有匹配证据时数据库拒绝进入 ready。
    bypass = environment.tasks.create_run(
        owner_context,
        service_id=environment.service_id,
        agent_release_id=environment.release_id,
        idempotency_key="synthetic-p405-bypass-run",
        budget=BUDGET,
        created_at=NOW,
    )
    environment.tasks.transition_run(owner_context, bypass.run_id, "planning", occurred_at=NOW)
    bypass_step = environment.tasks.append_step(
        owner_context,
        bypass.run_id,
        tool_id=KNOWLEDGE_TOOL_ID,
        tool_version=1,
        canonical_arguments_hash="c" * 64,
        created_at=NOW,
    )
    environment.tasks.transition_step(
        owner_context,
        bypass_step.step_id,
        "policy_checking",
        occurred_at=NOW,
    )
    # 策略权限码必须与工具定义一致，且旧时点允许证据不能被复用为当前决策。
    decision_values = {
        "decision_id": uuid4(),
        "workspace_id": environment.owner.workspace_id,
        "run_id": bypass.run_id,
        "step_id": bypass_step.step_id,
        "tool_id": KNOWLEDGE_TOOL_ID,
        "tool_version": 1,
        "canonical_arguments_hash": "c" * 64,
        "permission_code": "workflow.run.read",
        "policy_version": 1,
        "resource_scope_hash": "d" * 64,
        "field_mask_hash": "e" * 64,
        "evaluated_at": NOW,
    }
    with pytest.raises(DBAPIError), planning_database.sessions.begin() as session:
        session.execute(insert(tool_policy_decisions).values(**decision_values))
    with planning_database.sessions.begin() as session:
        session.execute(
            insert(tool_policy_decisions).values(
                **{
                    **decision_values,
                    "decision_id": uuid4(),
                    "permission_code": "knowledge.document.read",
                    "evaluated_at": NOW - timedelta(seconds=1),
                }
            )
        )
    with pytest.raises(DBAPIError):
        environment.tasks.transition_step(
            owner_context,
            bypass_step.step_id,
            "ready",
            occurred_at=NOW,
        )


def test_store_rolls_back_run_steps_and_policy_when_one_step_conflicts(
    planning_database: ApprovalHarness,
) -> None:
    environment = _environment(planning_database, "rollback")
    owner_context = context(environment.owner)
    run = environment.tasks.create_run(
        owner_context,
        service_id=environment.service_id,
        agent_release_id=environment.release_id,
        idempotency_key="synthetic-p405-rollback-run",
        budget=BUDGET,
        created_at=NOW,
    )
    allowed_run = environment.tasks.create_run(
        owner_context,
        service_id=environment.service_id,
        agent_release_id=environment.release_id,
        idempotency_key="synthetic-p405-template-run",
        budget=BUDGET,
        created_at=NOW,
    )
    template = environment.planning.freeze(
        owner_context,
        allowed_run.run_id,
        parse_candidate_tool_intents(
            {
                "tool_calls": [
                    {
                        "tool_key": "knowledge.search",
                        "tool_id": str(KNOWLEDGE_TOOL_ID),
                        "tool_version": 1,
                        "arguments": {"query": "合成回滚"},
                    }
                ]
            }
        ),
        evaluated_at=NOW,
    )
    duplicate_step_id = uuid4()
    first = FrozenToolPlanStep(
        step_id=duplicate_step_id,
        sequence_no=1,
        tool_id=template.steps[0].tool_id,
        tool_version=template.steps[0].tool_version,
        canonical_arguments_hash=template.steps[0].canonical_arguments_hash,
        budget=template.steps[0].budget,
        policy=replace(
            template.policies[0],
            decision_id=uuid4(),
            run_id=run.run_id,
            step_id=duplicate_step_id,
        ),
    )
    second = replace(
        first,
        sequence_no=2,
        policy=replace(first.policy, decision_id=uuid4()),
    )

    with pytest.raises(ToolRunConflictError):
        environment.store.freeze_read_plan(
            workspace_id=environment.owner.workspace_id,
            run_id=run.run_id,
            steps=(first, second),
            frozen_at=NOW,
        )

    with planning_database.sessions() as session:
        assert (
            session.scalar(select(tool_runs.c.state).where(tool_runs.c.run_id == run.run_id))
            == "pending"
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(tool_steps)
                .where(tool_steps.c.run_id == run.run_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(tool_policy_decisions)
                .where(tool_policy_decisions.c.run_id == run.run_id)
            )
            == 0
        )


def test_policy_facts_block_destructive_p405_downgrade(
    planning_database: ApprovalHarness,
) -> None:
    """P4-05 策略事实存在时 Migration 不能删除预算和授权证据。"""

    environment = _environment(planning_database, "downgrade")
    run = environment.tasks.create_run(
        context(environment.owner),
        service_id=environment.service_id,
        agent_release_id=environment.release_id,
        idempotency_key="synthetic-p405-downgrade-run",
        budget=BUDGET,
        created_at=NOW,
    )
    environment.planning.freeze(
        context(environment.owner),
        run.run_id,
        parse_candidate_tool_intents(
            {
                "tool_calls": [
                    {
                        "tool_key": "knowledge.search",
                        "tool_id": str(KNOWLEDGE_TOOL_ID),
                        "tool_version": 1,
                        "arguments": {"query": "合成降级保护"},
                    }
                ]
            }
        ),
        evaluated_at=NOW,
    )
    schema_map = planning_database.engine.get_execution_options().get("schema_translate_map")
    assert isinstance(schema_map, dict)
    schema = schema_map.get("ai_platform")
    assert isinstance(schema, str)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "infra/migrations"))
    config.set_main_option(
        "prepend_sys_path",
        f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
    )
    config.set_main_option(
        "sqlalchemy.url",
        planning_database.engine.url.render_as_string(hide_password=False),
    )
    config.set_main_option("ai_platform_schema", schema)

    # 先按生命周期受控旁路移除后置 P4-10 事实，确保本测试能到达 P4-05 自身的降级保护。
    with planning_database.sessions.begin() as session:
        session.execute(text("SET LOCAL ai_platform.lifecycle_purge = 'on'"))
        session.execute(delete(tool_progress_events))
    with pytest.raises(RuntimeError, match="存在工具计划策略事实"):
        command.downgrade(config, "20260816_0054")
