"""验证 P4-06 个人确认、企业审批、重新授权和 PostgreSQL 防绕过闭环。"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_control.infrastructure.approval_sqlalchemy import (
    SqlAlchemyAgentApprovalSubjectLifecycle,
)
from ai_platform_api.modules.authorization.domain.policy import PolicyDecision, ResourceScope
from ai_platform_api.modules.tool_execution.application.confirmations import ToolConfirmationService
from ai_platform_api.modules.tool_execution.application.definitions import parse_tool_definition
from ai_platform_api.modules.tool_execution.application.errors import (
    ToolConfirmationRequiredError,
    ToolConfirmationStaleError,
    ToolExecutionDeniedError,
)
from ai_platform_api.modules.tool_execution.application.tasks import ToolTaskService
from ai_platform_api.modules.tool_execution.domain.catalog import ToolDefinition
from ai_platform_api.modules.tool_execution.domain.confirmations import ToolConfirmation
from ai_platform_api.modules.tool_execution.domain.tasks import ToolRunBudget, ToolStep
from ai_platform_api.modules.tool_execution.infrastructure.confirmation_approval_sqlalchemy import (
    SqlAlchemyToolConfirmationSubjectLifecycle,
)
from ai_platform_api.modules.tool_execution.infrastructure.confirmations_sqlalchemy import (
    SqlAlchemyToolConfirmationStore,
)
from ai_platform_api.modules.tool_execution.infrastructure.tasks_sqlalchemy import (
    SqlAlchemyToolTaskStore,
)
from ai_platform_api.modules.workflow.application.approval_runtime import (
    ApprovalInstanceService,
)
from ai_platform_api.modules.workflow.domain.approval_runtime import ApprovalRuntimeCommand
from ai_platform_api.modules.workflow.domain.approvals import (
    ApprovalApproverSource,
    ApprovalLevelDefinition,
    ApprovalPolicyDefinition,
)
from ai_platform_api.modules.workflow.infrastructure.approval_runtime_sqlalchemy import (
    RoutedApprovalSubjectLifecycle,
    SqlAlchemyApprovalRuntimeUnitOfWork,
)
from ai_platform_api.persistence.tables import (
    agent_releases,
    agent_tool_definitions,
    agents,
    service_access_policy_versions,
    services,
    tool_confirmation_invalidations,
    tool_confirmations,
    tool_policy_decisions,
    tool_runs,
    tool_steps,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import DBAPIError

from tests.integration.test_p304_agent_evaluation_postgres import (
    RUNTIME_ID,
    RegisteredAccount,
    context,
    ensure_runtime_configuration,
)
from tests.integration.test_p305_agent_approval_postgres import (
    ApprovalHarness,
    add_enterprise_member,
    approval_harness,
    candidate_status,
    prepare_candidate,
    register,
)

ROOT = Path(__file__).parents[2]
WRITE_TOOL_ID = UUID("a7000000-0000-4000-8000-000000000406")
WRITE_PERMISSION = "synthetic.record.write"
RUN_BUDGET = ToolRunBudget(5, 2, 1_800, 50_000)
SCHEMA_DOCUMENT: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}
WRITE_DEFINITION = parse_tool_definition(
    {
        "tool_id": str(WRITE_TOOL_ID),
        "tool_version": 1,
        "tool_key": "synthetic.record_write",
        "display_name": "合成记录写入",
        "description": "仅用于验证确认与审批门禁, 不执行真实副作用。",
        "access_mode": "write",
        "risk_level": "high",
        "adapter_kind": "synthetic_internal_write",
        "input_schema_document": SCHEMA_DOCUMENT,
        "output_schema_document": SCHEMA_DOCUMENT,
        "permission_code": WRITE_PERMISSION,
        "credential_requirement": "none",
        "timeout_seconds": 30,
        "retry_mode": "idempotent_write",
        "status": "active",
        "synthetic": True,
    },
    created_at=datetime(2026, 8, 16, 12, tzinfo=UTC),
)


class SyntheticWriteCatalog:
    """返回可变的合成 PDP 结论，用于验证策略变化后的确认失效。"""

    def __init__(self) -> None:
        self.policy_version = 1
        self.field_mask: frozenset[str] = frozenset()

    def authorize_available_tool(
        self,
        request_context: RequestContext,
        *,
        workspace_id: UUID,
        tool_id: UUID,
        tool_version: int,
        resource_id: UUID | None = None,
    ) -> tuple[ToolDefinition, PolicyDecision]:
        """为同空间固定写工具生成一次新的、不可复用的 PDP 决策标识。"""

        del resource_id
        if (
            request_context.workspace_id != workspace_id
            or tool_id != WRITE_TOOL_ID
            or tool_version != 1
        ):
            raise AssertionError("合成 Catalog 收到越界工具请求")
        return WRITE_DEFINITION, PolicyDecision(
            uuid4(),
            "allow",
            WRITE_PERMISSION,
            workspace_id,
            ResourceScope(workspace=True),
            self.field_mask,
            self.policy_version,
            0,
            "synthetic_allow",
            "CONFIDENTIAL",
        )


@dataclass(frozen=True)
class ConfirmationEnvironment:
    """集中保存一个合成写步骤的任务、确认、审批和策略入口。"""

    owner: RegisteredAccount
    request_context: RequestContext
    step: ToolStep
    tasks: ToolTaskService
    store: SqlAlchemyToolConfirmationStore
    approvals: ApprovalInstanceService
    confirmations: ToolConfirmationService
    catalog: SyntheticWriteCatalog


@pytest.fixture(scope="module")
def confirmation_database() -> Iterator[ApprovalHarness]:
    """从空 Schema 迁移到 head，并注册唯一的合成写工具定义。"""

    for harness in approval_harness():
        with harness.sessions.begin() as session:
            session.execute(insert(agent_tool_definitions).values(**asdict(WRITE_DEFINITION)))
        yield harness


def _routed_approvals(harness: ApprovalHarness) -> ApprovalInstanceService:
    """组合 Agent 与工具生命周期，防止新增工具审批替换既有发布审批。"""

    return ApprovalInstanceService(
        SqlAlchemyApprovalRuntimeUnitOfWork(
            harness.sessions,
            lambda session: RoutedApprovalSubjectLifecycle(
                {
                    "agent.release": SqlAlchemyAgentApprovalSubjectLifecycle(session),
                    "tool.call": SqlAlchemyToolConfirmationSubjectLifecycle(session),
                }
            ),
        ),
        harness.policies,
    )


def _environment(
    harness: ApprovalHarness,
    owner: RegisteredAccount,
    suffix: str,
    *,
    approvals: ApprovalInstanceService | None = None,
) -> ConfirmationEnvironment:
    """建立运行中的合成写 Step；确认前不创建 ToolCall 或调用 Adapter。"""

    request_context = context(owner)
    now = datetime.now(UTC)
    service_id, release_id = _seed_service(harness, owner, suffix, now)
    task_store = SqlAlchemyToolTaskStore(harness.sessions)
    tasks = ToolTaskService(task_store)
    run = tasks.create_run(
        request_context,
        service_id=service_id,
        agent_release_id=release_id,
        idempotency_key=f"synthetic-p406-run-{suffix}",
        budget=RUN_BUDGET,
        created_at=now,
    )
    tasks.transition_run(request_context, run.run_id, "planning", occurred_at=now)
    step = tasks.append_step(
        request_context,
        run.run_id,
        tool_id=WRITE_TOOL_ID,
        tool_version=1,
        canonical_arguments_hash=hashlib.sha256(suffix.encode()).hexdigest(),
        created_at=now,
    )
    step = tasks.transition_step(
        request_context,
        step.step_id,
        "policy_checking",
        occurred_at=now,
    )
    tasks.transition_run(request_context, run.run_id, "running", occurred_at=now)
    routed = approvals or _routed_approvals(harness)
    store = SqlAlchemyToolConfirmationStore(harness.sessions)
    catalog = SyntheticWriteCatalog()
    return ConfirmationEnvironment(
        owner,
        request_context,
        step,
        tasks,
        store,
        routed,
        ToolConfirmationService(store, catalog, routed),
        catalog,
    )


def _seed_service(
    harness: ApprovalHarness,
    owner: RegisteredAccount,
    suffix: str,
    now: datetime,
) -> tuple[UUID, UUID]:
    """复用唯一 Runtime，创建仅满足工具 Run 复合外键的合成 Service。"""

    ensure_runtime_configuration(harness.sessions, owner.account_id)
    agent_id = uuid4()
    release_id = uuid4()
    service_id = uuid4()
    access_policy_id = uuid4()
    with harness.sessions.begin() as session:
        # 1. Service 与访问策略存在延迟循环外键，测试事务显式推迟到提交点校验。
        session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
        session.execute(
            insert(agents).values(
                agent_id=agent_id,
                workspace_id=owner.workspace_id,
                agent_key=f"synthetic-p406-agent-{suffix}",
                agent_kind="system",
                name=f"合成 P4-06 Agent {suffix}",
                description=None,
                status="active",
                created_by_account_id=owner.account_id,
                created_at=now,
                updated_at=now,
                version=1,
            )
        )
        # 2. Run 只绑定已发布的精确 Release；该合成 Release 不携带工具正文或 Adapter。
        session.execute(
            insert(agent_releases).values(
                release_id=release_id,
                agent_id=agent_id,
                workspace_id=owner.workspace_id,
                release_kind="system",
                version=1,
                status="released",
                runtime_config_version_id=RUNTIME_ID,
                config_hash="3" * 64,
                released_by_account_id=owner.account_id,
                released_at=now,
            )
        )
        # 3. 最小工作空间可见 Service 只满足 FK，不开放 HTTP、菜单或真实调用入口。
        session.execute(
            insert(service_access_policy_versions).values(
                access_policy_version_id=access_policy_id,
                service_id=service_id,
                workspace_id=owner.workspace_id,
                version=1,
                visibility="workspace",
                allowed_department_ids=[],
                allowed_account_ids=[],
                policy_hash="4" * 64,
                created_by_account_id=owner.account_id,
                created_at=now,
            )
        )
        session.execute(
            insert(services).values(
                service_id=service_id,
                workspace_id=owner.workspace_id,
                agent_id=agent_id,
                service_key=f"synthetic-p406-service-{suffix}",
                name=f"合成 P4-06 Service {suffix}",
                service_type="system_assistant",
                status="draft",
                access_policy_version_id=access_policy_id,
                created_by_account_id=owner.account_id,
                created_at=now,
                updated_by_account_id=owner.account_id,
                updated_at=now,
                version=1,
            )
        )
    return service_id, release_id


def _approve_personal(
    environment: ConfirmationEnvironment,
    suffix: str,
) -> ToolConfirmation:
    """申请并由个人空间所有者完成一级确认，返回批准投影。"""

    requested = environment.confirmations.request(
        environment.request_context,
        step_id=environment.step.step_id,
        idempotency_key=f"synthetic-p406-request-{suffix}",
    )
    environment.approvals.act(
        environment.request_context,
        approval_instance_id=requested.confirmation.approval_instance_id,
        command=ApprovalRuntimeCommand(
            "approve",
            environment.owner.account_id,
            f"synthetic-p406-approve-{suffix}",
        ),
    )
    approved = environment.store.get_confirmation(
        environment.owner.workspace_id,
        requested.confirmation.confirmation_id,
    )
    assert approved is not None and approved.state == "approved"
    return approved


def test_personal_confirmation_is_idempotent_and_requires_fresh_pdp(
    confirmation_database: ApprovalHarness,
) -> None:
    """个人所有者确认可重放，批准后旧 PDP 仍不能绕过重新授权。"""

    owner = register(confirmation_database, "p406-personal")
    environment = _environment(confirmation_database, owner, "personal")
    requested = environment.confirmations.request(
        environment.request_context,
        step_id=environment.step.step_id,
        idempotency_key="synthetic-p406-personal-request",
    )
    replayed = environment.confirmations.request(
        environment.request_context,
        step_id=environment.step.step_id,
        idempotency_key="synthetic-p406-personal-request",
    )
    assert requested.confirmation.mode == "personal_owner"
    assert requested.confirmation.state == "pending"
    assert requested.approval.state.instance.personal_owner_confirmation is True
    assert replayed.replayed is True
    assert replayed.confirmation == requested.confirmation

    outsider = register(confirmation_database, "p406-personal-outsider")
    with pytest.raises(ToolExecutionDeniedError):
        environment.confirmations.request(
            context(outsider),
            step_id=environment.step.step_id,
            idempotency_key="synthetic-p406-cross-workspace",
        )

    environment.approvals.act(
        environment.request_context,
        approval_instance_id=requested.confirmation.approval_instance_id,
        command=ApprovalRuntimeCommand(
            "approve",
            owner.account_id,
            "synthetic-p406-personal-approve",
        ),
    )
    approved = environment.store.get_confirmation(
        owner.workspace_id,
        requested.confirmation.confirmation_id,
    )
    assert approved is not None and approved.state == "approved"
    with pytest.raises(ToolExecutionDeniedError):
        environment.confirmations.resume(
            context(outsider),
            confirmation_id=approved.confirmation_id,
            expected_binding=approved.binding,
        )

    # 申请时 PDP 即使时间可猜，也不能充当批准后的重新授权证据。
    with confirmation_database.sessions() as session:
        current_version = session.scalar(
            select(tool_steps.c.version).where(tool_steps.c.step_id == environment.step.step_id)
        )
        assert isinstance(current_version, int)
        with pytest.raises(DBAPIError):
            session.execute(
                update(tool_steps)
                .where(tool_steps.c.step_id == environment.step.step_id)
                .values(
                    state="ready",
                    updated_at=approved.policy_evaluated_at,
                    version=current_version + 1,
                )
            )
            session.commit()
        session.rollback()

    resumed = environment.confirmations.resume(
        environment.request_context,
        confirmation_id=approved.confirmation_id,
        expected_binding=approved.binding,
    )
    assert resumed.step.state == "ready"
    assert resumed.run.state == "running"
    with confirmation_database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(tool_policy_decisions)
                .where(tool_policy_decisions.c.step_id == environment.step.step_id)
            )
            == 2
        )


def test_reject_withdraw_and_expire_never_make_step_ready(
    confirmation_database: ApprovalHarness,
) -> None:
    """驳回、撤回和到期只关闭确认事实，均不会产生执行就绪状态。"""

    terminal_cases: tuple[
        tuple[Literal["reject", "withdraw"], Literal["rejected", "withdrawn"]], ...
    ] = (("reject", "rejected"), ("withdraw", "withdrawn"))
    for action, expected in terminal_cases:
        owner = register(confirmation_database, f"p406-{action}")
        environment = _environment(confirmation_database, owner, action)
        requested = environment.confirmations.request(
            environment.request_context,
            step_id=environment.step.step_id,
            idempotency_key=f"synthetic-p406-{action}-request",
        )
        environment.approvals.act(
            environment.request_context,
            approval_instance_id=requested.confirmation.approval_instance_id,
            command=ApprovalRuntimeCommand(
                action,
                owner.account_id,
                f"synthetic-p406-{action}-action",
                reason_code="synthetic_decision",
            ),
        )
        terminal = environment.store.get_confirmation(
            owner.workspace_id,
            requested.confirmation.confirmation_id,
        )
        assert terminal is not None and terminal.state == expected
        with pytest.raises(
            ToolConfirmationRequiredError if action == "reject" else ToolConfirmationStaleError
        ):
            environment.confirmations.resume(
                environment.request_context,
                confirmation_id=terminal.confirmation_id,
                expected_binding=terminal.binding,
            )

    owner = register(confirmation_database, "p406-expire")
    expiring = _environment(confirmation_database, owner, "expire")
    requested = expiring.confirmations.request(
        expiring.request_context,
        step_id=expiring.step.step_id,
        idempotency_key="synthetic-p406-expire-request",
    )
    expired = expiring.confirmations.expire(
        expiring.request_context,
        confirmation_id=requested.confirmation.confirmation_id,
        occurred_at=requested.confirmation.expires_at + timedelta(seconds=1),
    )
    assert expired.state == "expired"
    with confirmation_database.sessions() as session:
        assert (
            session.scalar(
                select(tool_steps.c.state).where(tool_steps.c.step_id == expiring.step.step_id)
            )
            == "waiting_confirmation"
        )


def test_binding_policy_and_scope_changes_append_invalidation_facts(
    confirmation_database: ApprovalHarness,
) -> None:
    """批准后参数、策略版本或权限范围变化均追加失效事实并拒绝恢复。"""

    argument_owner = register(confirmation_database, "p406-arguments")
    argument_env = _environment(confirmation_database, argument_owner, "arguments")
    argument_confirmation = _approve_personal(argument_env, "arguments")
    with pytest.raises(ToolConfirmationStaleError):
        argument_env.confirmations.resume(
            argument_env.request_context,
            confirmation_id=argument_confirmation.confirmation_id,
            expected_binding=replace(
                argument_confirmation.binding,
                canonical_arguments_hash="f" * 64,
            ),
        )

    policy_owner = register(confirmation_database, "p406-policy")
    policy_env = _environment(confirmation_database, policy_owner, "policy")
    policy_confirmation = _approve_personal(policy_env, "policy")
    policy_env.catalog.policy_version = 2
    with pytest.raises(ToolConfirmationStaleError):
        policy_env.confirmations.resume(
            policy_env.request_context,
            confirmation_id=policy_confirmation.confirmation_id,
            expected_binding=policy_confirmation.binding,
        )

    scope_owner = register(confirmation_database, "p406-scope")
    scope_env = _environment(confirmation_database, scope_owner, "scope")
    scope_confirmation = _approve_personal(scope_env, "scope")
    scope_env.catalog.field_mask = frozenset({"restricted_field"})
    with pytest.raises(ToolConfirmationStaleError):
        scope_env.confirmations.resume(
            scope_env.request_context,
            confirmation_id=scope_confirmation.confirmation_id,
            expected_binding=scope_confirmation.binding,
        )

    with confirmation_database.sessions() as session:
        reasons = set(
            session.scalars(
                select(tool_confirmation_invalidations.c.reason_code).where(
                    tool_confirmation_invalidations.c.confirmation_id.in_(
                        (
                            argument_confirmation.confirmation_id,
                            policy_confirmation.confirmation_id,
                            scope_confirmation.confirmation_id,
                        )
                    )
                )
            )
        )
    assert reasons == {"arguments_changed", "policy_changed", "permission_revoked"}


def test_enterprise_two_level_approval_and_agent_lifecycle_coexist(
    confirmation_database: ApprovalHarness,
) -> None:
    """企业写工具执行两级审批，组合路由同时保留 Agent 发布审批生命周期。"""

    routed = _routed_approvals(confirmation_database)
    owner = register(confirmation_database, "p406-enterprise-owner")
    approver_a = register(confirmation_database, "p406-enterprise-a")
    approver_b = register(confirmation_database, "p406-enterprise-b")
    enterprise = confirmation_database.enterprise.create(
        context(owner),
        name="合成 P4-06 企业",
    )
    workspace_id = enterprise.workspace_id
    add_enterprise_member(confirmation_database, owner, approver_a, workspace_id)
    add_enterprise_member(confirmation_database, owner, approver_b, workspace_id)
    enterprise_owner = replace(owner, workspace_id=workspace_id)
    confirmation_database.policies.create(
        context(enterprise_owner),
        name="合成工具两级审批",
        definition=ApprovalPolicyDefinition(
            "tool.call",
            "execute",
            100,
            (),
            ("CONFIDENTIAL",),
            ("high",),
            (),
            (
                ApprovalLevelDefinition(
                    1,
                    "any",
                    (ApprovalApproverSource("accounts", (approver_a.account_id,)),),
                ),
                ApprovalLevelDefinition(
                    2,
                    "all",
                    (ApprovalApproverSource("accounts", (approver_b.account_id,)),),
                ),
            ),
        ),
    )
    environment = _environment(
        confirmation_database,
        enterprise_owner,
        "enterprise",
        approvals=routed,
    )
    requested = environment.confirmations.request(
        environment.request_context,
        step_id=environment.step.step_id,
        idempotency_key="synthetic-p406-enterprise-request",
    )
    assert requested.confirmation.mode == "enterprise_approval"
    first = routed.act(
        context(replace(approver_a, workspace_id=workspace_id)),
        approval_instance_id=requested.confirmation.approval_instance_id,
        command=ApprovalRuntimeCommand(
            "approve",
            approver_a.account_id,
            "synthetic-p406-enterprise-a",
        ),
    )
    assert first.state.instance.status == "pending"
    second = routed.act(
        context(replace(approver_b, workspace_id=workspace_id)),
        approval_instance_id=requested.confirmation.approval_instance_id,
        command=ApprovalRuntimeCommand(
            "approve",
            approver_b.account_id,
            "synthetic-p406-enterprise-b",
        ),
    )
    assert second.state.instance.status == "approved"

    # 同一 Routed 生命周期继续执行既有 Agent 发布绑定，防止工具审批替换旧能力。
    agent_owner = register(confirmation_database, "p406-agent-route")
    routed_harness = replace(confirmation_database, approvals=routed)
    agents, candidate_id = prepare_candidate(routed_harness, agent_owner, "p406-agent-route")
    agent_request = agents.request_approval(
        context(agent_owner),
        candidate_id=candidate_id,
        idempotency_key="synthetic-p406-agent-request",
    )
    routed.act(
        context(agent_owner),
        approval_instance_id=agent_request.state.instance.approval_instance_id,
        command=ApprovalRuntimeCommand(
            "approve",
            agent_owner.account_id,
            "synthetic-p406-agent-approve",
        ),
    )
    assert candidate_status(routed_harness, candidate_id) == "approved"


def test_confirmation_identity_is_immutable_and_blocks_destructive_downgrade(
    confirmation_database: ApprovalHarness,
) -> None:
    """数据库拒绝篡改确认绑定，存在确认事实时 P4-06 不允许破坏性降级。"""

    owner = register(confirmation_database, "p406-tamper")
    environment = _environment(confirmation_database, owner, "tamper")
    confirmation = _approve_personal(environment, "tamper")
    with confirmation_database.sessions() as session:
        with pytest.raises(DBAPIError):
            session.execute(
                update(tool_confirmations)
                .where(tool_confirmations.c.confirmation_id == confirmation.confirmation_id)
                .values(confirmation_hash="0" * 64, version=confirmation.version + 1)
            )
            session.commit()
        session.rollback()
        assert (
            session.scalar(
                select(tool_runs.c.state).where(tool_runs.c.run_id == confirmation.binding.run_id)
            )
            == "waiting_confirmation"
        )

    schema_map = confirmation_database.engine.get_execution_options().get("schema_translate_map")
    assert isinstance(schema_map, dict)
    schema = schema_map.get("ai_platform")
    assert isinstance(schema, str)
    migration = Config(str(ROOT / "alembic.ini"))
    migration.set_main_option("script_location", str(ROOT / "infra/migrations"))
    migration.set_main_option(
        "prepend_sys_path",
        f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
    )
    migration.set_main_option(
        "sqlalchemy.url",
        confirmation_database.engine.url.render_as_string(hide_password=False),
    )
    migration.set_main_option("ai_platform_schema", schema)
    with pytest.raises(RuntimeError, match="拒绝破坏性降级"):
        command.downgrade(migration, "20260816_0055")
