"""验证 P1D-05 模型供应商、加密凭证和治理状态持久化。"""

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
from ai_platform_api.modules.model_gateway.domain.configuration import CapabilityProbeResult
from ai_platform_api.modules.model_gateway.domain.configuration_errors import (
    ModelProviderDataPolicyDeniedError,
    PlatformAdministratorRequiredError,
)
from ai_platform_api.modules.model_gateway.domain.models import ModelCapability
from ai_platform_api.modules.model_gateway.infrastructure.configuration_sqlalchemy import (
    SqlAlchemyModelProviderUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    model_provider_credentials,
    platform_administrators,
    platform_audit_records,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, insert, select, text
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext("5" * 32, "6" * 16)


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
        capabilities: frozenset[ModelCapability],
    ) -> CapabilityProbeResult:
        assert base_url == "https://api.synthetic.example/v1"
        assert api_key.startswith("synthetic-provider-secret")
        assert model_id == "synthetic-chat"
        return CapabilityProbeResult("passed", capabilities)


@dataclass(frozen=True)
class ProviderHarness:
    engine: Engine
    sessions: sessionmaker[Session]
    registration: RegistrationService
    providers: ModelProviderConfigurationService


@pytest.fixture(scope="module")
def provider_database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ProviderHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1d05_test_{uuid4().hex}"
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
    key_path = tmp_path_factory.mktemp("p1d05-secrets") / "master.key"
    key_path.write_bytes(b"p" * 32)
    key_path.chmod(0o600)
    try:
        yield ProviderHarness(
            engine,
            sessions,
            RegistrationService(
                reader,
                SqlAlchemyRegistrationUnitOfWork(sessions),
                Argon2idPasswordAdapter(),
            ),
            ModelProviderConfigurationService(
                SqlAlchemyModelProviderUnitOfWork(sessions),
                EnvelopeSecretCipher(MasterKeyFile(str(key_path))),
                FixedUrlPolicy(),
                PassingProbe(),
            ),
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


def register_account(harness: ProviderHarness, identity: str) -> UUID:
    result = harness.registration.register(
        login_name=f"synthetic.provider.{identity}.{uuid4().hex}@example.com",
        display_name=f"合成供应商用户 {identity}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return result.account_id


def test_platform_admin_provider_lifecycle_is_encrypted_audited_and_policy_gated(
    provider_database: ProviderHarness,
) -> None:
    admin_id = register_account(provider_database, "admin")
    member_id = register_account(provider_database, "member")
    now = datetime.now(UTC)
    with provider_database.sessions.begin() as session:
        session.execute(
            insert(platform_administrators).values(
                account_id=admin_id,
                status="active",
                granted_by_account_id=admin_id,
                granted_at=now,
                revoked_at=None,
            )
        )

    with pytest.raises(PlatformAdministratorRequiredError):
        provider_database.providers.list_configurations(platform_context(member_id))

    plaintext_v1 = "synthetic-provider-secret-v1"
    created = provider_database.providers.create(
        platform_context(admin_id),
        provider_key="synthetic_gateway",
        display_name="合成 GPT 中转",
        adapter_kind="openai_compatible",
        base_url="https://api.synthetic.example/v1",
        probe_model_id="synthetic-chat",
        location="external",
        declared_capabilities=frozenset({"generation", "structured_output"}),
        api_key=plaintext_v1,
    )
    with provider_database.sessions() as session:
        credential = session.execute(
            select(model_provider_credentials).where(
                model_provider_credentials.c.provider_id == created.provider_id
            )
        ).one()
        assert plaintext_v1.encode() not in bytes(credential.ciphertext)
        assert credential.last_four == "t-v1"

    with pytest.raises(ModelProviderDataPolicyDeniedError):
        provider_database.providers.activate(platform_context(admin_id), created.provider_id)
    provider_database.providers.review_data_policy(
        platform_context(admin_id),
        created.provider_id,
        approved=True,
        max_security_level="INTERNAL",
        retention_days=0,
        training_usage_allowed=False,
        policy_url="https://policy.synthetic.example/privacy",
        policy_version="synthetic-v1",
    )
    provider_database.providers.probe(platform_context(admin_id), created.provider_id)
    active = provider_database.providers.activate(platform_context(admin_id), created.provider_id)
    assert active.status == "active"
    assert (
        provider_database.providers.require_export_allowed(
            created.provider_id, security_level="INTERNAL"
        ).provider_id
        == created.provider_id
    )
    with pytest.raises(ModelProviderDataPolicyDeniedError):
        provider_database.providers.require_export_allowed(
            created.provider_id, security_level="CONFIDENTIAL"
        )

    provider_database.providers.rotate_credential(
        platform_context(admin_id),
        created.provider_id,
        api_key="synthetic-provider-secret-v2",
    )
    with provider_database.sessions() as session:
        credential_statuses = tuple(
            tuple(row)
            for row in session.execute(
                select(
                    model_provider_credentials.c.credential_version,
                    model_provider_credentials.c.status,
                )
                .where(model_provider_credentials.c.provider_id == created.provider_id)
                .order_by(model_provider_credentials.c.credential_version)
            )
        )
        audit_count = session.scalar(
            select(func.count())
            .select_from(platform_audit_records)
            .where(platform_audit_records.c.provider_id == created.provider_id)
        )
    assert credential_statuses == ((1, "revoked"), (2, "active"))
    assert audit_count == 5
