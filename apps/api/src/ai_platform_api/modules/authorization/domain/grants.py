from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.authorization.domain.policy import DataScopeType

OWNER_PERMISSION_CODES = (
    "authorization.binding.create",
    "authorization.binding.revoke",
    "authorization.effective_role.read",
    "authorization.menu.manage",
    "authorization.menu.read",
    "authorization.menu_release.approve",
    "authorization.menu_release.create",
    "authorization.menu_release.publish",
    "authorization.menu_release.read",
    "authorization.menu_release.rollback",
    "authorization.menu_release.validate",
    "authorization.role.create",
    "authorization.role.read",
    "authorization.role.status",
    "authorization.role_permission.read",
    "authorization.role_permission.manage",
    "enterprise.invitation.accept",
    "enterprise.workspace.create",
    "knowledge.base.create",
    "knowledge.base.delete",
    "knowledge.base.read",
    "knowledge.document.create",
    "knowledge.document.delete",
    "knowledge.document.read",
    "knowledge.document.version.create",
    "knowledge.document.version.publish",
    "knowledge.document.version.ready",
    "knowledge.ingestion.read",
    "knowledge.ingestion.retry",
    "knowledge.production.access",
    "organization.assignment.read",
    "organization.assignment.write",
    "organization.department.create",
    "organization.department.move",
    "organization.department.read",
    "organization.department.status",
    "organization.position.create",
    "organization.position.read",
    "organization.position.status",
    "organization.structure.access",
    "system.runtime.access",
    "workspace.context.switch",
    "workspace.entitlement.read",
    "workspace.member.disable",
    "workspace.member.invite",
    "workspace.member.read",
    "workspace.members.access",
    "workspace.membership.leave",
    "workspace.open_api.manage",
    "workspace.overview.access",
)
MEMBER_PERMISSION_CODES = (
    "authorization.effective_role.read",
    "authorization.menu_release.read",
    "workspace.context.switch",
    "workspace.entitlement.read",
    "workspace.membership.leave",
    "workspace.overview.access",
)


@dataclass(frozen=True)
class RolePermissionGrant:
    workspace_id: UUID
    role_id: UUID
    permission_code: str
    scope_type: DataScopeType
    department_ids: frozenset[UUID] = frozenset()
    resource_ids: frozenset[UUID] = frozenset()
    maximum_security_level: SecurityLevel = "RESTRICTED"
    field_mask: frozenset[str] = frozenset()

    def assert_valid(self) -> None:
        if self.scope_type in {"workspace", "self"}:
            valid = not self.department_ids and not self.resource_ids
        elif self.scope_type == "department_tree":
            valid = bool(self.department_ids) and not self.resource_ids
        else:
            valid = bool(self.resource_ids) and not self.department_ids
        if not valid:
            raise InvalidRolePermissionGrantError


class InvalidRolePermissionGrantError(Exception):
    """权限的数据范围与其必需目标不一致。"""


class RolePermissionWriteConflictError(Exception):
    """数据库约束或角色版本变化拒绝本次权限替换。"""


@dataclass(frozen=True)
class PolicySubject:
    account_id: UUID
    membership_id: UUID
    role_version: int
    role_ids: frozenset[UUID]


class PolicyGrantReader(Protocol):
    def resolve_subject(self, context: RequestContext) -> PolicySubject | None: ...

    def list_role_grants(
        self,
        workspace_id: UUID,
        role_ids: frozenset[UUID],
    ) -> tuple[RolePermissionGrant, ...]: ...

    def expand_department_tree(
        self,
        workspace_id: UUID,
        department_ids: frozenset[UUID],
    ) -> frozenset[UUID]: ...


class RolePermissionRepository(PolicyGrantReader, Protocol):
    def get_requester_membership_type(
        self,
        workspace_id: UUID,
        account_id: UUID,
    ) -> str | None: ...

    def get_role(self, workspace_id: UUID, role_id: UUID) -> tuple[str, bool, str] | None: ...

    def replace_role_grants(
        self,
        workspace_id: UUID,
        role_id: UUID,
        grants: tuple[RolePermissionGrant, ...],
    ) -> None: ...

    def bump_role_version(self, workspace_id: UUID) -> int: ...


class RolePermissionUnitOfWork(Protocol):
    @property
    def permissions(self) -> RolePermissionRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> RolePermissionUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


def system_role_permission_seed(
    *,
    workspace_id: UUID,
    owner_role_id: UUID,
    member_role_id: UUID,
) -> tuple[RolePermissionGrant, ...]:
    """系统角色的默认授权必须同时供 Migration 与新空间写入使用。"""

    return tuple(
        RolePermissionGrant(
            workspace_id,
            owner_role_id,
            code,
            "workspace",
            maximum_security_level="RESTRICTED",
        )
        for code in OWNER_PERMISSION_CODES
    ) + tuple(
        RolePermissionGrant(
            workspace_id,
            member_role_id,
            code,
            "workspace",
            maximum_security_level="INTERNAL",
        )
        for code in MEMBER_PERMISSION_CODES
    )
