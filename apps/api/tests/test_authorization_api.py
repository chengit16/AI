from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from ai_platform_api.app.dependencies import ApplicationContainer
from ai_platform_api.app.errors import ErrorCatalog
from ai_platform_api.app.factory import create_app
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.config import Settings
from ai_platform_api.modules.authorization.application.grants import RolePermissionService
from ai_platform_api.modules.authorization.application.menus import MenuConfigurationService
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.authorization.domain.grants import RolePermissionGrant
from ai_platform_api.modules.authorization.domain.menus import (
    MenuConfiguration,
    RoleMenuVisibility,
    WorkspaceMenuOverride,
)
from ai_platform_api.modules.authorization.domain.policy import (
    DataScopeType,
    PolicyDecision,
    PolicyDecisionPoint,
    PolicyRequest,
    ResourceScope,
)
from ai_platform_api.modules.identity.application.authentication import (
    ApiKeyService,
    AuthenticationService,
)
from ai_platform_api.modules.identity.application.enterprise import EnterpriseWorkspaceService
from ai_platform_api.modules.identity.application.entitlements import EntitlementService
from ai_platform_api.modules.identity.application.organization import OrganizationService
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.application.roles import RoleService
from ai_platform_api.modules.identity.infrastructure.role_cache import ValkeyRoleResolutionCache
from ai_platform_api.modules.identity.infrastructure.security import EnvelopeSecretCipher
from ai_platform_api.modules.identity.infrastructure.session import ValkeySessionStore
from ai_platform_api.persistence.database import PlatformDatabase
from fastapi.testclient import TestClient

from test_support.authorization import AllowRegisteredPolicy

ROOT = Path(__file__).parents[3]
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000098")
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000098")
ROLE_ID = UUID("70000000-0000-4000-8000-000000000098")
DEPARTMENT_ID = UUID("50000000-0000-4000-8000-000000000098")
DIRECTORY_ID = UUID("82000000-0000-4000-8000-000000000001")
OVERVIEW_ID = UUID("82000000-0000-4000-8000-000000000002")


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
        assert session_token == "synthetic-authorization-session"
        if require_csrf:
            assert csrf_token == "synthetic-authorization-csrf"
        return RequestContext.trusted(
            actor_id=ACCOUNT_ID,
            user_id=ACCOUNT_ID,
            workspace_id=workspace_id,
            request_id=request_id,
            trace=trace,
            authentication_method="browser_session",
        )


class StubRolePermissionService(RolePermissionService):
    def __init__(self) -> None:
        pass

    def list(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        role_id: UUID,
    ) -> tuple[RolePermissionGrant, ...]:
        assert context.workspace_id == workspace_id and role_id == ROLE_ID
        return self._grants()

    def replace(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        role_id: UUID,
        entries: tuple[
            tuple[
                str,
                DataScopeType,
                frozenset[UUID],
                frozenset[UUID],
                SecurityLevel,
                frozenset[str],
            ],
            ...,
        ],
    ) -> tuple[RolePermissionGrant, ...]:
        assert context.workspace_id == workspace_id and role_id == ROLE_ID
        assert entries[0][1:] == (
            "department_tree",
            frozenset({DEPARTMENT_ID}),
            frozenset(),
            "RESTRICTED",
            frozenset(),
        )
        return self._grants()

    @staticmethod
    def _grants() -> tuple[RolePermissionGrant, ...]:
        return (
            RolePermissionGrant(
                WORKSPACE_ID,
                ROLE_ID,
                "organization.department.read",
                "department_tree",
                frozenset({DEPARTMENT_ID}),
            ),
        )


class StubMenuConfigurationService(MenuConfigurationService):
    def __init__(self) -> None:
        pass

    def get_workspace(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
    ) -> MenuConfiguration:
        assert context.workspace_id == workspace_id
        return self._configuration()

    def replace_workspace(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        entries: tuple[tuple[UUID, UUID | None, str, str | None, int, bool], ...],
    ) -> MenuConfiguration:
        assert context.workspace_id == workspace_id
        assert entries[0][:2] == (OVERVIEW_ID, DIRECTORY_ID)
        return self._configuration()

    def get_role(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        role_id: UUID,
    ) -> tuple[RoleMenuVisibility, ...]:
        assert context.workspace_id == workspace_id and role_id == ROLE_ID
        return (RoleMenuVisibility(workspace_id, role_id, OVERVIEW_ID, False),)

    def replace_role(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        role_id: UUID,
        entries: tuple[tuple[UUID, bool], ...],
    ) -> tuple[RoleMenuVisibility, ...]:
        assert entries == ((OVERVIEW_ID, False),)
        return self.get_role(context, workspace_id=workspace_id, role_id=role_id)

    @staticmethod
    def _configuration() -> MenuConfiguration:
        return MenuConfiguration(
            WORKSPACE_ID,
            2,
            (
                WorkspaceMenuOverride(
                    WORKSPACE_ID,
                    OVERVIEW_ID,
                    DIRECTORY_ID,
                    "合成工作台",
                    "panel-top",
                    10,
                    True,
                ),
            ),
        )


class DenyPolicy:
    def decide(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            uuid4(),
            "deny",
            request.permission_code,
            WORKSPACE_ID,
            ResourceScope(),
            frozenset(),
            1,
            0,
            "synthetic_deny",
        )


def authorization_client(policy: PolicyDecisionPoint) -> TestClient:
    settings = Settings(environment="test")
    closing = ClosingDependency()
    container = ApplicationContainer(
        settings=settings,
        database=cast(PlatformDatabase, closing),
        errors=ErrorCatalog.load(ROOT / "contracts/errors/catalog.v1.json"),
        resource_registry=load_resource_registry(
            ROOT / "contracts/authorization/resource-registry.v1.json"
        ),
        policy=policy,
        role_permissions=StubRolePermissionService(),
        menu_configuration=StubMenuConfigurationService(),
        authentication=StubAuthenticationService(),
        api_keys=cast("ApiKeyService", object()),
        registration=cast("RegistrationService", object()),
        enterprise_workspaces=cast("EnterpriseWorkspaceService", object()),
        entitlements=cast("EntitlementService", object()),
        organization=cast("OrganizationService", object()),
        roles=cast("RoleService", object()),
        role_cache=cast("ValkeyRoleResolutionCache", closing),
        secret_cipher=cast("EnvelopeSecretCipher", object()),
        sessions=cast("ValkeySessionStore", closing),
    )
    client = TestClient(create_app(settings, container))
    client.cookies.set("ai_platform_session", "synthetic-authorization-session")
    return client


def test_direct_api_access_is_denied_before_service_execution() -> None:
    client = authorization_client(cast("PolicyDecisionPoint", DenyPolicy()))
    with client:
        response = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/roles/{ROLE_ID}/permissions",
            headers={"X-Workspace-ID": str(WORKSPACE_ID)},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "POLICY_DENIED"


def test_role_permission_routes_share_registered_permission_codes() -> None:
    client = authorization_client(AllowRegisteredPolicy())
    headers = {
        "X-Workspace-ID": str(WORKSPACE_ID),
        "X-CSRF-Token": "synthetic-authorization-csrf",
    }
    path = f"/api/v1/workspaces/{WORKSPACE_ID}/roles/{ROLE_ID}/permissions"
    with client:
        listed = client.get(path, headers={"X-Workspace-ID": str(WORKSPACE_ID)})
        replaced = client.put(
            path,
            headers=headers,
            json={
                "items": [
                    {
                        "permission_code": "organization.department.read",
                        "scope_type": "department_tree",
                        "department_ids": [str(DEPARTMENT_ID)],
                        "resource_ids": [],
                    }
                ]
            },
        )

    assert listed.status_code == 200
    assert replaced.status_code == 200
    assert listed.json() == replaced.json()


def test_menu_routes_share_registered_permission_codes() -> None:
    client = authorization_client(AllowRegisteredPolicy())
    headers = {
        "X-Workspace-ID": str(WORKSPACE_ID),
        "X-CSRF-Token": "synthetic-authorization-csrf",
    }
    menu_path = f"/api/v1/workspaces/{WORKSPACE_ID}/menus"
    role_path = f"/api/v1/workspaces/{WORKSPACE_ID}/roles/{ROLE_ID}/menus"
    with client:
        menu_get = client.get(menu_path, headers={"X-Workspace-ID": str(WORKSPACE_ID)})
        menu_put = client.put(
            menu_path,
            headers=headers,
            json={
                "items": [
                    {
                        "menu_id": str(OVERVIEW_ID),
                        "parent_menu_id": str(DIRECTORY_ID),
                        "name": "合成工作台",
                        "icon_key": "panel-top",
                        "sort_order": 10,
                        "visible": True,
                    }
                ]
            },
        )
        role_get = client.get(role_path, headers={"X-Workspace-ID": str(WORKSPACE_ID)})
        role_put = client.put(
            role_path,
            headers=headers,
            json={"items": [{"menu_id": str(OVERVIEW_ID), "visible": False}]},
        )

    assert menu_get.status_code == menu_put.status_code == 200
    assert role_get.status_code == role_put.status_code == 200
    assert menu_get.json() == menu_put.json()
    assert role_get.json() == role_put.json()


def test_hidden_role_menu_does_not_bypass_backend_policy() -> None:
    client = authorization_client(cast("PolicyDecisionPoint", DenyPolicy()))
    with client:
        response = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/roles/{ROLE_ID}/menus",
            headers={"X-Workspace-ID": str(WORKSPACE_ID)},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "POLICY_DENIED"
