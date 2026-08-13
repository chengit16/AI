from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast
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
from ai_platform_api.modules.identity.application.enterprise import (
    EnterpriseWorkspaceService,
    WorkspaceMemberSummary,
    WorkspaceSummary,
)
from ai_platform_api.modules.identity.application.entitlements import EntitlementService
from ai_platform_api.modules.identity.application.organization import OrganizationService
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.application.roles import RoleService
from ai_platform_api.modules.identity.domain.enterprise import (
    WorkspaceInvitation,
    WorkspaceMembership,
)
from ai_platform_api.modules.identity.infrastructure.role_cache import ValkeyRoleResolutionCache
from ai_platform_api.modules.identity.infrastructure.security import EnvelopeSecretCipher
from ai_platform_api.modules.identity.infrastructure.session import ValkeySessionStore
from ai_platform_api.persistence.database import PlatformDatabase
from fastapi.testclient import TestClient

from test_support.authorization import AllowRegisteredPolicy

ROOT = Path(__file__).parents[3]
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000024")
API_KEY_ACTOR_ID = UUID("10000000-0000-4000-8000-000000000025")
PERSONAL_WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000024")
ENTERPRISE_WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000025")
INVITATION_ID = UUID("40000000-0000-4000-8000-000000000024")
MEMBERSHIP_ID = UUID("30000000-0000-4000-8000-000000000024")


class ClosingDependency:
    def close(self) -> None:
        return None


class StubAuthenticationService(AuthenticationService):
    """保留真实 HTTP 凭证分支，只隔离身份数据和密码学实现。"""

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
        assert session_token == "synthetic-enterprise-session"
        if require_csrf:
            assert csrf_token == "synthetic-enterprise-csrf"
        return RequestContext.trusted(
            actor_id=ACCOUNT_ID,
            user_id=ACCOUNT_ID,
            workspace_id=workspace_id,
            request_id=request_id,
            trace=trace,
            authentication_method="browser_session",
        )

    def api_key_context(
        self,
        *,
        credential: str,
        workspace_id: UUID,
        request_id: UUID,
        trace: TraceContext,
        now: datetime | None = None,
    ) -> RequestContext:
        assert credential == "synthetic-enterprise-api-key"
        assert now is None
        return RequestContext.trusted(
            actor_id=API_KEY_ACTOR_ID,
            user_id=ACCOUNT_ID,
            workspace_id=workspace_id,
            request_id=request_id,
            trace=trace,
            authentication_method="open_api_key",
            credential_scopes=frozenset({"workspace.member.manage"}),
        )


class StubEnterpriseWorkspaceService(EnterpriseWorkspaceService):
    """隔离数据库，只验证企业空间 Router 的协议映射和治理入口。"""

    def __init__(self) -> None:
        pass

    def create(self, context: RequestContext, *, name: str) -> WorkspaceSummary:
        self._browser_account(context)
        assert name == "合成企业空间"
        return self._summary()

    def invite(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        login_name: str,
    ) -> WorkspaceInvitation:
        self._browser_account(context)
        self._require_current_workspace(context, workspace_id)
        assert login_name == "member@example.com"
        now = datetime.now(UTC)
        return WorkspaceInvitation(
            INVITATION_ID,
            workspace_id,
            ACCOUNT_ID,
            ACCOUNT_ID,
            "pending",
            now,
            now + timedelta(days=7),
        )

    def accept_invitation(
        self,
        context: RequestContext,
        *,
        invitation_id: UUID,
    ) -> WorkspaceSummary:
        self._browser_account(context)
        assert invitation_id == INVITATION_ID
        return self._summary()

    def leave(self, context: RequestContext, *, workspace_id: UUID) -> WorkspaceMembership:
        self._browser_account(context)
        self._require_current_workspace(context, workspace_id)
        return self._membership("left")

    def disable_member(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
    ) -> WorkspaceMembership:
        self._browser_account(context)
        self._require_current_workspace(context, workspace_id)
        assert target_account_id == ACCOUNT_ID
        return self._membership("disabled")

    def list_workspaces(self, context: RequestContext) -> tuple[WorkspaceSummary, ...]:
        self._browser_account(context)
        return (self._summary(),)

    def list_members(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
    ) -> tuple[WorkspaceMemberSummary, ...]:
        self._browser_account(context)
        self._require_current_workspace(context, workspace_id)
        return (WorkspaceMemberSummary(ACCOUNT_ID, "合成成员", "member", "active"),)

    def switch(self, context: RequestContext, *, workspace_id: UUID) -> WorkspaceSummary:
        self._browser_account(context)
        assert workspace_id == ENTERPRISE_WORKSPACE_ID
        return self._summary()

    @staticmethod
    def _summary() -> WorkspaceSummary:
        return WorkspaceSummary(
            ENTERPRISE_WORKSPACE_ID,
            "enterprise",
            "合成企业空间",
            "active",
            "owner",
            "active",
        )

    @staticmethod
    def _membership(
        status: Literal["active", "disabled", "left"],
    ) -> WorkspaceMembership:
        now = datetime.now(UTC)
        return WorkspaceMembership(
            MEMBERSHIP_ID,
            ENTERPRISE_WORKSPACE_ID,
            ACCOUNT_ID,
            "member",
            status,
            now,
            now,
            2,
        )


def enterprise_client() -> TestClient:
    settings = Settings(environment="test")
    closing = ClosingDependency()
    container = ApplicationContainer(
        settings=settings,
        database=cast(PlatformDatabase, closing),
        errors=ErrorCatalog.load(ROOT / "contracts/errors/catalog.v1.json"),
        resource_registry=load_resource_registry(
            ROOT / "contracts/authorization/resource-registry.v1.json"
        ),
        policy=AllowRegisteredPolicy(),
        role_permissions=cast("RolePermissionService", object()),
        authentication=StubAuthenticationService(),
        api_keys=cast("ApiKeyService", object()),
        registration=cast("RegistrationService", object()),
        enterprise_workspaces=StubEnterpriseWorkspaceService(),
        entitlements=cast("EntitlementService", object()),
        organization=cast("OrganizationService", object()),
        roles=cast("RoleService", object()),
        role_cache=cast("ValkeyRoleResolutionCache", closing),
        secret_cipher=cast("EnvelopeSecretCipher", object()),
        sessions=cast("ValkeySessionStore", closing),
    )
    return TestClient(create_app(settings, container))


def test_enterprise_workspace_routes_preserve_contract_and_browser_governance() -> None:
    client = enterprise_client()
    client.cookies.set("ai_platform_session", "synthetic-enterprise-session")
    personal_headers = {
        "X-Workspace-ID": str(PERSONAL_WORKSPACE_ID),
        "X-CSRF-Token": "synthetic-enterprise-csrf",
    }
    enterprise_headers = {
        "X-Workspace-ID": str(ENTERPRISE_WORKSPACE_ID),
        "X-CSRF-Token": "synthetic-enterprise-csrf",
    }

    with client:
        created = client.post(
            "/api/v1/workspaces/enterprise",
            headers=personal_headers,
            json={"name": "合成企业空间"},
        )
        listed = client.get(
            "/api/v1/workspaces",
            headers={"X-Workspace-ID": str(PERSONAL_WORKSPACE_ID)},
        )
        invited = client.post(
            f"/api/v1/workspaces/{ENTERPRISE_WORKSPACE_ID}/invitations",
            headers=enterprise_headers,
            json={"login_name": "member@example.com"},
        )
        accepted = client.post(
            f"/api/v1/workspaces/invitations/{INVITATION_ID}/accept",
            headers=personal_headers,
        )
        switched = client.post(
            f"/api/v1/workspaces/{ENTERPRISE_WORKSPACE_ID}/switch",
            headers=personal_headers,
        )
        members = client.get(
            f"/api/v1/workspaces/{ENTERPRISE_WORKSPACE_ID}/members",
            headers={"X-Workspace-ID": str(ENTERPRISE_WORKSPACE_ID)},
        )
        disabled = client.post(
            f"/api/v1/workspaces/{ENTERPRISE_WORKSPACE_ID}/members/{ACCOUNT_ID}/disable",
            headers=enterprise_headers,
        )
        left = client.post(
            f"/api/v1/workspaces/{ENTERPRISE_WORKSPACE_ID}/leave",
            headers=enterprise_headers,
        )

    assert created.status_code == 201
    assert listed.json()["items"] == [created.json()]
    assert invited.status_code == 201
    assert invited.json()["invitation_id"] == str(INVITATION_ID)
    assert accepted.json() == created.json()
    assert switched.json() == created.json()
    assert members.json()["items"][0]["display_name"] == "合成成员"
    assert disabled.json()["status"] == "disabled"
    assert left.json()["status"] == "left"

    with client:
        denied = client.post(
            "/api/v1/workspaces/enterprise",
            headers={
                "Authorization": "Bearer synthetic-enterprise-api-key",
                "X-Workspace-ID": str(PERSONAL_WORKSPACE_ID),
            },
            json={"name": "合成企业空间"},
        )
    assert denied.status_code == 403
    assert denied.json()["code"] == "POLICY_DENIED"
