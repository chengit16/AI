"""验证 P4-02 非空升级、数据库约束、不可变目录和降级保护。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.application.policy import RbacPolicyDecisionPoint
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.infrastructure.sqlalchemy import (
    SqlAlchemyPolicyGrantRepository,
)
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.entitlements_sqlalchemy import (
    SqlAlchemyEntitlementAccessReader,
)
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.modules.tool_execution.application import verify_tool_definition
from ai_platform_api.modules.tool_execution.application.catalog import ToolCatalogService
from ai_platform_api.modules.tool_execution.infrastructure import (
    SqlAlchemyToolCatalogRepository,
)
from ai_platform_api.persistence.database import create_platform_engine
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TOOL_ID = UUID("a7000000-0000-4000-8000-000000000001")
TRACE = TraceContext("4" * 32, "2" * 16)


@pytest.fixture
def migration_database() -> Iterator[tuple[Config, Connection, str, str]]:
    """为每个场景创建独立 Schema，允许验证真实升级和失败回滚。"""

    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p402_test_{uuid4().hex}"
    engine = create_engine(database_url)
    connection = engine.connect()
    connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    connection.commit()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "infra/migrations"))
    config.set_main_option(
        "prepend_sys_path",
        f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
    )
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("ai_platform_schema", schema)
    try:
        yield config, connection, schema, database_url
    finally:
        connection.rollback()
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        connection.commit()
        connection.close()
        engine.dispose()


def _revision(connection: Connection, schema: str) -> str | None:
    return connection.execute(
        text(f'SELECT version_num FROM "{schema}".alembic_version')
    ).scalar_one_or_none()


def test_nonempty_catalog_upgrade_and_roundtrip(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    config, connection, schema, database_url = migration_database
    command.upgrade(config, "20260816_0052")
    connection.commit()
    assert (
        connection.execute(
            text(f'SELECT count(*) FROM "{schema}".agent_tool_definitions')
        ).scalar_one()
        == 5
    )
    connection.commit()

    # 1. 从真实非空 0052 目录升级，五个身份不变且完整治理摘要都可由应用复算。
    command.upgrade(config, "head")
    connection.commit()
    assert _revision(connection, schema) == "20260817_0066"
    platform_engine = create_platform_engine(database_url, schema)
    sessions = sessionmaker(platform_engine, expire_on_commit=False, class_=Session)
    try:
        repository = SqlAlchemyToolCatalogRepository(sessions)
        personal = repository.list_active_for_plan("personal_local")
        enterprise = repository.list_active_for_plan("enterprise_simulated")
        assert len(personal) == len(enterprise) == 5
        assert repository.list_active_for_plan("unknown_plan") == ()
        assert {item.tool_key for item in personal} == {
            "knowledge.search",
            "document.read_authorized_range",
            "workflow.get_status",
            "approval.get_status",
            "quota.get_usage",
        }
        for definition in personal:
            verify_tool_definition(definition)
    finally:
        platform_engine.dispose()
    connection.commit()

    # 2. 固定种子允许回到上一 Revision，再次升级必须恢复相同目录规模。
    command.downgrade(config, "20260816_0052")
    connection.commit()
    assert _revision(connection, schema) == "20260816_0052"
    assert (
        connection.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name = 'tool_plan_availability'"
            ),
            {"schema": schema},
        ).scalar_one()
        == 0
    )
    command.upgrade(config, "head")
    connection.commit()
    assert (
        connection.execute(
            text(f'SELECT count(*) FROM "{schema}".tool_plan_availability')
        ).scalar_one()
        == 10
    )


def test_database_rejects_mutation_unsafe_adapter_and_destructive_downgrade(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    config, connection, schema, _ = migration_database
    command.upgrade(config, "head")
    connection.commit()

    # 1. 工具版本和套餐映射均为追加式事实，生命周期清理开关也不能绕过保护。
    for statement in (
        text(
            f'UPDATE "{schema}".agent_tool_definitions SET timeout_seconds = 120 '
            "WHERE tool_id = CAST(:tool_id AS uuid)"
        ),
        text(
            f"UPDATE \"{schema}\".tool_plan_availability SET plan_code = 'forged_plan' "
            "WHERE tool_id = CAST(:tool_id AS uuid)"
        ),
    ):
        with pytest.raises(DBAPIError):
            connection.execute(statement, {"tool_id": str(TOOL_ID)})
        connection.rollback()

    # 2. INSERT 虽是唯一合法演进方式，数据库仍拒绝阶段外 HTTP Adapter。
    with pytest.raises(DBAPIError):
        connection.execute(
            text(
                f"""
                INSERT INTO "{schema}".agent_tool_definitions (
                    tool_id, tool_version, tool_key, display_name, description, access_mode,
                    risk_level, adapter_kind, input_schema_document, input_schema_hash,
                    output_schema_document, output_schema_hash, permission_code,
                    credential_requirement, timeout_seconds, retry_mode, status,
                    definition_hash, synthetic, created_at
                )
                SELECT tool_id, 2, tool_key, display_name, description, access_mode,
                       risk_level, 'http', input_schema_document, input_schema_hash,
                       output_schema_document, output_schema_hash, permission_code,
                       credential_requirement, timeout_seconds, retry_mode, status,
                       definition_hash, synthetic, created_at
                FROM "{schema}".agent_tool_definitions
                WHERE tool_id = CAST(:tool_id AS uuid) AND tool_version = 1
                """
            ),
            {"tool_id": str(TOOL_ID)},
        )
    connection.rollback()

    # 3. 一旦追加新版本，0053 降级必须阻断，不能静默删除后续工具事实。
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".agent_tool_definitions (
                tool_id, tool_version, tool_key, display_name, description, access_mode,
                risk_level, adapter_kind, input_schema_document, input_schema_hash,
                output_schema_document, output_schema_hash, permission_code,
                credential_requirement, timeout_seconds, retry_mode, status,
                definition_hash, synthetic, created_at
            )
            SELECT tool_id, 2, tool_key, display_name, description, access_mode,
                   risk_level, adapter_kind, input_schema_document, input_schema_hash,
                   output_schema_document, output_schema_hash, permission_code,
                   credential_requirement, timeout_seconds, retry_mode, 'retired',
                   definition_hash, synthetic, created_at
            FROM "{schema}".agent_tool_definitions
            WHERE tool_id = CAST(:tool_id AS uuid) AND tool_version = 1
            """
        ),
        {"tool_id": str(TOOL_ID)},
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="拒绝破坏性降级"):
        command.downgrade(config, "20260816_0052")


def test_real_personal_plan_and_rbac_only_narrow_platform_catalog(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    config, connection, schema, database_url = migration_database
    command.upgrade(config, "head")
    connection.commit()
    platform_engine = create_platform_engine(database_url, schema)
    sessions = sessionmaker(platform_engine, expire_on_commit=False, class_=Session)
    try:
        registration = RegistrationService(
            SqlAlchemyIdentityReader(sessions),
            SqlAlchemyRegistrationUnitOfWork(sessions),
            Argon2idPasswordAdapter(),
        )
        account = registration.register(
            login_name=f"synthetic.p402.{uuid4().hex}@example.com",
            display_name="合成 P4-02 个人用户",
            password="synthetic-password-123",
            request_id=uuid4(),
            trace=TRACE,
        )
        context = RequestContext.trusted(
            actor_id=account.account_id,
            user_id=account.account_id,
            workspace_id=account.personal_workspace_id,
            trace=TRACE,
            authentication_method="browser_session",
        )
        service = ToolCatalogService(
            SqlAlchemyToolCatalogRepository(sessions),
            SqlAlchemyEntitlementAccessReader(sessions),
            RbacPolicyDecisionPoint(
                load_resource_registry(
                    ROOT / "contracts" / "authorization" / "resource-registry.v1.json"
                ),
                SqlAlchemyPolicyGrantRepository(sessions),
            ),
        )

        catalog = service.list_available_tools(
            context,
            workspace_id=account.personal_workspace_id,
        )

        assert len(catalog) == 5
        assert {item.permission_code for item in catalog} == {
            "knowledge.document.read",
            "workflow.run.read",
            "approval.instance.read",
            "workspace.entitlement.read",
        }
    finally:
        platform_engine.dispose()
