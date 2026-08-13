from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.authentication import AuthenticationService
from ai_platform_api.modules.identity.application.enterprise import (
    EnterpriseWorkspaceService,
    WorkspaceGovernanceDeniedError,
    WorkspaceGovernanceNotFoundError,
    WorkspaceLifecycleConflictError,
)
from ai_platform_api.modules.identity.application.errors import WorkspaceContextDeniedError
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.enterprise_sqlalchemy import (
    SqlAlchemyEnterpriseUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.security import (
    Argon2idPasswordAdapter,
    Sha256SecretDigester,
)
from ai_platform_api.modules.identity.infrastructure.session import ValkeySessionStore
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    accounts,
    audit_records,
    outbox_events,
    workspace_invitations,
    workspace_memberships,
)
from alembic import command
from alembic.config import Config
from redis import Redis
from sqlalchemy import Engine, create_engine, func, select, text, update
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
DEFAULT_VALKEY_URL = "redis://127.0.0.1:6379/13"
TRACE = TraceContext.continue_from("00-1123456789abcdef0123456789abcdef-1123456789abcdef-01")


@dataclass(frozen=True)
class RegisteredAccount:
    account_id: UUID
    personal_workspace_id: UUID
    login_name: str


@dataclass(frozen=True)
class EnterpriseHarness:
    engine: Engine
    registration: RegistrationService
    enterprise: EnterpriseWorkspaceService
    authentication: AuthenticationService


@pytest.fixture(scope="module")
def enterprise_database() -> Iterator[EnterpriseHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    valkey_url = os.environ.get("AI_PLATFORM_TEST_VALKEY_URL", DEFAULT_VALKEY_URL)
    schema = f"p1b02_test_{uuid4().hex}"
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
    passwords = Argon2idPasswordAdapter()
    session_store = ValkeySessionStore(valkey_url)
    try:
        yield EnterpriseHarness(
            engine=engine,
            registration=RegistrationService(
                repository=reader,
                unit_of_work=SqlAlchemyRegistrationUnitOfWork(sessions),
                passwords=passwords,
            ),
            enterprise=EnterpriseWorkspaceService(SqlAlchemyEnterpriseUnitOfWork(sessions)),
            authentication=AuthenticationService(
                repository=reader,
                sessions=session_store,
                passwords=passwords,
                secrets_digester=Sha256SecretDigester(),
                session_ttl_seconds=300,
            ),
        )
    finally:
        session_store.close()
        valkey = Redis.from_url(valkey_url)
        valkey.flushdb()
        valkey.close()
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def register(
    harness: EnterpriseHarness,
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


def test_enterprise_invite_join_switch_disable_reactivate_and_leave(
    enterprise_database: EnterpriseHarness,
) -> None:
    owner = register(
        enterprise_database,
        login_name="synthetic.owner.p1b02@example.com",
        display_name="合成企业所有者",
    )
    member = register(
        enterprise_database,
        login_name="synthetic.member.p1b02@example.com",
        display_name="合成企业成员",
    )
    outsider = register(
        enterprise_database,
        login_name="synthetic.outsider.p1b02@example.com",
        display_name="合成无关账号",
    )

    workspace = enterprise_database.enterprise.create(
        context(owner),
        name="  合成企业试点空间  ",
    )
    owner_context = context(owner, workspace.workspace_id)
    invitation = enterprise_database.enterprise.invite(
        owner_context,
        workspace_id=workspace.workspace_id,
        login_name="SYNTHETIC.MEMBER.P1B02@EXAMPLE.COM",
    )

    with pytest.raises(WorkspaceLifecycleConflictError):
        enterprise_database.enterprise.invite(
            owner_context,
            workspace_id=workspace.workspace_id,
            login_name=member.login_name,
        )
    with pytest.raises(WorkspaceGovernanceNotFoundError):
        enterprise_database.enterprise.accept_invitation(
            context(outsider),
            invitation_id=invitation.invitation_id,
        )

    joined = enterprise_database.enterprise.accept_invitation(
        context(member),
        invitation_id=invitation.invitation_id,
    )
    assert joined.workspace_id == workspace.workspace_id
    assert joined.membership_type == "member"
    switched = enterprise_database.enterprise.switch(
        context(member),
        workspace_id=workspace.workspace_id,
    )
    assert switched.membership_status == "active"

    token, _, _ = enterprise_database.authentication.login(
        member.login_name,
        "synthetic-password-123",
    )
    try:
        trusted = enterprise_database.authentication.browser_context(
            session_token=token,
            csrf_token=None,
            require_csrf=False,
            workspace_id=workspace.workspace_id,
            request_id=uuid4(),
            trace=TRACE,
        )
        assert trusted.workspace_id == workspace.workspace_id

        disabled = enterprise_database.enterprise.disable_member(
            owner_context,
            workspace_id=workspace.workspace_id,
            target_account_id=member.account_id,
        )
        assert disabled.status == "disabled"
        with pytest.raises(WorkspaceGovernanceDeniedError):
            enterprise_database.enterprise.switch(
                context(member),
                workspace_id=workspace.workspace_id,
            )
        with pytest.raises(WorkspaceContextDeniedError):
            enterprise_database.authentication.browser_context(
                session_token=token,
                csrf_token=None,
                require_csrf=False,
                workspace_id=workspace.workspace_id,
                request_id=uuid4(),
                trace=TRACE,
            )
    finally:
        enterprise_database.authentication.logout(token)

    reinvitation = enterprise_database.enterprise.invite(
        owner_context,
        workspace_id=workspace.workspace_id,
        login_name=member.login_name,
    )
    enterprise_database.enterprise.accept_invitation(
        context(member),
        invitation_id=reinvitation.invitation_id,
    )
    left = enterprise_database.enterprise.leave(
        context(member, workspace.workspace_id),
        workspace_id=workspace.workspace_id,
    )
    assert left.status == "left"

    with pytest.raises(WorkspaceLifecycleConflictError):
        enterprise_database.enterprise.leave(
            owner_context,
            workspace_id=workspace.workspace_id,
        )
    with pytest.raises(WorkspaceLifecycleConflictError):
        enterprise_database.enterprise.disable_member(
            owner_context,
            workspace_id=workspace.workspace_id,
            target_account_id=owner.account_id,
        )
    with pytest.raises(WorkspaceGovernanceDeniedError):
        enterprise_database.enterprise.invite(
            context(owner),
            workspace_id=workspace.workspace_id,
            login_name=outsider.login_name,
        )

    owner_spaces = enterprise_database.enterprise.list_workspaces(context(owner))
    assert {item.workspace_id for item in owner_spaces} == {
        owner.personal_workspace_id,
        workspace.workspace_id,
    }
    members = enterprise_database.enterprise.list_members(
        owner_context,
        workspace_id=workspace.workspace_id,
    )
    assert [(item.account_id, item.membership_type, item.status) for item in members] == [
        (member.account_id, "member", "left"),
        (owner.account_id, "owner", "active"),
    ]

    with enterprise_database.engine.connect() as connection:
        membership_id = connection.scalar(
            select(workspace_memberships.c.membership_id).where(
                workspace_memberships.c.workspace_id == workspace.workspace_id,
                workspace_memberships.c.account_id == member.account_id,
            )
        )
        membership_events = [
            (row.event_type, row.aggregate_version)
            for row in connection.execute(
                select(outbox_events.c.event_type, outbox_events.c.aggregate_version)
                .where(outbox_events.c.aggregate_id == membership_id)
                .order_by(outbox_events.c.occurred_at, outbox_events.c.event_id)
            )
        ]
        assert membership_events == [
            ("workspace.member.joined", 1),
            ("workspace.member.disabled", 2),
            ("workspace.member.joined", 3),
            ("workspace.member.left", 4),
        ]
        assert connection.scalar(
            select(func.count())
            .select_from(audit_records)
            .where(audit_records.c.workspace_id == workspace.workspace_id)
        ) == connection.scalar(
            select(func.count())
            .select_from(outbox_events)
            .where(outbox_events.c.workspace_id == workspace.workspace_id)
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(accounts)
                .where(accounts.c.account_id == member.account_id)
            )
            == 1
        )


def test_expired_invitation_is_closed_before_reinvite(
    enterprise_database: EnterpriseHarness,
) -> None:
    owner = register(
        enterprise_database,
        login_name="synthetic.expiry.owner.p1b02@example.com",
        display_name="合成过期邀请所有者",
    )
    member = register(
        enterprise_database,
        login_name="synthetic.expiry.member.p1b02@example.com",
        display_name="合成过期邀请成员",
    )
    workspace = enterprise_database.enterprise.create(context(owner), name="合成过期邀请空间")
    owner_context = context(owner, workspace.workspace_id)
    expired = enterprise_database.enterprise.invite(
        owner_context,
        workspace_id=workspace.workspace_id,
        login_name=member.login_name,
    )
    with enterprise_database.engine.begin() as connection:
        connection.execute(
            update(workspace_invitations)
            .where(workspace_invitations.c.invitation_id == expired.invitation_id)
            .values(
                created_at=func.now() - text("INTERVAL '8 days'"),
                expires_at=func.now() - text("INTERVAL '1 day'"),
            )
        )

    renewed = enterprise_database.enterprise.invite(
        owner_context,
        workspace_id=workspace.workspace_id,
        login_name=member.login_name,
    )
    assert renewed.invitation_id != expired.invitation_id
    with enterprise_database.engine.connect() as connection:
        statuses = connection.execute(
            select(workspace_invitations.c.status)
            .where(workspace_invitations.c.workspace_id == workspace.workspace_id)
            .order_by(workspace_invitations.c.created_at)
        ).scalars()
        assert tuple(statuses) == ("expired", "pending")


def test_database_allows_only_one_owner_membership(
    enterprise_database: EnterpriseHarness,
) -> None:
    owner = register(
        enterprise_database,
        login_name="synthetic.unique.owner.p1b02@example.com",
        display_name="合成唯一所有者",
    )
    second = register(
        enterprise_database,
        login_name="synthetic.second.owner.p1b02@example.com",
        display_name="合成第二所有者",
    )
    workspace = enterprise_database.enterprise.create(context(owner), name="合成唯一所有者空间")
    with pytest.raises(IntegrityError), enterprise_database.engine.begin() as connection:
        connection.execute(
            workspace_memberships.insert().values(
                membership_id=uuid4(),
                workspace_id=workspace.workspace_id,
                account_id=second.account_id,
                membership_type="owner",
                status="active",
                created_at=func.now(),
                updated_at=func.now(),
                version=1,
            )
        )
