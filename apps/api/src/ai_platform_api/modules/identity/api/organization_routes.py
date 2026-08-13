from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.api.dependencies import (
    organization_service,
    trusted_request_context,
)
from ai_platform_api.modules.identity.api.organization_schemas import (
    AssignMemberOrganizationRequest,
    CreateDepartmentRequest,
    CreatePositionRequest,
    DepartmentListResponse,
    DepartmentResponse,
    MemberOrganizationResponse,
    MoveDepartmentRequest,
    OrganizationStatusRequest,
    PositionListResponse,
    PositionResponse,
)
from ai_platform_api.modules.identity.application.organization import (
    DepartmentSummary,
    OrganizationAssignment,
    OrganizationService,
    PositionSummary,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/organization", tags=["企业组织"])


def _department_response(department: DepartmentSummary) -> DepartmentResponse:
    return DepartmentResponse(
        department_id=department.department_id,
        parent_department_id=department.parent_department_id,
        name=department.name,
        status=department.status,
        effective_active=department.effective_active,
        depth=department.depth,
        version=department.version,
    )


def _position_response(position: PositionSummary) -> PositionResponse:
    return PositionResponse(
        position_id=position.position_id,
        department_id=position.department_id,
        name=position.name,
        status=position.status,
        effective_active=position.effective_active,
        version=position.version,
    )


def _assignment_response(assignment: OrganizationAssignment) -> MemberOrganizationResponse:
    return MemberOrganizationResponse(
        account_id=assignment.account_id,
        department_ids=list(assignment.department_ids),
        primary_department_id=assignment.primary_department_id,
        position_ids=list(assignment.position_ids),
        membership_version=assignment.membership_version,
    )


@router.post(
    "/departments",
    response_model=DepartmentResponse,
    operation_id="createEnterpriseDepartment",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def create_department(
    workspace_id: UUID,
    body: CreateDepartmentRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[OrganizationService, Depends(organization_service)],
) -> DepartmentResponse:
    return _department_response(
        service.create_department(
            context,
            workspace_id=workspace_id,
            name=body.name,
            parent_department_id=body.parent_department_id,
        )
    )


@router.get(
    "/departments",
    response_model=DepartmentListResponse,
    operation_id="listEnterpriseDepartments",
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
def list_departments(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[OrganizationService, Depends(organization_service)],
) -> DepartmentListResponse:
    return DepartmentListResponse(
        items=[
            _department_response(item)
            for item in service.list_departments(context, workspace_id=workspace_id)
        ]
    )


@router.post(
    "/departments/{department_id}/move",
    response_model=DepartmentResponse,
    operation_id="moveEnterpriseDepartment",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def move_department(
    workspace_id: UUID,
    department_id: UUID,
    body: MoveDepartmentRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[OrganizationService, Depends(organization_service)],
) -> DepartmentResponse:
    return _department_response(
        service.move_department(
            context,
            workspace_id=workspace_id,
            department_id=department_id,
            parent_department_id=body.parent_department_id,
        )
    )


@router.post(
    "/departments/{department_id}/status",
    response_model=DepartmentResponse,
    operation_id="setEnterpriseDepartmentStatus",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def set_department_status(
    workspace_id: UUID,
    department_id: UUID,
    body: OrganizationStatusRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[OrganizationService, Depends(organization_service)],
) -> DepartmentResponse:
    return _department_response(
        service.set_department_status(
            context,
            workspace_id=workspace_id,
            department_id=department_id,
            active=body.active,
        )
    )


@router.post(
    "/positions",
    response_model=PositionResponse,
    operation_id="createEnterprisePosition",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def create_position(
    workspace_id: UUID,
    body: CreatePositionRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[OrganizationService, Depends(organization_service)],
) -> PositionResponse:
    return _position_response(
        service.create_position(
            context,
            workspace_id=workspace_id,
            department_id=body.department_id,
            name=body.name,
        )
    )


@router.get(
    "/positions",
    response_model=PositionListResponse,
    operation_id="listEnterprisePositions",
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
def list_positions(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[OrganizationService, Depends(organization_service)],
) -> PositionListResponse:
    return PositionListResponse(
        items=[
            _position_response(item)
            for item in service.list_positions(context, workspace_id=workspace_id)
        ]
    )


@router.post(
    "/positions/{position_id}/status",
    response_model=PositionResponse,
    operation_id="setEnterprisePositionStatus",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def set_position_status(
    workspace_id: UUID,
    position_id: UUID,
    body: OrganizationStatusRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[OrganizationService, Depends(organization_service)],
) -> PositionResponse:
    return _position_response(
        service.set_position_status(
            context,
            workspace_id=workspace_id,
            position_id=position_id,
            active=body.active,
        )
    )


@router.put(
    "/members/{account_id}",
    response_model=MemberOrganizationResponse,
    operation_id="assignEnterpriseMemberOrganization",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def assign_member_organization(
    workspace_id: UUID,
    account_id: UUID,
    body: AssignMemberOrganizationRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[OrganizationService, Depends(organization_service)],
) -> MemberOrganizationResponse:
    return _assignment_response(
        service.assign_member(
            context,
            workspace_id=workspace_id,
            target_account_id=account_id,
            department_ids=tuple(body.department_ids),
            primary_department_id=body.primary_department_id,
            position_ids=tuple(body.position_ids),
        )
    )


@router.get(
    "/members/{account_id}",
    response_model=MemberOrganizationResponse,
    operation_id="getEnterpriseMemberOrganization",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def get_member_organization(
    workspace_id: UUID,
    account_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[OrganizationService, Depends(organization_service)],
) -> MemberOrganizationResponse:
    return _assignment_response(
        service.get_assignment(
            context,
            workspace_id=workspace_id,
            target_account_id=account_id,
        )
    )
