"""验证 P4-04 使用真实 PostgreSQL 工具目录执行五个内部只读 Adapter。"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from dataclasses import replace
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
from ai_platform_api.modules.tool_execution.application import (
    ToolAdapterService,
    ToolCatalogService,
)
from ai_platform_api.modules.tool_execution.domain.adapters import ToolAdapterRequest
from ai_platform_api.modules.tool_execution.infrastructure import (
    SqlAlchemyToolCatalogRepository,
    build_internal_read_adapters,
)
from ai_platform_api.persistence.database import create_platform_engine
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext("4" * 32, "6" * 16)
DOCUMENT_ID = UUID("a4040000-0000-4000-8000-000000000001")
WORKFLOW_RUN_ID = UUID("a4040000-0000-4000-8000-000000000002")
APPROVAL_INSTANCE_ID = UUID("a4040000-0000-4000-8000-000000000003")
CHUNK_ID = UUID("a4040000-0000-4000-8000-000000000004")


@pytest.fixture
def migration_database() -> Iterator[tuple[Config, Connection, str, str]]:
    """创建独立 Schema，避免 P4-04 验证污染公共开发数据库。"""

    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p404_test_{uuid4().hex}"
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


def test_real_catalog_executes_all_five_internal_read_adapters(
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
            login_name=f"synthetic.p404.{uuid4().hex}@example.com",
            display_name="合成 P4-04 用户",
            password="synthetic-password-123",
            request_id=uuid4(),
            trace=TRACE,
        )
        context = replace(
            RequestContext.trusted(
                actor_id=account.account_id,
                user_id=account.account_id,
                workspace_id=account.personal_workspace_id,
                trace=TRACE,
                authentication_method="browser_session",
            ),
            # 该测试验证目录到 Adapter 的闭环；资源级收窄另由单元门禁覆盖。
            authorized_workspace=True,
        )
        catalog = ToolCatalogService(
            SqlAlchemyToolCatalogRepository(sessions),
            SqlAlchemyEntitlementAccessReader(sessions),
            RbacPolicyDecisionPoint(
                load_resource_registry(
                    ROOT / "contracts" / "authorization" / "resource-registry.v1.json"
                ),
                SqlAlchemyPolicyGrantRepository(sessions),
            ),
        )
        definitions = catalog.list_available_tools(
            context,
            workspace_id=account.personal_workspace_id,
        )
        assert {item.tool_key for item in definitions} == {
            "knowledge.search",
            "document.read_authorized_range",
            "workflow.get_status",
            "approval.get_status",
            "quota.get_usage",
        }

        def knowledge_search(_: ToolAdapterRequest) -> Mapping[str, object]:
            return {
                "items": [
                    {
                        "chunk_id": str(CHUNK_ID),
                        "document_id": str(DOCUMENT_ID),
                        "text": "合成授权检索结果",
                        "score": 0.9,
                    }
                ]
            }

        def document_read(_: ToolAdapterRequest) -> Mapping[str, object]:
            return {
                "document_id": str(DOCUMENT_ID),
                "chunks": [
                    {
                        "chunk_id": str(CHUNK_ID),
                        "sequence": 1,
                        "text": "合成授权片段",
                        "page_number": 1,
                    }
                ],
            }

        adapters = build_internal_read_adapters(
            knowledge_search=knowledge_search,
            document_read_authorized_range=document_read,
            workflow_get_status=lambda _: {
                "workflow_run_id": str(WORKFLOW_RUN_ID),
                "status": "completed",
                "current_step": None,
                "total_steps": 0,
            },
            approval_get_status=lambda _: {
                "approval_instance_id": str(APPROVAL_INSTANCE_ID),
                "status": "approved",
                "current_level": None,
                "total_levels": 1,
            },
            quota_get_usage=lambda _: {"plan_code": "personal_local", "quotas": []},
        )
        service = ToolAdapterService(catalog, adapters)
        arguments: dict[str, Mapping[str, object]] = {
            "knowledge.search": {"query": "合成内容"},
            "document.read_authorized_range": {
                "document_id": str(DOCUMENT_ID),
                "start_sequence": 1,
                "end_sequence": 1,
            },
            "workflow.get_status": {"workflow_run_id": str(WORKFLOW_RUN_ID)},
            "approval.get_status": {"approval_instance_id": str(APPROVAL_INSTANCE_ID)},
            "quota.get_usage": {},
        }
        results = [
            service.execute(
                context,
                tool_id=definition.tool_id,
                tool_version=definition.tool_version,
                arguments=arguments[definition.tool_key],
            )
            for definition in definitions
        ]
        assert {item.tool_key for item in results} == set(arguments)
        assert all(len(item.result_sha256) == 64 for item in results)
        assert all(
            item.checks == ("schema", "size", "sensitive_fields", "prompt_injection")
            for item in results
        )
    finally:
        platform_engine.dispose()
