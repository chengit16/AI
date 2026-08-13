from datetime import UTC, datetime
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.organization_errors import (
    OrganizationConflictError,
    OrganizationGovernanceDeniedError,
    OrganizationNotFoundError,
)
from ai_platform_api.modules.identity.application.organization_support import (
    governance_account,
    organization_facts,
    require_active_enterprise,
    require_effective_department,
    require_owner,
    unique_ids,
    uuid_value,
)
from ai_platform_api.modules.identity.domain.organization import (
    OrganizationAssignment,
    OrganizationUnitOfWork,
    OrganizationWriteConflictError,
)


class MemberOrganizationService:
    def __init__(self, unit_of_work: OrganizationUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def assign(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
        department_ids: tuple[UUID, ...],
        primary_department_id: UUID | None,
        position_ids: tuple[UUID, ...],
    ) -> OrganizationAssignment:
        account_id = governance_account(context, workspace_id)
        normalized_departments = unique_ids(department_ids)
        normalized_positions = unique_ids(position_ids)
        if bool(normalized_departments) != (primary_department_id is not None) or (
            primary_department_id is not None
            and primary_department_id not in normalized_departments
        ):
            raise OrganizationConflictError
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                require_owner(unit_of_work.organization, workspace_id, account_id)
                membership = unit_of_work.organization.get_membership(
                    workspace_id, target_account_id, for_update=True
                )
                if membership is None or membership.status != "active":
                    raise OrganizationNotFoundError
                departments = unit_of_work.organization.list_departments(
                    workspace_id, for_update=True
                )
                for department_id in normalized_departments:
                    require_effective_department(departments, department_id)
                positions = unit_of_work.organization.list_positions(workspace_id, for_update=True)
                position_by_id = {position.position_id: position for position in positions}
                if any(
                    position_by_id.get(position_id) is None
                    or position_by_id[position_id].status != "active"
                    or position_by_id[position_id].department_id not in normalized_departments
                    for position_id in normalized_positions
                ):
                    raise OrganizationConflictError
                updated = unit_of_work.organization.replace_assignment(
                    workspace_id=workspace_id,
                    membership=membership,
                    department_ids=normalized_departments,
                    primary_department_id=primary_department_id,
                    position_ids=normalized_positions,
                    occurred_at=now,
                )
                unit_of_work.organization.bump_role_version(workspace_id)
                event, audit = organization_facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=membership.membership_id,
                    aggregate_version=updated.version,
                    event_type="organization.member.assigned",
                    action="organization.member.assign",
                    resource_type="workspace_membership",
                    occurred_at=now,
                    payload={
                        "department_ids": [str(value) for value in normalized_departments],
                        "primary_department_id": uuid_value(primary_department_id),
                        "position_ids": [str(value) for value in normalized_positions],
                    },
                    attributes={"target_account_id": str(target_account_id)},
                )
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except OrganizationWriteConflictError as error:
            raise OrganizationConflictError from error
        return OrganizationAssignment(
            target_account_id,
            normalized_departments,
            primary_department_id,
            normalized_positions,
            updated.version,
        )

    def get(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
    ) -> OrganizationAssignment:
        account_id = governance_account(context, workspace_id)
        with self._unit_of_work as unit_of_work:
            if context.authorized_permission_code is None:
                require_owner(unit_of_work.organization, workspace_id, account_id)
            else:
                require_active_enterprise(unit_of_work.organization, workspace_id, account_id)
                if (
                    not context.authorized_workspace
                    and target_account_id not in context.authorized_account_ids
                ):
                    raise OrganizationGovernanceDeniedError
            membership = unit_of_work.organization.get_membership(workspace_id, target_account_id)
            if membership is None or membership.status != "active":
                raise OrganizationNotFoundError
            return unit_of_work.organization.get_assignment(workspace_id, membership)
