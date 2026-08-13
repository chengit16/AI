from datetime import datetime
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
from ai_platform_api.modules.identity.domain.organization import (
    DepartmentSummary,
    OrganizationAssignment,
    PositionSummary,
)
from ai_platform_api.modules.identity.infrastructure.role_cache import ValkeyRoleResolutionCache
from ai_platform_api.modules.identity.infrastructure.security import EnvelopeSecretCipher
from ai_platform_api.modules.identity.infrastructure.session import ValkeySessionStore
from ai_platform_api.persistence.database import PlatformDatabase
from fastapi.testclient import TestClient

ROOT = Path(__file__).parents[3]
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000084")
API_KEY_ACTOR_ID = UUID("10000000-0000-4000-8000-000000000085")
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000084")
ROOT_DEPARTMENT_ID = UUID("50000000-0000-4000-8000-000000000084")
CHILD_DEPARTMENT_ID = UUID("50000000-0000-4000-8000-000000000085")
POSITION_ID = UUID("60000000-0000-4000-8000-000000000084")


class ClosingDependency:
    def close(self) -> None:
        return None


class StubAuthenticationService(AuthenticationService):
    """保留浏览器与 API Key 协议分支，只隔离身份存储。"""

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
        assert session_token == "synthetic-organization-session"
        if require_csrf:
            assert csrf_token == "synthetic-organization-csrf"
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
        assert credential == "synthetic-organization-api-key"
        assert now is None
        return RequestContext.trusted(
            actor_id=API_KEY_ACTOR_ID,
            user_id=ACCOUNT_ID,
            workspace_id=workspace_id,
            request_id=request_id,
            trace=trace,
            authentication_method="open_api_key",
            credential_scopes=frozenset({"organization.department.manage"}),
        )


class StubOrganizationService(OrganizationService):
    """隔离数据库，只验证组织 Router 映射和治理入口。"""

    def __init__(self) -> None:
        pass

    def create_department(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        name: str,
        parent_department_id: UUID | None,
    ) -> DepartmentSummary:
        self._governance_account(context, workspace_id)
        assert name in {"合成总部", "合成研发部"}
        if parent_department_id is None:
            return self._department_summary_fixture(ROOT_DEPARTMENT_ID, None, "合成总部", 0, 1)
        assert parent_department_id == ROOT_DEPARTMENT_ID
        return self._department_summary_fixture(
            CHILD_DEPARTMENT_ID, ROOT_DEPARTMENT_ID, "合成研发部", 1, 1
        )

    def move_department(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        department_id: UUID,
        parent_department_id: UUID | None,
    ) -> DepartmentSummary:
        self._governance_account(context, workspace_id)
        assert department_id == CHILD_DEPARTMENT_ID
        assert parent_department_id is None
        return self._department_summary_fixture(CHILD_DEPARTMENT_ID, None, "合成研发部", 0, 2)

    def set_department_status(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        department_id: UUID,
        active: bool,
    ) -> DepartmentSummary:
        self._governance_account(context, workspace_id)
        assert department_id == CHILD_DEPARTMENT_ID
        assert active is False
        return DepartmentSummary(
            CHILD_DEPARTMENT_ID,
            ROOT_DEPARTMENT_ID,
            "合成研发部",
            "disabled",
            False,
            1,
            2,
        )

    def list_departments(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
    ) -> tuple[DepartmentSummary, ...]:
        self._governance_account(context, workspace_id)
        return (
            self._department_summary_fixture(ROOT_DEPARTMENT_ID, None, "合成总部", 0, 1),
            self._department_summary_fixture(
                CHILD_DEPARTMENT_ID, ROOT_DEPARTMENT_ID, "合成研发部", 1, 1
            ),
        )

    def create_position(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        department_id: UUID,
        name: str,
    ) -> PositionSummary:
        self._governance_account(context, workspace_id)
        assert department_id == CHILD_DEPARTMENT_ID
        assert name == "合成研发工程师"
        return self._position_summary_fixture("active", True, 1)

    def set_position_status(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        position_id: UUID,
        active: bool,
    ) -> PositionSummary:
        self._governance_account(context, workspace_id)
        assert position_id == POSITION_ID
        assert active is False
        return self._position_summary_fixture("disabled", False, 2)

    def list_positions(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
    ) -> tuple[PositionSummary, ...]:
        self._governance_account(context, workspace_id)
        return (self._position_summary_fixture("active", True, 1),)

    def assign_member(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
        department_ids: tuple[UUID, ...],
        primary_department_id: UUID | None,
        position_ids: tuple[UUID, ...],
    ) -> OrganizationAssignment:
        self._governance_account(context, workspace_id)
        assert target_account_id == ACCOUNT_ID
        assert department_ids == (ROOT_DEPARTMENT_ID, CHILD_DEPARTMENT_ID)
        assert primary_department_id == CHILD_DEPARTMENT_ID
        assert position_ids == (POSITION_ID,)
        return self._assignment()

    def get_assignment(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
    ) -> OrganizationAssignment:
        self._governance_account(context, workspace_id)
        assert target_account_id == ACCOUNT_ID
        return self._assignment()

    @staticmethod
    def _department_summary_fixture(
        department_id: UUID,
        parent_id: UUID | None,
        name: str,
        depth: int,
        version: int,
    ) -> DepartmentSummary:
        return DepartmentSummary(
            department_id,
            parent_id,
            name,
            "active",
            True,
            depth,
            version,
        )

    @staticmethod
    def _position_summary_fixture(
        status: Literal["active", "disabled"],
        effective_active: bool,
        version: int,
    ) -> PositionSummary:
        return PositionSummary(
            POSITION_ID,
            CHILD_DEPARTMENT_ID,
            "合成研发工程师",
            status,
            effective_active,
            version,
        )

    @staticmethod
    def _assignment() -> OrganizationAssignment:
        return OrganizationAssignment(
            ACCOUNT_ID,
            (ROOT_DEPARTMENT_ID, CHILD_DEPARTMENT_ID),
            CHILD_DEPARTMENT_ID,
            (POSITION_ID,),
            2,
        )


def organization_client() -> TestClient:
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
        organization=StubOrganizationService(),
        roles=cast("RoleService", object()),
        role_cache=cast("ValkeyRoleResolutionCache", closing),
        secret_cipher=cast("EnvelopeSecretCipher", object()),
        sessions=cast("ValkeySessionStore", closing),
    )
    return TestClient(create_app(settings, container))


def test_organization_routes_map_contract_and_reject_api_key_governance() -> None:
    client = organization_client()
    client.cookies.set("ai_platform_session", "synthetic-organization-session")
    base = f"/api/v1/workspaces/{WORKSPACE_ID}/organization"
    headers = {
        "X-Workspace-ID": str(WORKSPACE_ID),
        "X-CSRF-Token": "synthetic-organization-csrf",
    }

    with client:
        root = client.post(f"{base}/departments", headers=headers, json={"name": "合成总部"})
        child = client.post(
            f"{base}/departments",
            headers=headers,
            json={"name": "合成研发部", "parent_department_id": str(ROOT_DEPARTMENT_ID)},
        )
        departments = client.get(
            f"{base}/departments", headers={"X-Workspace-ID": str(WORKSPACE_ID)}
        )
        moved = client.post(
            f"{base}/departments/{CHILD_DEPARTMENT_ID}/move",
            headers=headers,
            json={"parent_department_id": None},
        )
        disabled_department = client.post(
            f"{base}/departments/{CHILD_DEPARTMENT_ID}/status",
            headers=headers,
            json={"active": False},
        )
        position = client.post(
            f"{base}/positions",
            headers=headers,
            json={
                "department_id": str(CHILD_DEPARTMENT_ID),
                "name": "合成研发工程师",
            },
        )
        positions = client.get(f"{base}/positions", headers={"X-Workspace-ID": str(WORKSPACE_ID)})
        disabled_position = client.post(
            f"{base}/positions/{POSITION_ID}/status",
            headers=headers,
            json={"active": False},
        )
        assigned = client.put(
            f"{base}/members/{ACCOUNT_ID}",
            headers=headers,
            json={
                "department_ids": [str(ROOT_DEPARTMENT_ID), str(CHILD_DEPARTMENT_ID)],
                "primary_department_id": str(CHILD_DEPARTMENT_ID),
                "position_ids": [str(POSITION_ID)],
            },
        )
        loaded = client.get(
            f"{base}/members/{ACCOUNT_ID}",
            headers={"X-Workspace-ID": str(WORKSPACE_ID)},
        )

    assert root.status_code == 201
    assert child.json()["depth"] == 1
    assert len(departments.json()["items"]) == 2
    assert moved.json()["parent_department_id"] is None
    assert disabled_department.json()["effective_active"] is False
    assert position.status_code == 201
    assert len(positions.json()["items"]) == 1
    assert disabled_position.json()["status"] == "disabled"
    assert assigned.json() == loaded.json()
    assert assigned.json()["membership_version"] == 2

    with client:
        denied = client.post(
            f"{base}/departments",
            headers={
                "Authorization": "Bearer synthetic-organization-api-key",
                "X-Workspace-ID": str(WORKSPACE_ID),
            },
            json={"name": "合成总部"},
        )
    assert denied.status_code == 403
    assert denied.json()["code"] == "POLICY_DENIED"
