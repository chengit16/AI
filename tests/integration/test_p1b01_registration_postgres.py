from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.authentication import AuthenticationService
from ai_platform_api.modules.identity.application.errors import (
    RegistrationConflictError,
    WorkspaceContextDeniedError,
)
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.entitlements_sqlalchemy import (
    SqlAlchemyEntitlementAccessReader,
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
    role_permission_grants,
    roles,
    workspace_memberships,
    workspaces,
)
from alembic import command
from alembic.config import Config
from redis import Redis
from sqlalchemy import Engine, create_engine, func, insert, select, text

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
DEFAULT_VALKEY_URL = "redis://127.0.0.1:6379/14"
TRACE = TraceContext.continue_from("00-0123456789abcdef0123456789abcdef-0123456789abcdef-01")


@dataclass(frozen=True)
class RegistrationHarness:
    schema: str
    engine: Engine
    registration: RegistrationService
    authentication: AuthenticationService


@pytest.fixture(scope="module")
def registration_database() -> Iterator[RegistrationHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    valkey_url = os.environ.get("AI_PLATFORM_TEST_VALKEY_URL", DEFAULT_VALKEY_URL)
    schema = f"p1b01_test_{uuid4().hex}"
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
    entitlements = SqlAlchemyEntitlementAccessReader(sessions)
    passwords = Argon2idPasswordAdapter()
    session_store = ValkeySessionStore(valkey_url)
    registration = RegistrationService(
        repository=reader,
        unit_of_work=SqlAlchemyRegistrationUnitOfWork(sessions),
        passwords=passwords,
    )
    authentication = AuthenticationService(
        repository=reader,
        sessions=session_store,
        passwords=passwords,
        secrets_digester=Sha256SecretDigester(),
        session_ttl_seconds=300,
        entitlements=entitlements,
    )
    try:
        yield RegistrationHarness(schema, engine, registration, authentication)
    finally:
        session_store.close()
        valkey = Redis.from_url(valkey_url)
        valkey.flushdb()
        valkey.close()
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def test_registration_creates_exactly_one_personal_workspace_with_audit_and_event(
    registration_database: RegistrationHarness,
) -> None:
    request_id = uuid4()
    result = registration_database.registration.register(
        login_name="  SYNTHETIC.P1B01@EXAMPLE.COM ",
        display_name="合成个人用户",
        password="synthetic-password-123",
        request_id=request_id,
        trace=TRACE,
    )

    with registration_database.engine.connect() as connection:
        account = connection.execute(
            select(accounts).where(accounts.c.account_id == result.account_id)
        ).one()
        workspace = connection.execute(
            select(workspaces).where(workspaces.c.workspace_id == result.personal_workspace_id)
        ).one()
        memberships = connection.execute(
            select(workspace_memberships).where(
                workspace_memberships.c.account_id == result.account_id
            )
        ).all()
        workspace_count = connection.scalar(
            select(func.count())
            .select_from(workspaces)
            .where(
                workspaces.c.owner_account_id == result.account_id,
                workspaces.c.workspace_type == "personal",
            )
        )
        audit = connection.execute(
            select(audit_records).where(audit_records.c.request_id == request_id)
        ).one()
        event = connection.execute(
            select(outbox_events).where(outbox_events.c.request_id == request_id)
        ).one()
        owner_permissions = set(
            connection.execute(
                select(role_permission_grants.c.permission_code)
                .select_from(
                    role_permission_grants.join(
                        roles,
                        (roles.c.workspace_id == role_permission_grants.c.workspace_id)
                        & (roles.c.role_id == role_permission_grants.c.role_id),
                    )
                )
                .where(
                    roles.c.workspace_id == result.personal_workspace_id,
                    roles.c.role_key == "workspace_owner",
                    role_permission_grants.c.permission_code.in_(
                        (
                            "knowledge.base.read",
                            "knowledge.document.read",
                            "knowledge.ingestion.read",
                            "knowledge.ingestion.retry",
                            "knowledge.production.access",
                        )
                    ),
                )
            ).scalars()
        )

    assert account.login_name == "synthetic.p1b01@example.com"
    assert account.password_hash.startswith("$argon2id$")
    assert workspace.workspace_type == "personal"
    assert workspace.owner_account_id == result.account_id
    assert workspace_count == 1
    assert len(memberships) == 1
    assert memberships[0].status == "active"
    assert audit.attributes == {"workspace_type": "personal"}
    assert event.payload == {"personal_workspace_id": str(result.personal_workspace_id)}
    assert owner_permissions == {
        "knowledge.base.read",
        "knowledge.document.read",
        "knowledge.ingestion.read",
        "knowledge.ingestion.retry",
        "knowledge.production.access",
    }

    login = registration_database.authentication.login(
        "synthetic.p1b01@example.com",
        "synthetic-password-123",
    )
    try:
        context = registration_database.authentication.browser_context(
            session_token=login.session_token,
            csrf_token=None,
            require_csrf=False,
            workspace_id=result.personal_workspace_id,
            request_id=uuid4(),
            trace=TRACE,
        )
        assert context.workspace_id == result.personal_workspace_id
    finally:
        registration_database.authentication.logout(login.session_token)


def test_duplicate_login_rolls_back_without_second_personal_workspace(
    registration_database: RegistrationHarness,
) -> None:
    with pytest.raises(RegistrationConflictError):
        registration_database.registration.register(
            login_name="SYNTHETIC.P1B01@EXAMPLE.COM",
            display_name="重复合成用户",
            password="synthetic-password-456",
            request_id=uuid4(),
            trace=TRACE,
        )

    with registration_database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(accounts)
                .where(accounts.c.login_name == "synthetic.p1b01@example.com")
            )
            == 1
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(workspaces)
                .where(workspaces.c.workspace_type == "personal")
            )
            == 1
        )


def test_personal_workspace_rejects_non_owner_even_with_active_membership(
    registration_database: RegistrationHarness,
) -> None:
    owner = registration_database.registration.register(
        login_name="synthetic.owner.p1b01@example.com",
        display_name="合成所有者",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    intruder = registration_database.registration.register(
        login_name="synthetic.intruder.p1b01@example.com",
        display_name="合成非所有者",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    with registration_database.engine.begin() as connection:
        connection.execute(
            insert(workspace_memberships).values(
                membership_id=uuid4(),
                workspace_id=owner.personal_workspace_id,
                account_id=intruder.account_id,
                membership_type="member",
                status="active",
                created_at=func.now(),
                updated_at=func.now(),
                version=1,
            )
        )
    login = registration_database.authentication.login(
        "synthetic.intruder.p1b01@example.com",
        "synthetic-password-123",
    )
    try:
        with pytest.raises(WorkspaceContextDeniedError):
            registration_database.authentication.browser_context(
                session_token=login.session_token,
                csrf_token=None,
                require_csrf=False,
                workspace_id=owner.personal_workspace_id,
                request_id=uuid4(),
                trace=TRACE,
            )
    finally:
        registration_database.authentication.logout(login.session_token)
