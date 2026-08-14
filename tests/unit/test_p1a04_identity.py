from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.authentication import (
    ApiKeyService,
    AuthenticationService,
)
from ai_platform_api.modules.identity.application.errors import (
    ApiKeyConfigurationError,
    ApiKeyInvalidError,
    AuthenticationRequiredError,
    CsrfValidationError,
    InvalidCredentialsError,
    WorkspaceContextDeniedError,
)
from ai_platform_api.modules.identity.domain.entitlements import OpenApiEntitlement
from ai_platform_api.modules.identity.domain.models import (
    AccountCredential,
    ApiKeyWriter,
    BrowserSession,
    IdentityUnitOfWork,
    OpenApiKey,
    WorkspaceAccess,
)
from ai_platform_api.modules.identity.infrastructure.security import (
    Argon2idPasswordAdapter,
    EncryptedSecret,
    EnvelopeSecretCipher,
    MasterKeyFile,
    Sha256SecretDigester,
)
from cryptography.exceptions import InvalidTag

ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000001")
OTHER_WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000002")
KEY_ID = UUID("30000000-0000-4000-8000-000000000001")
ACTOR_ID = UUID("40000000-0000-4000-8000-000000000001")
REQUEST_ID = UUID("50000000-0000-4000-8000-000000000001")
TRACE = TraceContext.continue_from("00-0123456789abcdef0123456789abcdef-0123456789abcdef-01")


class MemorySessions:
    def __init__(self) -> None:
        self.sessions: dict[str, BrowserSession] = {}
        self.created_ttl: int | None = None

    def create(self, session: BrowserSession, ttl_seconds: int) -> str:
        self.created_ttl = ttl_seconds
        self.sessions["synthetic-session-token"] = session
        return "synthetic-session-token"

    def resolve(self, token: str) -> BrowserSession | None:
        return self.sessions.get(token)

    def revoke(self, token: str) -> None:
        self.sessions.pop(token, None)

    def close(self) -> None:
        self.sessions.clear()


class MemoryIdentity:
    def __init__(self, password_hash: str) -> None:
        self.account = AccountCredential(
            account_id=ACCOUNT_ID,
            login_name="owner@example.com",
            password_hash=password_hash,
            status="active",
            auth_version=1,
        )
        self.accesses = {
            WORKSPACE_ID: WorkspaceAccess(
                workspace_id=WORKSPACE_ID,
                account_id=ACCOUNT_ID,
                workspace_status="active",
                membership_status="active",
            )
        }
        self.api_keys: dict[UUID, OpenApiKey] = {}
        self.personal_workspace_id: UUID | None = WORKSPACE_ID

    def get_account_by_login(self, login_name: str) -> AccountCredential | None:
        return self.account if login_name == self.account.login_name else None

    def get_account(self, account_id: UUID) -> AccountCredential | None:
        return self.account if account_id == self.account.account_id else None

    def get_personal_workspace_id(self, account_id: UUID) -> UUID | None:
        return self.personal_workspace_id if account_id == ACCOUNT_ID else None

    def get_workspace_access(
        self,
        account_id: UUID,
        workspace_id: UUID,
    ) -> WorkspaceAccess | None:
        return self.accesses.get(workspace_id) if account_id == ACCOUNT_ID else None

    def get_api_key(self, key_id: UUID) -> OpenApiKey | None:
        return self.api_keys.get(key_id)


class MemoryApiKeyWriter:
    def __init__(self, identity: MemoryIdentity) -> None:
        self.identity = identity
        self.last_name: str | None = None
        self.last_four: str | None = None

    def add(self, api_key: OpenApiKey, *, name: str, last_four: str) -> None:
        self.identity.api_keys[api_key.key_id] = api_key
        self.last_name = name
        self.last_four = last_four

    def revoke(self, workspace_id: UUID, key_id: UUID, revoked_at: datetime) -> bool:
        api_key = self.identity.api_keys.get(key_id)
        if (
            api_key is None
            or api_key.workspace_id != workspace_id
            or api_key.revoked_at is not None
        ):
            return False
        self.identity.api_keys[key_id] = OpenApiKey(
            key_id=api_key.key_id,
            actor_id=api_key.actor_id,
            workspace_id=api_key.workspace_id,
            created_by_account_id=api_key.created_by_account_id,
            secret_digest=api_key.secret_digest,
            scopes=api_key.scopes,
            expires_at=api_key.expires_at,
            revoked_at=revoked_at,
        )
        return True


class MemoryUnitOfWork:
    def __init__(self, writer: MemoryApiKeyWriter) -> None:
        self.api_keys: ApiKeyWriter = writer
        self.committed = False

    def __enter__(self) -> IdentityUnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def commit(self) -> None:
        self.committed = True


class MemoryEntitlementAccess:
    def __init__(self, *, active: bool = True) -> None:
        self.active = active

    def get_open_api_entitlement(self, workspace_id: UUID) -> OpenApiEntitlement | None:
        assert workspace_id == WORKSPACE_ID
        return OpenApiEntitlement(self.active, self.active)


def authentication_fixture() -> tuple[
    AuthenticationService,
    MemoryIdentity,
    MemorySessions,
    Argon2idPasswordAdapter,
]:
    passwords = Argon2idPasswordAdapter()
    identity = MemoryIdentity(passwords.hash("synthetic-password-123"))
    sessions = MemorySessions()
    service = AuthenticationService(
        repository=identity,
        sessions=sessions,
        passwords=passwords,
        secrets_digester=Sha256SecretDigester(),
        session_ttl_seconds=43_200,
        entitlements=MemoryEntitlementAccess(),
    )
    return service, identity, sessions, passwords


def test_password_uses_argon2id_and_rejects_short_or_wrong_password() -> None:
    passwords = Argon2idPasswordAdapter()
    password_hash = passwords.hash("synthetic-password-123")

    assert password_hash.startswith("$argon2id$")
    assert "synthetic-password-123" not in password_hash
    assert passwords.verify(password_hash, "synthetic-password-123") is True
    assert passwords.verify(password_hash, "wrong-password") is False
    assert passwords.verify(None, "wrong-password") is False
    with pytest.raises(ValueError):
        passwords.hash("too-short")


def test_browser_session_requires_valid_csrf_and_active_workspace_membership() -> None:
    service, _, sessions, _ = authentication_fixture()
    result = service.login(
        "OWNER@EXAMPLE.COM",
        "synthetic-password-123",
    )

    context = service.browser_context(
        session_token=result.session_token,
        csrf_token=result.csrf_token,
        require_csrf=True,
        workspace_id=WORKSPACE_ID,
        request_id=REQUEST_ID,
        trace=TRACE,
    )

    assert result.account_id == ACCOUNT_ID
    assert result.personal_workspace_id == WORKSPACE_ID
    assert sessions.created_ttl == 43_200
    assert context.actor_id == ACCOUNT_ID
    assert context.user_id == ACCOUNT_ID
    assert context.workspace_id == WORKSPACE_ID
    assert context.authentication_method == "browser_session"
    assert context.credential_scopes is None
    with pytest.raises(CsrfValidationError):
        service.browser_context(
            session_token=result.session_token,
            csrf_token="forged-csrf",
            require_csrf=True,
            workspace_id=WORKSPACE_ID,
            request_id=REQUEST_ID,
            trace=TRACE,
        )


def test_platform_browser_context_does_not_forge_workspace_membership() -> None:
    service, identity, _, _ = authentication_fixture()
    result = service.login("owner@example.com", "synthetic-password-123")
    identity.accesses.clear()

    platform_context = service.platform_browser_context(
        session_token=result.session_token,
        csrf_token=result.csrf_token,
        require_csrf=True,
        request_id=REQUEST_ID,
        trace=TRACE,
    )

    assert platform_context.account_id == ACCOUNT_ID
    assert platform_context.actor_id == ACCOUNT_ID
    assert not hasattr(platform_context, "workspace_id")
    with pytest.raises(CsrfValidationError):
        service.platform_browser_context(
            session_token=result.session_token,
            csrf_token="forged-csrf",
            require_csrf=True,
            request_id=REQUEST_ID,
            trace=TRACE,
        )
    with pytest.raises(WorkspaceContextDeniedError):
        service.browser_context(
            session_token=result.session_token,
            csrf_token=None,
            require_csrf=False,
            workspace_id=OTHER_WORKSPACE_ID,
            request_id=REQUEST_ID,
            trace=TRACE,
        )


def test_disabled_account_or_changed_auth_version_invalidates_session() -> None:
    service, identity, sessions, _ = authentication_fixture()
    result = service.login("owner@example.com", "synthetic-password-123")
    identity.account = AccountCredential(
        account_id=identity.account.account_id,
        login_name=identity.account.login_name,
        password_hash=identity.account.password_hash,
        status="active",
        auth_version=2,
    )

    with pytest.raises(AuthenticationRequiredError):
        service.browser_context(
            session_token=result.session_token,
            csrf_token=None,
            require_csrf=False,
            workspace_id=WORKSPACE_ID,
            request_id=REQUEST_ID,
            trace=TRACE,
        )

    assert sessions.resolve(result.session_token) is None


def test_login_does_not_distinguish_unknown_account_from_wrong_password() -> None:
    service, _, _, _ = authentication_fixture()

    with pytest.raises(InvalidCredentialsError):
        service.login("unknown@example.com", "synthetic-password-123")
    with pytest.raises(InvalidCredentialsError):
        service.login("owner@example.com", "wrong-password")


def test_login_rejects_account_without_active_personal_workspace() -> None:
    service, identity, _, _ = authentication_fixture()
    identity.personal_workspace_id = None

    with pytest.raises(InvalidCredentialsError):
        service.login("owner@example.com", "synthetic-password-123")


def test_open_api_key_is_one_time_secret_with_scoped_actor() -> None:
    auth, identity, _, _ = authentication_fixture()
    writer = MemoryApiKeyWriter(identity)
    unit_of_work = MemoryUnitOfWork(writer)
    api_keys = ApiKeyService(
        identity,
        unit_of_work,
        Sha256SecretDigester(),
        MemoryEntitlementAccess(),
    )
    browser_context = RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TRACE,
        request_id=REQUEST_ID,
        authentication_method="browser_session",
    )

    issued = api_keys.issue(
        context=browser_context,
        name=" Synthetic integration ",
        scopes=("knowledge.document.read", "knowledge.document.read"),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    stored = identity.api_keys[issued.key_id]
    api_context = auth.api_key_context(
        credential=issued.plaintext,
        workspace_id=WORKSPACE_ID,
        request_id=REQUEST_ID,
        trace=TRACE,
    )

    assert unit_of_work.committed is True
    assert writer.last_name == "Synthetic integration"
    assert writer.last_four == issued.last_four
    assert issued.plaintext not in stored.secret_digest
    assert len(stored.secret_digest) == 64
    assert api_context.actor_id == issued.actor_id
    assert api_context.user_id == ACCOUNT_ID
    assert api_context.credential_scopes == frozenset({"knowledge.document.read"})
    with pytest.raises(ApiKeyInvalidError):
        auth.api_key_context(
            credential=issued.plaintext + "forged",
            workspace_id=WORKSPACE_ID,
            request_id=REQUEST_ID,
            trace=TRACE,
        )

    api_keys.revoke(context=browser_context, key_id=issued.key_id)
    with pytest.raises(ApiKeyInvalidError):
        auth.api_key_context(
            credential=issued.plaintext,
            workspace_id=WORKSPACE_ID,
            request_id=REQUEST_ID,
            trace=TRACE,
        )


def test_api_key_rejects_empty_or_malformed_scope() -> None:
    _, identity, _, _ = authentication_fixture()
    api_keys = ApiKeyService(
        identity,
        MemoryUnitOfWork(MemoryApiKeyWriter(identity)),
        Sha256SecretDigester(),
        MemoryEntitlementAccess(),
    )
    context = RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TRACE,
    )

    with pytest.raises(ApiKeyConfigurationError):
        api_keys.issue(context=context, name="empty", scopes=(), expires_at=None)
    with pytest.raises(ApiKeyConfigurationError):
        api_keys.issue(
            context=context,
            name="invalid",
            scopes=("admin",),
            expires_at=None,
        )


def test_envelope_cipher_requires_secure_master_key_and_detects_tampering(
    tmp_path: Path,
) -> None:
    key_path = tmp_path / "master.key"
    key_path.write_bytes(bytes(range(32)))
    key_path.chmod(0o600)
    cipher = EnvelopeSecretCipher(MasterKeyFile(str(key_path)))
    associated_data = b"synthetic-provider:version-1"
    encrypted = cipher.encrypt("sk-test-synthetic-credential", associated_data=associated_data)

    assert cipher.decrypt(encrypted, associated_data=associated_data) == (
        "sk-test-synthetic-credential"
    )
    assert encrypted.last_four == "tial"
    assert b"sk-test-synthetic-credential" not in encrypted.ciphertext
    tampered = EncryptedSecret(
        key_version=encrypted.key_version,
        encrypted_data_key=encrypted.encrypted_data_key,
        data_key_nonce=encrypted.data_key_nonce,
        ciphertext=encrypted.ciphertext[:-1] + bytes([encrypted.ciphertext[-1] ^ 1]),
        data_nonce=encrypted.data_nonce,
        last_four=encrypted.last_four,
    )
    with pytest.raises(InvalidTag):
        cipher.decrypt(tampered, associated_data=associated_data)

    key_path.chmod(0o644)
    with pytest.raises(PermissionError):
        MasterKeyFile(str(key_path)).load()
