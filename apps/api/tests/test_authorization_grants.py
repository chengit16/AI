"""验证角色权限读取、乐观并发和不可修改角色边界。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.application.field_registry import (
    load_field_policy_registry,
)
from ai_platform_api.modules.authorization.application.grants import (
    RolePermissionConflictError,
    RolePermissionDeniedError,
    RolePermissionService,
)
from ai_platform_api.modules.authorization.application.resources import (
    load_resource_registry,
)
from ai_platform_api.modules.authorization.domain.grants import (
    PolicySubject,
    RoleGovernanceFacts,
    RoleGovernanceMemberFact,
    RolePermissionGrant,
    RolePermissionRepository,
    RolePermissionUnitOfWork,
    RolePermissionWriteConflictError,
)
from ai_platform_api.modules.identity.domain.enterprise import WorkspaceMembership
from ai_platform_api.modules.identity.domain.organization import Department
from ai_platform_api.modules.identity.domain.roles import Role, RoleBinding

ROOT = Path(__file__).parents[3]
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000205")
OTHER_WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000206")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000205")
ROLE_ID = UUID("70000000-0000-4000-8000-000000000205")
MEMBERSHIP_ID = UUID("30000000-0000-4000-8000-000000000205")
DISABLED_MEMBERSHIP_ID = UUID("30000000-0000-4000-8000-000000000206")
DISABLED_ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000206")
DEPARTMENT_ID = UUID("50000000-0000-4000-8000-000000000205")
NOW = datetime(2026, 9, 3, tzinfo=UTC)


class MemoryWriter:
    """收集同一事务中的审计或事件事实。"""

    def __init__(self) -> None:
        self.items: list[object] = []

    def add(self, item: object) -> None:
        self.items.append(item)


class MemoryRolePermissions:
    """模拟条件版本更新，明确区分成功与过期页面。"""

    def __init__(self, *, role_version: int = 7, system_managed: bool = False) -> None:
        self.role_version = role_version
        self.system_managed = system_managed
        self.grants: tuple[RolePermissionGrant, ...] = ()

    def get_requester_membership_type(self, workspace_id: UUID, account_id: UUID) -> str | None:
        return "owner" if (workspace_id, account_id) == (WORKSPACE_ID, ACCOUNT_ID) else None

    def resolve_subject(self, context: RequestContext) -> PolicySubject | None:
        """测试仓储不参与 PDP 主体解析，仅满足正式窄协议。"""

        return None

    def get_role(self, workspace_id: UUID, role_id: UUID) -> tuple[str, bool, str] | None:
        if (workspace_id, role_id) != (WORKSPACE_ID, ROLE_ID):
            return None
        return ("synthetic_reviewer", self.system_managed, "active")

    def get_role_version(self, workspace_id: UUID) -> int | None:
        return self.role_version if workspace_id == WORKSPACE_ID else None

    def get_governance_facts(self, workspace_id: UUID) -> RoleGovernanceFacts | None:
        if workspace_id != WORKSPACE_ID:
            return None
        role = Role(
            ROLE_ID,
            WORKSPACE_ID,
            "synthetic_reviewer",
            "合成审核员",
            "active",
            self.system_managed,
            NOW,
            NOW,
            1,
        )
        binding = RoleBinding(
            UUID("71000000-0000-4000-8000-000000000205"),
            WORKSPACE_ID,
            ROLE_ID,
            "workspace",
            None,
            None,
            "active",
            NOW,
            None,
            1,
        )
        department = Department(
            DEPARTMENT_ID,
            WORKSPACE_ID,
            None,
            "合成研发部",
            "active",
            NOW,
            NOW,
            1,
        )
        active = WorkspaceMembership(
            MEMBERSHIP_ID,
            WORKSPACE_ID,
            ACCOUNT_ID,
            "owner",
            "active",
            NOW,
            NOW,
            1,
        )
        disabled = WorkspaceMembership(
            DISABLED_MEMBERSHIP_ID,
            WORKSPACE_ID,
            DISABLED_ACCOUNT_ID,
            "member",
            "disabled",
            NOW,
            NOW,
            1,
        )
        return RoleGovernanceFacts(
            self.role_version,
            (role,),
            self.grants,
            (binding,),
            (department,),
            (
                RoleGovernanceMemberFact(active, "合成所有者", (DEPARTMENT_ID,)),
                RoleGovernanceMemberFact(disabled, "合成停用成员", (DEPARTMENT_ID,)),
            ),
        )

    def list_role_grants(
        self, workspace_id: UUID, role_ids: frozenset[UUID]
    ) -> tuple[RolePermissionGrant, ...]:
        assert workspace_id == WORKSPACE_ID and role_ids == frozenset({ROLE_ID})
        return self.grants

    def expand_department_tree(
        self, workspace_id: UUID, department_ids: frozenset[UUID]
    ) -> frozenset[UUID]:
        return department_ids

    def replace_role_grants(
        self,
        workspace_id: UUID,
        role_id: UUID,
        grants: tuple[RolePermissionGrant, ...],
    ) -> None:
        assert (workspace_id, role_id) == (WORKSPACE_ID, ROLE_ID)
        self.grants = grants

    def bump_role_version(self, workspace_id: UUID, expected_version: int) -> int:
        if workspace_id != WORKSPACE_ID or expected_version != self.role_version:
            raise RolePermissionWriteConflictError
        self.role_version += 1
        return self.role_version


class MemoryRolePermissionUnitOfWork:
    def __init__(self, permissions: MemoryRolePermissions) -> None:
        self.permissions = permissions
        self.audit = MemoryWriter()
        self.outbox = MemoryWriter()
        self.committed = False

    def __enter__(self) -> RolePermissionUnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def commit(self) -> None:
        self.committed = True


def _context(workspace_id: UUID = WORKSPACE_ID) -> RequestContext:
    return RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=workspace_id,
        trace=TraceContext("a" * 32, "b" * 16),
        authentication_method="browser_session",
    )


def _service(
    permissions: MemoryRolePermissions,
) -> tuple[RolePermissionService, MemoryRolePermissionUnitOfWork]:
    unit_of_work = MemoryRolePermissionUnitOfWork(permissions)
    return (
        RolePermissionService(
            load_resource_registry(ROOT / "contracts/authorization/resource-registry.v1.json"),
            unit_of_work,
            load_field_policy_registry(
                ROOT / "contracts/authorization/field-policy-registry.v1.json"
            ),
        ),
        unit_of_work,
    )


def test_list_returns_grants_and_role_version_from_one_snapshot() -> None:
    permissions = MemoryRolePermissions()
    permissions.grants = (
        RolePermissionGrant(WORKSPACE_ID, ROLE_ID, "organization.department.read", "workspace"),
    )
    service, _ = _service(permissions)

    snapshot = service.list(_context(), workspace_id=WORKSPACE_ID, role_id=ROLE_ID)

    assert snapshot.role_version == 7
    assert snapshot.grants == permissions.grants


def test_stale_role_version_rejects_without_replacing_grants() -> None:
    permissions = MemoryRolePermissions()
    service, unit_of_work = _service(permissions)

    with pytest.raises(RolePermissionConflictError):
        service.replace(
            _context(),
            workspace_id=WORKSPACE_ID,
            role_id=ROLE_ID,
            expected_role_version=6,
            entries=(
                (
                    "organization.department.read",
                    "workspace",
                    frozenset(),
                    frozenset(),
                    "INTERNAL",
                    frozenset(),
                ),
            ),
        )

    assert permissions.grants == ()
    assert unit_of_work.audit.items == []
    assert unit_of_work.outbox.items == []
    assert unit_of_work.committed is False


def test_governance_uses_server_facts_for_roles_catalog_and_affected_members() -> None:
    permissions = MemoryRolePermissions()
    permissions.grants = (
        RolePermissionGrant(
            WORKSPACE_ID,
            ROLE_ID,
            "operations.records.read",
            "workspace",
            maximum_security_level="CONFIDENTIAL",
            field_mask=frozenset({"actor_id"}),
        ),
    )
    service, _ = _service(permissions)

    snapshot = service.get_governance(_context(), workspace_id=WORKSPACE_ID)

    assert snapshot.role_version == 7
    role = snapshot.roles[0]
    assert role.editable is True
    assert role.bindings[0].scope_name == "当前工作空间"
    assert [member.display_name for member in role.affected_members] == ["合成所有者"]
    assert role.affected_members[0].sources[0].scope_type == "workspace"
    operations = next(group for group in snapshot.permission_groups if group.domain == "operations")
    record_permission = next(
        item for item in operations.items if item.permission_code == "operations.records.read"
    )
    assert {field.field_name for field in record_permission.fields} == {
        "actor_id",
        "attributes",
        "user_id",
    }


def test_system_role_and_cross_workspace_are_rejected() -> None:
    system_service, _ = _service(MemoryRolePermissions(system_managed=True))
    with pytest.raises(RolePermissionConflictError):
        system_service.replace(
            _context(),
            workspace_id=WORKSPACE_ID,
            role_id=ROLE_ID,
            expected_role_version=7,
            entries=(),
        )

    service, _ = _service(MemoryRolePermissions())
    with pytest.raises(RolePermissionDeniedError):
        service.list(_context(OTHER_WORKSPACE_ID), workspace_id=WORKSPACE_ID, role_id=ROLE_ID)


def test_successful_replace_increments_version_and_commits_facts() -> None:
    permissions = MemoryRolePermissions()
    service, unit_of_work = _service(permissions)

    snapshot = service.replace(
        _context(),
        workspace_id=WORKSPACE_ID,
        role_id=ROLE_ID,
        expected_role_version=7,
        entries=(
            (
                "organization.department.read",
                "workspace",
                frozenset(),
                frozenset(),
                "INTERNAL",
                frozenset(),
            ),
        ),
    )

    assert snapshot.role_version == 8
    assert len(snapshot.grants) == 1
    assert unit_of_work.committed is True
    assert len(unit_of_work.audit.items) == len(unit_of_work.outbox.items) == 1


_REPOSITORY_PROTOCOL_CHECK: RolePermissionRepository
_REPOSITORY_PROTOCOL_CHECK = MemoryRolePermissions()
