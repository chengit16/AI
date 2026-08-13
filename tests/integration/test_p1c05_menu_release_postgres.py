from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.application.menu_releases import MenuReleaseService
from ai_platform_api.modules.authorization.application.menus import MenuConfigurationService
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.infrastructure.menu_sqlalchemy import (
    SqlAlchemyMenuConfigurationUnitOfWork,
)
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    audit_records,
    menu_releases,
    outbox_events,
    workspace_menu_publications,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select, text

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext.continue_from("00-b123456789abcdef0123456789abcdef-b123456789abcdef-01")
DIRECTORY_ID = UUID("82000000-0000-4000-8000-000000000001")
OVERVIEW_ID = UUID("82000000-0000-4000-8000-000000000002")


@dataclass(frozen=True)
class ReleaseHarness:
    engine: Engine
    registration: RegistrationService
    menus: MenuConfigurationService
    releases: MenuReleaseService


@pytest.fixture(scope="module")
def release_database() -> Iterator[ReleaseHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1c05_test_{uuid4().hex}"
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
    registry = load_resource_registry(ROOT / "contracts/authorization/resource-registry.v1.json")
    unit_of_work = SqlAlchemyMenuConfigurationUnitOfWork(sessions)
    try:
        yield ReleaseHarness(
            engine=engine,
            registration=RegistrationService(
                reader,
                SqlAlchemyRegistrationUnitOfWork(sessions),
                Argon2idPasswordAdapter(),
            ),
            menus=MenuConfigurationService(registry, unit_of_work),
            releases=MenuReleaseService(registry, unit_of_work),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def test_menu_release_snapshot_publish_and_rollback_are_persistent(
    release_database: ReleaseHarness,
) -> None:
    registration = release_database.registration.register(
        login_name="synthetic.p1c05.owner@example.com",
        display_name="合成菜单发布所有者",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    context = RequestContext.trusted(
        actor_id=registration.account_id,
        user_id=registration.account_id,
        workspace_id=registration.personal_workspace_id,
        trace=TRACE,
        authentication_method="browser_session",
    )
    workspace_id = registration.personal_workspace_id
    release_database.menus.replace_workspace(
        context,
        workspace_id=workspace_id,
        entries=((OVERVIEW_ID, DIRECTORY_ID, "首版工作台", "panel-top", 10, True),),
    )
    first = release_database.releases.create_draft(context, workspace_id=workspace_id)
    first_digest = first.snapshot_digest

    # 草稿创建后修改配置，首版快照仍必须保持创建时的内容。
    release_database.menus.replace_workspace(
        context,
        workspace_id=workspace_id,
        entries=((OVERVIEW_ID, DIRECTORY_ID, "二版工作台", "panel-top", 20, True),),
    )
    first = release_database.releases.validate(
        context,
        workspace_id=workspace_id,
        release_id=first.release_id,
    )
    release_database.releases.decide(
        context,
        workspace_id=workspace_id,
        release_id=first.release_id,
        approved=True,
        reason=None,
    )
    first = release_database.releases.publish(
        context,
        workspace_id=workspace_id,
        release_id=first.release_id,
    )
    second = release_database.releases.create_draft(context, workspace_id=workspace_id)
    second = release_database.releases.validate(
        context,
        workspace_id=workspace_id,
        release_id=second.release_id,
    )
    release_database.releases.decide(
        context,
        workspace_id=workspace_id,
        release_id=second.release_id,
        approved=True,
        reason=None,
    )
    second = release_database.releases.publish(
        context,
        workspace_id=workspace_id,
        release_id=second.release_id,
    )
    rollback = release_database.releases.rollback(
        context,
        workspace_id=workspace_id,
        source_release_id=first.release_id,
    )

    first_menu = next(item for item in first.snapshot.menus if item.menu_id == OVERVIEW_ID)
    second_menu = next(item for item in second.snapshot.menus if item.menu_id == OVERVIEW_ID)
    assert first_menu.name == "首版工作台"
    assert second_menu.name == "二版工作台"
    assert rollback.snapshot == first.snapshot
    assert rollback.snapshot_digest == first_digest
    assert rollback.release_number == 3
    assert release_database.releases.get_current(context, workspace_id=workspace_id) == rollback

    with release_database.engine.connect() as connection:
        stored_first = connection.execute(
            select(menu_releases.c.snapshot, menu_releases.c.snapshot_digest).where(
                menu_releases.c.release_id == first.release_id
            )
        ).one()
        assert stored_first.snapshot["menus"][1]["name"] == "首版工作台"
        assert stored_first.snapshot_digest == first_digest
        assert (
            connection.scalar(
                select(workspace_menu_publications.c.current_release_id).where(
                    workspace_menu_publications.c.workspace_id == workspace_id
                )
            )
            == rollback.release_id
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(audit_records)
                .where(audit_records.c.resource_type == "menu_release")
            )
            == 9
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(outbox_events)
                .where(outbox_events.c.aggregate_id.in_((first.release_id, second.release_id)))
            )
            == 8
        )
