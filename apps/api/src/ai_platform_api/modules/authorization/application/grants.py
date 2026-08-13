from __future__ import annotations

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
    RolePermissionGrant,
    RolePermissionRepository,
    RolePermissionUnitOfWork,
    RolePermissionWriteConflictError,
)
from ai_platform_api.modules.authorization.domain.policy import DataScopeType
from ai_platform_api.modules.authorization.domain.resources import ResourceRegistry
from ai_platform_api.modules.integration.domain.events import IntegrationEvent


class RolePermissionDeniedError(PlatformError):
    error_code = "POLICY_DENIED"


class RolePermissionNotFoundError(PlatformError):
    error_code = "RESOURCE_NOT_FOUND"


class RolePermissionValidationError(PlatformError):
    error_code = "VALIDATION_ERROR"


class RolePermissionConflictError(PlatformError):
    error_code = "ROLE_CONFLICT"


__all__ = [
    "DataScopeType",
    "RolePermissionConflictError",
    "RolePermissionDeniedError",
    "RolePermissionGrant",
    "RolePermissionNotFoundError",
    "RolePermissionService",
    "RolePermissionValidationError",
]


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
    ) -> tuple[RolePermissionGrant, ...]:
        account_id = _browser_account(context, workspace_id)
        with self._unit_of_work as unit_of_work:
            _require_owner(unit_of_work.permissions, workspace_id, account_id)
            role = unit_of_work.permissions.get_role(workspace_id, role_id)
            if role is None:
                raise RolePermissionNotFoundError
            return unit_of_work.permissions.list_role_grants(workspace_id, frozenset({role_id}))

    def replace(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        role_id: UUID,
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
    ) -> tuple[RolePermissionGrant, ...]:
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
                role_version = unit_of_work.permissions.bump_role_version(workspace_id)
                unit_of_work.permissions.replace_role_grants(workspace_id, role_id, grants)
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
        return grants


def _browser_account(context: RequestContext, workspace_id: UUID) -> UUID:
    if (
        context.user_id is None
        or context.authentication_method != "browser_session"
        or context.workspace_id != workspace_id
    ):
        raise RolePermissionDeniedError
    return context.user_id


def _require_owner(
    repository: RolePermissionRepository,
    workspace_id: UUID,
    account_id: UUID,
) -> None:
    membership_type = repository.get_requester_membership_type(workspace_id, account_id)
    if membership_type != "owner":
        raise RolePermissionDeniedError
