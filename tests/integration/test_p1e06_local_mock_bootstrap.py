"""验证 P1E-06 本地 Mock 运行配置的幂等自举、恢复和真实草稿边界。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from ai_platform_api.app.local_mock import (
    LOCAL_MOCK_PROVIDER_ID,
    LOCAL_MOCK_PROVIDER_KEY,
    LocalMockRuntimeBootstrap,
)
from ai_platform_api.common.security import EnvelopeSecretCipher, MasterKeyFile
from ai_platform_api.modules.model_gateway.application.runtime_configurations import (
    AiRuntimeConfigurationService,
)
from ai_platform_api.modules.model_gateway.domain.runtime_errors import (
    AiRuntimeConfigNotActiveError,
)
from ai_platform_api.modules.model_gateway.infrastructure.runtime_sqlalchemy import (
    SqlAlchemyRuntimeConfigurationUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    accounts,
    ai_runtime_config_publication,
    ai_runtime_config_versions,
    model_provider_configurations,
    model_provider_credentials,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, insert, select, text, update
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)


@dataclass(frozen=True)
class LocalMockHarness:
    """集中持有临时 Schema 的 Session 与本地自举服务。"""

    engine: Engine
    sessions: sessionmaker[Session]
    bootstrap: LocalMockRuntimeBootstrap


@pytest.fixture()
def local_mock_database(tmp_path: Path) -> Iterator[LocalMockHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1e06_mock_{uuid4().hex}"
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
    key_path = tmp_path / "synthetic-master.key"
    key_path.write_bytes(b"m" * 32)
    key_path.chmod(0o600)
    runtime_configurations = AiRuntimeConfigurationService(
        SqlAlchemyRuntimeConfigurationUnitOfWork(sessions)
    )
    try:
        yield LocalMockHarness(
            engine,
            sessions,
            LocalMockRuntimeBootstrap(
                sessions,
                EnvelopeSecretCipher(MasterKeyFile(str(key_path))),
                runtime_configurations,
            ),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def test_bootstrap_is_idempotent_and_recovers_disabled_builtin(
    local_mock_database: LocalMockHarness,
) -> None:
    first = local_mock_database.bootstrap.ensure(uuid4())
    repeated = local_mock_database.bootstrap.ensure(uuid4())

    assert repeated == first
    with local_mock_database.sessions.begin() as session:
        assert session.scalar(select(func.count()).select_from(model_provider_configurations)) == 1
        assert session.scalar(select(func.count()).select_from(ai_runtime_config_versions)) == 1
        assert session.scalar(select(func.count()).select_from(ai_runtime_config_publication)) == 1
        session.execute(
            update(model_provider_configurations)
            .where(model_provider_configurations.c.provider_id == LOCAL_MOCK_PROVIDER_ID)
            .values(status="disabled")
        )
        session.execute(
            update(model_provider_credentials)
            .where(model_provider_credentials.c.provider_id == LOCAL_MOCK_PROVIDER_ID)
            .values(status="revoked", revoked_at=datetime.now(UTC))
        )

    recovered = local_mock_database.bootstrap.ensure(uuid4())
    with local_mock_database.sessions() as session:
        provider_row = session.execute(
            select(
                model_provider_configurations.c.provider_key,
                model_provider_configurations.c.status,
                model_provider_configurations.c.version,
            ).where(model_provider_configurations.c.provider_id == LOCAL_MOCK_PROVIDER_ID)
        ).one()
        active_credential = session.execute(
            select(
                model_provider_credentials.c.status,
                model_provider_credentials.c.credential_version,
            ).where(
                model_provider_credentials.c.provider_id == LOCAL_MOCK_PROVIDER_ID,
                model_provider_credentials.c.status == "active",
            )
        ).one()

    assert recovered == first
    assert tuple(provider_row) == (LOCAL_MOCK_PROVIDER_KEY, "active", 2)
    assert tuple(active_credential) == ("active", 2)


def test_existing_real_draft_is_not_overwritten(local_mock_database: LocalMockHarness) -> None:
    account_id = uuid4()
    runtime_config_version_id = uuid4()
    now = datetime.now(UTC)
    with local_mock_database.sessions.begin() as session:
        session.execute(
            insert(accounts).values(
                account_id=account_id,
                login_name=f"synthetic.real.draft.{account_id}@example.com",
                display_name="合成真实配置管理员",
                password_hash="disabled-synthetic-account",
                status="disabled",
                auth_version=1,
                created_at=now,
                created_by_actor_id=account_id,
                updated_at=now,
                updated_by_actor_id=account_id,
                version=1,
            )
        )
        session.execute(
            insert(ai_runtime_config_versions).values(
                runtime_config_version_id=runtime_config_version_id,
                version_number=1,
                display_name="合成真实运行配置草稿",
                content_hash="a" * 64,
                system_prompt_template="合成真实配置提示词",
                system_prompt_hash="b" * 64,
                component_versions={
                    "chunking": "synthetic-v1",
                    "embedding": "synthetic-v1",
                    "index_schema": "synthetic-v1",
                    "reranker": "synthetic-v1",
                    "retrieval": "synthetic-v1",
                    "source_ranking": "synthetic-v1",
                    "safety": "rag-safety-v2",
                    "data_source_interface": "data-source-v1",
                    "relevance_grader_interface": "relevance-grader-v1",
                    "multimodal_router_interface": "multimodal-router-v1",
                },
                attempt_timeout_ms=500,
                total_timeout_ms=2_000,
                max_attempts_per_route=1,
                max_prompt_characters=4_000,
                max_output_tokens=256,
                max_response_characters=8_000,
                circuit_failure_threshold=3,
                circuit_recovery_ms=30_000,
                rule_degradation_message=None,
                max_estimated_cost_microunits=5_000_000,
                created_by_account_id=account_id,
                created_at=now,
            )
        )

    with pytest.raises(AiRuntimeConfigNotActiveError):
        local_mock_database.bootstrap.ensure(account_id)

    with local_mock_database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(model_provider_configurations)) == 0
        assert session.scalar(select(func.count()).select_from(ai_runtime_config_versions)) == 1
        assert session.scalar(select(func.count()).select_from(ai_runtime_config_publication)) == 0
