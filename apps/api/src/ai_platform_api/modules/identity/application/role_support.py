from datetime import datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.role_errors import (
    RoleConflictError,
    RoleGovernanceDeniedError,
    RoleNotFoundError,
    RoleValidationError,
)
from ai_platform_api.modules.identity.domain.roles import Role, RoleRepository


def browser_account(context: RequestContext, workspace_id: UUID) -> UUID:
    if (
        context.user_id is None
        or context.authentication_method != "browser_session"
        or context.workspace_id != workspace_id
    ):
        raise RoleGovernanceDeniedError
    return context.user_id


def require_owner(repository: RoleRepository, workspace_id: UUID, account_id: UUID) -> None:
    workspace = repository.get_workspace(workspace_id, for_update=True)
    membership = repository.get_membership(workspace_id, account_id, for_update=True)
    if (
        workspace is None
        or workspace.workspace_type != "enterprise"
        or workspace.status != "active"
        or membership is None
        or membership.status != "active"
        or membership.membership_type != "owner"
    ):
        raise RoleGovernanceDeniedError


def normalized_role_name(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 120:
        raise RoleValidationError
    return normalized


def find_role(roles: tuple[Role, ...], role_id: UUID) -> Role:
    for role in roles:
        if role.role_id == role_id:
            return role
    raise RoleNotFoundError


def role_facts(
    *,
    context: RequestContext,
    workspace_id: UUID,
    aggregate_id: UUID,
    aggregate_version: int,
    event_type: str,
    action: str,
    resource_type: str,
    occurred_at: datetime,
    role_version: int,
    attributes: dict[str, object],
) -> tuple[IntegrationEvent, AuditRecord]:
    return (
        IntegrationEvent(
            event_id=uuid4(),
            event_type=event_type,
            workspace_id=workspace_id,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload={"role_version": role_version},
        ),
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=aggregate_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            attributes={**attributes, "role_version": role_version},
        ),
    )


def next_role_version(repository: RoleRepository, workspace_id: UUID) -> int:
    current = repository.get_role_version(workspace_id, for_update=True)
    if current is None:
        raise RoleNotFoundError
    try:
        return repository.bump_role_version(workspace_id, current)
    except ValueError as error:
        raise RoleConflictError from error
