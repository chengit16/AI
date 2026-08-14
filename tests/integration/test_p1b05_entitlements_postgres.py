"""验证 P1B-05 权益、开关、额度和并发用量账本。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.authentication import (
    ApiKeyService,
    AuthenticationService,
)
from ai_platform_api.modules.identity.application.enterprise import EnterpriseWorkspaceService
from ai_platform_api.modules.identity.application.entitlement_errors import (
    EntitlementConflictError,
    EntitlementFeatureDeniedError,
    EntitlementGovernanceDeniedError,
    QuotaExceededError,
)
from ai_platform_api.modules.identity.application.entitlements import EntitlementService
from ai_platform_api.modules.identity.application.errors import ApiKeyInvalidError
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.enterprise_sqlalchemy import (
    SqlAlchemyEnterpriseUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.entitlements_sqlalchemy import (
    SqlAlchemyEntitlementAccessReader,
    SqlAlchemyEntitlementUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.security import (
    Argon2idPasswordAdapter,
    Sha256SecretDigester,
)
from ai_platform_api.modules.identity.infrastructure.session import ValkeySessionStore
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyIdentityUnitOfWork,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    audit_records,
    outbox_events,
    workspace_entitlements,
    workspace_feature_settings,
    workspace_usage_counters,
    workspace_usage_records,
    workspaces,
)
from alembic import command
from alembic.config import Config
from redis import Redis
from sqlalchemy import Engine, create_engine, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
DEFAULT_VALKEY_URL = "redis://127.0.0.1:6379/12"
TRACE = TraceContext.continue_from("00-4123456789abcdef0123456789abcdef-4123456789abcdef-01")


@dataclass(frozen=True)
class RegisteredAccount:
    account_id: UUID
    personal_workspace_id: UUID
    login_name: str


@dataclass(frozen=True)
class EntitlementHarness:
    engine: Engine
    registration: RegistrationService
    enterprise: EnterpriseWorkspaceService
    entitlements: EntitlementService
    access_reader: SqlAlchemyEntitlementAccessReader
    authentication: AuthenticationService
    api_keys: ApiKeyService
    sessions: ValkeySessionStore
    valkey_url: str


@pytest.fixture(scope="module")
def entitlement_database() -> Iterator[EntitlementHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    valkey_url = os.environ.get("AI_PLATFORM_TEST_VALKEY_URL", DEFAULT_VALKEY_URL)
    schema = f"p1b05_test_{uuid4().hex}"
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
    session_factory = create_session_factory(engine)
    identity_reader = SqlAlchemyIdentityReader(session_factory)
    access_reader = SqlAlchemyEntitlementAccessReader(session_factory)
    session_store = ValkeySessionStore(valkey_url)
    passwords = Argon2idPasswordAdapter()
    digester = Sha256SecretDigester()
    try:
        yield EntitlementHarness(
            engine=engine,
            registration=RegistrationService(
                repository=identity_reader,
                unit_of_work=SqlAlchemyRegistrationUnitOfWork(session_factory),
                passwords=passwords,
            ),
            enterprise=EnterpriseWorkspaceService(SqlAlchemyEnterpriseUnitOfWork(session_factory)),
            entitlements=EntitlementService(SqlAlchemyEntitlementUnitOfWork(session_factory)),
            access_reader=access_reader,
            authentication=AuthenticationService(
                identity_reader,
                session_store,
                passwords,
                digester,
                session_ttl_seconds=300,
                entitlements=access_reader,
            ),
            api_keys=ApiKeyService(
                identity_reader,
                SqlAlchemyIdentityUnitOfWork(session_factory),
                digester,
                access_reader,
            ),
            sessions=session_store,
            valkey_url=valkey_url,
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
    harness: EntitlementHarness,
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
    harness: EntitlementHarness,
    owner: RegisteredAccount,
    member: RegisteredAccount,
    *,
    name: str,
) -> tuple[UUID, RequestContext, RequestContext]:
    workspace = harness.enterprise.create(context(owner), name=name)
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
    return workspace.workspace_id, owner_context, context(member, workspace.workspace_id)


def test_default_snapshots_and_open_api_governance(
    entitlement_database: EntitlementHarness,
) -> None:
    owner = register(
        entitlement_database,
        login_name="synthetic.entitlement.owner.p1b05@example.com",
        display_name="合成权益所有者",
    )
    member = register(
        entitlement_database,
        login_name="synthetic.entitlement.member.p1b05@example.com",
        display_name="合成权益成员",
    )
    personal = entitlement_database.entitlements.get_snapshot(
        context(owner),
        workspace_id=owner.personal_workspace_id,
    )
    assert personal.plan_code == "personal_local"
    assert personal.open_api_allowed is False
    assert [(quota.metric, quota.limit_value) for quota in personal.quotas] == [
        ("members", 1),
        ("storage_bytes", 5 * 1024**3),
        ("knowledge_bases", 5),
        ("published_agents", 3),
        ("questions_monthly", 2_000),
    ]
    with pytest.raises(EntitlementFeatureDeniedError):
        entitlement_database.entitlements.set_open_api_enabled(
            context(owner),
            workspace_id=owner.personal_workspace_id,
            enabled=True,
        )

    workspace_id, owner_context, member_context = join_workspace(
        entitlement_database,
        owner,
        member,
        name="合成套餐治理企业",
    )
    owner_snapshot = entitlement_database.entitlements.get_snapshot(
        owner_context,
        workspace_id=workspace_id,
    )
    member_snapshot = entitlement_database.entitlements.get_snapshot(
        member_context,
        workspace_id=workspace_id,
    )
    assert owner_snapshot == member_snapshot
    assert owner_snapshot.plan_code == "enterprise_simulated"
    assert owner_snapshot.quotas[0].used_value == 2
    with pytest.raises(EntitlementGovernanceDeniedError):
        entitlement_database.entitlements.set_open_api_enabled(
            member_context,
            workspace_id=workspace_id,
            enabled=True,
        )

    enabled = entitlement_database.entitlements.set_open_api_enabled(
        owner_context,
        workspace_id=workspace_id,
        enabled=True,
    )
    assert enabled.open_api_enabled is True
    assert enabled.entitlement_version == owner_snapshot.entitlement_version + 1
    access = entitlement_database.access_reader.get_open_api_entitlement(workspace_id)
    assert access is not None and access.active

    issued = entitlement_database.api_keys.issue(
        context=owner_context,
        name="合成套餐即时失效 Key",
        scopes=("knowledge.document.read",),
        expires_at=None,
    )
    entitlement_database.authentication.api_key_context(
        credential=issued.plaintext,
        workspace_id=workspace_id,
        request_id=uuid4(),
        trace=TRACE,
    )
    disabled = entitlement_database.entitlements.set_open_api_enabled(
        owner_context,
        workspace_id=workspace_id,
        enabled=False,
    )
    assert disabled.entitlement_version == enabled.entitlement_version + 1
    with pytest.raises(ApiKeyInvalidError):
        entitlement_database.authentication.api_key_context(
            credential=issued.plaintext,
            workspace_id=workspace_id,
            request_id=uuid4(),
            trace=TRACE,
        )


def test_usage_is_atomic_idempotent_and_month_scoped(
    entitlement_database: EntitlementHarness,
) -> None:
    owner = register(
        entitlement_database,
        login_name="synthetic.usage.owner.p1b05@example.com",
        display_name="合成配额所有者",
    )
    workspace = entitlement_database.enterprise.create(context(owner), name="合成配额企业")
    workspace_id = workspace.workspace_id
    owner_context = context(owner, workspace_id)

    first = entitlement_database.entitlements.consume(
        context=owner_context,
        workspace_id=workspace_id,
        metric="knowledge_bases",
        delta_value=1,
        idempotency_key="knowledge:create:synthetic-001",
    )
    duplicate = entitlement_database.entitlements.consume(
        context=owner_context,
        workspace_id=workspace_id,
        metric="knowledge_bases",
        delta_value=1,
        idempotency_key="knowledge:create:synthetic-001",
    )
    assert duplicate == first
    with pytest.raises(EntitlementConflictError):
        entitlement_database.entitlements.consume(
            context=owner_context,
            workspace_id=workspace_id,
            metric="knowledge_bases",
            delta_value=2,
            idempotency_key="knowledge:create:synthetic-001",
        )

    august = entitlement_database.entitlements.consume(
        context=owner_context,
        workspace_id=workspace_id,
        metric="questions_monthly",
        delta_value=20,
        idempotency_key="questions:synthetic:2026-08",
        occurred_at=datetime(2026, 8, 31, 23, 59, tzinfo=UTC),
    )
    september = entitlement_database.entitlements.consume(
        context=owner_context,
        workspace_id=workspace_id,
        metric="questions_monthly",
        delta_value=3,
        idempotency_key="questions:synthetic:2026-09",
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert (august.period_key, august.resulting_value) == ("2026-08", 20)
    assert (september.period_key, september.resulting_value) == ("2026-09", 3)

    with pytest.raises(QuotaExceededError):
        entitlement_database.entitlements.consume(
            context=owner_context,
            workspace_id=workspace_id,
            metric="knowledge_bases",
            delta_value=50,
            idempotency_key="knowledge:overflow:synthetic-001",
        )
    with pytest.raises(QuotaExceededError):
        entitlement_database.entitlements.consume(
            context=owner_context,
            workspace_id=workspace_id,
            metric="knowledge_bases",
            delta_value=-2,
            idempotency_key="knowledge:release:synthetic-001",
        )

    snapshot = entitlement_database.entitlements.get_snapshot(
        owner_context,
        workspace_id=workspace_id,
        now=datetime(2026, 9, 1, tzinfo=UTC),
    )
    quotas = {quota.metric: quota for quota in snapshot.quotas}
    assert quotas["knowledge_bases"].used_value == 1
    assert quotas["questions_monthly"].period_key == "2026-09"
    assert quotas["questions_monthly"].used_value == 3

    with entitlement_database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(workspace_usage_records)
                .where(workspace_usage_records.c.workspace_id == workspace_id)
            )
            == 3
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(audit_records)
                .where(
                    audit_records.c.workspace_id == workspace_id,
                    audit_records.c.resource_type == "workspace_usage",
                )
            )
            == 3
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(outbox_events)
                .where(
                    outbox_events.c.workspace_id == workspace_id,
                    outbox_events.c.event_type == "workspace.usage.recorded",
                )
            )
            == 3
        )

    with entitlement_database.engine.begin() as connection:
        connection.execute(
            update(workspaces)
            .where(workspaces.c.workspace_id == workspace_id)
            .values(status="suspended")
        )
    with pytest.raises(EntitlementGovernanceDeniedError):
        entitlement_database.entitlements.consume(
            context=owner_context,
            workspace_id=workspace_id,
            metric="knowledge_bases",
            delta_value=1,
            idempotency_key="knowledge:suspended:synthetic-001",
        )


def test_member_limit_and_database_constraints_are_enforced(
    entitlement_database: EntitlementHarness,
) -> None:
    owner = register(
        entitlement_database,
        login_name="synthetic.limit.owner.p1b05@example.com",
        display_name="合成成员上限所有者",
    )
    member = register(
        entitlement_database,
        login_name="synthetic.limit.member.p1b05@example.com",
        display_name="合成成员上限成员",
    )
    workspace = entitlement_database.enterprise.create(context(owner), name="合成成员上限企业")
    owner_context = context(owner, workspace.workspace_id)
    invitation = entitlement_database.enterprise.invite(
        owner_context,
        workspace_id=workspace.workspace_id,
        login_name=member.login_name,
    )
    with entitlement_database.engine.begin() as connection:
        connection.execute(
            update(workspace_entitlements)
            .where(workspace_entitlements.c.workspace_id == workspace.workspace_id)
            .values(max_members=1)
        )
    with pytest.raises(QuotaExceededError):
        entitlement_database.enterprise.accept_invitation(
            context(member),
            invitation_id=invitation.invitation_id,
        )

    now = datetime.now(UTC)
    missing_workspace_id = uuid4()
    with pytest.raises(IntegrityError), entitlement_database.engine.begin() as connection:
        connection.execute(
            insert(workspace_usage_counters).values(
                workspace_id=missing_workspace_id,
                metric="knowledge_bases",
                period_key="lifetime",
                used_value=1,
                updated_at=now,
                version=1,
            )
        )
    with pytest.raises(IntegrityError), entitlement_database.engine.begin() as connection:
        connection.execute(
            insert(workspace_usage_counters).values(
                workspace_id=workspace.workspace_id,
                metric="unsupported_metric",
                period_key="lifetime",
                used_value=1,
                updated_at=now,
                version=1,
            )
        )
    with pytest.raises(IntegrityError), entitlement_database.engine.begin() as connection:
        connection.execute(
            insert(workspace_usage_records).values(
                usage_record_id=uuid4(),
                workspace_id=workspace.workspace_id,
                metric="knowledge_bases",
                period_key="lifetime",
                idempotency_key="invalid:negative:synthetic-001",
                delta_value=-1,
                resulting_value=-1,
                occurred_at=now,
            )
        )

    with entitlement_database.engine.connect() as connection:
        setting = connection.execute(
            select(workspace_feature_settings).where(
                workspace_feature_settings.c.workspace_id == workspace.workspace_id
            )
        ).one()
        assert setting.open_api_enabled is False
