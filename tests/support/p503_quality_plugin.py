"""提供 P5-03 真实 PostgreSQL 评估测试的隔离 Schema Fixture。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.modules.quality.application.service import QualitySampleService
from ai_platform_api.modules.quality.infrastructure.sqlalchemy import (
    SqlAlchemyQualityUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from tests.support.p503_quality import QualityEvaluationHarness

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)


@pytest.fixture(scope="module")
def quality_evaluation_database() -> Iterator[QualityEvaluationHarness]:
    """迁移独立 Schema，并在模块结束后清理全部合成评估事实。"""

    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p503_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    migration = Config(str(ROOT / "alembic.ini"))
    migration.set_main_option("script_location", str(ROOT / "infra/migrations"))
    migration.set_main_option(
        "prepend_sys_path",
        f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
    )
    migration.set_main_option("sqlalchemy.url", database_url)
    migration.set_main_option("ai_platform_schema", schema)
    command.upgrade(migration, "head")

    engine = create_platform_engine(database_url, schema)
    sessions = create_session_factory(engine)
    try:
        yield QualityEvaluationHarness(
            engine,
            sessions,
            RegistrationService(
                SqlAlchemyIdentityReader(sessions),
                SqlAlchemyRegistrationUnitOfWork(sessions),
                Argon2idPasswordAdapter(),
            ),
            QualitySampleService(SqlAlchemyQualityUnitOfWork(sessions)),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()
