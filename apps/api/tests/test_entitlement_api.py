from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from ai_platform_api.app.dependencies import ApplicationContainer
from ai_platform_api.app.errors import ErrorCatalog
from ai_platform_api.app.factory import create_app
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.config import Settings
from ai_platform_api.modules.identity.application.authentication import (
    ApiKeyService,
    AuthenticationService,
)
from ai_platform_api.modules.identity.application.enterprise import EnterpriseWorkspaceService
from ai_platform_api.modules.identity.application.entitlements import EntitlementService
from ai_platform_api.modules.identity.application.organization import OrganizationService
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.application.roles import RoleService
from ai_platform_api.modules.identity.domain.entitlements import (
    EntitlementSnapshot,
    QuotaSnapshot,
)
from ai_platform_api.modules.identity.infrastructure.role_cache import ValkeyRoleResolutionCache
from ai_platform_api.modules.identity.infrastructure.security import EnvelopeSecretCipher
from ai_platform_api.modules.identity.infrastructure.session import ValkeySessionStore
from ai_platform_api.persistence.database import PlatformDatabase
from fastapi.testclient import TestClient

ROOT = Path(__file__).parents[3]
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000031")
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000031")


class ClosingDependency:
    def close(self) -> None:
        return None


class StubAuthenticationService(AuthenticationService):
    def __init__(self) -> None:
        pass

    def browser_context(
        self,
        *,
        session_token: str | None,
        csrf_token: str | None,
        require_csrf: bool,
        workspace_id: UUID,
        request_id: UUID,
        trace: TraceContext,
    ) -> RequestContext:
        assert session_token == "synthetic-entitlement-session"
        if require_csrf:
            assert csrf_token == "synthetic-entitlement-csrf"
        return RequestContext.trusted(
            actor_id=ACCOUNT_ID,
            user_id=ACCOUNT_ID,
            workspace_id=workspace_id,
            request_id=request_id,
            trace=trace,
            authentication_method="browser_session",
        )


class StubEntitlementService(EntitlementService):
    def __init__(self) -> None:
        pass

    def get_snapshot(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        now: datetime | None = None,
    ) -> EntitlementSnapshot:
        assert context.workspace_id == workspace_id
        assert now is None
        return self._fixture(False, 1)

    def set_open_api_enabled(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        enabled: bool,
    ) -> EntitlementSnapshot:
        assert context.workspace_id == workspace_id
        assert enabled is True
        return self._fixture(True, 2)

    @staticmethod
    def _fixture(enabled: bool, version: int) -> EntitlementSnapshot:
        return EntitlementSnapshot(
            WORKSPACE_ID,
            "active",
            "enterprise_simulated",
            version,
            True,
            enabled,
            False,
            (
                QuotaSnapshot("members", "lifetime", 2, 100),
                QuotaSnapshot("storage_bytes", "lifetime", 1024, 100 * 1024**3),
                QuotaSnapshot("knowledge_bases", "lifetime", 1, 50),
                QuotaSnapshot("published_agents", "lifetime", 0, 20),
                QuotaSnapshot("questions_monthly", "2026-08", 9, 20_000),
            ),
        )


def entitlement_client() -> TestClient:
    settings = Settings(environment="test")
    closing = ClosingDependency()
    container = ApplicationContainer(
        settings=settings,
        database=cast(PlatformDatabase, closing),
        errors=ErrorCatalog.load(ROOT / "contracts/errors/catalog.v1.json"),
        authentication=StubAuthenticationService(),
        api_keys=cast("ApiKeyService", object()),
        registration=cast("RegistrationService", object()),
        enterprise_workspaces=cast("EnterpriseWorkspaceService", object()),
        entitlements=StubEntitlementService(),
        organization=cast("OrganizationService", object()),
        roles=cast("RoleService", object()),
        role_cache=cast("ValkeyRoleResolutionCache", closing),
        secret_cipher=cast("EnvelopeSecretCipher", object()),
        sessions=cast("ValkeySessionStore", closing),
    )
    return TestClient(create_app(settings, container))


def test_entitlement_routes_map_quota_and_feature_contracts() -> None:
    client = entitlement_client()
    client.cookies.set("ai_platform_session", "synthetic-entitlement-session")
    base = f"/api/v1/workspaces/{WORKSPACE_ID}/entitlements"

    with client:
        loaded = client.get(base, headers={"X-Workspace-ID": str(WORKSPACE_ID)})
        enabled = client.post(
            f"{base}/features/open-api",
            headers={
                "X-Workspace-ID": str(WORKSPACE_ID),
                "X-CSRF-Token": "synthetic-entitlement-csrf",
            },
            json={"enabled": True},
        )

    assert loaded.status_code == 200
    assert loaded.json()["plan_code"] == "enterprise_simulated"
    assert loaded.json()["quotas"][0] == {
        "metric": "members",
        "period_key": "lifetime",
        "used_value": 2,
        "limit_value": 100,
        "remaining_value": 98,
    }
    assert enabled.status_code == 200
    assert enabled.json()["open_api_enabled"] is True
    assert enabled.json()["entitlement_version"] == 2
