from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import cast
from uuid import UUID

from ai_platform_api.app.dependencies import ApplicationContainer
from ai_platform_api.app.errors import ErrorCatalog
from ai_platform_api.app.factory import create_app
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.config import Settings
from ai_platform_api.modules.authorization.application.grants import RolePermissionService
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.identity.application.authentication import (
    ApiKeyService,
    AuthenticationService,
)
from ai_platform_api.modules.identity.application.enterprise import EnterpriseWorkspaceService
from ai_platform_api.modules.identity.application.entitlements import EntitlementService
from ai_platform_api.modules.identity.application.organization import OrganizationService
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.application.roles import RoleService
from ai_platform_api.modules.identity.domain.entitlements import OpenApiEntitlement
from ai_platform_api.modules.identity.domain.models import (
    AccountCredential,
    ApiKeyWriter,
    BrowserSession,
    IdentityUnitOfWork,
    OpenApiKey,
    WorkspaceAccess,
)
from ai_platform_api.modules.identity.domain.registration import RegistrationResult
from ai_platform_api.modules.identity.infrastructure.role_cache import ValkeyRoleResolutionCache
from ai_platform_api.modules.identity.infrastructure.security import (
    Argon2idPasswordAdapter,
    EnvelopeSecretCipher,
    Sha256SecretDigester,
)
from ai_platform_api.modules.identity.infrastructure.session import ValkeySessionStore
from ai_platform_api.persistence.database import PlatformDatabase
from fastapi.testclient import TestClient

from test_support.authorization import AllowRegisteredPolicy

ROOT = Path(__file__).parents[3]
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000021")
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000021")
OTHER_WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000022")


class MemorySessions:
    def __init__(self) -> None:
        self.items: dict[str, BrowserSession] = {}

    def create(self, session: BrowserSession, ttl_seconds: int) -> str:
        assert ttl_seconds == 43_200
        self.items["synthetic-http-session"] = session
        return "synthetic-http-session"

    def resolve(self, token: str) -> BrowserSession | None:
        return self.items.get(token)

    def revoke(self, token: str) -> None:
        self.items.pop(token, None)

    def close(self) -> None:
        self.items.clear()


class MemoryIdentity:
    def __init__(self, password_hash: str) -> None:
        self.account = AccountCredential(
            account_id=ACCOUNT_ID,
            login_name="owner@example.com",
            password_hash=password_hash,
            status="active",
            auth_version=1,
        )
        self.api_keys: dict[UUID, OpenApiKey] = {}

    def get_account_by_login(self, login_name: str) -> AccountCredential | None:
        return self.account if login_name == self.account.login_name else None

    def get_account(self, account_id: UUID) -> AccountCredential | None:
        return self.account if account_id == ACCOUNT_ID else None

    def get_personal_workspace_id(self, account_id: UUID) -> UUID | None:
        return WORKSPACE_ID if account_id == ACCOUNT_ID else None

    def get_workspace_access(
        self,
        account_id: UUID,
        workspace_id: UUID,
    ) -> WorkspaceAccess | None:
        if account_id != ACCOUNT_ID or workspace_id != WORKSPACE_ID:
            return None
        return WorkspaceAccess(
            workspace_id=WORKSPACE_ID,
            account_id=ACCOUNT_ID,
            workspace_status="active",
            membership_status="active",
        )

    def get_api_key(self, key_id: UUID) -> OpenApiKey | None:
        return self.api_keys.get(key_id)


class MemoryApiKeyWriter:
    def __init__(self, identity: MemoryIdentity) -> None:
        self._identity = identity

    def add(self, api_key: OpenApiKey, *, name: str, last_four: str) -> None:
        assert name
        assert len(last_four) == 4
        self._identity.api_keys[api_key.key_id] = api_key

    def revoke(self, workspace_id: UUID, key_id: UUID, revoked_at: datetime) -> bool:
        return False


class MemoryUnitOfWork:
    def __init__(self, identity: MemoryIdentity) -> None:
        self.api_keys: ApiKeyWriter = MemoryApiKeyWriter(identity)

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
        return None


class MemoryEntitlementAccess:
    def get_open_api_entitlement(self, workspace_id: UUID) -> OpenApiEntitlement | None:
        assert workspace_id == WORKSPACE_ID
        return OpenApiEntitlement(True, True)


class ClosingDatabase:
    def close(self) -> None:
        return None


class StubRegistrationService(RegistrationService):
    def __init__(self) -> None:
        pass

    def register(
        self,
        *,
        login_name: str,
        display_name: str,
        password: str,
        request_id: UUID,
        trace: TraceContext,
    ) -> RegistrationResult:
        assert login_name == "NEW.USER@EXAMPLE.COM"
        assert display_name == "合成新用户"
        assert password == "synthetic-password-456"
        assert request_id
        assert trace.trace_id
        return RegistrationResult(
            account_id=UUID("10000000-0000-4000-8000-000000000023"),
            personal_workspace_id=UUID("20000000-0000-4000-8000-000000000023"),
        )


def identity_client() -> tuple[TestClient, ApiKeyService, MemorySessions]:
    settings = Settings(environment="test")
    passwords = Argon2idPasswordAdapter()
    identity = MemoryIdentity(passwords.hash("synthetic-password-123"))
    sessions = MemorySessions()
    digester = Sha256SecretDigester()
    entitlements = MemoryEntitlementAccess()
    authentication = AuthenticationService(
        identity,
        sessions,
        passwords,
        digester,
        settings.session_ttl_seconds,
        entitlements,
    )
    api_keys = ApiKeyService(identity, MemoryUnitOfWork(identity), digester, entitlements)
    container = ApplicationContainer(
        settings=settings,
        database=cast(PlatformDatabase, ClosingDatabase()),
        errors=ErrorCatalog.load(ROOT / "contracts/errors/catalog.v1.json"),
        resource_registry=load_resource_registry(
            ROOT / "contracts/authorization/resource-registry.v1.json"
        ),
        policy=AllowRegisteredPolicy(),
        role_permissions=cast("RolePermissionService", object()),
        authentication=authentication,
        api_keys=api_keys,
        registration=StubRegistrationService(),
        enterprise_workspaces=cast("EnterpriseWorkspaceService", object()),
        entitlements=cast("EntitlementService", object()),
        organization=cast("OrganizationService", object()),
        roles=cast("RoleService", object()),
        role_cache=cast("ValkeyRoleResolutionCache", sessions),
        secret_cipher=cast(EnvelopeSecretCipher, object()),
        sessions=cast(ValkeySessionStore, sessions),
    )
    return TestClient(create_app(settings, container)), api_keys, sessions


def test_login_cookie_context_csrf_and_logout_flow() -> None:
    client, _, sessions = identity_client()
    workspace_header = {"X-Workspace-ID": str(WORKSPACE_ID)}

    with client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "login_name": "OWNER@EXAMPLE.COM",
                "password": "synthetic-password-123",
            },
        )
        csrf_token = login.json()["csrf_token"]
        cookie = login.headers["set-cookie"]

        assert login.status_code == 200
        assert login.json()["personal_workspace_id"] == str(WORKSPACE_ID)
        assert login.headers["cache-control"] == "no-store"
        assert "ai_platform_session=synthetic-http-session" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=strict" in cookie
        assert "Secure" not in cookie
        assert "synthetic-password-123" not in login.text

        context = client.get(
            "/api/v1/auth/context",
            headers={**workspace_header, "X-Actor-ID": str(UUID(int=999))},
        )
        assert context.status_code == 200
        assert context.json()["actor_id"] == str(ACCOUNT_ID)
        assert context.json()["user_id"] == str(ACCOUNT_ID)
        assert context.json()["workspace_id"] == str(WORKSPACE_ID)
        assert context.json()["authentication_method"] == "browser_session"

        missing_csrf = client.post("/api/v1/auth/logout", headers=workspace_header)
        assert missing_csrf.status_code == 403
        assert missing_csrf.json()["code"] == "CSRF_INVALID"

        cross_workspace = client.get(
            "/api/v1/auth/context",
            headers={"X-Workspace-ID": str(OTHER_WORKSPACE_ID)},
        )
        assert cross_workspace.status_code == 403
        assert cross_workspace.json()["code"] == "POLICY_DENIED"

        logout = client.post(
            "/api/v1/auth/logout",
            headers={**workspace_header, "X-CSRF-Token": csrf_token},
        )
        assert logout.status_code == 200
        assert logout.json() == {"logged_out": True}
        assert sessions.items == {}

        expired = client.get("/api/v1/auth/context", headers=workspace_header)
        assert expired.status_code == 401
    assert expired.json()["code"] == "AUTH_REQUIRED"


def test_registration_returns_account_and_default_personal_workspace() -> None:
    client, _, _ = identity_client()

    with client:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "login_name": "NEW.USER@EXAMPLE.COM",
                "display_name": "合成新用户",
                "password": "synthetic-password-456",
            },
        )

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "account_id": "10000000-0000-4000-8000-000000000023",
        "personal_workspace_id": "20000000-0000-4000-8000-000000000023",
    }
    assert "synthetic-password-456" not in response.text


def test_missing_workspace_and_invalid_login_use_stable_errors() -> None:
    client, _, _ = identity_client()

    with client:
        invalid_login = client.post(
            "/api/v1/auth/login",
            json={"login_name": "unknown@example.com", "password": "synthetic-password-123"},
        )
        missing_workspace = client.get("/api/v1/auth/context")

    assert invalid_login.status_code == 401
    assert invalid_login.json()["code"] == "AUTH_INVALID_CREDENTIALS"
    assert missing_workspace.status_code == 400
    assert missing_workspace.json()["code"] == "WORKSPACE_REQUIRED"


def test_bearer_api_key_builds_scoped_non_user_actor_context() -> None:
    client, api_keys, _ = identity_client()
    browser_context = RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TraceContext.new(),
        authentication_method="browser_session",
    )
    issued = api_keys.issue(
        context=browser_context,
        name="合成 HTTP Key",
        scopes=("knowledge.document.read",),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    with client:
        response = client.get(
            "/api/v1/auth/context",
            headers={
                "Authorization": f"Bearer {issued.plaintext}",
                "X-Workspace-ID": str(WORKSPACE_ID),
                "X-Actor-ID": str(ACCOUNT_ID),
            },
        )

    assert response.status_code == 200
    assert response.json()["actor_id"] == str(issued.actor_id)
    assert response.json()["user_id"] == str(ACCOUNT_ID)
    assert response.json()["credential_scopes"] == ["knowledge.document.read"]
    assert response.json()["authentication_method"] == "open_api_key"
    assert issued.plaintext not in response.text

    with client:
        logout = client.post(
            "/api/v1/auth/logout",
            headers={
                "Authorization": f"Bearer {issued.plaintext}",
                "X-Workspace-ID": str(WORKSPACE_ID),
                "X-CSRF-Token": "not-applicable-to-api-key",
            },
        )
    assert logout.status_code == 401
    assert logout.json()["code"] == "AUTH_REQUIRED"
