"""验证 P1B-04 角色状态、绑定和继承计算规则。"""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from ai_platform_api.modules.identity.domain.enterprise import WorkspaceMembership
from ai_platform_api.modules.identity.domain.organization import Department
from ai_platform_api.modules.identity.domain.roles import (
    Role,
    RoleBinding,
    deterministic_role_uuid,
    resolve_effective_roles,
    system_role_seed,
)

NOW = datetime(2026, 8, 14, tzinfo=UTC)
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000091")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000091")
MEMBERSHIP_ID = UUID("30000000-0000-4000-8000-000000000091")
ROOT_ID = UUID("50000000-0000-4000-8000-000000000091")
CHILD_ID = UUID("50000000-0000-4000-8000-000000000092")
WORKSPACE_ROLE_ID = UUID("70000000-0000-4000-8000-000000000091")
DEPARTMENT_ROLE_ID = UUID("70000000-0000-4000-8000-000000000092")
MEMBER_ROLE_ID = UUID("70000000-0000-4000-8000-000000000093")


def membership(
    status: Literal["active", "disabled", "left"] = "active",
) -> WorkspaceMembership:
    return WorkspaceMembership(
        MEMBERSHIP_ID,
        WORKSPACE_ID,
        ACCOUNT_ID,
        "member",
        status,
        NOW,
        NOW,
        1,
    )


def department(
    department_id: UUID,
    parent_id: UUID | None,
    status: Literal["active", "disabled"] = "active",
) -> Department:
    return Department(
        department_id,
        WORKSPACE_ID,
        parent_id,
        f"合成部门-{department_id}",
        status,
        NOW,
        NOW,
        1,
    )


def role(
    role_id: UUID,
    role_key: str,
    status: Literal["active", "disabled"] = "active",
) -> Role:
    return Role(
        role_id,
        WORKSPACE_ID,
        role_key,
        f"合成角色-{role_key}",
        status,
        False,
        NOW,
        NOW,
        1,
    )


def binding(
    binding_id: UUID,
    role_id: UUID,
    scope_type: Literal["workspace", "department", "member"],
    *,
    department_id: UUID | None = None,
    membership_id: UUID | None = None,
) -> RoleBinding:
    return RoleBinding(
        binding_id,
        WORKSPACE_ID,
        role_id,
        scope_type,
        department_id,
        membership_id,
        "active",
        NOW,
        None,
        1,
    )


def test_effective_roles_merge_sources_in_deterministic_order() -> None:
    role_set = resolve_effective_roles(
        membership=membership(),
        role_version=7,
        roles=(
            role(MEMBER_ROLE_ID, "member_direct"),
            role(DEPARTMENT_ROLE_ID, "department_lead"),
            role(WORKSPACE_ROLE_ID, "workspace_auditor"),
        ),
        bindings=(
            binding(UUID(int=1), WORKSPACE_ROLE_ID, "workspace"),
            binding(
                UUID(int=2),
                DEPARTMENT_ROLE_ID,
                "department",
                department_id=ROOT_ID,
            ),
            binding(
                UUID(int=3),
                DEPARTMENT_ROLE_ID,
                "department",
                department_id=CHILD_ID,
            ),
            binding(
                UUID(int=4),
                MEMBER_ROLE_ID,
                "member",
                membership_id=MEMBERSHIP_ID,
            ),
        ),
        departments=(
            department(ROOT_ID, None),
            department(CHILD_ID, ROOT_ID),
        ),
        assigned_department_ids=(CHILD_ID,),
    )

    assert [item.role_key for item in role_set.roles] == [
        "department_lead",
        "member_direct",
        "workspace_auditor",
    ]
    assert [source.scope_id for source in role_set.roles[0].sources] == [ROOT_ID, CHILD_ID]
    assert role_set.role_version == 7


def test_disabled_ancestor_and_inactive_member_fail_closed() -> None:
    department_binding = binding(
        UUID(int=5),
        DEPARTMENT_ROLE_ID,
        "department",
        department_id=ROOT_ID,
    )
    disabled = resolve_effective_roles(
        membership=membership(),
        role_version=8,
        roles=(role(DEPARTMENT_ROLE_ID, "department_lead"),),
        bindings=(department_binding,),
        departments=(
            department(ROOT_ID, None, "disabled"),
            department(CHILD_ID, ROOT_ID),
        ),
        assigned_department_ids=(CHILD_ID,),
    )
    inactive = resolve_effective_roles(
        membership=membership("left"),
        role_version=9,
        roles=(role(DEPARTMENT_ROLE_ID, "department_lead"),),
        bindings=(department_binding,),
        departments=(department(ROOT_ID, None),),
        assigned_department_ids=(ROOT_ID,),
    )

    assert disabled.roles == ()
    assert inactive.roles == ()


def test_system_role_seed_is_reproducible() -> None:
    first = system_role_seed(
        workspace_id=WORKSPACE_ID,
        owner_membership_id=MEMBERSHIP_ID,
        occurred_at=NOW,
    )
    second = system_role_seed(
        workspace_id=WORKSPACE_ID,
        owner_membership_id=MEMBERSHIP_ID,
        occurred_at=NOW,
    )

    assert first == second
    assert [item.role_key for item in first[0]] == ["workspace_owner", "workspace_member"]
    assert deterministic_role_uuid(UUID(int=1), UUID(int=2), "合成") == deterministic_role_uuid(
        UUID(int=1), UUID(int=2), "合成"
    )
