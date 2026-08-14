"""定义角色、绑定、继承计算和确定性系统角色种子。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter

from ai_platform_api.modules.identity.domain.enterprise import (
    WorkspaceMembership,
    WorkspaceRecord,
)
from ai_platform_api.modules.identity.domain.organization import (
    Department,
    build_department_closure,
    summarize_departments,
)

RoleStatus = Literal["active", "disabled"]
RoleScopeType = Literal["workspace", "department", "member"]
RoleBindingStatus = Literal["active", "revoked"]
OWNER_ROLE_NAMESPACE = UUID("9e7bdf6f-572d-49d0-9aa1-ecb13dc57520")
MEMBER_ROLE_NAMESPACE = UUID("3eb54a68-38d4-484c-888a-15b56db82f05")
OWNER_BINDING_NAMESPACE = UUID("13cb98f4-88eb-49f0-ae9a-57b31e0dbe19")
MEMBER_BINDING_NAMESPACE = UUID("760d6766-f06d-419e-a661-f7d8b86df2e9")


@dataclass(frozen=True)
class Role:
    """表示工作空间内可授权的角色及其乐观并发版本。"""

    role_id: UUID
    workspace_id: UUID
    role_key: str
    name: str
    status: RoleStatus
    system_managed: bool
    created_at: datetime
    updated_at: datetime
    version: int

    def disable(self, *, occurred_at: datetime) -> Role:
        if self.system_managed or self.status != "active":
            raise InvalidRoleTransitionError
        return replace(
            self,
            status="disabled",
            updated_at=occurred_at,
            version=self.version + 1,
        )

    def activate(self, *, occurred_at: datetime) -> Role:
        if self.system_managed or self.status != "disabled":
            raise InvalidRoleTransitionError
        return replace(
            self,
            status="active",
            updated_at=occurred_at,
            version=self.version + 1,
        )


@dataclass(frozen=True)
class RoleBinding:
    """将角色绑定到整个空间、部门子树或指定成员。"""

    binding_id: UUID
    workspace_id: UUID
    role_id: UUID
    scope_type: RoleScopeType
    department_id: UUID | None
    membership_id: UUID | None
    status: RoleBindingStatus
    created_at: datetime
    revoked_at: datetime | None
    version: int

    def revoke(self, *, occurred_at: datetime) -> RoleBinding:
        if self.status != "active":
            raise InvalidRoleTransitionError
        return replace(
            self,
            status="revoked",
            revoked_at=occurred_at,
            version=self.version + 1,
        )


@dataclass(frozen=True)
class EffectiveRoleSource:
    """标识一个有效角色来自空间、部门还是成员直接绑定。"""

    scope_type: RoleScopeType
    scope_id: UUID


@dataclass(frozen=True)
class EffectiveRole:
    """合并同一角色的全部有效授权来源，便于审计与解释。"""

    role_id: UUID
    role_key: str
    name: str
    sources: tuple[EffectiveRoleSource, ...]


@dataclass(frozen=True)
class EffectiveRoleSet:
    """记录指定成员在角色版本下解析出的稳定角色集合。"""

    workspace_id: UUID
    account_id: UUID
    membership_id: UUID
    role_version: int
    roles: tuple[EffectiveRole, ...]


class InvalidRoleTransitionError(Exception):
    """系统角色、角色状态或绑定状态不允许当前转换。"""


class RoleWriteConflictError(Exception):
    """数据库唯一约束或乐观锁拒绝并发角色写入。"""


def resolve_effective_roles(
    *,
    membership: WorkspaceMembership,
    role_version: int,
    roles: tuple[Role, ...],
    bindings: tuple[RoleBinding, ...],
    departments: tuple[Department, ...],
    assigned_department_ids: tuple[UUID, ...],
) -> EffectiveRoleSet:
    """只累计有效授权来源；停用部门不会把祖先角色继续传递给成员。"""

    if membership.status != "active":
        return EffectiveRoleSet(
            membership.workspace_id,
            membership.account_id,
            membership.membership_id,
            role_version,
            (),
        )
    role_by_id = {role.role_id: role for role in roles if role.status == "active"}
    summaries = {item.department_id: item for item in summarize_departments(departments)}
    active_assignments = {
        department_id
        for department_id in assigned_department_ids
        if summaries.get(department_id) is not None and summaries[department_id].effective_active
    }
    applicable_departments: set[UUID] = set()
    for closure in build_department_closure(departments):
        if (
            closure.descendant_department_id in active_assignments
            and summaries[closure.ancestor_department_id].effective_active
        ):
            applicable_departments.add(closure.ancestor_department_id)

    sources_by_role: dict[UUID, set[EffectiveRoleSource]] = {}
    for binding in bindings:
        if binding.status != "active" or binding.role_id not in role_by_id:
            continue
        source: EffectiveRoleSource | None = None
        if binding.scope_type == "workspace":
            source = EffectiveRoleSource("workspace", membership.workspace_id)
        elif (
            binding.scope_type == "department"
            and binding.department_id is not None
            and binding.department_id in applicable_departments
        ):
            source = EffectiveRoleSource("department", binding.department_id)
        elif binding.scope_type == "member" and binding.membership_id == membership.membership_id:
            source = EffectiveRoleSource("member", membership.membership_id)
        if source is not None:
            sources_by_role.setdefault(binding.role_id, set()).add(source)

    source_order = {"workspace": 0, "department": 1, "member": 2}
    effective = tuple(
        EffectiveRole(
            role.role_id,
            role.role_key,
            role.name,
            tuple(
                sorted(
                    sources,
                    key=lambda source: (source_order[source.scope_type], source.scope_id.int),
                )
            ),
        )
        for role_id, sources in sources_by_role.items()
        if (role := role_by_id.get(role_id)) is not None
    )
    return EffectiveRoleSet(
        membership.workspace_id,
        membership.account_id,
        membership.membership_id,
        role_version,
        tuple(sorted(effective, key=lambda role: (role.role_key, role.role_id.int))),
    )


class RoleRepository(Protocol):
    """在工作空间隔离和角色版本控制下维护角色及绑定。"""

    def get_workspace(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> WorkspaceRecord | None: ...

    def get_role_version(self, workspace_id: UUID, *, for_update: bool = False) -> int | None: ...

    def bump_role_version(self, workspace_id: UUID, expected_version: int) -> int: ...

    def get_membership(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceMembership | None: ...

    def list_roles(self, workspace_id: UUID, *, for_update: bool = False) -> tuple[Role, ...]: ...

    def add_role(self, role: Role) -> None: ...

    def save_role(self, role: Role) -> None: ...

    def list_bindings(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> tuple[RoleBinding, ...]: ...

    def add_binding(self, binding: RoleBinding) -> None: ...

    def save_binding(self, binding: RoleBinding) -> None: ...

    def list_departments(self, workspace_id: UUID) -> tuple[Department, ...]: ...

    def list_membership_department_ids(
        self, workspace_id: UUID, membership_id: UUID
    ) -> tuple[UUID, ...]: ...


class RoleUnitOfWork(Protocol):
    """保证角色、绑定、审计和 Outbox 在同一事务内提交。"""

    @property
    def roles(self) -> RoleRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> RoleUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class RoleResolutionCache(Protocol):
    """按成员与角色版本缓存有效角色，版本变化即自然失效。"""

    def get(
        self, workspace_id: UUID, membership_id: UUID, role_version: int
    ) -> EffectiveRoleSet | None: ...

    def put(self, role_set: EffectiveRoleSet) -> None: ...


def system_role_seed(
    *,
    workspace_id: UUID,
    owner_membership_id: UUID,
    occurred_at: datetime,
) -> tuple[tuple[Role, ...], tuple[RoleBinding, ...]]:
    """新旧空间使用同一确定性 ID 规则，Migration 往返和应用写入结果保持一致。"""

    owner_role_id = deterministic_role_uuid(OWNER_ROLE_NAMESPACE, workspace_id, "owner")
    member_role_id = deterministic_role_uuid(MEMBER_ROLE_NAMESPACE, workspace_id, "member")
    roles = (
        Role(
            owner_role_id,
            workspace_id,
            "workspace_owner",
            "空间所有者",
            "active",
            True,
            occurred_at,
            occurred_at,
            1,
        ),
        Role(
            member_role_id,
            workspace_id,
            "workspace_member",
            "空间成员",
            "active",
            True,
            occurred_at,
            occurred_at,
            1,
        ),
    )
    bindings = (
        RoleBinding(
            deterministic_role_uuid(OWNER_BINDING_NAMESPACE, owner_membership_id, "owner"),
            workspace_id,
            owner_role_id,
            "member",
            None,
            owner_membership_id,
            "active",
            occurred_at,
            None,
            1,
        ),
        RoleBinding(
            deterministic_role_uuid(MEMBER_BINDING_NAMESPACE, workspace_id, "member"),
            workspace_id,
            member_role_id,
            "workspace",
            None,
            None,
            "active",
            occurred_at,
            None,
            1,
        ),
    )
    return roles, bindings


def deterministic_role_uuid(namespace: UUID, value: UUID, suffix: str) -> UUID:
    """生成与 Migration SQL 一致的确定性角色或绑定标识。"""

    digest = hashlib.md5(
        f"{namespace}:{value}:{suffix}".encode(), usedforsecurity=False
    ).hexdigest()
    # 这里只需要可复现标识，不用于密码学；固定版本位便于与 Migration SQL 一致。
    return UUID(f"{digest[:8]}-{digest[8:12]}-5{digest[13:16]}-a{digest[17:20]}-{digest[20:32]}")
