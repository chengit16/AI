from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.application.menus import (
    MenuConfigurationDeniedError,
    MenuConfigurationService,
)
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.infrastructure.menu_sqlalchemy import (
    SqlAlchemyMenuConfigurationUnitOfWork,
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
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import audit_records, outbox_events, role_menus, roles
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select, text

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext.continue_from("00-a123456789abcdef0123456789abcdef-a123456789abcdef-01")
DIRECTORY_ID = UUID("82000000-0000-4000-8000-000000000001")
OVERVIEW_ID = UUID("82000000-0000-4000-8000-000000000002")
ACTION_ID = UUID("82000000-0000-4000-8000-000000000101")


@dataclass(frozen=True)
class Account:
    account_id: UUID
    personal_workspace_id: UUID
    login_name: str


@dataclass(frozen=True)
class MenuHarness:
    engine: Engine
    registration: RegistrationService
    enterprise: EnterpriseWorkspaceService
    menus: MenuConfigurationService


@pytest.fixture(scope="module")
def menu_database() -> Iterator[MenuHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1c04_test_{uuid4().hex}"
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
        yield MenuHarness(
            engine,
            RegistrationService(
                reader,
                SqlAlchemyRegistrationUnitOfWork(sessions),
                Argon2idPasswordAdapter(),
            ),
            EnterpriseWorkspaceService(SqlAlchemyEnterpriseUnitOfWork(sessions)),
            MenuConfigurationService(
                load_resource_registry(ROOT / "contracts/authorization/resource-registry.v1.json"),
                SqlAlchemyMenuConfigurationUnitOfWork(sessions),
            ),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def register(harness: MenuHarness, name: str) -> Account:
    login_name = f"synthetic.p1c04.{name}@example.com"
    result = harness.registration.register(
        login_name=login_name,
        display_name=f"合成{name}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return Account(result.account_id, result.personal_workspace_id, login_name)


def context(account: Account, workspace_id: UUID | None = None) -> RequestContext:
    return RequestContext.trusted(
        actor_id=account.account_id,
        user_id=account.account_id,
        workspace_id=workspace_id or account.personal_workspace_id,
        trace=TRACE,
        authentication_method="browser_session",
    )


def test_personal_and_enterprise_menu_configuration_is_persistent_and_isolated(
    menu_database: MenuHarness,
) -> None:
    owner = register(menu_database, "owner")
    other = register(menu_database, "other")
    personal = menu_database.menus.replace_workspace(
        context(owner),
        workspace_id=owner.personal_workspace_id,
        entries=((OVERVIEW_ID, DIRECTORY_ID, "个人工作台", "panel-top", 10, True),),
    )
    assert personal.menu_version == 2

    enterprise = menu_database.enterprise.create(context(owner), name="合成菜单企业")
    enterprise_context = context(owner, enterprise.workspace_id)
    configured = menu_database.menus.replace_workspace(
        enterprise_context,
        workspace_id=enterprise.workspace_id,
        entries=((OVERVIEW_ID, DIRECTORY_ID, "企业工作台", "building-2", 20, True),),
    )
    with menu_database.engine.connect() as connection:
        owner_role_id = connection.scalar(
            select(roles.c.role_id).where(
                roles.c.workspace_id == enterprise.workspace_id,
                roles.c.role_key == "workspace_owner",
            )
        )
    assert isinstance(owner_role_id, UUID)
    menu_database.menus.replace_role(
        enterprise_context,
        workspace_id=enterprise.workspace_id,
        role_id=owner_role_id,
        entries=((ACTION_ID, False),),
    )

    assert configured.menu_version == 2
    assert (
        menu_database.menus.get_workspace(
            enterprise_context,
            workspace_id=enterprise.workspace_id,
        )
        .overrides[0]
        .name
        == "企业工作台"
    )
    with pytest.raises(MenuConfigurationDeniedError):
        menu_database.menus.get_workspace(
            context(other, enterprise.workspace_id),
            workspace_id=enterprise.workspace_id,
        )

    with menu_database.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(role_menus)) == 1
        assert (
            connection.scalar(
                select(func.count())
                .select_from(audit_records)
                .where(audit_records.c.action == "authorization.menu_configuration.replace")
            )
            == 2
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(outbox_events)
                .where(outbox_events.c.event_type == "authorization.role_menus.replaced")
            )
            == 1
        )
