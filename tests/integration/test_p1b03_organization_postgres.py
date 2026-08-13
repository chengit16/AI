from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.enterprise import EnterpriseWorkspaceService
from ai_platform_api.modules.identity.application.organization import (
    OrganizationConflictError,
    OrganizationGovernanceDeniedError,
    OrganizationService,
)
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.enterprise_sqlalchemy import (
    SqlAlchemyEnterpriseUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.organization_sqlalchemy import (
    SqlAlchemyOrganizationUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    audit_records,
    department_closure,
    membership_departments,
    membership_positions,
    outbox_events,
    positions,
    workspace_memberships,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, insert, select, text
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext.continue_from("00-2123456789abcdef0123456789abcdef-2123456789abcdef-01")


@dataclass(frozen=True)
class RegisteredAccount:
    account_id: UUID
    personal_workspace_id: UUID
    login_name: str


@dataclass(frozen=True)
class OrganizationHarness:
    engine: Engine
    registration: RegistrationService
    enterprise: EnterpriseWorkspaceService
    organization: OrganizationService


@pytest.fixture(scope="module")
def organization_database() -> Iterator[OrganizationHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1b03_test_{uuid4().hex}"
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
    reader = SqlAlchemyIdentityReader(sessions)
    try:
        yield OrganizationHarness(
            engine=engine,
            registration=RegistrationService(
                repository=reader,
                unit_of_work=SqlAlchemyRegistrationUnitOfWork(sessions),
                passwords=Argon2idPasswordAdapter(),
            ),
            enterprise=EnterpriseWorkspaceService(SqlAlchemyEnterpriseUnitOfWork(sessions)),
            organization=OrganizationService(SqlAlchemyOrganizationUnitOfWork(sessions)),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def register(
    harness: OrganizationHarness,
    *,
    login_name: str,
    display_name: str,
) -> RegisteredAccount:
    result = harness.registration.register(
        login_name=login_name,
        display_name=display_name,
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return RegisteredAccount(result.account_id, result.personal_workspace_id, login_name)


def context(account: RegisteredAccount, workspace_id: UUID | None = None) -> RequestContext:
    return RequestContext.trusted(
        actor_id=account.account_id,
        user_id=account.account_id,
        workspace_id=workspace_id or account.personal_workspace_id,
        trace=TRACE,
        authentication_method="browser_session",
    )


def join_workspace(
    harness: OrganizationHarness,
    owner: RegisteredAccount,
    member: RegisteredAccount,
) -> tuple[UUID, RequestContext]:
    workspace = harness.enterprise.create(context(owner), name="合成组织测试企业")
    owner_context = context(owner, workspace.workspace_id)
    invitation = harness.enterprise.invite(
        owner_context,
        workspace_id=workspace.workspace_id,
        login_name=member.login_name,
    )
    harness.enterprise.accept_invitation(
        context(member),
        invitation_id=invitation.invitation_id,
    )
    return workspace.workspace_id, owner_context


def test_department_tree_move_status_position_and_member_assignment(
    organization_database: OrganizationHarness,
) -> None:
    owner = register(
        organization_database,
        login_name="synthetic.org.owner.p1b03@example.com",
        display_name="合成组织所有者",
    )
    member = register(
        organization_database,
        login_name="synthetic.org.member.p1b03@example.com",
        display_name="合成组织成员",
    )
    workspace_id, owner_context = join_workspace(organization_database, owner, member)

    root = organization_database.organization.create_department(
        owner_context,
        workspace_id=workspace_id,
        name="合成总部",
        parent_department_id=None,
    )
    research = organization_database.organization.create_department(
        owner_context,
        workspace_id=workspace_id,
        name="合成研发部",
        parent_department_id=root.department_id,
    )
    platform = organization_database.organization.create_department(
        owner_context,
        workspace_id=workspace_id,
        name="合成平台组",
        parent_department_id=research.department_id,
    )
    finance = organization_database.organization.create_department(
        owner_context,
        workspace_id=workspace_id,
        name="合成财务部",
        parent_department_id=root.department_id,
    )

    assert [
        item.depth
        for item in organization_database.organization.list_departments(
            owner_context, workspace_id=workspace_id
        )
    ] == [0, 1, 1, 2]
    moved = organization_database.organization.move_department(
        owner_context,
        workspace_id=workspace_id,
        department_id=platform.department_id,
        parent_department_id=root.department_id,
    )
    assert moved.depth == 1
    with pytest.raises(OrganizationConflictError):
        organization_database.organization.move_department(
            owner_context,
            workspace_id=workspace_id,
            department_id=root.department_id,
            parent_department_id=research.department_id,
        )
    with pytest.raises(OrganizationConflictError):
        organization_database.organization.create_department(
            owner_context,
            workspace_id=workspace_id,
            name="合成研发部",
            parent_department_id=root.department_id,
        )

    research_position = organization_database.organization.create_position(
        owner_context,
        workspace_id=workspace_id,
        department_id=research.department_id,
        name="合成研发工程师",
    )
    finance_position = organization_database.organization.create_position(
        owner_context,
        workspace_id=workspace_id,
        department_id=finance.department_id,
        name="合成财务专员",
    )
    assignment = organization_database.organization.assign_member(
        owner_context,
        workspace_id=workspace_id,
        target_account_id=member.account_id,
        department_ids=(platform.department_id, research.department_id),
        primary_department_id=research.department_id,
        position_ids=(research_position.position_id,),
    )
    assert assignment.department_ids == tuple(
        sorted((research.department_id, platform.department_id), key=lambda value: value.int)
    )
    assert assignment.primary_department_id == research.department_id
    assert assignment.membership_version == 2
    assert (
        organization_database.organization.get_assignment(
            owner_context,
            workspace_id=workspace_id,
            target_account_id=member.account_id,
        )
        == assignment
    )
    with pytest.raises(OrganizationConflictError):
        organization_database.organization.assign_member(
            owner_context,
            workspace_id=workspace_id,
            target_account_id=member.account_id,
            department_ids=(research.department_id,),
            primary_department_id=research.department_id,
            position_ids=(finance_position.position_id,),
        )

    disabled = organization_database.organization.set_department_status(
        owner_context,
        workspace_id=workspace_id,
        department_id=root.department_id,
        active=False,
    )
    assert disabled.effective_active is False
    assert all(
        item.effective_active is False
        for item in organization_database.organization.list_departments(
            owner_context, workspace_id=workspace_id
        )
    )
    assert all(
        item.effective_active is False
        for item in organization_database.organization.list_positions(
            owner_context, workspace_id=workspace_id
        )
    )
    organization_database.organization.set_department_status(
        owner_context,
        workspace_id=workspace_id,
        department_id=root.department_id,
        active=True,
    )

    organization_database.enterprise.disable_member(
        owner_context,
        workspace_id=workspace_id,
        target_account_id=member.account_id,
    )
    with organization_database.engine.connect() as connection:
        membership_id = connection.scalar(
            select(workspace_memberships.c.membership_id).where(
                workspace_memberships.c.workspace_id == workspace_id,
                workspace_memberships.c.account_id == member.account_id,
            )
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(membership_departments)
                .where(
                    membership_departments.c.workspace_id == workspace_id,
                    membership_departments.c.membership_id == membership_id,
                )
            )
            == 0
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(membership_positions)
                .where(
                    membership_positions.c.workspace_id == workspace_id,
                    membership_positions.c.membership_id == membership_id,
                )
            )
            == 0
        )
        assert (
            connection.scalar(
                select(workspace_memberships.c.version).where(
                    workspace_memberships.c.membership_id == membership_id
                )
            )
            == 3
        )
        assert connection.scalar(
            select(func.count())
            .select_from(audit_records)
            .where(audit_records.c.workspace_id == workspace_id)
        ) == connection.scalar(
            select(func.count())
            .select_from(outbox_events)
            .where(outbox_events.c.workspace_id == workspace_id)
        )


def test_database_rejects_cross_workspace_organization_links(
    organization_database: OrganizationHarness,
) -> None:
    first_owner = register(
        organization_database,
        login_name="synthetic.cross.first.p1b03@example.com",
        display_name="合成第一组织所有者",
    )
    second_owner = register(
        organization_database,
        login_name="synthetic.cross.second.p1b03@example.com",
        display_name="合成第二组织所有者",
    )
    first_workspace = organization_database.enterprise.create(
        context(first_owner), name="合成第一企业"
    )
    second_workspace = organization_database.enterprise.create(
        context(second_owner), name="合成第二企业"
    )
    department = organization_database.organization.create_department(
        context(first_owner, first_workspace.workspace_id),
        workspace_id=first_workspace.workspace_id,
        name="合成隔离部门",
        parent_department_id=None,
    )
    with pytest.raises(IntegrityError), organization_database.engine.begin() as connection:
        connection.execute(
            insert(positions).values(
                position_id=uuid4(),
                workspace_id=second_workspace.workspace_id,
                department_id=department.department_id,
                name="合成越权岗位",
                status="active",
                created_at=func.now(),
                updated_at=func.now(),
                version=1,
            )
        )

    with pytest.raises(OrganizationGovernanceDeniedError):
        organization_database.organization.create_department(
            context(first_owner),
            workspace_id=first_owner.personal_workspace_id,
            name="个人空间非法部门",
            parent_department_id=None,
        )


def test_closure_matches_current_adjacency_after_move(
    organization_database: OrganizationHarness,
) -> None:
    owner = register(
        organization_database,
        login_name="synthetic.closure.owner.p1b03@example.com",
        display_name="合成闭包所有者",
    )
    workspace = organization_database.enterprise.create(context(owner), name="合成闭包企业")
    owner_context = context(owner, workspace.workspace_id)
    root = organization_database.organization.create_department(
        owner_context,
        workspace_id=workspace.workspace_id,
        name="合成闭包根",
        parent_department_id=None,
    )
    child = organization_database.organization.create_department(
        owner_context,
        workspace_id=workspace.workspace_id,
        name="合成闭包子",
        parent_department_id=root.department_id,
    )
    leaf = organization_database.organization.create_department(
        owner_context,
        workspace_id=workspace.workspace_id,
        name="合成闭包叶",
        parent_department_id=child.department_id,
    )
    organization_database.organization.move_department(
        owner_context,
        workspace_id=workspace.workspace_id,
        department_id=leaf.department_id,
        parent_department_id=root.department_id,
    )
    with organization_database.engine.connect() as connection:
        ancestors = connection.execute(
            select(
                department_closure.c.ancestor_department_id,
                department_closure.c.depth,
            )
            .where(
                department_closure.c.workspace_id == workspace.workspace_id,
                department_closure.c.descendant_department_id == leaf.department_id,
            )
            .order_by(department_closure.c.depth)
        ).all()
        assert [(row.ancestor_department_id, row.depth) for row in ancestors] == [
            (leaf.department_id, 0),
            (root.department_id, 1),
        ]
