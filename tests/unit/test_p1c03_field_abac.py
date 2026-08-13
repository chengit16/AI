from pathlib import Path
from uuid import UUID

from ai_platform_api.modules.authorization.application.field_registry import (
    load_field_policy_registry,
)
from ai_platform_api.modules.authorization.application.fields import FieldProjectionService
from ai_platform_api.modules.authorization.application.policy import RbacPolicyDecisionPoint
from ai_platform_api.modules.authorization.application.resources import load_resource_registry
from ai_platform_api.modules.authorization.domain.grants import PolicySubject, RolePermissionGrant
from ai_platform_api.modules.authorization.domain.policy import PolicyRequest, ResourceReference
from ai_platform_api.modules.model_gateway.application.context import AuthorizedModelContextBuilder

ROOT = Path(__file__).parents[2]
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000103")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000103")
ROLE_ID = UUID("70000000-0000-4000-8000-000000000103")
SECOND_ROLE_ID = UUID("70000000-0000-4000-8000-000000000104")


class GrantReader:
    def __init__(self, grants: tuple[RolePermissionGrant, ...]) -> None:
        self.grants = grants

    def resolve_subject(self, context: object) -> PolicySubject:
        del context
        return PolicySubject(
            ACCOUNT_ID,
            UUID(int=1),
            12,
            frozenset(grant.role_id for grant in self.grants),
        )

    def list_role_grants(
        self,
        workspace_id: UUID,
        role_ids: frozenset[UUID],
    ) -> tuple[RolePermissionGrant, ...]:
        assert workspace_id == WORKSPACE_ID
        assert role_ids == frozenset(grant.role_id for grant in self.grants)
        return self.grants

    def expand_department_tree(
        self,
        workspace_id: UUID,
        department_ids: frozenset[UUID],
    ) -> frozenset[UUID]:
        assert workspace_id == WORKSPACE_ID
        return department_ids


def test_security_clearance_and_explicit_masks_are_merged() -> None:
    from ai_platform_api.common.request_context import RequestContext
    from ai_platform_api.common.trace import TraceContext

    registry = load_resource_registry(ROOT / "contracts/authorization/resource-registry.v1.json")
    field_registry = load_field_policy_registry(
        ROOT / "contracts/authorization/field-policy-registry.v1.json"
    )
    grants = (
        RolePermissionGrant(
            WORKSPACE_ID,
            ROLE_ID,
            "workspace.member.read",
            "workspace",
            maximum_security_level="INTERNAL",
            field_mask=frozenset({"display_name"}),
        ),
    )
    context = RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TraceContext.new(),
    )
    decision = RbacPolicyDecisionPoint(registry, GrantReader(grants), field_registry).decide(
        PolicyRequest(
            context,
            "workspace.member.read",
            ResourceReference("workspace_member", WORKSPACE_ID, WORKSPACE_ID, {}),
        )
    )

    assert decision.allowed is True
    assert decision.field_mask == frozenset(
        {"account_id", "display_name", "login_name", "phone_number", "identity_number"}
    )
    assert decision.cache_ttl_seconds == 0


def test_multiple_roles_can_only_relax_an_explicit_mask_when_one_role_allows_field() -> None:
    from ai_platform_api.common.request_context import RequestContext
    from ai_platform_api.common.trace import TraceContext

    registry = load_resource_registry(ROOT / "contracts/authorization/resource-registry.v1.json")
    field_registry = load_field_policy_registry(
        ROOT / "contracts/authorization/field-policy-registry.v1.json"
    )
    grants = (
        RolePermissionGrant(
            WORKSPACE_ID,
            SECOND_ROLE_ID,
            "workspace.member.read",
            "workspace",
            maximum_security_level="INTERNAL",
            field_mask=frozenset({"display_name"}),
        ),
        RolePermissionGrant(
            WORKSPACE_ID,
            ROLE_ID,
            "workspace.member.read",
            "self",
            maximum_security_level="CONFIDENTIAL",
        ),
    )
    context = RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TraceContext.new(),
    )
    decision = RbacPolicyDecisionPoint(registry, GrantReader(grants), field_registry).decide(
        PolicyRequest(
            context,
            "workspace.member.read",
            ResourceReference(
                "workspace_member",
                ACCOUNT_ID,
                WORKSPACE_ID,
                {"account_id": ACCOUNT_ID},
            ),
        )
    )

    assert decision.field_mask == frozenset({"login_name", "phone_number", "identity_number"})


def test_collection_uses_most_restrictive_fields_across_data_scopes() -> None:
    from ai_platform_api.common.request_context import RequestContext
    from ai_platform_api.common.trace import TraceContext

    registry = load_resource_registry(ROOT / "contracts/authorization/resource-registry.v1.json")
    field_registry = load_field_policy_registry(
        ROOT / "contracts/authorization/field-policy-registry.v1.json"
    )
    grants = (
        RolePermissionGrant(
            WORKSPACE_ID,
            ROLE_ID,
            "workspace.member.read",
            "workspace",
            maximum_security_level="CONFIDENTIAL",
        ),
        RolePermissionGrant(
            WORKSPACE_ID,
            SECOND_ROLE_ID,
            "workspace.member.read",
            "self",
            maximum_security_level="PUBLIC",
            field_mask=frozenset({"membership_type"}),
        ),
    )
    context = RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TraceContext.new(),
    )
    decision = RbacPolicyDecisionPoint(registry, GrantReader(grants), field_registry).decide(
        PolicyRequest(
            context,
            "workspace.member.read",
            ResourceReference("workspace_member", WORKSPACE_ID, WORKSPACE_ID, {}),
        )
    )

    assert decision.field_mask == frozenset(
        {
            "account_id",
            "display_name",
            "identity_number",
            "login_name",
            "membership_type",
            "phone_number",
        }
    )


def test_response_log_retrieval_and_model_context_share_projection() -> None:
    field_registry = load_field_policy_registry(
        ROOT / "contracts/authorization/field-policy-registry.v1.json"
    )
    projection = FieldProjectionService(field_registry)
    payload = {
        "content": "合成公开正文",
        "source_position": {"source_path": "/synthetic/secret.pdf"},
        "identity_number": "synthetic-id-0001",
        "security_level": "RESTRICTED",
        "unregistered_internal_value": "never-export",
    }
    mask = frozenset({"source_position", "identity_number"})

    expected = {"content": "合成公开正文", "security_level": "RESTRICTED"}
    assert projection.response("chunk", payload, mask) == expected
    assert projection.log_attributes("chunk", payload, mask) == expected
    assert projection.retrieval_metadata("chunk", payload, mask) == expected
    message = AuthorizedModelContextBuilder(projection).build_message(
        resource_type="chunk",
        payload=payload,
        field_mask=mask,
    )
    assert message.content == '{"content":"合成公开正文","security_level":"RESTRICTED"}'
    assert "synthetic-id-0001" not in message.content
    assert "/synthetic/secret.pdf" not in message.content
