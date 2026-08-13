from datetime import datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.organization_errors import (
    OrganizationConflictError,
    OrganizationGovernanceDeniedError,
    OrganizationNotFoundError,
    OrganizationValidationError,
)
from ai_platform_api.modules.identity.domain.organization import (
    Department,
    DepartmentSummary,
    OrganizationRepository,
    Position,
    PositionSummary,
    summarize_departments,
)


def normalized_name(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 120:
        raise OrganizationValidationError
    return normalized


def unique_ids(values: tuple[UUID, ...]) -> tuple[UUID, ...]:
    return tuple(sorted(set(values), key=lambda value: value.int))


def governance_account(context: RequestContext, workspace_id: UUID) -> UUID:
    if (
        context.user_id is None
        or context.authentication_method != "browser_session"
        or context.workspace_id != workspace_id
    ):
        raise OrganizationGovernanceDeniedError
    return context.user_id


def require_owner(
    repository: OrganizationRepository,
    workspace_id: UUID,
    account_id: UUID,
) -> None:
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
        raise OrganizationGovernanceDeniedError


def require_active_enterprise(
    repository: OrganizationRepository,
    workspace_id: UUID,
    account_id: UUID,
) -> None:
    workspace = repository.get_workspace(workspace_id)
    membership = repository.get_membership(workspace_id, account_id)
    if (
        workspace is None
        or workspace.workspace_type != "enterprise"
        or workspace.status != "active"
        or membership is None
        or membership.status != "active"
    ):
        raise OrganizationGovernanceDeniedError


def find_department(departments: tuple[Department, ...], department_id: UUID) -> Department:
    for department in departments:
        if department.department_id == department_id:
            return department
    raise OrganizationNotFoundError


def require_effective_department(departments: tuple[Department, ...], department_id: UUID) -> None:
    summaries = {item.department_id: item for item in summarize_departments(departments)}
    department = summaries.get(department_id)
    if department is None:
        raise OrganizationNotFoundError
    if not department.effective_active:
        raise OrganizationConflictError


def require_parent(departments: tuple[Department, ...], parent_department_id: UUID | None) -> None:
    if parent_department_id is not None:
        require_effective_department(departments, parent_department_id)


def require_unique_department_name(
    departments: tuple[Department, ...],
    *,
    parent_department_id: UUID | None,
    name: str,
) -> None:
    if any(
        item.parent_department_id == parent_department_id
        and item.name.casefold() == name.casefold()
        for item in departments
    ):
        raise OrganizationConflictError


def department_summary(
    departments: tuple[Department, ...], department_id: UUID
) -> DepartmentSummary:
    for summary in summarize_departments(departments):
        if summary.department_id == department_id:
            return summary
    raise OrganizationNotFoundError


def find_position(positions: tuple[Position, ...], position_id: UUID) -> Position:
    for position in positions:
        if position.position_id == position_id:
            return position
    raise OrganizationNotFoundError


def position_summary(
    position: Position,
    effective_departments: dict[UUID, bool],
) -> PositionSummary:
    return PositionSummary(
        position.position_id,
        position.department_id,
        position.name,
        position.status,
        position.status == "active" and effective_departments.get(position.department_id, False),
        position.version,
    )


def uuid_value(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


def organization_facts(
    *,
    context: RequestContext,
    workspace_id: UUID,
    aggregate_id: UUID,
    aggregate_version: int,
    event_type: str,
    action: str,
    resource_type: str,
    occurred_at: datetime,
    payload: dict[str, object],
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
            payload=payload,
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
            attributes=attributes,
        ),
    )
