from __future__ import annotations

from types import TracebackType
from uuid import UUID

import pytest
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.errors import (
    RegistrationConflictError,
    RegistrationValidationError,
)
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.domain.models import AccountCredential
from ai_platform_api.modules.identity.domain.registration import AccountRegistration
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

REQUEST_ID = UUID("50000000-0000-4000-8000-000000000061")
TRACE = TraceContext.continue_from("00-0123456789abcdef0123456789abcdef-0123456789abcdef-01")


class MemoryIdentityReader:
    def __init__(self) -> None:
        self.account: AccountCredential | None = None

    def get_account_by_login(self, login_name: str) -> AccountCredential | None:
        if self.account is not None and self.account.login_name == login_name:
            return self.account
        return None

    def get_account(self, account_id: UUID) -> AccountCredential | None:
        if self.account is not None and self.account.account_id == account_id:
            return self.account
        return None

    def get_workspace_access(self, account_id: UUID, workspace_id: UUID) -> None:
        return None

    def get_api_key(self, key_id: UUID) -> None:
        return None


class RegistrationWriter:
    def __init__(self) -> None:
        self.registration: AccountRegistration | None = None

    def add(self, registration: AccountRegistration) -> None:
        self.registration = registration


class AuditWriter:
    def __init__(self) -> None:
        self.record: AuditRecord | None = None

    def add(self, record: AuditRecord) -> None:
        self.record = record


class OutboxWriter:
    def __init__(self) -> None:
        self.event: IntegrationEvent | None = None

    def add(self, event: IntegrationEvent) -> None:
        self.event = event


class MemoryRegistrationUnitOfWork:
    def __init__(self) -> None:
        self._registrations = RegistrationWriter()
        self._audit = AuditWriter()
        self._outbox = OutboxWriter()
        self.committed = False

    @property
    def registrations(self) -> RegistrationWriter:
        return self._registrations

    @property
    def audit(self) -> AuditWriter:
        return self._audit

    @property
    def outbox(self) -> OutboxWriter:
        return self._outbox

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def commit(self) -> None:
        self.committed = True


def registration_service(
    reader: MemoryIdentityReader | None = None,
) -> tuple[RegistrationService, MemoryRegistrationUnitOfWork]:
    unit_of_work = MemoryRegistrationUnitOfWork()
    return (
        RegistrationService(
            repository=reader or MemoryIdentityReader(),
            unit_of_work=unit_of_work,
            passwords=Argon2idPasswordAdapter(),
        ),
        unit_of_work,
    )


def test_registration_normalizes_identity_and_emits_minimal_audit() -> None:
    service, unit_of_work = registration_service()

    result = service.register(
        login_name="  SYNTHETIC.USER@EXAMPLE.COM  ",
        display_name="  合成用户  ",
        password="synthetic-password-123",
        request_id=REQUEST_ID,
        trace=TRACE,
    )

    registration = unit_of_work.registrations.registration
    audit = unit_of_work.audit.record
    event = unit_of_work.outbox.event
    assert unit_of_work.committed is True
    assert registration is not None
    assert registration.login_name == "synthetic.user@example.com"
    assert registration.display_name == "合成用户"
    assert registration.personal_workspace_id == result.personal_workspace_id
    assert registration.password_hash.startswith("$argon2id$")
    assert "synthetic-password-123" not in registration.password_hash
    assert audit is not None
    assert audit.attributes == {"workspace_type": "personal"}
    assert "login" not in repr(audit).lower()
    assert event is not None
    assert event.payload == {"personal_workspace_id": str(result.personal_workspace_id)}


def test_registration_rejects_invalid_or_existing_login() -> None:
    service, unit_of_work = registration_service()
    with pytest.raises(RegistrationValidationError):
        service.register(
            login_name="invalid name",
            display_name="合成用户",
            password="synthetic-password-123",
            request_id=REQUEST_ID,
            trace=TRACE,
        )
    assert unit_of_work.committed is False

    reader = MemoryIdentityReader()
    reader.account = AccountCredential(
        account_id=UUID("10000000-0000-4000-8000-000000000061"),
        login_name="existing@example.com",
        password_hash="synthetic-hash",
        status="active",
        auth_version=1,
    )
    duplicate_service, _ = registration_service(reader)
    with pytest.raises(RegistrationConflictError):
        duplicate_service.register(
            login_name="EXISTING@EXAMPLE.COM",
            display_name="合成用户",
            password="synthetic-password-123",
            request_id=REQUEST_ID,
            trace=TRACE,
        )
