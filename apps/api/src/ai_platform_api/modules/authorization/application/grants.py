"""编排角色权限读取与原子替换，并同步审计、版本和 Outbox。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.fields import (
    FieldPolicyRegistry,
    SecurityLevel,
)
from ai_platform_api.modules.authorization.domain.grants import (
    InvalidRolePermissionGrantError,
    RoleGovernanceFacts,
    RolePermissionGrant,
    RolePermissionRepository,
    RolePermissionUnitOfWork,
    RolePermissionWriteConflictError,
)
from ai_platform_api.modules.authorization.domain.policy import DataScopeType
from ai_platform_api.modules.authorization.domain.resources import ResourceRegistry
from ai_platform_api.modules.identity.domain.enterprise import MembershipType
from ai_platform_api.modules.identity.domain.roles import (
    RoleScopeType,
    RoleStatus,
    resolve_effective_roles,
)
from ai_platform_api.modules.integration.domain.events import IntegrationEvent


class RolePermissionDeniedError(PlatformError):
    """表示角色权限拒绝错误，由协议层映射为稳定错误码。"""

    error_code = "POLICY_DENIED"


class RolePermissionNotFoundError(PlatformError):
    """表示角色权限未找到错误，由协议层映射为稳定错误码。"""

    error_code = "RESOURCE_NOT_FOUND"


class RolePermissionValidationError(PlatformError):
    """表示角色权限校验错误，由协议层映射为稳定错误码。"""

    error_code = "VALIDATION_ERROR"


class RolePermissionConflictError(PlatformError):
    """表示角色权限冲突错误，由协议层映射为稳定错误码。"""

    error_code = "ROLE_CONFLICT"


__all__ = [
    "DataScopeType",
    "RoleGovernanceSnapshot",
    "RolePermissionConflictError",
    "RolePermissionDeniedError",
    "RolePermissionGrant",
    "RolePermissionNotFoundError",
    "RolePermissionService",
    "RolePermissionSnapshot",
    "RolePermissionValidationError",
]


@dataclass(frozen=True)
class PermissionFieldCatalogItem:
    """描述权限资源可配置遮罩的字段与敏感级别。"""

    field_name: str
    security_level: SecurityLevel


@dataclass(frozen=True)
class PermissionCatalogItem:
    """描述权限矩阵中的稳定权限码、动作和可选治理维度。"""

    permission_code: str
    resource_type: str
    action: str
    allowed_scope_types: tuple[DataScopeType, ...]
    fields: tuple[PermissionFieldCatalogItem, ...]


@dataclass(frozen=True)
class PermissionCatalogGroup:
    """按权限码产品域分组，避免页面自行解析权限命名。"""

    domain: str
    items: tuple[PermissionCatalogItem, ...]


@dataclass(frozen=True)
class RoleBindingSummary:
    """描述一个活动角色绑定来源及其可读目标名称。"""

    scope_type: RoleScopeType
    scope_id: UUID
    scope_name: str


@dataclass(frozen=True)
class RoleAffectedMember:
    """描述被角色实际影响的低敏成员及全部有效来源。"""

    account_id: UUID
    display_name: str
    membership_type: MembershipType
    sources: tuple[RoleBindingSummary, ...]


@dataclass(frozen=True)
class RoleGovernanceItem:
    """汇总角色状态、可编辑性、授权项、绑定与服务端影响范围。"""

    role_id: UUID
    role_key: str
    name: str
    status: RoleStatus
    system_managed: bool
    editable: bool
    grants: tuple[RolePermissionGrant, ...]
    bindings: tuple[RoleBindingSummary, ...]
    affected_members: tuple[RoleAffectedMember, ...]


@dataclass(frozen=True)
class RoleGovernanceSnapshot:
    """返回权限页一次读取所需的版本、角色与权限目录。"""

    role_version: int
    roles: tuple[RoleGovernanceItem, ...]
    permission_groups: tuple[PermissionCatalogGroup, ...]


@dataclass(frozen=True)
class RolePermissionSnapshot:
    """返回同一数据库快照中的角色版本与授权集合。"""

    role_version: int
    grants: tuple[RolePermissionGrant, ...]


class RolePermissionService:
    """集中管理角色授权；替换、版本失效、审计和事件必须一次提交。"""

    def __init__(
        self,
        registry: ResourceRegistry,
        unit_of_work: RolePermissionUnitOfWork,
        field_registry: FieldPolicyRegistry | None = None,
    ) -> None:
        self._registry = registry
        self._unit_of_work = unit_of_work
        self._field_registry = field_registry or FieldPolicyRegistry(1, 1, ())

    def list(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        role_id: UUID,
    ) -> RolePermissionSnapshot:
        """校验空间所有者身份后列出角色授权，并拒绝跨空间角色标识。"""

        account_id = _browser_account(context, workspace_id)
        with self._unit_of_work as unit_of_work:
            _require_owner(unit_of_work.permissions, workspace_id, account_id)
            role = unit_of_work.permissions.get_role(workspace_id, role_id)
            if role is None:
                raise RolePermissionNotFoundError
            role_version = unit_of_work.permissions.get_role_version(workspace_id)
            if role_version is None:
                raise RolePermissionNotFoundError
            return RolePermissionSnapshot(
                role_version,
                unit_of_work.permissions.list_role_grants(workspace_id, frozenset({role_id})),
            )

    def get_governance(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
    ) -> RoleGovernanceSnapshot:
        """返回服务端聚合的角色、绑定、影响成员和权限目录。"""

        account_id = _browser_account(context, workspace_id)
        with self._unit_of_work as unit_of_work:
            _require_owner(unit_of_work.permissions, workspace_id, account_id)
            facts = unit_of_work.permissions.get_governance_facts(workspace_id)
            if facts is None:
                raise RolePermissionNotFoundError
            return RoleGovernanceSnapshot(
                role_version=facts.role_version,
                roles=_governance_roles(workspace_id, facts),
                permission_groups=_permission_groups(self._registry, self._field_registry),
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
        """整体替换角色权限并递增角色版本，使有效角色和策略缓存自然失效。"""

        # 1. 先把协议输入转为不可变授权项，并校验权限码、数据范围和字段掩码。
        account_id = _browser_account(context, workspace_id)
        permission_by_code = {item.code: item for item in self._registry.permissions}
        grants = tuple(
            RolePermissionGrant(
                workspace_id,
                role_id,
                permission_code,
                scope_type,
                department_ids,
                resource_ids,
                maximum_security_level,
                field_mask,
            )
            for (
                permission_code,
                scope_type,
                department_ids,
                resource_ids,
                maximum_security_level,
                field_mask,
            ) in entries
        )
        if len({grant.permission_code for grant in grants}) != len(grants):
            raise RolePermissionValidationError
        try:
            for grant in grants:
                grant.assert_valid()
                permission = permission_by_code.get(grant.permission_code)
                if permission is None or permission.status != "active":
                    raise RolePermissionValidationError
                if not grant.field_mask.issubset(
                    self._field_registry.fields_for(permission.resource_type)
                ):
                    raise RolePermissionValidationError
        except InvalidRolePermissionGrantError as error:
            raise RolePermissionValidationError from error

        # 2. 锁定目标角色并拒绝修改系统角色或停用角色。
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                _require_owner(unit_of_work.permissions, workspace_id, account_id)
                role = unit_of_work.permissions.get_role(workspace_id, role_id)
                if role is None:
                    raise RolePermissionNotFoundError
                _, system_managed, status = role
                if system_managed or status != "active":
                    raise RolePermissionConflictError
                # 3. 权限集合整体替换并递增角色版本，策略缓存不会读取到部分更新。
                # 期望版本由读取响应带回；条件更新失败时整笔事务回滚，避免旧页面覆盖新配置。
                role_version = unit_of_work.permissions.bump_role_version(
                    workspace_id, expected_role_version
                )
                unit_of_work.permissions.replace_role_grants(workspace_id, role_id, grants)
                # 4. 授权、审计和 Outbox 版本事件同事务提交。
                unit_of_work.audit.add(
                    AuditRecord(
                        audit_id=uuid4(),
                        workspace_id=workspace_id,
                        actor_id=context.actor_id,
                        user_id=context.user_id,
                        action="authorization.role_permissions.replace",
                        resource_type="role",
                        resource_id=role_id,
                        outcome="succeeded",
                        occurred_at=now,
                        request_id=context.request_id,
                        trace_id=context.trace.trace_id,
                        traceparent=context.trace.traceparent,
                        authorization=context.audit_authorization,
                        attributes={
                            "grant_count": len(grants),
                            "role_version": role_version,
                        },
                    )
                )
                unit_of_work.outbox.add(
                    IntegrationEvent(
                        event_id=uuid4(),
                        event_type="authorization.role_permissions.replaced",
                        workspace_id=workspace_id,
                        aggregate_id=role_id,
                        aggregate_version=role_version,
                        occurred_at=now,
                        trace_id=context.trace.trace_id,
                        traceparent=context.trace.traceparent,
                        actor_id=context.actor_id,
                        user_id=context.user_id,
                        request_id=context.request_id,
                        payload={"role_version": role_version},
                    )
                )
                unit_of_work.commit()
        except RolePermissionWriteConflictError as error:
            raise RolePermissionConflictError from error
        return RolePermissionSnapshot(role_version, grants)


def _browser_account(context: RequestContext, workspace_id: UUID) -> UUID:
    if (
        context.user_id is None
        or context.authentication_method != "browser_session"
        or context.workspace_id != workspace_id
    ):
        raise RolePermissionDeniedError
    return context.user_id


def _governance_roles(
    workspace_id: UUID, facts: RoleGovernanceFacts
) -> tuple[RoleGovernanceItem, ...]:
    """复用正式有效角色解析器计算每个角色的真实影响成员。"""

    # 1. 先构建名称、有效绑定和授权索引，确保后续角色聚合只基于同一事务快照。
    department_name = {item.department_id: item.name for item in facts.departments}
    member_name = {item.membership.membership_id: item.display_name for item in facts.members}
    active_bindings = tuple(item for item in facts.bindings if item.status == "active")
    grants_by_role: dict[UUID, list[RolePermissionGrant]] = {}
    for grant in facts.grants:
        grants_by_role.setdefault(grant.role_id, []).append(grant)
    result: list[RoleGovernanceItem] = []
    # 2. 对每个角色复用正式有效角色解析器，服务端计算实际影响成员及全部绑定来源。
    for role in facts.roles:
        bindings = tuple(
            _binding_summary(
                workspace_id,
                binding.scope_type,
                binding.department_id,
                binding.membership_id,
                department_name,
                member_name,
            )
            for binding in active_bindings
            if binding.role_id == role.role_id
        )
        affected: list[RoleAffectedMember] = []
        for member in facts.members:
            effective = resolve_effective_roles(
                membership=member.membership,
                role_version=facts.role_version,
                roles=facts.roles,
                bindings=facts.bindings,
                departments=facts.departments,
                assigned_department_ids=member.department_ids,
            )
            effective_role = next(
                (item for item in effective.roles if item.role_id == role.role_id), None
            )
            if effective_role is None:
                continue
            affected.append(
                RoleAffectedMember(
                    account_id=member.membership.account_id,
                    display_name=member.display_name,
                    membership_type=member.membership.membership_type,
                    sources=tuple(
                        _binding_summary(
                            workspace_id,
                            source.scope_type,
                            source.scope_id if source.scope_type == "department" else None,
                            source.scope_id if source.scope_type == "member" else None,
                            department_name,
                            member_name,
                        )
                        for source in effective_role.sources
                    ),
                )
            )
        result.append(
            RoleGovernanceItem(
                role_id=role.role_id,
                role_key=role.role_key,
                name=role.name,
                status=role.status,
                system_managed=role.system_managed,
                editable=not role.system_managed and role.status == "active",
                grants=tuple(
                    sorted(
                        grants_by_role.get(role.role_id, ()), key=lambda item: item.permission_code
                    )
                ),
                bindings=bindings,
                affected_members=tuple(affected),
            )
        )
    return tuple(result)


def _binding_summary(
    workspace_id: UUID,
    scope_type: RoleScopeType,
    department_id: UUID | None,
    membership_id: UUID | None,
    department_name: dict[UUID, str],
    member_name: dict[UUID, str],
) -> RoleBindingSummary:
    if scope_type == "workspace":
        return RoleBindingSummary(scope_type, workspace_id, "当前工作空间")
    if scope_type == "department" and department_id is not None:
        return RoleBindingSummary(
            scope_type, department_id, department_name.get(department_id, "未知部门")
        )
    if scope_type == "member" and membership_id is not None:
        return RoleBindingSummary(
            scope_type, membership_id, member_name.get(membership_id, "未知成员")
        )
    raise RolePermissionValidationError


def _permission_groups(
    registry: ResourceRegistry, field_registry: FieldPolicyRegistry
) -> tuple[PermissionCatalogGroup, ...]:
    groups: dict[str, list[PermissionCatalogItem]] = {}
    for permission in registry.permissions:
        if permission.status != "active" or permission.scope in {"platform", "account"}:
            continue
        fields = tuple(
            PermissionFieldCatalogItem(rule.field_name, rule.security_level)
            for rule in field_registry.rules
            if rule.resource_type == permission.resource_type
        )
        groups.setdefault(permission.code.split(".", 1)[0], []).append(
            PermissionCatalogItem(
                permission.code,
                permission.resource_type,
                permission.action,
                ("workspace", "department_tree", "self", "resource"),
                fields,
            )
        )
    return tuple(
        PermissionCatalogGroup(domain, tuple(sorted(items, key=lambda item: item.permission_code)))
        for domain, items in sorted(groups.items())
    )


def _require_owner(
    repository: RolePermissionRepository,
    workspace_id: UUID,
    account_id: UUID,
) -> None:
    membership_type = repository.get_requester_membership_type(workspace_id, account_id)
    if membership_type != "owner":
        raise RolePermissionDeniedError
