from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
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
from ai_platform_api.modules.identity.application.organization import OrganizationService
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.application.roles import RoleService
from ai_platform_api.modules.identity.domain.roles import (
    EffectiveRole,
    EffectiveRoleSet,
    EffectiveRoleSource,
    Role,
    RoleBinding,
)
from ai_platform_api.modules.identity.infrastructure.role_cache import ValkeyRoleResolutionCache
from ai_platform_api.modules.identity.infrastructure.security import EnvelopeSecretCipher
from ai_platform_api.modules.identity.infrastructure.session import ValkeySessionStore
from ai_platform_api.persistence.database import PlatformDatabase
from fastapi.testclient import TestClient

ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 8, 14, tzinfo=UTC)
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000094")
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000094")
MEMBERSHIP_ID = UUID("30000000-0000-4000-8000-000000000094")
DEPARTMENT_ID = UUID("50000000-0000-4000-8000-000000000094")
ROLE_ID = UUID("70000000-0000-4000-8000-000000000094")
BINDING_ID = UUID("80000000-0000-4000-8000-000000000094")


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
        assert session_token == "synthetic-role-session"
        if require_csrf:
            assert csrf_token == "synthetic-role-csrf"
        return RequestContext.trusted(
            actor_id=ACCOUNT_ID,
            user_id=ACCOUNT_ID,
            workspace_id=workspace_id,
            request_id=request_id,
            trace=trace,
            authentication_method="browser_session",
        )


class StubRoleService(RoleService):
    def __init__(self) -> None:
        pass

    def create(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        role_key: str,
        name: str,
    ) -> Role:
        assert context.workspace_id == workspace_id
        assert (role_key, name) == ("department_lead", "合成部门负责人")
        return self._role()

    def list_roles(self, context: RequestContext, *, workspace_id: UUID) -> tuple[Role, ...]:
        assert context.workspace_id == workspace_id
        return (self._role(),)

    def set_status(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        role_id: UUID,
        active: bool,
    ) -> Role:
        assert context.workspace_id == workspace_id
        assert role_id == ROLE_ID and active is False
        role = self._role()
        return Role(
            role.role_id,
            role.workspace_id,
            role.role_key,
            role.name,
            "disabled",
            False,
            role.created_at,
            role.updated_at,
            2,
        )

    def bind(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        role_id: UUID,
        scope_type: Literal["workspace", "department", "member"],
        department_id: UUID | None,
        target_account_id: UUID | None,
    ) -> RoleBinding:
        assert context.workspace_id == workspace_id
        assert role_id == ROLE_ID
        assert (scope_type, department_id, target_account_id) == (
            "department",
            DEPARTMENT_ID,
            None,
        )
        return self._binding()

    def revoke(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        binding_id: UUID,
    ) -> RoleBinding:
        assert context.workspace_id == workspace_id
        assert binding_id == BINDING_ID
        binding = self._binding()
        return RoleBinding(
            binding.binding_id,
            binding.workspace_id,
            binding.role_id,
            binding.scope_type,
            binding.department_id,
            binding.membership_id,
            "revoked",
            binding.created_at,
            NOW,
            2,
        )

    def effective_roles(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
    ) -> EffectiveRoleSet:
        assert context.workspace_id == workspace_id
        assert target_account_id == ACCOUNT_ID
        role = self._role()
        return EffectiveRoleSet(
            WORKSPACE_ID,
            ACCOUNT_ID,
            MEMBERSHIP_ID,
            4,
            (
                EffectiveRole(
                    role.role_id,
                    role.role_key,
                    role.name,
                    (EffectiveRoleSource("department", DEPARTMENT_ID),),
                ),
            ),
        )

    @staticmethod
    def _role() -> Role:
        return Role(
            ROLE_ID,
            WORKSPACE_ID,
            "department_lead",
            "合成部门负责人",
            "active",
            False,
            NOW,
            NOW,
            1,
        )

    @staticmethod
    def _binding() -> RoleBinding:
        return RoleBinding(
            BINDING_ID,
            WORKSPACE_ID,
            ROLE_ID,
            "department",
            DEPARTMENT_ID,
            None,
            "active",
            NOW,
            None,
            1,
        )


def role_client() -> TestClient:
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
        organization=cast("OrganizationService", object()),
        roles=StubRoleService(),
        role_cache=cast("ValkeyRoleResolutionCache", closing),
        secret_cipher=cast("EnvelopeSecretCipher", object()),
        sessions=cast("ValkeySessionStore", closing),
    )
    return TestClient(create_app(settings, container))


def test_role_routes_map_role_binding_and_effective_role_contracts() -> None:
    client = role_client()
    client.cookies.set("ai_platform_session", "synthetic-role-session")
    base = f"/api/v1/workspaces/{WORKSPACE_ID}/roles"
    headers = {
        "X-Workspace-ID": str(WORKSPACE_ID),
        "X-CSRF-Token": "synthetic-role-csrf",
    }

    with client:
        created = client.post(
            base,
            headers=headers,
            json={"role_key": "department_lead", "name": "合成部门负责人"},
        )
        listed = client.get(base, headers={"X-Workspace-ID": str(WORKSPACE_ID)})
        bound = client.post(
            f"{base}/bindings",
            headers=headers,
            json={
                "role_id": str(ROLE_ID),
                "scope_type": "department",
                "department_id": str(DEPARTMENT_ID),
            },
        )
        effective = client.get(
            f"{base}/effective/{ACCOUNT_ID}",
            headers={"X-Workspace-ID": str(WORKSPACE_ID)},
        )
        revoked = client.post(f"{base}/bindings/{BINDING_ID}/revoke", headers=headers)
        disabled = client.post(
            f"{base}/{ROLE_ID}/status",
            headers=headers,
            json={"active": False},
        )

    assert created.status_code == 201
    assert listed.json()["items"] == [created.json()]
    assert bound.status_code == 201
    assert effective.json()["roles"][0]["sources"] == [
        {"scope_type": "department", "scope_id": str(DEPARTMENT_ID)}
    ]
    assert revoked.json()["status"] == "revoked"
    assert disabled.json()["status"] == "disabled"
