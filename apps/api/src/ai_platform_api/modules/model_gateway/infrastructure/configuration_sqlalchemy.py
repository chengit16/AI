from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime
from types import TracebackType
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import CursorResult, Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.common.security import EncryptedSecret
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.model_gateway.domain.configuration import (
    CredentialStatus,
    ModelProviderConfiguration,
    ModelProviderCredential,
    ModelProviderUnitOfWork,
    PolicyReviewStatus,
    ProbeStatus,
    ProviderAdapterKind,
    ProviderStatus,
)
from ai_platform_api.modules.model_gateway.domain.configuration_errors import (
    ModelProviderConflictError,
)
from ai_platform_api.modules.model_gateway.domain.models import (
    ModelCapability,
    ProviderLocation,
)
from ai_platform_api.persistence.tables import (
    model_provider_configurations,
    model_provider_credentials,
    platform_administrators,
    platform_audit_records,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyModelProviderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def is_platform_administrator(self, account_id: UUID) -> bool:
        return bool(
            self._session.scalar(
                select(func.count())
                .select_from(platform_administrators)
                .where(
                    platform_administrators.c.account_id == account_id,
                    platform_administrators.c.status == "active",
                )
            )
        )

    def list_configurations(self) -> tuple[ModelProviderConfiguration, ...]:
        rows = self._session.execute(
            select(model_provider_configurations).order_by(
                model_provider_configurations.c.provider_key
            )
        )
        return tuple(_configuration(row) for row in rows)

    def get_configuration(
        self,
        provider_id: UUID,
        *,
        for_update: bool = False,
    ) -> ModelProviderConfiguration | None:
        statement = select(model_provider_configurations).where(
            model_provider_configurations.c.provider_id == provider_id
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _configuration(row) if row is not None else None

    def add_configuration(self, configuration: ModelProviderConfiguration) -> None:
        try:
            self._session.execute(
                insert(model_provider_configurations).values(**_configuration_values(configuration))
            )
        except IntegrityError as error:
            raise ModelProviderConflictError from error

    def save_configuration(self, configuration: ModelProviderConfiguration) -> None:
        previous_version = configuration.version - 1
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(model_provider_configurations)
                .where(
                    model_provider_configurations.c.provider_id == configuration.provider_id,
                    model_provider_configurations.c.version == previous_version,
                )
                .values(**_configuration_values(configuration))
            ),
        )
        if result.rowcount != 1:
            raise ModelProviderConflictError

    def next_credential_version(self, provider_id: UUID) -> int:
        current = self._session.scalar(
            select(model_provider_credentials.c.credential_version)
            .where(model_provider_credentials.c.provider_id == provider_id)
            .order_by(model_provider_credentials.c.credential_version.desc())
            .limit(1)
            .with_for_update()
        )
        return int(current or 0) + 1

    def get_active_credential(
        self,
        provider_id: UUID,
        *,
        for_update: bool = False,
    ) -> ModelProviderCredential | None:
        statement = select(model_provider_credentials).where(
            model_provider_credentials.c.provider_id == provider_id,
            model_provider_credentials.c.status == "active",
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _credential(row) if row is not None else None

    def replace_active_credential(self, credential: ModelProviderCredential) -> None:
        now = credential.created_at
        self._session.execute(
            update(model_provider_credentials)
            .where(
                model_provider_credentials.c.provider_id == credential.provider_id,
                model_provider_credentials.c.status == "active",
            )
            .values(status="revoked", revoked_at=now)
        )
        try:
            self._session.execute(
                insert(model_provider_credentials).values(**_credential_values(credential))
            )
        except IntegrityError as error:
            raise ModelProviderConflictError from error


class SqlAlchemyPlatformAuditWriter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        *,
        account_id: UUID,
        provider_id: UUID | None = None,
        runtime_config_version_id: UUID | None = None,
        action: str,
        request_id: UUID,
        trace_id: str,
        occurred_at: datetime,
        details: dict[str, object],
    ) -> None:
        self._session.execute(
            insert(platform_audit_records).values(
                audit_id=uuid4(),
                account_id=account_id,
                provider_id=provider_id,
                runtime_config_version_id=runtime_config_version_id,
                action=action,
                request_id=request_id,
                trace_id=trace_id,
                occurred_at=occurred_at,
                details=details,
            )
        )


class SqlAlchemyModelProviderUnitOfWork(ModelProviderUnitOfWork):
    """供应商事实、凭证轮换和平台审计必须位于同一数据库事务。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[Session, SqlAlchemyModelProviderRepository, SqlAlchemyPlatformAuditWriter] | None
        ] = ContextVar("model_provider_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyModelProviderUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Model Provider Unit of Work 不允许在同一上下文重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyModelProviderRepository(session),
                SqlAlchemyPlatformAuditWriter(session),
            )
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        state = self._state.get()
        if state is not None:
            session = state[0]
            if exc_type is not None:
                session.rollback()
            session.close()
            self._state.set(None)

    @property
    def providers(self) -> SqlAlchemyModelProviderRepository:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Model Provider Unit of Work 尚未进入事务范围")
        return state[1]

    @property
    def audit(self) -> SqlAlchemyPlatformAuditWriter:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Model Provider Unit of Work 尚未进入事务范围")
        return state[2]

    def commit(self) -> None:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Model Provider Unit of Work 尚未进入事务范围")
        state[0].commit()


def _configuration(row: Row[Any]) -> ModelProviderConfiguration:
    return ModelProviderConfiguration(
        provider_id=row.provider_id,
        provider_key=row.provider_key,
        display_name=row.display_name,
        adapter_kind=cast("ProviderAdapterKind", row.adapter_kind),
        base_url=row.base_url,
        probe_model_id=row.probe_model_id,
        location=cast("ProviderLocation", row.location),
        declared_capabilities=frozenset(cast("list[ModelCapability]", row.declared_capabilities)),
        policy_review_status=cast("PolicyReviewStatus", row.policy_review_status),
        max_security_level=cast("SecurityLevel", row.max_security_level),
        retention_days=row.retention_days,
        training_usage_allowed=row.training_usage_allowed,
        policy_url=row.policy_url,
        policy_version=row.policy_version,
        policy_reviewed_by_account_id=row.policy_reviewed_by_account_id,
        policy_reviewed_at=row.policy_reviewed_at,
        probe_status=cast("ProbeStatus", row.probe_status),
        probed_capabilities=frozenset(cast("list[ModelCapability]", row.probed_capabilities)),
        last_probe_error_code=row.last_probe_error_code,
        last_probed_at=row.last_probed_at,
        status=cast("ProviderStatus", row.status),
        created_by_account_id=row.created_by_account_id,
        created_at=row.created_at,
        updated_by_account_id=row.updated_by_account_id,
        updated_at=row.updated_at,
        version=row.version,
    )


def _configuration_values(configuration: ModelProviderConfiguration) -> dict[str, object]:
    return {
        "provider_id": configuration.provider_id,
        "provider_key": configuration.provider_key,
        "display_name": configuration.display_name,
        "adapter_kind": configuration.adapter_kind,
        "base_url": configuration.base_url,
        "probe_model_id": configuration.probe_model_id,
        "location": configuration.location,
        "declared_capabilities": sorted(configuration.declared_capabilities),
        "policy_review_status": configuration.policy_review_status,
        "max_security_level": configuration.max_security_level,
        "retention_days": configuration.retention_days,
        "training_usage_allowed": configuration.training_usage_allowed,
        "policy_url": configuration.policy_url,
        "policy_version": configuration.policy_version,
        "policy_reviewed_by_account_id": configuration.policy_reviewed_by_account_id,
        "policy_reviewed_at": configuration.policy_reviewed_at,
        "probe_status": configuration.probe_status,
        "probed_capabilities": sorted(configuration.probed_capabilities),
        "last_probe_error_code": configuration.last_probe_error_code,
        "last_probed_at": configuration.last_probed_at,
        "status": configuration.status,
        "created_by_account_id": configuration.created_by_account_id,
        "created_at": configuration.created_at,
        "updated_by_account_id": configuration.updated_by_account_id,
        "updated_at": configuration.updated_at,
        "version": configuration.version,
    }


def _credential(row: Row[Any]) -> ModelProviderCredential:
    return ModelProviderCredential(
        credential_id=row.credential_id,
        provider_id=row.provider_id,
        credential_version=row.credential_version,
        envelope=EncryptedSecret(
            key_version=row.master_key_version,
            encrypted_data_key=bytes(row.encrypted_data_key),
            data_key_nonce=bytes(row.data_key_nonce),
            ciphertext=bytes(row.ciphertext),
            data_nonce=bytes(row.data_nonce),
            last_four=row.last_four,
        ),
        status=cast("CredentialStatus", row.status),
        created_by_account_id=row.created_by_account_id,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


def _credential_values(credential: ModelProviderCredential) -> dict[str, object]:
    return {
        "credential_id": credential.credential_id,
        "provider_id": credential.provider_id,
        "credential_version": credential.credential_version,
        "master_key_version": credential.envelope.key_version,
        "encrypted_data_key": credential.envelope.encrypted_data_key,
        "data_key_nonce": credential.envelope.data_key_nonce,
        "ciphertext": credential.envelope.ciphertext,
        "data_nonce": credential.envelope.data_nonce,
        "last_four": credential.envelope.last_four,
        "status": credential.status,
        "created_by_account_id": credential.created_by_account_id,
        "created_at": credential.created_at,
        "revoked_at": credential.revoked_at,
    }
