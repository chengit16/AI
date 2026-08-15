"""验证 P3-05 Agent 个人与企业审批、失效和数据库防绕过闭环。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.modules.agent_control.application.service import (
    AgentControlService,
    AgentNotFoundError,
)
from ai_platform_api.modules.agent_control.domain.approval import (
    PERSONAL_OWNER_APPROVAL_POLICY_VERSION_ID,
)
from ai_platform_api.modules.agent_control.infrastructure.approval_sqlalchemy import (
    SqlAlchemyAgentApprovalSubjectLifecycle,
)
from ai_platform_api.modules.agent_control.infrastructure.sqlalchemy import (
    SqlAlchemyAgentControlUnitOfWork,
)
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
from ai_platform_api.modules.workflow.application.approval_runtime import (
    ApprovalInstanceDenied,
    ApprovalInstanceService,
)
from ai_platform_api.modules.workflow.application.approvals import ApprovalPolicyService
from ai_platform_api.modules.workflow.domain.approval_runtime import ApprovalRuntimeCommand
from ai_platform_api.modules.workflow.domain.approvals import (
    ApprovalApproverSource,
    ApprovalLevelDefinition,
    ApprovalPolicyDefinition,
)
from ai_platform_api.modules.workflow.infrastructure.approval_runtime_sqlalchemy import (
    SqlAlchemyApprovalRuntimeUnitOfWork,
)
from ai_platform_api.modules.workflow.infrastructure.approvals_sqlalchemy import (
    SqlAlchemyApprovalPolicyUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    accounts,
    agent_approval_bindings,
    agent_release_candidates,
    outbox_events,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_p304_agent_evaluation_postgres import (
    TRACE,
    EvaluationHarness,
    RegisteredAccount,
    SyntheticEvaluationExecutor,
    context,
    create_candidate_and_dataset,
)

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)


@dataclass(frozen=True)
class ApprovalHarness(EvaluationHarness):
    """扩展评估 Harness，集中持有企业、策略和审批运行服务。"""

    enterprise: EnterpriseWorkspaceService
    policies: ApprovalPolicyService
    approvals: ApprovalInstanceService


@pytest.fixture(scope="module")
def approval_database() -> Iterator[ApprovalHarness]:
    """从空 Schema 迁移到 head，并装配共享审批引擎与 Agent 生命周期 Adapter。"""

    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p305_test_{uuid4().hex}"
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
    policies = ApprovalPolicyService(SqlAlchemyApprovalPolicyUnitOfWork(sessions))
    approvals = ApprovalInstanceService(
        SqlAlchemyApprovalRuntimeUnitOfWork(
            sessions,
            SqlAlchemyAgentApprovalSubjectLifecycle,
        ),
        policies,
    )
    try:
        yield ApprovalHarness(
            engine,
            sessions,
            RegistrationService(
                SqlAlchemyIdentityReader(sessions),
                SqlAlchemyRegistrationUnitOfWork(sessions),
                Argon2idPasswordAdapter(),
            ),
            EnterpriseWorkspaceService(SqlAlchemyEnterpriseUnitOfWork(sessions)),
            policies,
            approvals,
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def register(harness: ApprovalHarness, identity: str) -> RegisteredAccount:
    """注册仅包含合成信息的账号和默认个人空间。"""

    result = harness.registration.register(
        login_name=f"synthetic.agent.approval.{identity}.{uuid4().hex}@example.com",
        display_name=f"合成 Agent 审批用户 {identity}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return RegisteredAccount(result.account_id, result.personal_workspace_id)


def service(
    harness: ApprovalHarness,
    executor: SyntheticEvaluationExecutor,
) -> AgentControlService:
    """装配共享审批运行时的 Agent 控制面服务。"""

    return AgentControlService(
        SqlAlchemyAgentControlUnitOfWork(harness.sessions),
        executor,
        harness.approvals,
    )


def prepare_candidate(
    harness: ApprovalHarness,
    owner: RegisteredAccount,
    suffix: str,
) -> tuple[AgentControlService, UUID]:
    """创建候选并完成确定性评估，使其进入待申请审批状态。"""

    agents = service(harness, SyntheticEvaluationExecutor())
    candidate, dataset = create_candidate_and_dataset(harness, agents, owner, suffix)
    report = agents.run_evaluation(
        context(owner),
        candidate_id=candidate.candidate_id,
        dataset_version_id=dataset.dataset_version_id,
    )
    assert report.run.status == "passed"
    return agents, candidate.candidate_id


def add_enterprise_member(
    harness: ApprovalHarness,
    owner: RegisteredAccount,
    member: RegisteredAccount,
    workspace_id: UUID,
) -> None:
    """通过正式邀请流程把合成账号加入企业空间。"""

    invitation = harness.enterprise.invite(
        context(replace(owner, workspace_id=workspace_id)),
        workspace_id=workspace_id,
        login_name=_login_name(harness.sessions, member.account_id),
    )
    harness.enterprise.accept_invitation(
        context(member),
        invitation_id=invitation.invitation_id,
    )


def _login_name(sessions: sessionmaker[Session], account_id: UUID) -> str:
    with sessions() as session:
        value = session.scalar(
            select(accounts.c.login_name).where(accounts.c.account_id == account_id)
        )
    assert isinstance(value, str)
    return value


def candidate_status(harness: ApprovalHarness, candidate_id: UUID) -> str:
    with harness.sessions() as session:
        value = session.scalar(
            select(agent_release_candidates.c.status).where(
                agent_release_candidates.c.candidate_id == candidate_id
            )
        )
    assert isinstance(value, str)
    return value


def test_personal_owner_can_approve_once_with_frozen_evidence(
    approval_database: ApprovalHarness,
) -> None:
    """个人空间固定所有者一级审批，并对申请和动作保持幂等。"""

    owner = register(approval_database, "personal-owner")
    agents, candidate_id = prepare_candidate(approval_database, owner, "personal")

    requested = agents.request_approval(
        context(owner),
        candidate_id=candidate_id,
        idempotency_key="synthetic-personal-agent-approval",
    )
    replayed = agents.request_approval(
        context(owner),
        candidate_id=candidate_id,
        idempotency_key="synthetic-personal-agent-approval",
    )

    assert requested.state.instance.personal_owner_confirmation is True
    assert requested.binding.approval_policy_version_id == PERSONAL_OWNER_APPROVAL_POLICY_VERSION_ID
    assert replayed.replayed is True
    assert replayed.binding == requested.binding
    assert candidate_status(approval_database, candidate_id) == "approval_pending"

    approved = approval_database.approvals.act(
        context(owner),
        approval_instance_id=requested.state.instance.approval_instance_id,
        command=ApprovalRuntimeCommand(
            "approve",
            owner.account_id,
            "synthetic-personal-approve",
        ),
    )
    repeated_action = approval_database.approvals.act(
        context(owner),
        approval_instance_id=requested.state.instance.approval_instance_id,
        command=ApprovalRuntimeCommand(
            "approve",
            owner.account_id,
            "synthetic-personal-approve",
        ),
    )

    assert approved.state.instance.status == "approved"
    assert repeated_action.replayed is True
    assert candidate_status(approval_database, candidate_id) == "approved"
    with approval_database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(outbox_events)
                .where(outbox_events.c.event_type == "agent.approval.approved")
            )
            or 0
        ) >= 1


def test_enterprise_two_level_policy_blocks_self_approval_and_freezes_version(
    approval_database: ApprovalHarness,
) -> None:
    """企业候选复用两级策略，自审限制和冻结策略版本在运行期保持有效。"""

    owner = register(approval_database, "enterprise-owner")
    approver_a = register(approval_database, "enterprise-approver-a")
    approver_b = register(approval_database, "enterprise-approver-b")
    enterprise = approval_database.enterprise.create(context(owner), name="合成 Agent 审批企业")
    workspace_id = enterprise.workspace_id
    add_enterprise_member(approval_database, owner, approver_a, workspace_id)
    add_enterprise_member(approval_database, owner, approver_b, workspace_id)
    enterprise_owner = replace(owner, workspace_id=workspace_id)
    policy, policy_version = approval_database.policies.create(
        context(enterprise_owner),
        name="Agent 发布两级审批",
        definition=ApprovalPolicyDefinition(
            "agent.release",
            "approve",
            100,
            (),
            ("INTERNAL",),
            ("high",),
            (),
            (
                ApprovalLevelDefinition(
                    1,
                    "any",
                    (ApprovalApproverSource("accounts", (approver_a.account_id,)),),
                    timeout_action="reject",
                ),
                ApprovalLevelDefinition(
                    2,
                    "all",
                    (ApprovalApproverSource("accounts", (approver_b.account_id,)),),
                ),
            ),
            allow_self_approval=False,
        ),
    )
    agents, candidate_id = prepare_candidate(
        approval_database,
        enterprise_owner,
        "enterprise-two-level",
    )
    requested = agents.request_approval(
        context(enterprise_owner),
        candidate_id=candidate_id,
        idempotency_key="synthetic-enterprise-agent-approval",
    )

    assert requested.binding.approval_policy_version_id == policy_version.approval_policy_version_id
    assert requested.state.instance.approval_policy_id == policy.approval_policy_id
    with pytest.raises(ApprovalInstanceDenied):
        approval_database.approvals.act(
            context(enterprise_owner),
            approval_instance_id=requested.state.instance.approval_instance_id,
            command=ApprovalRuntimeCommand(
                "approve",
                owner.account_id,
                "synthetic-owner-self-approve",
            ),
        )

    first = approval_database.approvals.act(
        context(replace(approver_a, workspace_id=workspace_id)),
        approval_instance_id=requested.state.instance.approval_instance_id,
        command=ApprovalRuntimeCommand(
            "approve",
            approver_a.account_id,
            "synthetic-enterprise-approve-a",
        ),
    )
    assert first.state.instance.status == "pending"
    assert first.state.instance.current_sequence_no == 2
    second = approval_database.approvals.act(
        context(replace(approver_b, workspace_id=workspace_id)),
        approval_instance_id=requested.state.instance.approval_instance_id,
        command=ApprovalRuntimeCommand(
            "approve",
            approver_b.account_id,
            "synthetic-enterprise-approve-b",
        ),
    )
    assert second.state.instance.status == "approved"
    assert candidate_status(approval_database, candidate_id) == "approved"


def test_timeout_rejection_updates_agent_candidate(
    approval_database: ApprovalHarness,
) -> None:
    """企业策略的超时驳回由既有调度逻辑执行，并同步拒绝 Agent 候选。"""

    owner = register(approval_database, "timeout-owner")
    approver = register(approval_database, "timeout-approver")
    enterprise = approval_database.enterprise.create(context(owner), name="合成超时审批企业")
    workspace_id = enterprise.workspace_id
    add_enterprise_member(approval_database, owner, approver, workspace_id)
    enterprise_owner = replace(owner, workspace_id=workspace_id)
    approval_database.policies.create(
        context(enterprise_owner),
        name="Agent 超时驳回策略",
        definition=ApprovalPolicyDefinition(
            "agent.release",
            "approve",
            100,
            (),
            ("INTERNAL",),
            ("high",),
            (),
            (
                ApprovalLevelDefinition(
                    1,
                    "any",
                    (ApprovalApproverSource("accounts", (approver.account_id,)),),
                    reminder_after_minutes=1,
                    timeout_after_minutes=2,
                    timeout_action="reject",
                ),
            ),
        ),
    )
    agents, candidate_id = prepare_candidate(approval_database, enterprise_owner, "timeout")
    requested = agents.request_approval(
        context(enterprise_owner),
        candidate_id=candidate_id,
        idempotency_key="synthetic-timeout-agent-approval",
    )
    timeout_at = requested.state.levels[0].timeout_at
    assert timeout_at is not None

    results = approval_database.approvals.process_due(
        context(enterprise_owner),
        limit=10,
        now=timeout_at + timedelta(seconds=1),
    )

    assert any(item.state.instance.status == "rejected" for item in results)
    assert candidate_status(approval_database, candidate_id) == "rejected"


def test_draft_change_supersedes_old_approval_and_database_rejects_bypass(
    approval_database: ApprovalHarness,
) -> None:
    """草稿变化立即使旧审批失效，数据库拒绝伪造状态和篡改绑定。"""

    owner = register(approval_database, "stale-owner")
    outsider = register(approval_database, "stale-outsider")
    agents, candidate_id = prepare_candidate(approval_database, owner, "stale")

    # 1. 没有绑定时不能直接把通过测试的候选推进到审批中。
    with approval_database.sessions() as session:
        with pytest.raises(DBAPIError):
            session.execute(
                update(agent_release_candidates)
                .where(agent_release_candidates.c.candidate_id == candidate_id)
                .values(status="approval_pending", version=3)
            )
        session.rollback()

    requested = agents.request_approval(
        context(owner),
        candidate_id=candidate_id,
        idempotency_key="synthetic-stale-agent-approval",
    )
    with approval_database.sessions() as session:
        # 审批实例尚未通过时，数据库不能被直接更新为候选通过。
        current_version = session.scalar(
            select(agent_release_candidates.c.version).where(
                agent_release_candidates.c.candidate_id == candidate_id
            )
        )
        assert isinstance(current_version, int)
        with pytest.raises(DBAPIError):
            session.execute(
                update(agent_release_candidates)
                .where(agent_release_candidates.c.candidate_id == candidate_id)
                .values(status="approved", version=current_version + 1)
            )
        session.rollback()
        with pytest.raises(DBAPIError):
            session.execute(
                update(agent_approval_bindings)
                .where(agent_approval_bindings.c.candidate_id == candidate_id)
                .values(candidate_hash="f" * 64)
            )
        session.rollback()

    _, draft = agents.get_agent(context(owner), agent_id=requested.binding.agent_id)
    replacement_prompt = agents.create_prompt_version(
        context(owner),
        name="合成失效 Prompt",
        template="仅回答新 revision 的合成问题。",
    )
    changed_configuration = {
        **draft.configuration,
        "prompt_version_id": str(replacement_prompt.prompt_version_id),
    }
    agents.update_draft(
        context(owner),
        agent_id=requested.binding.agent_id,
        expected_revision=draft.revision,
        configuration=changed_configuration,
        idempotency_key="synthetic-supersede-old-approval",
    )
    assert candidate_status(approval_database, candidate_id) == "superseded"

    historical = approval_database.approvals.act(
        context(owner),
        approval_instance_id=requested.state.instance.approval_instance_id,
        command=ApprovalRuntimeCommand(
            "approve",
            owner.account_id,
            "synthetic-approve-superseded",
        ),
    )
    assert historical.state.instance.status == "approved"
    assert candidate_status(approval_database, candidate_id) == "superseded"
    with pytest.raises(AgentNotFoundError):
        agents.get_approval(context(outsider), candidate_id=candidate_id)
