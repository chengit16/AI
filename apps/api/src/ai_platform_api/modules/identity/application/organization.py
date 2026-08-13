from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.organization_departments import (
    DepartmentService,
)
from ai_platform_api.modules.identity.application.organization_errors import (
    OrganizationConflictError,
    OrganizationGovernanceDeniedError,
    OrganizationNotFoundError,
    OrganizationValidationError,
)
from ai_platform_api.modules.identity.application.organization_members import (
    MemberOrganizationService,
)
from ai_platform_api.modules.identity.application.organization_positions import PositionService
from ai_platform_api.modules.identity.application.organization_support import governance_account
from ai_platform_api.modules.identity.domain.organization import (
    DepartmentSummary,
    OrganizationAssignment,
    OrganizationUnitOfWork,
    PositionSummary,
)

__all__ = [
    "DepartmentSummary",
    "OrganizationAssignment",
    "OrganizationConflictError",
    "OrganizationGovernanceDeniedError",
    "OrganizationNotFoundError",
    "OrganizationService",
    "OrganizationValidationError",
    "PositionSummary",
]


class OrganizationService:
    """为 Router 提供稳定入口，具体事务按部门、岗位和成员归属分治。"""

    def __init__(self, unit_of_work: OrganizationUnitOfWork) -> None:
        self._departments = DepartmentService(unit_of_work)
        self._positions = PositionService(unit_of_work)
        self._members = MemberOrganizationService(unit_of_work)

    def create_department(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        name: str,
        parent_department_id: UUID | None,
    ) -> DepartmentSummary:
        return self._departments.create(
            context,
            workspace_id=workspace_id,
            name=name,
            parent_department_id=parent_department_id,
        )

    def move_department(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        department_id: UUID,
        parent_department_id: UUID | None,
    ) -> DepartmentSummary:
        return self._departments.move(
            context,
            workspace_id=workspace_id,
            department_id=department_id,
            parent_department_id=parent_department_id,
        )

    def set_department_status(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        department_id: UUID,
        active: bool,
    ) -> DepartmentSummary:
        return self._departments.set_status(
            context,
            workspace_id=workspace_id,
            department_id=department_id,
            active=active,
        )

    def list_departments(
        self, context: RequestContext, *, workspace_id: UUID
    ) -> tuple[DepartmentSummary, ...]:
        return self._departments.list(context, workspace_id=workspace_id)

    def create_position(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        department_id: UUID,
        name: str,
    ) -> PositionSummary:
        return self._positions.create(
            context,
            workspace_id=workspace_id,
            department_id=department_id,
            name=name,
        )

    def set_position_status(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        position_id: UUID,
        active: bool,
    ) -> PositionSummary:
        return self._positions.set_status(
            context,
            workspace_id=workspace_id,
            position_id=position_id,
            active=active,
        )

    def list_positions(
        self, context: RequestContext, *, workspace_id: UUID
    ) -> tuple[PositionSummary, ...]:
        return self._positions.list(context, workspace_id=workspace_id)

    def assign_member(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
        department_ids: tuple[UUID, ...],
        primary_department_id: UUID | None,
        position_ids: tuple[UUID, ...],
    ) -> OrganizationAssignment:
        return self._members.assign(
            context,
            workspace_id=workspace_id,
            target_account_id=target_account_id,
            department_ids=department_ids,
            primary_department_id=primary_department_id,
            position_ids=position_ids,
        )

    def get_assignment(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
    ) -> OrganizationAssignment:
        return self._members.get(
            context,
            workspace_id=workspace_id,
            target_account_id=target_account_id,
        )

    _governance_account = staticmethod(governance_account)
