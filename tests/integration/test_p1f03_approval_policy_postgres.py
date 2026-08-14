"""验证 P1F-03 审批策略版本、组织来源和 HTTP 预计算的 PostgreSQL 闭环。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.identity.application.enterprise import EnterpriseWorkspaceService
from ai_platform_api.modules.identity.application.organization import OrganizationService
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.application.roles import RoleService
from ai_platform_api.modules.identity.infrastructure.enterprise_sqlalchemy import (
    SqlAlchemyEnterpriseUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.organization_sqlalchemy import (
    SqlAlchemyOrganizationUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.roles_sqlalchemy import (
    SqlAlchemyRoleUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.modules.workflow.api.approval_routes import router as approval_router
from ai_platform_api.modules.workflow.application.approvals import (
    ApprovalApproverUnavailable,
    ApprovalPolicyService,
)
from ai_platform_api.modules.workflow.domain.approvals import (
    ApprovalApproverSource,
    ApprovalLevelDefinition,
    ApprovalPolicyDefinition,
    ApprovalSubject,
)
from ai_platform_api.modules.workflow.infrastructure.approvals_sqlalchemy import (
    SqlAlchemyApprovalPolicyUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    approval_policy_versions,
    audit_records,
    outbox_events,
)
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext("c" * 32, "d" * 16)


@dataclass(frozen=True)
class RegisteredAccount:
    """保留合成账号、登录名和默认个人空间标识。"""

    account_id: UUID
    login_name: str
    workspace_id: UUID


@dataclass(frozen=True)
class ApprovalHarness:
    """集中持有临时 Schema 所需的身份、组织、角色和审批服务。"""

    engine: Engine
    sessions: sessionmaker[Session]
    registration: RegistrationService
    enterprise: EnterpriseWorkspaceService
    organization: OrganizationService
    roles: RoleService
    approvals: ApprovalPolicyService


@pytest.fixture(scope="module")
def approval_database() -> Iterator[ApprovalHarness]:
    """在独立 Schema 迁移到 head，测试结束后整体删除合成事实。"""

    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1f03_test_{uuid4().hex}"
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
        yield ApprovalHarness(
            engine,
            sessions,
            RegistrationService(
                SqlAlchemyIdentityReader(sessions),
                SqlAlchemyRegistrationUnitOfWork(sessions),
                Argon2idPasswordAdapter(),
            ),
            EnterpriseWorkspaceService(SqlAlchemyEnterpriseUnitOfWork(sessions)),
            OrganizationService(SqlAlchemyOrganizationUnitOfWork(sessions)),
            RoleService(SqlAlchemyRoleUnitOfWork(sessions)),
            ApprovalPolicyService(SqlAlchemyApprovalPolicyUnitOfWork(sessions)),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def register(harness: ApprovalHarness, identity: str) -> RegisteredAccount:
    """注册仅含合成信息的账号。"""

    login_name = f"synthetic.approval.{identity}.{uuid4().hex}@example.com"
    result = harness.registration.register(
        login_name=login_name,
        display_name=f"合成审批用户 {identity}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return RegisteredAccount(result.account_id, login_name, result.personal_workspace_id)


def context(account: RegisteredAccount, *, workspace_id: UUID | None = None) -> RequestContext:
    """构造已获工作空间范围的可信浏览器上下文。"""

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


def test_all_approver_sources_versions_and_http_preview(
    approval_database: ApprovalHarness,
) -> None:
    """四类审批人来源在同一组织事实下解析，并由 API 返回稳定审批链。"""

    # 1. 建立企业、两级部门和跨部门负责人职位，所有数据均为合成测试事实。
    owner = register(approval_database, "owner")
    approver = register(approval_database, "approver")
    enterprise = approval_database.enterprise.create(context(owner), name="合成审批企业")
    workspace_id = enterprise.workspace_id
    owner_context = context(owner, workspace_id=workspace_id)
    invitation = approval_database.enterprise.invite(
        owner_context,
        workspace_id=workspace_id,
        login_name=approver.login_name,
    )
    approval_database.enterprise.accept_invitation(
        context(approver),
        invitation_id=invitation.invitation_id,
    )
    parent = approval_database.organization.create_department(
        owner_context,
        workspace_id=workspace_id,
        name="合成总部",
        parent_department_id=None,
    )
    child = approval_database.organization.create_department(
        owner_context,
        workspace_id=workspace_id,
        name="合成业务部",
        parent_department_id=parent.department_id,
    )
    parent_manager = approval_database.organization.create_position(
        owner_context,
        workspace_id=workspace_id,
        department_id=parent.department_id,
        name="合成总部负责人",
    )
    child_manager = approval_database.organization.create_position(
        owner_context,
        workspace_id=workspace_id,
        department_id=child.department_id,
        name="合成部门负责人",
    )
    approval_database.organization.assign_member(
        owner_context,
        workspace_id=workspace_id,
        target_account_id=owner.account_id,
        department_ids=(child.department_id,),
        primary_department_id=child.department_id,
        position_ids=(),
    )
    approval_database.organization.assign_member(
        owner_context,
        workspace_id=workspace_id,
        target_account_id=approver.account_id,
        department_ids=(parent.department_id, child.department_id),
        primary_department_id=child.department_id,
        position_ids=(parent_manager.position_id, child_manager.position_id),
    )

    # 2. 自定义角色直接绑定审批人，策略四级分别覆盖账号、角色、部门和上级负责人。
    reviewer_role = approval_database.roles.create(
        owner_context,
        workspace_id=workspace_id,
        role_key="synthetic_reviewer",
        name="合成审批角色",
    )
    approval_database.roles.bind(
        owner_context,
        workspace_id=workspace_id,
        role_id=reviewer_role.role_id,
        scope_type="member",
        department_id=None,
        target_account_id=approver.account_id,
    )
    definition = ApprovalPolicyDefinition(
        "workflow.approval",
        "submit",
        100,
        (child.department_id,),
        ("CONFIDENTIAL",),
        ("high",),
        (),
        (
            ApprovalLevelDefinition(
                1,
                "any",
                (ApprovalApproverSource("accounts", (approver.account_id,)),),
            ),
            ApprovalLevelDefinition(
                2,
                "all",
                (ApprovalApproverSource("roles", (reviewer_role.role_id,)),),
            ),
            ApprovalLevelDefinition(
                3,
                "any",
                (
                    ApprovalApproverSource(
                        "department_managers",
                        (child_manager.position_id,),
                    ),
                ),
            ),
            ApprovalLevelDefinition(
                4,
                "any",
                (
                    ApprovalApproverSource(
                        "upper_managers",
                        (parent_manager.position_id,),
                        1,
                    ),
                ),
            ),
        ),
    )
    policy, first_version = approval_database.approvals.create(
        owner_context,
        name="合成四级审批",
        definition=definition,
    )
    revised_policy, second_version = approval_database.approvals.revise(
        owner_context,
        approval_policy_id=policy.approval_policy_id,
        expected_version=policy.version,
        definition=replace(definition, priority=200),
    )
    subject = ApprovalSubject(
        workspace_id,
        owner.account_id,
        "workflow.approval",
        "submit",
        None,
        (child.department_id,),
        "CONFIDENTIAL",
        "high",
        {"amount": 1_800},
    )
    chain = approval_database.approvals.preview_chain(owner_context, subject=subject)

    assert second_version.version_number == 2
    assert revised_policy.current_version_id == second_version.approval_policy_version_id
    assert chain.approval_policy_version_id == second_version.approval_policy_version_id
    assert [level.mode for level in chain.levels] == ["any", "all", "any", "any"]
    assert all(level.approver_account_ids == (approver.account_id,) for level in chain.levels)

    # 3. HTTP 使用认证账号构造申请人，客户端只提交业务主题，返回结果与应用服务一致。
    application = FastAPI()
    application.state.approval_policy_service = approval_database.approvals
    application.include_router(approval_router, prefix="/api/v1")
    application.dependency_overrides[trusted_request_context] = lambda: owner_context
    client = TestClient(application)
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/approval-policies/preview-chain",
        json={
            "resource_type": "workflow.approval",
            "operation": "submit",
            "department_ids": [str(child.department_id)],
            "security_level": "CONFIDENTIAL",
            "risk_level": "high",
            "fields": {"amount": 1_800},
        },
    )
    assert response.status_code == 200
    assert response.json()["chain_digest"] == chain.chain_digest

    # 4. 历史版本由数据库触发器保护，审计与 Outbox 只记录摘要和版本标识。
    with pytest.raises(DBAPIError), approval_database.sessions.begin() as session:
        session.execute(
            update(approval_policy_versions)
            .where(
                approval_policy_versions.c.approval_policy_version_id
                == first_version.approval_policy_version_id
            )
            .values(definition={"schema_version": 1})
        )
    with approval_database.sessions() as session:
        assert (session.scalar(select(func.count()).select_from(audit_records)) or 0) >= 2
        assert (session.scalar(select(func.count()).select_from(outbox_events)) or 0) >= 2

    # 停用角色后第二级不再解析审批人，新审批链按具体层级失败关闭。
    approval_database.roles.set_status(
        owner_context,
        workspace_id=workspace_id,
        role_id=reviewer_role.role_id,
        active=False,
    )
    with pytest.raises(ApprovalApproverUnavailable) as captured:
        approval_database.approvals.preview_chain(owner_context, subject=subject)
    assert captured.value.sequence_no == 2


def test_personal_workspace_uses_owner_confirmation_without_policy(
    approval_database: ApprovalHarness,
) -> None:
    """个人空间不要求企业多级策略，数据库所有者事实直接生成单级确认。"""

    owner = register(approval_database, "personal-owner")
    owner_context = context(owner)
    chain = approval_database.approvals.preview_chain(
        owner_context,
        subject=ApprovalSubject(
            owner.workspace_id,
            owner.account_id,
            "workflow.approval",
            "submit",
            None,
            (),
            "INTERNAL",
            "normal",
            {},
        ),
    )

    assert chain.personal_owner_confirmation is True
    assert chain.approval_policy_version_id is None
    assert chain.levels[0].approver_account_ids == (owner.account_id,)
