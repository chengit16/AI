from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.authentication import (
    ApiKeyService,
    AuthenticationService,
)
from ai_platform_api.modules.identity.application.errors import (
    ApiKeyInvalidError,
    AuthenticationRequiredError,
    WorkspaceContextDeniedError,
)
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
    SqlAlchemyIdentityUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    accounts,
    open_api_keys,
    workspace_entitlements,
    workspace_feature_settings,
    workspace_memberships,
    workspaces,
)
from alembic import command
from alembic.config import Config
from redis import Redis
from sqlalchemy import Engine, create_engine, insert, select, text, update
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
DEFAULT_VALKEY_URL = "redis://127.0.0.1:6379/15"
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000011")
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000011")
OTHER_WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000012")
MEMBERSHIP_ID = UUID("30000000-0000-4000-8000-000000000011")
TRACE = TraceContext.continue_from("00-0123456789abcdef0123456789abcdef-0123456789abcdef-01")


@dataclass(frozen=True)
class IdentityHarness:
    schema: str
    engine: Engine
    sessions: sessionmaker[Session]
    session_store: ValkeySessionStore
    authentication: AuthenticationService
    api_keys: ApiKeyService
    valkey_url: str


@pytest.fixture(scope="module")
def identity_database() -> Iterator[IdentityHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    valkey_url = os.environ.get("AI_PLATFORM_TEST_VALKEY_URL", DEFAULT_VALKEY_URL)
    schema = f"p1a04_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "infra/migrations"))
    config.set_main_option("prepend_sys_path", str(ROOT / "apps/api/src"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("ai_platform_schema", schema)
    command.upgrade(config, "head")

    engine = create_platform_engine(database_url, schema)
    sessions = create_session_factory(engine)
    passwords = Argon2idPasswordAdapter()
    now = datetime.now(UTC)
    with sessions.begin() as session:
        session.execute(
            insert(accounts).values(
                account_id=ACCOUNT_ID,
                login_name="owner@example.com",
                display_name="合成空间所有者",
                password_hash=passwords.hash("synthetic-password-123"),
                status="active",
                auth_version=1,
                created_at=now,
                created_by_actor_id=ACCOUNT_ID,
                updated_at=now,
                updated_by_actor_id=ACCOUNT_ID,
                version=1,
            )
        )
        session.execute(
            insert(workspaces),
            [
                {
                    "workspace_id": WORKSPACE_ID,
                    "workspace_type": "personal",
                    "name": "合成个人空间",
                    "owner_account_id": ACCOUNT_ID,
                    "entitlement_version": 1,
                    "status": "active",
                    "created_at": now,
                    "created_by_actor_id": ACCOUNT_ID,
                    "updated_at": now,
                    "updated_by_actor_id": ACCOUNT_ID,
                    "version": 1,
                },
                {
                    "workspace_id": OTHER_WORKSPACE_ID,
                    "workspace_type": "enterprise",
                    "name": "无成员关系的合成企业空间",
                    "owner_account_id": None,
                    "entitlement_version": 1,
                    "status": "active",
                    "created_at": now,
                    "created_by_actor_id": ACCOUNT_ID,
                    "updated_at": now,
                    "updated_by_actor_id": ACCOUNT_ID,
                    "version": 1,
                },
            ],
        )
        session.execute(
            insert(workspace_memberships).values(
                membership_id=MEMBERSHIP_ID,
                workspace_id=WORKSPACE_ID,
                account_id=ACCOUNT_ID,
                membership_type="owner",
                status="active",
                created_at=now,
                updated_at=now,
                version=1,
            )
        )
        # 该夹具专门验证 API Key 生命周期，因此显式开启测试权益，避免依赖商业套餐默认值。
        session.execute(
            insert(workspace_entitlements).values(
                workspace_id=WORKSPACE_ID,
                plan_code="identity_test",
                max_storage_bytes=0,
                max_members=1,
                max_knowledge_bases=0,
                max_published_agents=0,
                max_monthly_questions=0,
                open_api_allowed=True,
                public_publish_allowed=False,
                created_at=now,
                updated_at=now,
                version=1,
            )
        )
        session.execute(
            insert(workspace_feature_settings).values(
                workspace_id=WORKSPACE_ID,
                open_api_enabled=True,
                updated_at=now,
                version=1,
            )
        )

    session_store = ValkeySessionStore(valkey_url)
    reader = SqlAlchemyIdentityReader(sessions)
    entitlements = SqlAlchemyEntitlementAccessReader(sessions)
    digester = Sha256SecretDigester()
    harness = IdentityHarness(
        schema=schema,
        engine=engine,
        sessions=sessions,
        session_store=session_store,
        authentication=AuthenticationService(
            reader,
            session_store,
            passwords,
            digester,
            session_ttl_seconds=300,
            entitlements=entitlements,
        ),
        api_keys=ApiKeyService(
            reader,
            SqlAlchemyIdentityUnitOfWork(sessions),
            digester,
            entitlements,
        ),
        valkey_url=valkey_url,
    )
    try:
        yield harness
    finally:
        session_store.close()
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def browser_context() -> RequestContext:
    return RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TRACE,
        authentication_method="browser_session",
    )


def test_session_token_is_hashed_in_valkey_and_cross_workspace_is_denied(
    identity_database: IdentityHarness,
) -> None:
    token, csrf_token, _ = identity_database.authentication.login(
        "owner@example.com",
        "synthetic-password-123",
    )
    client = Redis.from_url(identity_database.valkey_url, decode_responses=True)
    try:
        matching_keys = cast(list[str], client.keys("session:v1:*"))

        assert matching_keys
        assert all(token not in key for key in matching_keys)
        with pytest.raises(WorkspaceContextDeniedError):
            identity_database.authentication.browser_context(
                session_token=token,
                csrf_token=csrf_token,
                require_csrf=True,
                workspace_id=OTHER_WORKSPACE_ID,
                request_id=uuid4(),
                trace=TRACE,
            )
    finally:
        identity_database.authentication.logout(token)
        client.close()


def test_membership_change_is_checked_on_each_request(
    identity_database: IdentityHarness,
) -> None:
    token, _, _ = identity_database.authentication.login(
        "owner@example.com",
        "synthetic-password-123",
    )
    with identity_database.sessions.begin() as session:
        session.execute(
            update(workspace_memberships)
            .where(workspace_memberships.c.membership_id == MEMBERSHIP_ID)
            .values(status="disabled")
        )
    try:
        with pytest.raises(WorkspaceContextDeniedError):
            identity_database.authentication.browser_context(
                session_token=token,
                csrf_token=None,
                require_csrf=False,
                workspace_id=WORKSPACE_ID,
                request_id=uuid4(),
                trace=TRACE,
            )
    finally:
        with identity_database.sessions.begin() as session:
            session.execute(
                update(workspace_memberships)
                .where(workspace_memberships.c.membership_id == MEMBERSHIP_ID)
                .values(status="active")
            )
        identity_database.authentication.logout(token)


def test_api_key_plaintext_never_enters_postgres_and_revocation_is_immediate(
    identity_database: IdentityHarness,
) -> None:
    issued = identity_database.api_keys.issue(
        context=browser_context(),
        name="合成集成测试 Key",
        scopes=("knowledge.document.read",),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    with identity_database.sessions() as session:
        row = session.execute(
            select(open_api_keys).where(open_api_keys.c.key_id == issued.key_id)
        ).one()

    assert row.last_four == issued.last_four
    assert row.secret_digest not in issued.plaintext
    assert issued.plaintext not in repr(row)
    context = identity_database.authentication.api_key_context(
        credential=issued.plaintext,
        workspace_id=WORKSPACE_ID,
        request_id=uuid4(),
        trace=TRACE,
    )
    assert context.actor_id == issued.actor_id
    assert context.credential_scopes == frozenset({"knowledge.document.read"})

    identity_database.api_keys.revoke(context=browser_context(), key_id=issued.key_id)
    with pytest.raises(ApiKeyInvalidError):
        identity_database.authentication.api_key_context(
            credential=issued.plaintext,
            workspace_id=WORKSPACE_ID,
            request_id=uuid4(),
            trace=TRACE,
        )


def test_auth_version_change_invalidates_existing_session(
    identity_database: IdentityHarness,
) -> None:
    token, _, _ = identity_database.authentication.login(
        "owner@example.com",
        "synthetic-password-123",
    )
    with identity_database.sessions.begin() as session:
        session.execute(
            update(accounts).where(accounts.c.account_id == ACCOUNT_ID).values(auth_version=2)
        )

    with pytest.raises(AuthenticationRequiredError):
        identity_database.authentication.browser_context(
            session_token=token,
            csrf_token=None,
            require_csrf=False,
            workspace_id=WORKSPACE_ID,
            request_id=uuid4(),
            trace=TRACE,
        )

    with identity_database.sessions.begin() as session:
        session.execute(
            update(accounts).where(accounts.c.account_id == ACCOUNT_ID).values(auth_version=1)
        )
