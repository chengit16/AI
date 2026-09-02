"""验证角色授权、菜单配置和菜单发布 HTTP 安全边界。"""

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from ai_platform_api.app.dependencies import ApplicationContainer
from ai_platform_api.app.errors import ErrorCatalog
from ai_platform_api.app.factory import create_app
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.config import Settings
from ai_platform_api.modules.authorization.application.grants import (
    PermissionCatalogGroup,
    PermissionCatalogItem,
    PermissionFieldCatalogItem,
    RoleAffectedMember,
    RoleBindingSummary,
    RoleGovernanceItem,
    RoleGovernanceSnapshot,
    RolePermissionService,
    RolePermissionSnapshot,
)
from ai_platform_api.modules.authorization.application.menu_releases import MenuReleaseService
from ai_platform_api.modules.authorization.application.menus import MenuConfigurationService
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.authorization.domain.grants import RolePermissionGrant
from ai_platform_api.modules.authorization.domain.menus import (
    MenuConfiguration,
    MenuRelease,
    MenuReleaseSnapshot,
    MenuSnapshotItem,
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
from ai_platform_api.modules.authorization.domain.resources import ApiResource
from ai_platform_api.modules.identity.api.dependencies import _resource_reference
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
from starlette.requests import Request
from starlette.types import Scope

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
    ) -> RolePermissionSnapshot:
        assert context.workspace_id == workspace_id and role_id == ROLE_ID
        return RolePermissionSnapshot(7, self._grants())

    def get_governance(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
    ) -> RoleGovernanceSnapshot:
        assert context.workspace_id == workspace_id
        source = RoleBindingSummary("workspace", WORKSPACE_ID, "当前工作空间")
        return RoleGovernanceSnapshot(
            role_version=7,
            roles=(
                RoleGovernanceItem(
                    role_id=ROLE_ID,
                    role_key="synthetic_reviewer",
                    name="合成审核员",
                    status="active",
                    system_managed=False,
                    editable=True,
                    grants=self._grants(),
                    bindings=(source,),
                    affected_members=(
                        RoleAffectedMember(
                            account_id=ACCOUNT_ID,
                            display_name="合成所有者",
                            membership_type="owner",
                            sources=(source,),
                        ),
                    ),
                ),
            ),
            permission_groups=(
                PermissionCatalogGroup(
                    domain="operations",
                    items=(
                        PermissionCatalogItem(
                            permission_code="operations.records.read",
                            resource_type="operations_record",
                            action="read",
                            allowed_scope_types=(
                                "workspace",
                                "department_tree",
                                "self",
                                "resource",
                            ),
                            fields=(PermissionFieldCatalogItem("actor_id", "CONFIDENTIAL"),),
                        ),
                    ),
                ),
            ),
        )

    def replace(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        role_id: UUID,
        expected_role_version: int,
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
    ) -> RolePermissionSnapshot:
        assert context.workspace_id == workspace_id and role_id == ROLE_ID
        assert expected_role_version == 7
        assert entries[0][1:] == (
            "department_tree",
            frozenset({DEPARTMENT_ID}),
            frozenset(),
            "RESTRICTED",
            frozenset(),
        )
        return RolePermissionSnapshot(8, self._grants())

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


class StubMenuReleaseService(MenuReleaseService):
    def __init__(self) -> None:
        pass

    def create_draft(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
    ) -> MenuRelease:
        assert context.workspace_id == workspace_id
        return self._release("draft")

    def validate(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        release_id: UUID,
    ) -> MenuRelease:
        assert context.workspace_id == workspace_id and release_id == self._release_id()
        return self._release("validated")

    def decide(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        release_id: UUID,
        approved: bool,
        reason: str | None,
    ) -> MenuRelease:
        assert context.workspace_id == workspace_id and release_id == self._release_id()
        assert approved is True and reason is None
        return self._release("approved")

    def publish(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        release_id: UUID,
    ) -> MenuRelease:
        assert context.workspace_id == workspace_id and release_id == self._release_id()
        return self._release("published")

    def rollback(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        source_release_id: UUID,
    ) -> MenuRelease:
        assert context.workspace_id == workspace_id and source_release_id == self._release_id()
        return self._release("published", release_kind="rollback")

    def list(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
    ) -> tuple[MenuRelease, ...]:
        assert context.workspace_id == workspace_id
        return (self._release("published"),)

    def get_current(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
    ) -> MenuRelease | None:
        assert context.workspace_id == workspace_id
        return self._release("published")

    @staticmethod
    def _release_id() -> UUID:
        return UUID("83000000-0000-4000-8000-000000000001")

    @classmethod
    def _release(
        cls,
        status: str,
        *,
        release_kind: str = "standard",
    ) -> MenuRelease:
        from typing import cast

        from ai_platform_api.modules.authorization.domain.menus import (
            MenuReleaseKind,
            MenuReleaseStatus,
        )

        now = datetime(2026, 8, 14, tzinfo=UTC)
        snapshot = MenuReleaseSnapshot(
            schema_version=1,
            registry_version=3,
            workspace_id=WORKSPACE_ID,
            menu_version=2,
            menus=(
                MenuSnapshotItem(
                    menu_id=OVERVIEW_ID,
                    menu_key="navigation.workspace.overview",
                    parent_menu_id=DIRECTORY_ID,
                    name="合成工作台",
                    menu_type="page",
                    page_resource_id=UUID("80000000-0000-4000-8000-000000000002"),
                    permission_code="workspace.overview.access",
                    icon_key="panel-top",
                    sort_order=10,
                    source="system",
                    status="active",
                    visible=True,
                ),
            ),
            role_menus=(RoleMenuVisibility(WORKSPACE_ID, ROLE_ID, OVERVIEW_ID, True),),
            menu_api_bindings=(),
        )
        return MenuRelease(
            release_id=cls._release_id(),
            workspace_id=WORKSPACE_ID,
            release_number=1,
            release_kind=cast("MenuReleaseKind", release_kind),
            source_release_id=cls._release_id() if release_kind == "rollback" else None,
            status=cast("MenuReleaseStatus", status),
            snapshot=snapshot,
            snapshot_digest="a" * 64,
            validation_errors=(),
            rejection_reason=None,
            created_by_account_id=ACCOUNT_ID,
            decided_by_account_id=ACCOUNT_ID if status in {"approved", "published"} else None,
            created_at=now,
            validated_at=now if status in {"validated", "approved", "published"} else None,
            decided_at=now if status in {"approved", "published"} else None,
            published_at=now if status == "published" else None,
            version=1,
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


class UnavailablePolicy:
    """模拟版本见证异常，协议边缘必须返回可重试策略错误。"""

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            uuid4(),
            "deny",
            request.permission_code,
            WORKSPACE_ID,
            ResourceScope(),
            frozenset(),
            9,
            0,
            "policy_unavailable",
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
        menu_releases=StubMenuReleaseService(),
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


def test_resource_paths_keep_target_identity_and_attributes() -> None:
    """成员、邀请、目录和标签策略必须接收路径中的真实资源 ID。"""

    context = RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TraceContext("a" * 32, "b" * 16),
        authentication_method="browser_session",
    )
    for path_name, resource_type, permission_code in (
        ("account_id", "workspace_member", "workspace.member.update"),
        ("invitation_id", "workspace_invitation", "workspace.invitation.cancel"),
        ("folder_id", "knowledge_folder", "knowledge.folder.update"),
        ("tag_id", "knowledge_tag", "knowledge.tag.update"),
    ):
        resource_id = uuid4()
        request = Request(cast("Scope", {"type": "http", "path_params": {path_name: resource_id}}))
        api_resource = ApiResource(
            uuid4(),
            f"synthetic.{path_name}",
            f"synthetic{path_name}",
            "PATCH",
            f"/api/v1/workspaces/{{workspace_id}}/{path_name}/{{{path_name}}}",
            "authorized",
            permission_code,
            "high",
            "active",
        )

        reference = _resource_reference(request, context, api_resource, resource_type)

        assert reference.resource_id == resource_id
        assert reference.attributes[path_name] == resource_id


def test_stale_policy_cache_returns_retryable_service_unavailable() -> None:
    client = authorization_client(cast("PolicyDecisionPoint", UnavailablePolicy()))
    with client:
        response = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/roles/{ROLE_ID}/permissions",
            headers={"X-Workspace-ID": str(WORKSPACE_ID)},
        )

    assert response.status_code == 503
    assert response.json()["code"] == "POLICY_UNAVAILABLE"
    assert response.json()["retryable"] is True


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
                "expected_role_version": 7,
                "items": [
                    {
                        "permission_code": "organization.department.read",
                        "scope_type": "department_tree",
                        "department_ids": [str(DEPARTMENT_ID)],
                        "resource_ids": [],
                    }
                ],
            },
        )

    assert listed.status_code == 200
    assert replaced.status_code == 200
    assert listed.json()["role_version"] == 7
    assert replaced.json()["role_version"] == 8
    assert listed.json()["items"] == replaced.json()["items"]


def test_role_governance_route_returns_catalog_bindings_and_affected_members() -> None:
    client = authorization_client(AllowRegisteredPolicy())
    with client:
        response = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/roles/governance",
            headers={"X-Workspace-ID": str(WORKSPACE_ID)},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["role_version"] == 7
    assert body["roles"][0]["editable"] is True
    assert body["roles"][0]["affected_member_count"] == 1
    assert body["roles"][0]["bindings"][0]["scope_type"] == "workspace"
    assert body["permission_groups"][0]["domain"] == "operations"
    assert body["permission_groups"][0]["items"][0]["fields"] == [
        {"field_name": "actor_id", "security_level": "CONFIDENTIAL"}
    ]


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


def test_menu_release_routes_share_registered_permissions_and_snapshot_contract() -> None:
    client = authorization_client(AllowRegisteredPolicy())
    base = f"/api/v1/workspaces/{WORKSPACE_ID}/menu-releases"
    release_id = StubMenuReleaseService._release_id()
    read_headers = {"X-Workspace-ID": str(WORKSPACE_ID)}
    write_headers = {**read_headers, "X-CSRF-Token": "synthetic-authorization-csrf"}
    with client:
        created = client.post(base, headers=write_headers)
        validated = client.post(f"{base}/{release_id}/validate", headers=write_headers)
        decided = client.post(
            f"{base}/{release_id}/decision",
            headers=write_headers,
            json={"approved": True},
        )
        published = client.post(f"{base}/{release_id}/publish", headers=write_headers)
        rolled_back = client.post(f"{base}/{release_id}/rollback", headers=write_headers)
        listed = client.get(base, headers=read_headers)
        current = client.get(f"{base}/current", headers=read_headers)

    assert [
        created.status_code,
        validated.status_code,
        decided.status_code,
        published.status_code,
        rolled_back.status_code,
        listed.status_code,
        current.status_code,
    ] == [200] * 7
    assert created.json()["status"] == "draft"
    assert validated.json()["status"] == "validated"
    assert decided.json()["status"] == "approved"
    assert published.json()["status"] == "published"
    assert rolled_back.json()["release_kind"] == "rollback"
    assert listed.json()["items"][0]["release_id"] == str(release_id)
    assert current.json()["snapshot"]["menus"][0]["name"] == "合成工作台"
