from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.organization_errors import (
    OrganizationConflictError,
)
from ai_platform_api.modules.identity.application.organization_support import (
    find_position,
    governance_account,
    normalized_name,
    organization_facts,
    position_summary,
    require_effective_department,
    require_owner,
)
from ai_platform_api.modules.identity.domain.organization import (
    InvalidOrganizationTransitionError,
    OrganizationUnitOfWork,
    OrganizationWriteConflictError,
    Position,
    PositionSummary,
    summarize_departments,
)


class PositionService:
    def __init__(self, unit_of_work: OrganizationUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def create(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        department_id: UUID,
        name: str,
    ) -> PositionSummary:
        account_id = governance_account(context, workspace_id)
        now = datetime.now(UTC)
        position = Position(
            uuid4(), workspace_id, department_id, normalized_name(name), "active", now, now, 1
        )
        try:
            with self._unit_of_work as unit_of_work:
                require_owner(unit_of_work.organization, workspace_id, account_id)
                departments = unit_of_work.organization.list_departments(
                    workspace_id, for_update=True
                )
                require_effective_department(departments, department_id)
                current_positions = unit_of_work.organization.list_positions(
                    workspace_id, for_update=True
                )
                if any(
                    item.department_id == department_id
                    and item.name.casefold() == position.name.casefold()
                    for item in current_positions
                ):
                    raise OrganizationConflictError
                event, audit = organization_facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=position.position_id,
                    aggregate_version=1,
                    event_type="organization.position.created",
                    action="organization.position.create",
                    resource_type="position",
                    occurred_at=now,
                    payload={"department_id": str(department_id)},
                    attributes={"status": position.status},
                )
                unit_of_work.organization.add_position(position)
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except OrganizationWriteConflictError as error:
            raise OrganizationConflictError from error
        return PositionSummary(
            position.position_id,
            department_id,
            position.name,
            "active",
            True,
            1,
        )

    def set_status(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        position_id: UUID,
        active: bool,
    ) -> PositionSummary:
        account_id = governance_account(context, workspace_id)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                require_owner(unit_of_work.organization, workspace_id, account_id)
                departments = unit_of_work.organization.list_departments(workspace_id)
                positions = unit_of_work.organization.list_positions(workspace_id, for_update=True)
                current = find_position(positions, position_id)
                updated = (
                    current.activate(occurred_at=now)
                    if active
                    else current.disable(occurred_at=now)
                )
                event, audit = organization_facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=position_id,
                    aggregate_version=updated.version,
                    event_type=f"organization.position.{updated.status}",
                    action="organization.position.status.update",
                    resource_type="position",
                    occurred_at=now,
                    payload={"status": updated.status},
                    attributes={"previous_status": current.status},
                )
                unit_of_work.organization.save_position(updated)
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except (InvalidOrganizationTransitionError, OrganizationWriteConflictError) as error:
            raise OrganizationConflictError from error
        effective_departments = {
            item.department_id: item.effective_active for item in summarize_departments(departments)
        }
        return position_summary(updated, effective_departments)

    def list(self, context: RequestContext, *, workspace_id: UUID) -> tuple[PositionSummary, ...]:
        account_id = governance_account(context, workspace_id)
        with self._unit_of_work as unit_of_work:
            require_owner(unit_of_work.organization, workspace_id, account_id)
            departments = unit_of_work.organization.list_departments(workspace_id)
            positions = unit_of_work.organization.list_positions(workspace_id)
        effective_departments = {
            item.department_id: item.effective_active for item in summarize_departments(departments)
        }
        return tuple(position_summary(item, effective_departments) for item in positions)
