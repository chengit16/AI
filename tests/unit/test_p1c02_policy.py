from pathlib import Path
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.application.policy import RbacPolicyDecisionPoint
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.domain.grants import (
    PolicySubject,
    RolePermissionGrant,
)
from ai_platform_api.modules.authorization.domain.policy import PolicyRequest, ResourceReference

REGISTRY_PATH = Path(__file__).parents[2] / "contracts/authorization/resource-registry.v1.json"
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000097")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000097")
ROLE_ID = UUID("70000000-0000-4000-8000-000000000097")
ROOT_DEPARTMENT_ID = UUID("50000000-0000-4000-8000-000000000097")
CHILD_DEPARTMENT_ID = UUID("50000000-0000-4000-8000-000000000098")
OUTSIDE_DEPARTMENT_ID = UUID("50000000-0000-4000-8000-000000000099")
TRACE = TraceContext.continue_from("00-7123456789abcdef0123456789abcdef-7123456789abcdef-01")


class GrantReader:
    def __init__(self, grants: tuple[RolePermissionGrant, ...], *, available: bool = True) -> None:
        self.grants = grants
        self.available = available

    def resolve_subject(self, context: RequestContext) -> PolicySubject | None:
        if not self.available:
            raise RuntimeError("合成策略存储不可用")
        return PolicySubject(ACCOUNT_ID, UUID(int=1), 9, frozenset({ROLE_ID}))

    def list_role_grants(
        self,
        workspace_id: UUID,
        role_ids: frozenset[UUID],
    ) -> tuple[RolePermissionGrant, ...]:
        assert workspace_id == WORKSPACE_ID and role_ids == frozenset({ROLE_ID})
        return self.grants

    def expand_department_tree(
        self,
        workspace_id: UUID,
        department_ids: frozenset[UUID],
    ) -> frozenset[UUID]:
        assert workspace_id == WORKSPACE_ID
        if ROOT_DEPARTMENT_ID in department_ids:
            return frozenset({ROOT_DEPARTMENT_ID, CHILD_DEPARTMENT_ID})
        return department_ids


def context(*, scopes: frozenset[str] | None = None) -> RequestContext:
    return RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TRACE,
        authentication_method="browser_session" if scopes is None else "open_api_key",
        credential_scopes=scopes,
    )


def request(
    permission_code: str,
    resource_type: str,
    resource_id: UUID,
    *,
    request_context: RequestContext | None = None,
    attributes: dict[str, object] | None = None,
) -> PolicyRequest:
    return PolicyRequest(
        request_context or context(),
        permission_code,
        ResourceReference(
            resource_type,
            resource_id,
            WORKSPACE_ID,
            attributes or {},
        ),
    )


def policy(
    grants: tuple[RolePermissionGrant, ...],
    *,
    available: bool = True,
) -> RbacPolicyDecisionPoint:
    return RbacPolicyDecisionPoint(
        load_resource_registry(REGISTRY_PATH),
        GrantReader(grants, available=available),
    )


def test_workspace_permission_allows_registered_resource() -> None:
    decision = policy(
        (
            RolePermissionGrant(
                WORKSPACE_ID,
                ROLE_ID,
                "workspace.member.read",
                "workspace",
            ),
        )
    ).decide(request("workspace.member.read", "workspace_member", WORKSPACE_ID))

    assert decision.allowed is True
    assert decision.resource_scope.workspace is True
    assert decision.policy_version == 9


def test_department_tree_and_self_scope_are_merged_and_enforced() -> None:
    department_policy = policy(
        (
            RolePermissionGrant(
                WORKSPACE_ID,
                ROLE_ID,
                "organization.department.read",
                "department_tree",
                frozenset({ROOT_DEPARTMENT_ID}),
            ),
        )
    )
    child = department_policy.decide(
        request(
            "organization.department.read",
            "department",
            CHILD_DEPARTMENT_ID,
            attributes={"department_id": CHILD_DEPARTMENT_ID},
        )
    )
    outside = department_policy.decide(
        request(
            "organization.department.read",
            "department",
            OUTSIDE_DEPARTMENT_ID,
            attributes={"department_id": OUTSIDE_DEPARTMENT_ID},
        )
    )

    assert child.allowed is True
    assert child.resource_scope.department_ids == frozenset(
        {ROOT_DEPARTMENT_ID, CHILD_DEPARTMENT_ID}
    )
    assert outside.allowed is False
    assert outside.reason == "resource_out_of_scope"

    self_decision = policy(
        (
            RolePermissionGrant(
                WORKSPACE_ID,
                ROLE_ID,
                "authorization.effective_role.read",
                "self",
            ),
        )
    ).decide(
        request(
            "authorization.effective_role.read",
            "effective_role",
            ACCOUNT_ID,
            attributes={"account_id": ACCOUNT_ID},
        )
    )
    assert self_decision.allowed is True


def test_credential_scope_and_policy_failure_default_to_deny() -> None:
    grant = RolePermissionGrant(
        WORKSPACE_ID,
        ROLE_ID,
        "workspace.entitlement.read",
        "workspace",
    )
    scoped = policy((grant,)).decide(
        request(
            "workspace.entitlement.read",
            "workspace_entitlement",
            WORKSPACE_ID,
            request_context=context(scopes=frozenset({"workspace.member.read"})),
        )
    )
    unavailable = policy((grant,), available=False).decide(
        request(
            "workspace.entitlement.read",
            "workspace_entitlement",
            WORKSPACE_ID,
        )
    )

    assert scoped.allowed is False and scoped.reason == "credential_scope_denied"
    assert unavailable.allowed is False and unavailable.reason == "policy_unavailable"


def test_unregistered_permission_and_resource_type_mismatch_are_denied() -> None:
    reader = GrantReader(())
    decision_point = RbacPolicyDecisionPoint(load_resource_registry(REGISTRY_PATH), reader)

    unknown = decision_point.decide(
        request("unknown.resource.read", "workspace_member", WORKSPACE_ID)
    )
    mismatched = decision_point.decide(request("workspace.member.read", "department", WORKSPACE_ID))

    assert unknown.reason == "permission_not_registered"
    assert mismatched.reason == "permission_not_registered"
