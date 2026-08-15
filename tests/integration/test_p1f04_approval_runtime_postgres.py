"""验证 P1F-04 审批实例、异常动作和工作流恢复的 PostgreSQL/HTTP 闭环。"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.app.errors import ErrorCatalog, register_error_handlers
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
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.identity.application.enterprise import EnterpriseWorkspaceService
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.enterprise_sqlalchemy import (
    SqlAlchemyEnterpriseUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.modules.workflow.api.approval_runtime_routes import (
    router as approval_runtime_router,
)
from ai_platform_api.modules.workflow.application.approval_runtime import (
    ApprovalInstanceDenied,
    ApprovalInstanceService,
    ApprovalRuntimeCommand,
)
from ai_platform_api.modules.workflow.application.approvals import ApprovalPolicyService
from ai_platform_api.modules.workflow.application.executor import WorkflowRunExecutor
from ai_platform_api.modules.workflow.application.service import WorkflowDefinitionService
from ai_platform_api.modules.workflow.domain.approval_runtime import create_approval_runtime
from ai_platform_api.modules.workflow.domain.approvals import (
    ApprovalApproverSource,
    ApprovalChain,
    ApprovalLevelDefinition,
    ApprovalPolicyDefinition,
    ApprovalSubject,
    ApprovalTimeoutAction,
    ResolvedApprovalLevel,
)
from ai_platform_api.modules.workflow.domain.execution import (
    WorkflowKnowledgeResult,
    WorkflowModelResult,
)
from ai_platform_api.modules.workflow.domain.models import WorkflowEdge, WorkflowGraph, WorkflowNode
from ai_platform_api.modules.workflow.infrastructure.approval_runtime_sqlalchemy import (
    SqlAlchemyApprovalRuntimeUnitOfWork,
)
from ai_platform_api.modules.workflow.infrastructure.approvals_sqlalchemy import (
    SqlAlchemyApprovalPolicyUnitOfWork,
)
from ai_platform_api.modules.workflow.infrastructure.execution_sqlalchemy import (
    SqlAlchemyWorkflowExecutionStore,
)
from ai_platform_api.modules.workflow.infrastructure.sqlalchemy import (
    SqlAlchemyWorkflowUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    approval_actions,
    approval_instances,
    audit_records,
    outbox_events,
    workflow_run_steps,
    workflow_runs,
)
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext("1" * 32, "2" * 16)
TIMEOUT_ACTIONS: tuple[ApprovalTimeoutAction, ...] = (
    "escalate",
    "transfer",
    "reject",
    "wait",
)


@dataclass(frozen=True)
class RegisteredAccount:
    """保留合成账号、登录名和默认个人空间标识。"""

    account_id: UUID
    login_name: str
    workspace_id: UUID


@dataclass(frozen=True)
class ApprovalRuntimeHarness:
    """集中持有审批运行、工作流和临时 Schema 数据库入口。"""

    engine: Engine
    sessions: sessionmaker[Session]
    registration: RegistrationService
    enterprise: EnterpriseWorkspaceService
    workflows: WorkflowDefinitionService
    policies: ApprovalPolicyService
    runtimes: ApprovalInstanceService
    executor: WorkflowRunExecutor


class UnexpectedKnowledge:
    """审批测试图不包含检索节点，调用即表示恢复路径重复或越界。"""

    def retrieve(self, *args: object, **kwargs: object) -> WorkflowKnowledgeResult:
        del args, kwargs
        raise AssertionError("审批工作流不应执行知识检索")


class UnexpectedModels:
    """审批测试图不包含模型节点，调用即表示执行器走错节点。"""

    def invoke(self, *args: object, **kwargs: object) -> WorkflowModelResult:
        del args, kwargs
        raise AssertionError("审批工作流不应调用模型")


@pytest.fixture(scope="module")
def approval_runtime_database() -> Iterator[ApprovalRuntimeHarness]:
    """迁移独立 Schema，并用正式 Adapter 装配审批与工作流服务。"""

    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1f04_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    # 1. 测试 Schema 必须从空库迁移到 head，避免依赖本地开发库的历史状态。
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "infra/migrations"))
    config.set_main_option(
        "prepend_sys_path",
        f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
    )
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("ai_platform_schema", schema)
    command.upgrade(config, "head")

    # 2. 工作流执行器使用真实 PDP 和 PostgreSQL Store，仅外部检索、模型保持拒绝型 Adapter。
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
    policies = ApprovalPolicyService(SqlAlchemyApprovalPolicyUnitOfWork(sessions))
    try:
        yield ApprovalRuntimeHarness(
            engine,
            sessions,
            RegistrationService(
                SqlAlchemyIdentityReader(sessions),
                SqlAlchemyRegistrationUnitOfWork(sessions),
                Argon2idPasswordAdapter(),
            ),
            EnterpriseWorkspaceService(SqlAlchemyEnterpriseUnitOfWork(sessions)),
            WorkflowDefinitionService(SqlAlchemyWorkflowUnitOfWork(sessions)),
            policies,
            ApprovalInstanceService(SqlAlchemyApprovalRuntimeUnitOfWork(sessions), policies),
            WorkflowRunExecutor(
                SqlAlchemyWorkflowExecutionStore(sessions),
                policy,
                UnexpectedKnowledge(),
                UnexpectedModels(),
                approvals=policies,
            ),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def register(harness: ApprovalRuntimeHarness, identity: str) -> RegisteredAccount:
    """注册仅含合成信息的测试账号。"""

    login_name = f"synthetic.approval.runtime.{identity}.{uuid4().hex}@example.com"
    result = harness.registration.register(
        login_name=login_name,
        display_name=f"合成审批运行用户 {identity}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return RegisteredAccount(result.account_id, login_name, result.personal_workspace_id)


def context(account: RegisteredAccount, *, workspace_id: UUID | None = None) -> RequestContext:
    """构造具备工作空间范围的可信浏览器上下文。"""

    return replace(
        RequestContext.trusted(
            actor_id=account.account_id,
            user_id=account.account_id,
            workspace_id=workspace_id or account.workspace_id,
            trace=TRACE,
            authentication_method="browser_session",
        ),
        authorized_workspace=True,
    )


def approval_subject(account: RegisteredAccount, workspace_id: UUID) -> ApprovalSubject:
    """构造不包含真实业务字段的固定审批主题。"""

    return ApprovalSubject(
        workspace_id,
        account.account_id,
        "workflow.approval",
        "submit",
        None,
        (),
        "INTERNAL",
        "normal",
        {"amount": 1800, "source": "synthetic"},
    )


def add_enterprise_member(
    harness: ApprovalRuntimeHarness,
    owner: RegisteredAccount,
    member: RegisteredAccount,
    workspace_id: UUID,
) -> None:
    """通过正式邀请流程把合成账号加入企业空间。"""

    invitation = harness.enterprise.invite(
        context(owner, workspace_id=workspace_id),
        workspace_id=workspace_id,
        login_name=member.login_name,
    )
    harness.enterprise.accept_invitation(
        context(member),
        invitation_id=invitation.invitation_id,
    )


def create_enterprise_policy(
    harness: ApprovalRuntimeHarness,
    owner: RegisteredAccount,
    workspace_id: UUID,
    approver_id: UUID,
) -> None:
    """建立命中固定主题且支持转交的单级企业审批策略。"""

    harness.policies.create(
        context(owner, workspace_id=workspace_id),
        name=f"合成运行策略 {uuid4().hex[:8]}",
        definition=ApprovalPolicyDefinition(
            "workflow.approval",
            "submit",
            100,
            (),
            ("INTERNAL",),
            ("normal",),
            (),
            (
                ApprovalLevelDefinition(
                    1,
                    "any",
                    (ApprovalApproverSource("accounts", (approver_id,)),),
                    reminder_after_minutes=30,
                    timeout_after_minutes=60,
                    timeout_action="wait",
                ),
            ),
        ),
    )


def test_independent_instance_http_idempotency_visibility_and_cross_workspace_denial(
    approval_runtime_database: ApprovalRuntimeHarness,
) -> None:
    """HTTP 创建可重放且不泄漏主题字段，跨空间路径在服务端拒绝。"""

    owner = register(approval_runtime_database, "http-owner")
    outsider = register(approval_runtime_database, "http-outsider")
    active_context = [context(owner)]
    application = FastAPI()
    application.state.approval_instance_service = approval_runtime_database.runtimes
    application.state.workflow_run_executor = approval_runtime_database.executor
    application.include_router(approval_runtime_router, prefix="/api/v1")
    application.dependency_overrides[trusted_request_context] = lambda: active_context[0]
    register_error_handlers(
        application,
        ErrorCatalog.load(ROOT / "contracts/errors/catalog.v1.json"),
    )
    client = TestClient(application, raise_server_exceptions=False)
    path = f"/api/v1/workspaces/{owner.workspace_id}/approval-instances"
    payload = {
        "idempotency_key": "synthetic-http-create",
        "resource_type": "workflow.approval",
        "operation": "submit",
        "department_ids": [],
        "security_level": "INTERNAL",
        "risk_level": "normal",
        "fields": {"amount": 1800, "secret": "synthetic-hidden-value"},
    }

    # 1. 相同请求只生成一个实例，协议只返回主题摘要而不返回条件字段原文。
    first = client.post(path, json=payload)
    replay = client.post(path, json=payload)
    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["replayed"] is True
    assert (
        replay.json()["instance"]["approval_instance_id"]
        == first.json()["instance"]["approval_instance_id"]
    )
    assert "fields" not in first.json()["instance"]
    assert "synthetic-hidden-value" not in first.text

    # 2. 申请人可列出并读取实例，另一个工作空间上下文不能借 URL 穿透。
    instance_id = first.json()["instance"]["approval_instance_id"]
    assert client.get(path).json()["items"][0]["approval_instance_id"] == instance_id
    assert client.get(f"{path}/{instance_id}").status_code == 200
    active_context[0] = context(outsider)
    denied = client.get(f"{path}/{instance_id}")
    assert denied.status_code == 403
    assert denied.json()["code"] == "APPROVAL_ACTION_DENIED"


def test_transfer_reject_withdraw_and_concurrent_action_idempotency(
    approval_runtime_database: ApprovalRuntimeHarness,
) -> None:
    """企业参与者范围、异常动作与并发重复命令均由 PostgreSQL 聚合锁保护。"""

    owner = register(approval_runtime_database, "enterprise-owner")
    approver = register(approval_runtime_database, "enterprise-approver")
    target = register(approval_runtime_database, "enterprise-target")
    enterprise = approval_runtime_database.enterprise.create(
        context(owner),
        name="合成审批运行企业",
    )
    workspace_id = enterprise.workspace_id
    add_enterprise_member(approval_runtime_database, owner, approver, workspace_id)
    add_enterprise_member(approval_runtime_database, owner, target, workspace_id)
    create_enterprise_policy(
        approval_runtime_database,
        owner,
        workspace_id,
        approver.account_id,
    )
    owner_context = context(owner, workspace_id=workspace_id)
    approver_context = context(approver, workspace_id=workspace_id)
    target_context = context(target, workspace_id=workspace_id)

    # 1. 非参与者在转交前不可见，转交后历史责任和新责任均保持可追溯。
    transferred_state = approval_runtime_database.runtimes.start(
        owner_context,
        subject=approval_subject(owner, workspace_id),
        idempotency_key="synthetic-transfer",
    ).state
    with pytest.raises(ApprovalInstanceDenied):
        approval_runtime_database.runtimes.get(
            target_context,
            approval_instance_id=transferred_state.instance.approval_instance_id,
        )
    transferred = approval_runtime_database.runtimes.act(
        approver_context,
        approval_instance_id=transferred_state.instance.approval_instance_id,
        command=ApprovalRuntimeCommand(
            "transfer",
            approver.account_id,
            "synthetic-transfer-action",
            target.account_id,
            "load_balance",
        ),
    ).state
    assert [item.status for item in transferred.assignments] == ["transferred", "pending"]
    assert (
        approval_runtime_database.runtimes.get(
            target_context,
            approval_instance_id=transferred.instance.approval_instance_id,
        ).instance.status
        == "pending"
    )
    rejected = approval_runtime_database.runtimes.act(
        target_context,
        approval_instance_id=transferred.instance.approval_instance_id,
        command=ApprovalRuntimeCommand(
            "reject", target.account_id, "synthetic-reject", reason_code="risk_rejected"
        ),
    )
    assert rejected.state.instance.status == "rejected"

    # 2. 申请人可撤回独立实例；并发相同通过命令只追加一个动作事实并返回一次重放。
    withdrawn_state = approval_runtime_database.runtimes.start(
        owner_context,
        subject=approval_subject(owner, workspace_id),
        idempotency_key="synthetic-withdraw",
    ).state
    withdrawn = approval_runtime_database.runtimes.act(
        owner_context,
        approval_instance_id=withdrawn_state.instance.approval_instance_id,
        command=ApprovalRuntimeCommand(
            "withdraw", owner.account_id, "synthetic-withdraw-action", reason_code="cancelled"
        ),
    )
    assert withdrawn.state.instance.status == "withdrawn"

    concurrent_state = approval_runtime_database.runtimes.start(
        owner_context,
        subject=approval_subject(owner, workspace_id),
        idempotency_key="synthetic-concurrent",
    ).state

    def approve_once() -> bool:
        return approval_runtime_database.runtimes.act(
            approver_context,
            approval_instance_id=concurrent_state.instance.approval_instance_id,
            command=ApprovalRuntimeCommand(
                "approve", approver.account_id, "synthetic-concurrent-action"
            ),
        ).replayed

    with ThreadPoolExecutor(max_workers=2) as pool:
        replayed = sorted(pool.map(lambda _: approve_once(), range(2)))
    assert replayed == [False, True]
    with approval_runtime_database.sessions() as session:
        actions = tuple(
            session.scalars(
                select(approval_actions.c.approval_action_id).where(
                    approval_actions.c.approval_instance_id
                    == concurrent_state.instance.approval_instance_id
                )
            )
        )
    assert len(actions) == 1


def test_all_timeout_actions_and_action_facts_are_immutable(
    approval_runtime_database: ApprovalRuntimeHarness,
) -> None:
    """四种超时动作均持久化稳定事实，数据库拒绝修改或删除动作历史。"""

    owner = register(approval_runtime_database, "timeout-owner")
    approver = register(approval_runtime_database, "timeout-approver")
    fallback = register(approval_runtime_database, "timeout-fallback")
    enterprise = approval_runtime_database.enterprise.create(context(owner), name="合成超时企业")
    workspace_id = enterprise.workspace_id
    add_enterprise_member(approval_runtime_database, owner, approver, workspace_id)
    add_enterprise_member(approval_runtime_database, owner, fallback, workspace_id)
    created_at = datetime.now(UTC) - timedelta(minutes=10)
    states = []

    # 1. 直接冻结四条确定性审批链，隔离策略匹配对到期状态机测试的干扰。
    for action in TIMEOUT_ACTIONS:
        chain = ApprovalChain(
            workspace_id,
            None,
            None,
            False,
            (
                ResolvedApprovalLevel(
                    1,
                    "any",
                    (approver.account_id,),
                    1,
                    2,
                    action,
                    (fallback.account_id,) if action in {"escalate", "transfer"} else (),
                ),
            ),
            hashlib.sha256(action.encode("ascii")).hexdigest(),
        )
        state = create_approval_runtime(
            chain,
            approval_subject(owner, workspace_id),
            idempotency_key=f"synthetic-timeout-{action}",
            trace_id=TRACE.trace_id,
            traceparent=TRACE.traceparent,
            now=created_at,
        )
        with SqlAlchemyApprovalRuntimeUnitOfWork(
            approval_runtime_database.sessions
        ) as unit_of_work:
            unit_of_work.runtimes.add_state(state)
            unit_of_work.commit()
        states.append(state)

    # 2. 一个短批次推进全部实例，拒绝进入终态，其余策略按冻结动作续期或切换候补。
    results = approval_runtime_database.runtimes.process_due(
        context(owner, workspace_id=workspace_id),
        limit=10,
        now=datetime.now(UTC),
    )
    assert len(results) == 4
    states_by_key = {result.state.instance.idempotency_key: result.state for result in results}
    assert states_by_key["synthetic-timeout-reject"].instance.status == "rejected"
    assert states_by_key["synthetic-timeout-wait"].instance.status == "pending"
    for action in ("escalate", "transfer"):
        state = states_by_key[f"synthetic-timeout-{action}"]
        assert state.levels[0].fallback_activated is True
        assert any(
            item.approver_account_id == fallback.account_id and item.status == "pending"
            for item in state.assignments
        )

    # 3. 动作类型、审计和 Outbox 同时可见，动作表的数据库触发器拒绝事后篡改。
    instance_ids = [state.instance.approval_instance_id for state in states]
    with approval_runtime_database.sessions() as session:
        action_rows = tuple(
            session.execute(
                select(approval_actions).where(
                    approval_actions.c.approval_instance_id.in_(instance_ids)
                )
            )
        )
        audit_actions = set(
            session.scalars(
                select(audit_records.c.action).where(audit_records.c.resource_id.in_(instance_ids))
            )
        )
        outbox_types = set(
            session.scalars(
                select(outbox_events.c.event_type).where(
                    outbox_events.c.aggregate_id.in_(instance_ids)
                )
            )
        )
    expected = {"escalate", "timeout_transfer", "timeout_reject", "timeout_wait"}
    assert {row.action for row in action_rows} == expected
    assert {f"approval.instance.{action}" for action in expected} <= audit_actions
    assert {f"approval.instance.{action}" for action in expected} <= outbox_types
    with pytest.raises(DBAPIError), approval_runtime_database.sessions.begin() as session:
        session.execute(
            update(approval_actions)
            .where(approval_actions.c.approval_action_id == action_rows[0].approval_action_id)
            .values(reason_code="tampered")
        )


def approval_graph() -> WorkflowGraph:
    """构造审批前后都含节点的最小可恢复 DAG。"""

    return WorkflowGraph(
        1,
        "trigger",
        (
            WorkflowNode("trigger", "trigger", "触发", {}),
            WorkflowNode(
                "approval",
                "approval",
                "所有者确认",
                {"risk_level": "normal", "subject_path": "input.request_id"},
            ),
            WorkflowNode("result", "result", "结果", {"source_path": "input.request_id"}),
        ),
        (
            WorkflowEdge("e1", "trigger", "approval"),
            WorkflowEdge("e2", "approval", "result"),
        ),
    )


def test_workflow_approval_outcome_is_atomic_and_resume_skips_historical_steps(
    approval_runtime_database: ApprovalRuntimeHarness,
) -> None:
    """审批终态与工作流重新排队原子可见，恢复只执行尚未出现的结果节点。"""

    owner = register(approval_runtime_database, "workflow-owner")
    owner_context = context(owner)
    workflow, draft = approval_runtime_database.workflows.create(
        owner_context,
        name="合成审批恢复工作流",
        description="仅用于 P1F-04 原子恢复验收",
        graph=approval_graph(),
    )
    _, version, _ = approval_runtime_database.workflows.publish(
        owner_context,
        workflow_id=workflow.workflow_id,
        expected_revision=draft.revision,
    )
    run = approval_runtime_database.workflows.create_run(
        owner_context,
        workflow_id=workflow.workflow_id,
        workflow_version_id=version.workflow_version_id,
        idempotency_key="synthetic-workflow-approval",
        input_payload={"request_id": "synthetic-request-001"},
    )

    # 1. 首次执行把 Run、Step、审批实例、审计和 Outbox 一次提交到等待态。
    waiting = approval_runtime_database.executor.execute(owner_context, run.workflow_run_id)
    assert waiting.status == "waiting_approval"
    approval_state = approval_runtime_database.runtimes.list(owner_context, limit=10)[0]
    assert approval_state.instance.workflow_run_id == run.workflow_run_id

    # 2. 审批通过后，审批终态、等待 Step 成功和 Run queued 在同一提交点可见。
    approved = approval_runtime_database.runtimes.act(
        owner_context,
        approval_instance_id=approval_state.instance.approval_instance_id,
        command=ApprovalRuntimeCommand("approve", owner.account_id, "synthetic-workflow-approve"),
    )
    assert approved.resume is not None
    with approval_runtime_database.sessions() as session:
        queued_run = session.execute(
            select(workflow_runs).where(workflow_runs.c.workflow_run_id == run.workflow_run_id)
        ).one()
        approval_row = session.execute(
            select(approval_instances).where(
                approval_instances.c.approval_instance_id
                == approval_state.instance.approval_instance_id
            )
        ).one()
        waiting_step = session.execute(
            select(workflow_run_steps).where(
                workflow_run_steps.c.workflow_step_id == approval_state.instance.workflow_step_id
            )
        ).one()
    assert queued_run.status == "queued"
    assert approval_row.status == "approved"
    assert waiting_step.status == "succeeded"
    assert waiting_step.output_payload["approval_status"] == "approved"

    # 3. 恢复读取既有 Step 和累计预算，只新增 Result，Trigger 与 Approval 不重复执行。
    resumed = approval_runtime_database.executor.execute(owner_context, run.workflow_run_id)
    assert resumed.status == "succeeded"
    with approval_runtime_database.sessions() as session:
        final_run = session.execute(
            select(workflow_runs).where(workflow_runs.c.workflow_run_id == run.workflow_run_id)
        ).one()
        steps = tuple(
            session.execute(
                select(workflow_run_steps)
                .where(workflow_run_steps.c.workflow_run_id == run.workflow_run_id)
                .order_by(workflow_run_steps.c.sequence_no)
            )
        )
        workflow_events = set(
            session.scalars(
                select(outbox_events.c.event_type).where(
                    outbox_events.c.aggregate_id == run.workflow_run_id
                )
            )
        )
    assert final_run.status == "succeeded"
    assert final_run.steps_executed == 3
    assert [step.node_id for step in steps] == ["trigger", "approval", "result"]
    assert "workflow.run.queued_after_approval" in workflow_events
    assert "workflow.run.succeeded" in workflow_events
