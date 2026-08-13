from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.role_errors import (
    RoleConflictError,
    RoleGovernanceDeniedError,
    RoleNotFoundError,
    RoleValidationError,
)
from ai_platform_api.modules.identity.application.role_support import (
    browser_account,
    find_role,
    next_role_version,
    normalized_role_name,
    require_owner,
    role_facts,
)
from ai_platform_api.modules.identity.domain.roles import (
    EffectiveRoleSet,
    InvalidRoleTransitionError,
    Role,
    RoleBinding,
    RoleRepository,
    RoleResolutionCache,
    RoleScopeType,
    RoleUnitOfWork,
    RoleWriteConflictError,
    resolve_effective_roles,
)

ROLE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")

__all__ = [
    "EffectiveRoleSet",
    "Role",
    "RoleBinding",
    "RoleConflictError",
    "RoleGovernanceDeniedError",
    "RoleNotFoundError",
    "RoleService",
    "RoleValidationError",
]


class RoleService:
    """集中隐藏角色事实、继承计算、版本失效和可重建缓存。"""

    def __init__(
        self,
        unit_of_work: RoleUnitOfWork,
        cache: RoleResolutionCache | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._cache = cache

    def create(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        role_key: str,
        name: str,
    ) -> Role:
        account_id = browser_account(context, workspace_id)
        normalized_key = role_key.strip().casefold()
        if ROLE_KEY_PATTERN.fullmatch(normalized_key) is None:
            raise RoleValidationError
        now = datetime.now(UTC)
        role = Role(
            uuid4(),
            workspace_id,
            normalized_key,
            normalized_role_name(name),
            "active",
            False,
            now,
            now,
            1,
        )
        try:
            with self._unit_of_work as unit_of_work:
                require_owner(unit_of_work.roles, workspace_id, account_id)
                existing = unit_of_work.roles.list_roles(workspace_id, for_update=True)
                if any(
                    item.role_key == role.role_key or item.name.casefold() == role.name.casefold()
                    for item in existing
                ):
                    raise RoleConflictError
                role_version = next_role_version(unit_of_work.roles, workspace_id)
                event, audit = role_facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=role.role_id,
                    aggregate_version=1,
                    event_type="identity.role.created",
                    action="identity.role.create",
                    resource_type="role",
                    occurred_at=now,
                    role_version=role_version,
                    attributes={"role_key": role.role_key},
                )
                unit_of_work.roles.add_role(role)
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except RoleWriteConflictError as error:
            raise RoleConflictError from error
        return role

    def set_status(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        role_id: UUID,
        active: bool,
    ) -> Role:
        account_id = browser_account(context, workspace_id)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                require_owner(unit_of_work.roles, workspace_id, account_id)
                current = find_role(
                    unit_of_work.roles.list_roles(workspace_id, for_update=True),
                    role_id,
                )
                updated = (
                    current.activate(occurred_at=now)
                    if active
                    else current.disable(occurred_at=now)
                )
                role_version = next_role_version(unit_of_work.roles, workspace_id)
                event, audit = role_facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=role_id,
                    aggregate_version=updated.version,
                    event_type=f"identity.role.{updated.status}",
                    action="identity.role.status.update",
                    resource_type="role",
                    occurred_at=now,
                    role_version=role_version,
                    attributes={"previous_status": current.status},
                )
                unit_of_work.roles.save_role(updated)
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except (InvalidRoleTransitionError, RoleWriteConflictError) as error:
            raise RoleConflictError from error
        return updated

    def list_roles(self, context: RequestContext, *, workspace_id: UUID) -> tuple[Role, ...]:
        account_id = browser_account(context, workspace_id)
        with self._unit_of_work as unit_of_work:
            require_owner(unit_of_work.roles, workspace_id, account_id)
            return unit_of_work.roles.list_roles(workspace_id)

    def bind(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        role_id: UUID,
        scope_type: RoleScopeType,
        department_id: UUID | None,
        target_account_id: UUID | None,
    ) -> RoleBinding:
        account_id = browser_account(context, workspace_id)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                require_owner(unit_of_work.roles, workspace_id, account_id)
                roles = unit_of_work.roles.list_roles(workspace_id, for_update=True)
                role = find_role(roles, role_id)
                if role.status != "active" or role.system_managed:
                    raise RoleConflictError
                membership_id = self._validate_scope(
                    unit_of_work.roles,
                    workspace_id=workspace_id,
                    scope_type=scope_type,
                    department_id=department_id,
                    target_account_id=target_account_id,
                )
                active_bindings = tuple(
                    binding
                    for binding in unit_of_work.roles.list_bindings(workspace_id, for_update=True)
                    if binding.status == "active"
                )
                if any(
                    binding.role_id == role_id
                    and binding.scope_type == scope_type
                    and binding.department_id == department_id
                    and binding.membership_id == membership_id
                    for binding in active_bindings
                ):
                    raise RoleConflictError
                binding = RoleBinding(
                    uuid4(),
                    workspace_id,
                    role_id,
                    scope_type,
                    department_id,
                    membership_id,
                    "active",
                    now,
                    None,
                    1,
                )
                role_version = next_role_version(unit_of_work.roles, workspace_id)
                event, audit = role_facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=binding.binding_id,
                    aggregate_version=1,
                    event_type="identity.role.bound",
                    action="identity.role.bind",
                    resource_type="role_binding",
                    occurred_at=now,
                    role_version=role_version,
                    attributes={
                        "role_id": str(role_id),
                        "scope_type": scope_type,
                    },
                )
                unit_of_work.roles.add_binding(binding)
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except RoleWriteConflictError as error:
            raise RoleConflictError from error
        return binding

    def revoke(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        binding_id: UUID,
    ) -> RoleBinding:
        account_id = browser_account(context, workspace_id)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                require_owner(unit_of_work.roles, workspace_id, account_id)
                current = next(
                    (
                        item
                        for item in unit_of_work.roles.list_bindings(workspace_id, for_update=True)
                        if item.binding_id == binding_id
                    ),
                    None,
                )
                if current is None:
                    raise RoleNotFoundError
                role = find_role(
                    unit_of_work.roles.list_roles(workspace_id, for_update=True),
                    current.role_id,
                )
                if role.system_managed:
                    raise RoleConflictError
                updated = current.revoke(occurred_at=now)
                role_version = next_role_version(unit_of_work.roles, workspace_id)
                event, audit = role_facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=binding_id,
                    aggregate_version=updated.version,
                    event_type="identity.role.revoked",
                    action="identity.role.revoke",
                    resource_type="role_binding",
                    occurred_at=now,
                    role_version=role_version,
                    attributes={"role_id": str(current.role_id)},
                )
                unit_of_work.roles.save_binding(updated)
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except (InvalidRoleTransitionError, RoleWriteConflictError) as error:
            raise RoleConflictError from error
        return updated

    def effective_roles(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
    ) -> EffectiveRoleSet:
        account_id = browser_account(context, workspace_id)
        with self._unit_of_work as unit_of_work:
            workspace = unit_of_work.roles.get_workspace(workspace_id)
            requester = unit_of_work.roles.get_membership(workspace_id, account_id)
            if (
                workspace is None
                or workspace.status != "active"
                or requester is None
                or requester.status != "active"
                or (target_account_id != account_id and requester.membership_type != "owner")
            ):
                raise RoleGovernanceDeniedError
            target = unit_of_work.roles.get_membership(workspace_id, target_account_id)
            if target is None or target.status != "active":
                raise RoleNotFoundError
            role_version = unit_of_work.roles.get_role_version(workspace_id)
            if role_version is None:
                raise RoleNotFoundError
            if self._cache is not None:
                cached = self._cache.get(workspace_id, target.membership_id, role_version)
                if cached is not None:
                    return cached
            role_set = resolve_effective_roles(
                membership=target,
                role_version=role_version,
                roles=unit_of_work.roles.list_roles(workspace_id),
                bindings=unit_of_work.roles.list_bindings(workspace_id),
                departments=unit_of_work.roles.list_departments(workspace_id),
                assigned_department_ids=unit_of_work.roles.list_membership_department_ids(
                    workspace_id, target.membership_id
                ),
            )
        if self._cache is not None:
            self._cache.put(role_set)
        return role_set

    @staticmethod
    def _validate_scope(
        repository: RoleRepository,
        *,
        workspace_id: UUID,
        scope_type: RoleScopeType,
        department_id: UUID | None,
        target_account_id: UUID | None,
    ) -> UUID | None:
        if scope_type == "workspace":
            if department_id is not None or target_account_id is not None:
                raise RoleConflictError
            return None
        if scope_type == "department":
            if department_id is None or target_account_id is not None:
                raise RoleConflictError
            departments = repository.list_departments(workspace_id)
            if not any(
                item.department_id == department_id and item.status == "active"
                for item in departments
            ):
                raise RoleNotFoundError
            return None
        if department_id is not None or target_account_id is None:
            raise RoleConflictError
        membership = repository.get_membership(workspace_id, target_account_id)
        if membership is None or membership.status != "active":
            raise RoleNotFoundError
        return membership.membership_id
