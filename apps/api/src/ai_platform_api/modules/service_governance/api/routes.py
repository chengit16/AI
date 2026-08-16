"""映射服务列表、访问策略、灰度、晋级和回滚管理协议。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.service_governance.api.schemas import (
    CreateServiceRequest,
    PromoteServiceRouteRequest,
    RollbackServiceRouteRequest,
    ServiceAccessPolicyResponse,
    ServiceDeploymentResponse,
    ServiceListResponse,
    ServicePublicationResponse,
    ServiceResponse,
    ServiceRouteResponse,
    StartServiceCanaryRequest,
    UpdateServiceRequest,
)
from ai_platform_api.modules.service_governance.application.errors import ServiceDeniedError
from ai_platform_api.modules.service_governance.application.service import (
    ServiceDeployment,
    ServiceGovernanceService,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/services", tags=["服务管理"])


def service_governance_service(request: Request) -> ServiceGovernanceService:
    """从组合根解析服务治理用例，Router 不直接修改 Route 当前指针。"""

    service = getattr(request.app.state, "service_governance_service", None)
    if not isinstance(service, ServiceGovernanceService):
        raise RuntimeError("服务治理用例尚未完成装配")
    return service


@router.get(
    "",
    response_model=ServiceListResponse,
    operation_id="listServices",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_services(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ServiceGovernanceService, Depends(service_governance_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ServiceListResponse:
    """列出当前主体可管理的服务及当前路由。"""

    _require_workspace_path(context, workspace_id)
    return ServiceListResponse(
        items=[_deployment(item) for item in service.list_services(context, limit=limit)]
    )


@router.post(
    "",
    response_model=ServiceDeploymentResponse,
    operation_id="createService",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def create_service(
    workspace_id: UUID,
    body: CreateServiceRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ServiceGovernanceService, Depends(service_governance_service)],
) -> ServiceDeploymentResponse:
    """从有效自定义 Release 原子创建服务、策略、首个 Route 和当前指针。"""

    _require_workspace_path(context, workspace_id)
    return _deployment(
        service.create_service(
            context,
            name=body.name,
            release_id=body.release_id,
            service_type=body.service_type,
            visibility=body.visibility,
            allowed_department_ids=body.allowed_department_ids,
            allowed_account_ids=body.allowed_account_ids,
            idempotency_key=idempotency_key,
        )
    )


@router.put(
    "/{service_id}",
    response_model=ServiceDeploymentResponse,
    operation_id="updateService",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def update_service(
    workspace_id: UUID,
    service_id: UUID,
    body: UpdateServiceRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ServiceGovernanceService, Depends(service_governance_service)],
) -> ServiceDeploymentResponse:
    """更新服务定义或访问策略，旧策略和历史 Route 保持不可变。"""

    _require_workspace_path(context, workspace_id)
    return _deployment(
        service.update_service(
            context,
            service_id=service_id,
            expected_version=body.expected_version,
            name=body.name,
            target_status=body.target_status,
            visibility=body.visibility,
            allowed_department_ids=body.allowed_department_ids,
            allowed_account_ids=body.allowed_account_ids,
            idempotency_key=idempotency_key,
        )
    )


@router.post(
    "/{service_id}/routes/canary",
    response_model=ServiceDeploymentResponse,
    operation_id="startServiceCanary",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def start_service_canary(
    workspace_id: UUID,
    service_id: UUID,
    body: StartServiceCanaryRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ServiceGovernanceService, Depends(service_governance_service)],
) -> ServiceDeploymentResponse:
    """追加稳定百分比灰度 Route，不修改任何既有 Release。"""

    _require_workspace_path(context, workspace_id)
    return _deployment(
        service.start_canary(
            context,
            service_id=service_id,
            release_id=body.release_id,
            canary_percent=body.canary_percent,
            expected_generation=body.expected_generation,
            idempotency_key=idempotency_key,
        )
    )


@router.post(
    "/{service_id}/routes/promote",
    response_model=ServiceDeploymentResponse,
    operation_id="promoteServiceRoute",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def promote_service_route(
    workspace_id: UUID,
    service_id: UUID,
    body: PromoteServiceRouteRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ServiceGovernanceService, Depends(service_governance_service)],
) -> ServiceDeploymentResponse:
    """把指定 Release 原子晋级为唯一正式路由。"""

    _require_workspace_path(context, workspace_id)
    return _deployment(
        service.promote_route(
            context,
            service_id=service_id,
            release_id=body.release_id,
            expected_generation=body.expected_generation,
            idempotency_key=idempotency_key,
        )
    )


@router.post(
    "/{service_id}/routes/rollback",
    response_model=ServiceDeploymentResponse,
    operation_id="rollbackServiceRoute",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def rollback_service_route(
    workspace_id: UUID,
    service_id: UUID,
    body: RollbackServiceRouteRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ServiceGovernanceService, Depends(service_governance_service)],
) -> ServiceDeploymentResponse:
    """追加回滚 Route 并恢复最近稳定版本，当前 generation 竞争时失败关闭。"""

    _require_workspace_path(context, workspace_id)
    return _deployment(
        service.rollback_route(
            context,
            service_id=service_id,
            expected_generation=body.expected_generation,
            idempotency_key=idempotency_key,
        )
    )


def _require_workspace_path(context: RequestContext, workspace_id: UUID) -> None:
    if context.workspace_id != workspace_id:
        raise ServiceDeniedError


def _deployment(value: ServiceDeployment) -> ServiceDeploymentResponse:
    service = value.service
    policy = value.access_policy
    route = value.route
    publication = value.publication
    return ServiceDeploymentResponse(
        service=ServiceResponse(
            service_id=service.service_id,
            agent_id=service.agent_id,
            service_key=service.service_key,
            name=service.name,
            service_type=service.service_type,
            status=service.status,
            version=service.version,
            updated_at=service.updated_at,
        ),
        access_policy=ServiceAccessPolicyResponse(
            access_policy_version_id=policy.access_policy_version_id,
            version=policy.version,
            visibility=policy.visibility,
            allowed_department_ids=policy.allowed_department_ids,
            allowed_account_ids=policy.allowed_account_ids,
            policy_hash=policy.policy_hash,
        ),
        route=ServiceRouteResponse(
            route_id=route.route_id,
            route_version=route.route_version,
            route_mode=route.route_mode,
            primary_release_id=route.primary_release_id,
            canary_release_id=route.canary_release_id,
            canary_percent=route.canary_percent,
            previous_route_id=route.previous_route_id,
            route_hash=route.route_hash,
            created_at=route.created_at,
        ),
        publication=ServicePublicationResponse(
            route_id=publication.route_id,
            generation=publication.generation,
            published_at=publication.published_at,
        ),
    )
