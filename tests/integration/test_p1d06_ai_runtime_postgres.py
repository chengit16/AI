"""验证 P1D-06 不可变运行配置、发布指针和调用记录。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import PlatformRequestContext
from ai_platform_api.common.security import EnvelopeSecretCipher, MasterKeyFile
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.modules.model_gateway.application.configurations import (
    ModelProviderConfigurationService,
)
from ai_platform_api.modules.model_gateway.application.runtime_configurations import (
    AiRuntimeConfigurationService,
)
from ai_platform_api.modules.model_gateway.application.runtime_gateway import (
    RuntimeModelGatewayService,
)
from ai_platform_api.modules.model_gateway.domain.configuration import (
    CapabilityProbeResult,
    RuntimeProviderAccess,
)
from ai_platform_api.modules.model_gateway.domain.models import (
    GatewayPolicy,
    ModelCapability,
    ModelMessage,
    ModelRequest,
)
from ai_platform_api.modules.model_gateway.domain.runtime import (
    RuntimeComponentVersions,
    RuntimeRouteDraft,
)
from ai_platform_api.modules.model_gateway.domain.runtime_errors import (
    ModelInvocationConflictError,
)
from ai_platform_api.modules.model_gateway.infrastructure.configuration_sqlalchemy import (
    SqlAlchemyModelProviderUnitOfWork,
)
from ai_platform_api.modules.model_gateway.infrastructure.mock import MockProvider
from ai_platform_api.modules.model_gateway.infrastructure.runtime_sqlalchemy import (
    SqlAlchemyRuntimeConfigurationReader,
    SqlAlchemyRuntimeConfigurationUnitOfWork,
    SqlAlchemyRuntimeInvocationStore,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    ai_runtime_config_publication,
    ai_runtime_config_versions,
    ai_runtime_model_routes,
    model_invocation_attempts,
    model_invocations,
    platform_administrators,
    platform_audit_records,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext("8" * 32, "9" * 16)


class FixedUrlPolicy:
    def normalize_and_validate(self, value: str) -> str:
        assert value == "https://api.synthetic.example/v1"
        return value


class PassingProbe:
    def probe(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str,
        wire_api: str,
        capabilities: frozenset[ModelCapability],
    ) -> CapabilityProbeResult:
        assert base_url == "https://api.synthetic.example/v1"
        assert api_key.startswith("synthetic-runtime-secret")
        assert model_id.startswith("synthetic-probe")
        assert wire_api == "chat_completions"
        return CapabilityProbeResult("passed", capabilities)


class MockFactory:
    def __init__(self, providers: dict[str, MockProvider]) -> None:
        self.providers = providers

    def create(self, access: RuntimeProviderAccess) -> MockProvider:
        return self.providers[str(access.configuration.provider_id)]


@dataclass(frozen=True)
class RuntimeHarness:
    engine: Engine
    sessions: sessionmaker[Session]
    registration: RegistrationService
    providers: ModelProviderConfigurationService
    configurations: AiRuntimeConfigurationService


@pytest.fixture(scope="module")
def runtime_database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[RuntimeHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1d06_test_{uuid4().hex}"
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
    key_path = tmp_path_factory.mktemp("p1d06-secrets") / "master.key"
    key_path.write_bytes(b"r" * 32)
    key_path.chmod(0o600)
    provider_service = ModelProviderConfigurationService(
        SqlAlchemyModelProviderUnitOfWork(sessions),
        EnvelopeSecretCipher(MasterKeyFile(str(key_path))),
        FixedUrlPolicy(),
        PassingProbe(),
    )
    try:
        yield RuntimeHarness(
            engine,
            sessions,
            RegistrationService(
                reader,
                SqlAlchemyRegistrationUnitOfWork(sessions),
                Argon2idPasswordAdapter(),
            ),
            provider_service,
            AiRuntimeConfigurationService(SqlAlchemyRuntimeConfigurationUnitOfWork(sessions)),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def platform_context(account_id: UUID) -> PlatformRequestContext:
    return PlatformRequestContext(
        request_id=uuid4(),
        trace=TRACE,
        actor_id=account_id,
        account_id=account_id,
    )


def components() -> RuntimeComponentVersions:
    return RuntimeComponentVersions(
        "recursive-cjk-v1",
        "deterministic-hash-1024-v1",
        "index-v1",
        "bge-reranker-v1",
        "hybrid-rrf-v1",
        "source-priority-v1",
        "rag-safety-v2",
        "data-source-v1",
        "relevance-grader-v1",
        "multimodal-router-v1",
    )


def gateway_policy() -> GatewayPolicy:
    return GatewayPolicy(
        attempt_timeout_ms=500,
        total_timeout_ms=2_000,
        max_attempts_per_route=2,
        max_prompt_characters=4_000,
        max_output_tokens=256,
        max_response_characters=8_000,
        circuit_failure_threshold=3,
        circuit_recovery_ms=30_000,
        max_estimated_cost_microunits=5_000_000,
    )


def activate_provider(
    harness: RuntimeHarness,
    context: PlatformRequestContext,
    provider_key: str,
) -> UUID:
    created = harness.providers.create(
        context,
        provider_key=provider_key,
        display_name=f"合成供应商 {provider_key}",
        adapter_kind="openai_compatible",
        base_url="https://api.synthetic.example/v1",
        probe_model_id=f"synthetic-probe-{provider_key}",
        location="external",
        declared_capabilities=frozenset({"generation"}),
        api_key=f"synthetic-runtime-secret-{provider_key}",
    )
    harness.providers.review_data_policy(
        context,
        created.provider_id,
        approved=True,
        max_security_level="INTERNAL",
        retention_days=0,
        training_usage_allowed=False,
        policy_url="https://policy.synthetic.example/privacy",
        policy_version="synthetic-v1",
    )
    harness.providers.probe(context, created.provider_id)
    harness.providers.activate(context, created.provider_id)
    return created.provider_id


def test_runtime_config_publication_gateway_usage_and_immutability_are_persistent(
    runtime_database: RuntimeHarness,
) -> None:
    registration = runtime_database.registration.register(
        login_name=f"synthetic.runtime.{uuid4().hex}@example.com",
        display_name="合成运行管理员",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    now = datetime.now(UTC)
    with runtime_database.sessions.begin() as session:
        session.execute(
            insert(platform_administrators).values(
                account_id=registration.account_id,
                status="active",
                granted_by_account_id=registration.account_id,
                granted_at=now,
                revoked_at=None,
            )
        )
    context = platform_context(registration.account_id)
    primary_id = activate_provider(runtime_database, context, "synthetic_primary")
    backup_id = activate_provider(runtime_database, context, "synthetic_backup")
    routes = (
        RuntimeRouteDraft(
            primary_id,
            1,
            "synthetic-primary-model",
            frozenset({"generation"}),
            20_000_000,
            40_000_000,
        ),
        RuntimeRouteDraft(
            backup_id,
            2,
            "synthetic-backup-model",
            frozenset({"generation"}),
            10_000_000,
            20_000_000,
        ),
    )
    first = runtime_database.configurations.create(
        context,
        display_name="合成运行配置 V1",
        system_prompt_template="你是只使用授权证据的合成助手。",
        components=components(),
        policy=gateway_policy(),
        routes=routes,
    )
    assert (
        runtime_database.configurations.activate(
            context, first.runtime_config_version_id
        ).generation
        == 1
    )
    second = runtime_database.configurations.create(
        context,
        display_name="合成运行配置 V2",
        system_prompt_template="你是只使用授权证据的合成助手。当前发布为 V2。",
        components=components(),
        policy=gateway_policy(),
        routes=routes,
    )
    assert (
        runtime_database.configurations.activate(
            context, second.runtime_config_version_id
        ).generation
        == 2
    )

    primary = MockProvider(str(primary_id), outcomes=["timeout", "timeout"])
    backup = MockProvider(str(backup_id), response_prefix="合成备用模型")
    gateway = RuntimeModelGatewayService(
        SqlAlchemyRuntimeConfigurationReader(runtime_database.sessions),
        SqlAlchemyRuntimeInvocationStore(runtime_database.sessions),
        runtime_database.providers,
        MockFactory({str(primary_id): primary, str(backup_id): backup}),
    )
    invocation_id = uuid4()
    request = ModelRequest(
        invocation_id=invocation_id,
        workspace_id=registration.personal_workspace_id,
        trace_id=TRACE.trace_id,
        traceparent=TRACE.traceparent,
        task_type="chat.answer",
        messages=(ModelMessage("user", "请回答合成问题"),),
        required_capabilities=frozenset({"generation"}),
        max_output_tokens=32,
        external_data_allowed=True,
        security_level="INTERNAL",
    )
    result = gateway.invoke(request, runtime_config_version_id=first.runtime_config_version_id)
    assert result.provider_id == str(backup_id)
    assert result.runtime_config_version_id == first.runtime_config_version_id
    with pytest.raises(ModelInvocationConflictError):
        gateway.invoke(request, runtime_config_version_id=first.runtime_config_version_id)

    with runtime_database.sessions() as session:
        invocation = session.execute(
            select(model_invocations).where(model_invocations.c.invocation_id == invocation_id)
        ).one()
        attempts = session.execute(
            select(model_invocation_attempts)
            .where(model_invocation_attempts.c.invocation_id == invocation_id)
            .order_by(model_invocation_attempts.c.attempt_index)
        ).all()
        assert invocation.status == "succeeded"
        assert invocation.runtime_config_version_id == first.runtime_config_version_id
        assert invocation.selected_provider_id == backup_id
        assert invocation.input_tokens > 0
        assert invocation.estimated_cost_microunits > 0
        assert [item.failure_kind for item in attempts] == ["timeout", "timeout", None]
        assert [item.credential_version for item in attempts] == [1, 1, 1]
        assert session.scalar(select(func.count()).select_from(ai_runtime_model_routes)) == 4

    # 不可变触发器必须阻止维护代码直接覆盖历史名称或路由价格。
    with pytest.raises(DBAPIError), runtime_database.sessions.begin() as session:
        session.execute(
            update(ai_runtime_config_versions)
            .where(
                ai_runtime_config_versions.c.runtime_config_version_id
                == first.runtime_config_version_id
            )
            .values(display_name="被篡改")
        )

    assert (
        runtime_database.configurations.activate(
            context, first.runtime_config_version_id
        ).generation
        == 3
    )
    with runtime_database.sessions() as session:
        publication = session.execute(select(ai_runtime_config_publication)).one()
        assert publication.runtime_config_version_id == first.runtime_config_version_id
        assert publication.generation == 3
        assert session.scalar(select(func.count()).select_from(platform_audit_records)) == 13
