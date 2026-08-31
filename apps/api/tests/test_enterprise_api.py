"""验证企业空间与成员邀请生命周期 HTTP 协议。"""

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
from ai_platform_api.modules.authorization.application.field_registry import (
    load_field_policy_registry,
)
from ai_platform_api.modules.authorization.application.fields import FieldProjectionService
from ai_platform_api.modules.authorization.application.grants import RolePermissionService
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.domain.policy import (
    PolicyDecision,
    PolicyDecisionPoint,
    PolicyRequest,
    ResourceScope,
)
from ai_platform_api.modules.identity.application.authentication import (
    ApiKeyService,
    AuthenticationService,
)
from ai_platform_api.modules.identity.application.enterprise import (
    EnterpriseWorkspaceService,
    WorkspaceGovernanceDeniedError,
    WorkspaceMemberSummary,
    WorkspaceSummary,
)
from ai_platform_api.modules.identity.application.entitlements import EntitlementService
from ai_platform_api.modules.identity.application.organization import OrganizationService
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.application.roles import RoleService
from ai_platform_api.modules.identity.application.team_management import TeamManagementService
from ai_platform_api.modules.identity.application.team_management_views import (
    InvitationLifecycleView,
    MembershipLifecycleView,
    TeamManagementView,
    invitation_lifecycle_view,
    membership_lifecycle_view,
    team_management_view,
)
from ai_platform_api.modules.identity.domain.enterprise import (
    EnterpriseConsoleRecentDocument,
    EnterpriseConsoleSnapshot,
    EnterpriseConsoleStatistics,
    EnterpriseConsoleTrendPoint,
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceRecord,
)
from ai_platform_api.modules.identity.domain.organization import (
    DepartmentSummary,
    PositionSummary,
)
from ai_platform_api.modules.identity.domain.team_management import (
    TeamAuditSummary,
    TeamEffectiveRoleSummary,
    TeamInvitationSummary,
    TeamManagementSnapshot,
    TeamManagementStatistics,
    TeamMemberSummary,
    TeamRoleSummary,
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
TEAM_MEMBER_ID = UUID("10000000-0000-4000-8000-000000000026")
TEAM_MEMBERSHIP_ID = UUID("30000000-0000-4000-8000-000000000025")
TEAM_DEPARTMENT_ID = UUID("50000000-0000-4000-8000-000000000024")
TEAM_POSITION_ID = UUID("60000000-0000-4000-8000-000000000024")
TEAM_ROLE_ID = UUID("70000000-0000-4000-8000-000000000024")


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

    def get_console_snapshot(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        trend_months: int = 6,
        recent_limit: int = 10,
    ) -> EnterpriseConsoleSnapshot:
        self._browser_account(context)
        self._require_current_workspace(context, workspace_id)
        if workspace_id != ENTERPRISE_WORKSPACE_ID:
            raise WorkspaceGovernanceDeniedError
        assert trend_months == 6 and recent_limit == 10
        generated_at = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
        return EnterpriseConsoleSnapshot(
            workspace=WorkspaceRecord(workspace_id, "enterprise", "合成企业空间", "active"),
            statistics=EnterpriseConsoleStatistics(2, 1, 3, 2, 1, 0, 1024, 4096),
            trend=(EnterpriseConsoleTrendPoint("2026-08", 3),),
            recent_documents=(
                EnterpriseConsoleRecentDocument(
                    UUID("50000000-0000-4000-8000-000000000024"),
                    UUID("60000000-0000-4000-8000-000000000024"),
                    "合成知识库",
                    "合成制度文档",
                    generated_at,
                    generated_at,
                    "published",
                ),
            ),
            generated_at=generated_at,
            time_window_start=datetime(2026, 3, 1, tzinfo=UTC),
            time_window_end=generated_at,
            consistency="eventually_consistent",
            profile_description=None,
            profile_logo_url=None,
        )

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


class StubTeamManagementService(TeamManagementService):
    """隔离数据库并记录团队管理 Router 传入的资源与原子配置。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def get_snapshot(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        audit_limit: int = 20,
    ) -> TeamManagementView:
        assert context.workspace_id == workspace_id == ENTERPRISE_WORKSPACE_ID
        self.calls.append(("snapshot", audit_limit))
        now = datetime(2026, 8, 31, 9, 30, tzinfo=UTC)
        return team_management_view(
            TeamManagementSnapshot(
                workspace=WorkspaceRecord(workspace_id, "enterprise", "合成企业空间", "active"),
                statistics=TeamManagementStatistics(2, 1, 1, 1, 1),
                members=(
                    TeamMemberSummary(
                        account_id=TEAM_MEMBER_ID,
                        display_name="合成团队成员",
                        login_name="team-member@example.test",
                        membership_type="member",
                        status="active",
                        department_ids=(TEAM_DEPARTMENT_ID,),
                        primary_department_id=TEAM_DEPARTMENT_ID,
                        position_ids=(TEAM_POSITION_ID,),
                        direct_role_ids=(TEAM_ROLE_ID,),
                        effective_roles=(
                            TeamEffectiveRoleSummary(
                                TEAM_ROLE_ID,
                                "synthetic_reviewer",
                                "合成审核员",
                                ("member",),
                            ),
                        ),
                        joined_at=now,
                        updated_at=now,
                        last_active_at=None,
                        version=4,
                    ),
                ),
                invitations=(
                    TeamInvitationSummary(
                        INVITATION_ID,
                        TEAM_MEMBER_ID,
                        "合成团队成员",
                        "team-member@example.test",
                        "合成所有者",
                        "pending",
                        now,
                        now + timedelta(days=7),
                        None,
                    ),
                ),
                departments=(
                    DepartmentSummary(TEAM_DEPARTMENT_ID, None, "合成研发部", "active", True, 0, 1),
                ),
                positions=(
                    PositionSummary(
                        TEAM_POSITION_ID, TEAM_DEPARTMENT_ID, "合成工程师", "active", True, 1
                    ),
                ),
                roles=(TeamRoleSummary(TEAM_ROLE_ID, "synthetic_reviewer", "合成审核员"),),
                recent_audits=(
                    TeamAuditSummary(
                        UUID("91000000-0000-4000-8000-000000000024"),
                        "合成所有者",
                        "workspace.member.configuration.update",
                        "organization_assignment",
                        TEAM_MEMBER_ID,
                        "succeeded",
                        now,
                    ),
                ),
                generated_at=now,
            )
        )

    def cancel_invitation(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        invitation_id: UUID,
    ) -> InvitationLifecycleView:
        assert context.workspace_id == workspace_id == ENTERPRISE_WORKSPACE_ID
        assert invitation_id == INVITATION_ID
        self.calls.append(("cancel", invitation_id))
        now = datetime.now(UTC)
        return invitation_lifecycle_view(
            WorkspaceInvitation(
                invitation_id, workspace_id, TEAM_MEMBER_ID, ACCOUNT_ID, "cancelled", now, now
            )
        )

    def update_member(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
        expected_version: int,
        department_ids: tuple[UUID, ...],
        primary_department_id: UUID | None,
        position_ids: tuple[UUID, ...],
        direct_role_ids: tuple[UUID, ...],
    ) -> MembershipLifecycleView:
        assert context.workspace_id == workspace_id == ENTERPRISE_WORKSPACE_ID
        assert target_account_id == TEAM_MEMBER_ID
        self.calls.append(
            (
                "update",
                (
                    expected_version,
                    department_ids,
                    primary_department_id,
                    position_ids,
                    direct_role_ids,
                ),
            )
        )
        return membership_lifecycle_view(self._membership("active", version=expected_version + 1))

    def activate_member(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
    ) -> MembershipLifecycleView:
        assert context.workspace_id == workspace_id == ENTERPRISE_WORKSPACE_ID
        assert target_account_id == TEAM_MEMBER_ID
        self.calls.append(("activate", target_account_id))
        return membership_lifecycle_view(self._membership("active", version=5))

    def remove_member(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
    ) -> MembershipLifecycleView:
        assert context.workspace_id == workspace_id == ENTERPRISE_WORKSPACE_ID
        assert target_account_id == TEAM_MEMBER_ID
        self.calls.append(("remove", target_account_id))
        return membership_lifecycle_view(self._membership("left", version=6))

    @staticmethod
    def _membership(
        status: Literal["active", "disabled", "left"], *, version: int
    ) -> WorkspaceMembership:
        now = datetime.now(UTC)
        return WorkspaceMembership(
            TEAM_MEMBERSHIP_ID,
            ENTERPRISE_WORKSPACE_ID,
            TEAM_MEMBER_ID,
            "member",
            status,
            now,
            now,
            version,
        )


class MaskMemberIdentityPolicy:
    def decide(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            decision_id=UUID("90000000-0000-4000-8000-000000000024"),
            decision="allow",
            permission_code=request.permission_code,
            workspace_id=request.context.workspace_id,
            resource_scope=ResourceScope(workspace=True),
            field_mask=frozenset({"account_id", "display_name"}),
            policy_version=1,
            cache_ttl_seconds=0,
            reason="synthetic_field_mask",
        )


class DenyEnterpriseConsolePolicy:
    """模拟统一 PDP 默认拒绝，确保 Router 不会直接调用领域服务。"""

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            decision_id=UUID("90000000-0000-4000-8000-000000000025"),
            decision="deny",
            permission_code=request.permission_code,
            workspace_id=request.context.workspace_id,
            resource_scope=ResourceScope(),
            field_mask=frozenset(),
            policy_version=1,
            cache_ttl_seconds=0,
            reason="synthetic_default_deny",
        )


def enterprise_client(
    policy: PolicyDecisionPoint | None = None,
    team_management: TeamManagementService | None = None,
) -> TestClient:
    settings = Settings(environment="test")
    closing = ClosingDependency()
    field_registry = load_field_policy_registry(
        ROOT / "contracts/authorization/field-policy-registry.v1.json"
    )
    container = ApplicationContainer(
        settings=settings,
        database=cast(PlatformDatabase, closing),
        errors=ErrorCatalog.load(ROOT / "contracts/errors/catalog.v1.json"),
        resource_registry=load_resource_registry(
            ROOT / "contracts/authorization/resource-registry.v1.json"
        ),
        policy=policy or AllowRegisteredPolicy(),
        role_permissions=cast("RolePermissionService", object()),
        authentication=StubAuthenticationService(),
        api_keys=cast("ApiKeyService", object()),
        registration=cast("RegistrationService", object()),
        enterprise_workspaces=StubEnterpriseWorkspaceService(),
        entitlements=cast("EntitlementService", object()),
        organization=cast("OrganizationService", object()),
        roles=cast("RoleService", object()),
        team_management=team_management or StubTeamManagementService(),
        role_cache=cast("ValkeyRoleResolutionCache", closing),
        secret_cipher=cast("EnvelopeSecretCipher", object()),
        sessions=cast("ValkeySessionStore", closing),
        field_policy_registry=field_registry,
        field_projection=FieldProjectionService(field_registry),
    )
    return TestClient(create_app(settings, container))


def test_team_management_routes_preserve_resources_and_atomic_payload() -> None:
    """五个团队接口必须保留路径资源、版本和完整组织角色请求体。"""

    service = StubTeamManagementService()
    client = enterprise_client(team_management=service)
    client.cookies.set("ai_platform_session", "synthetic-enterprise-session")
    read_headers = {"X-Workspace-ID": str(ENTERPRISE_WORKSPACE_ID)}
    write_headers = {
        **read_headers,
        "X-CSRF-Token": "synthetic-enterprise-csrf",
    }
    base = f"/api/v1/workspaces/{ENTERPRISE_WORKSPACE_ID}"

    with client:
        snapshot = client.get(f"{base}/team-management", headers=read_headers)
        cancelled = client.post(f"{base}/invitations/{INVITATION_ID}/cancel", headers=write_headers)
        updated = client.put(
            f"{base}/team-management/members/{TEAM_MEMBER_ID}",
            headers=write_headers,
            json={
                "expected_version": 4,
                "department_ids": [str(TEAM_DEPARTMENT_ID)],
                "primary_department_id": str(TEAM_DEPARTMENT_ID),
                "position_ids": [str(TEAM_POSITION_ID)],
                "direct_role_ids": [str(TEAM_ROLE_ID)],
            },
        )
        activated = client.post(
            f"{base}/team-management/members/{TEAM_MEMBER_ID}/activate",
            headers=write_headers,
        )
        removed = client.post(
            f"{base}/team-management/members/{TEAM_MEMBER_ID}/remove",
            headers=write_headers,
        )

    assert snapshot.status_code == 200
    assert snapshot.json()["members"][0]["account_id"] == str(TEAM_MEMBER_ID)
    assert snapshot.json()["members"][0]["last_active_at"] is None
    assert snapshot.json()["recent_audits"][0]["action"] == (
        "workspace.member.configuration.update"
    )
    assert cancelled.json()["status"] == "cancelled"
    assert updated.json()["status"] == "active"
    assert activated.json()["status"] == "active"
    assert removed.json()["status"] == "left"
    assert service.calls == [
        ("snapshot", 20),
        ("cancel", INVITATION_ID),
        (
            "update",
            (
                4,
                (TEAM_DEPARTMENT_ID,),
                TEAM_DEPARTMENT_ID,
                (TEAM_POSITION_ID,),
                (TEAM_ROLE_ID,),
            ),
        ),
        ("activate", TEAM_MEMBER_ID),
        ("remove", TEAM_MEMBER_ID),
    ]


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
        console = client.get(
            f"/api/v1/workspaces/{ENTERPRISE_WORKSPACE_ID}/enterprise-console",
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
    assert console.status_code == 200
    assert console.json()["statistics"] == {
        "active_member_count": 2,
        "active_knowledge_base_count": 1,
        "active_document_count": 3,
        "published_document_count": 2,
        "processing_document_count": 1,
        "failed_document_count": 0,
        "storage_used_bytes": 1024,
        "storage_limit_bytes": 4096,
    }
    assert console.json()["recent_documents"][0]["title"] == "合成制度文档"
    assert "object_key" not in console.text
    assert "content" not in console.json()["recent_documents"][0]
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


def test_enterprise_console_query_contract_rejects_out_of_range_values() -> None:
    client = enterprise_client()
    client.cookies.set("ai_platform_session", "synthetic-enterprise-session")
    headers = {"X-Workspace-ID": str(ENTERPRISE_WORKSPACE_ID)}

    with client:
        invalid_months = client.get(
            f"/api/v1/workspaces/{ENTERPRISE_WORKSPACE_ID}/enterprise-console",
            headers=headers,
            params={"trend_months": 13},
        )
        invalid_limit = client.get(
            f"/api/v1/workspaces/{ENTERPRISE_WORKSPACE_ID}/enterprise-console",
            headers=headers,
            params={"recent_limit": 0},
        )

    assert invalid_months.status_code == 422
    assert invalid_limit.status_code == 422


def test_enterprise_console_http_rejects_policy_personal_and_workspace_mismatch() -> None:
    """HTTP 边缘必须拒绝默认 PDP、个人空间以及路径与 Header 空间不一致。"""

    denied_client = enterprise_client(cast("PolicyDecisionPoint", DenyEnterpriseConsolePolicy()))
    denied_client.cookies.set("ai_platform_session", "synthetic-enterprise-session")
    with denied_client:
        policy_denied = denied_client.get(
            f"/api/v1/workspaces/{ENTERPRISE_WORKSPACE_ID}/enterprise-console",
            headers={"X-Workspace-ID": str(ENTERPRISE_WORKSPACE_ID)},
        )

    client = enterprise_client()
    client.cookies.set("ai_platform_session", "synthetic-enterprise-session")
    with client:
        personal_denied = client.get(
            f"/api/v1/workspaces/{PERSONAL_WORKSPACE_ID}/enterprise-console",
            headers={"X-Workspace-ID": str(PERSONAL_WORKSPACE_ID)},
        )
        header_mismatch = client.get(
            f"/api/v1/workspaces/{ENTERPRISE_WORKSPACE_ID}/enterprise-console",
            headers={"X-Workspace-ID": str(PERSONAL_WORKSPACE_ID)},
        )

    assert policy_denied.status_code == 403
    assert policy_denied.json()["code"] == "POLICY_DENIED"
    assert personal_denied.status_code == 403
    assert personal_denied.json()["code"] == "POLICY_DENIED"
    assert header_mismatch.status_code == 403
    assert header_mismatch.json()["code"] == "POLICY_DENIED"


def test_member_response_omits_masked_fields_before_serialization() -> None:
    client = enterprise_client(cast("PolicyDecisionPoint", MaskMemberIdentityPolicy()))
    client.cookies.set("ai_platform_session", "synthetic-enterprise-session")

    with client:
        response = client.get(
            f"/api/v1/workspaces/{ENTERPRISE_WORKSPACE_ID}/members",
            headers={"X-Workspace-ID": str(ENTERPRISE_WORKSPACE_ID)},
        )

    assert response.status_code == 200
    assert response.json() == {"items": [{"membership_type": "member", "status": "active"}]}
    assert "合成成员" not in response.text
    assert str(ACCOUNT_ID) not in response.text
