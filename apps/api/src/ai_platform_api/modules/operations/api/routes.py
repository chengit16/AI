"""映射受控运营工作台读取和索引维护 HTTP 协议。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.operations.api.schemas import (
    IndexMaintenanceCommandBody,
    IndexMaintenanceRequestListResponse,
    IndexMaintenanceRequestResponse,
    IndexMaintenanceRunListResponse,
    IndexMaintenanceRunResponse,
    LifecycleOperationListResponse,
    LifecycleOperationResponse,
    OperationsIngestionJobListResponse,
    OperationsIngestionJobResponse,
    OperationsOverviewResponse,
)
from ai_platform_api.modules.operations.application.service import OperationsWorkbenchService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/operations/workbench",
    tags=["运营工作台"],
)


def operations_workbench_service(request: Request) -> OperationsWorkbenchService:
    """从组合根解析运营工作台服务，Router 不直接跨模块查询数据库。"""

    service = getattr(request.app.state, "operations_workbench_service", None)
    if not isinstance(service, OperationsWorkbenchService):
        raise RuntimeError("运营工作台服务尚未完成装配")
    return service


@router.get(
    "/overview",
    response_model=OperationsOverviewResponse,
    operation_id="getOperationsWorkbenchOverview",
    responses=error_responses(400, 401, 403, 422, 500),
)
def get_overview(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[OperationsWorkbenchService, Depends(operations_workbench_service)],
) -> OperationsOverviewResponse:
    """返回任务、索引、Outbox 和生命周期的安全聚合指标。"""

    return OperationsOverviewResponse.from_domain(
        service.overview(context, workspace_id=workspace_id)
    )


@router.get(
    "/ingestion-jobs",
    response_model=OperationsIngestionJobListResponse,
    operation_id="listOperationsWorkbenchIngestionJobs",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_ingestion_jobs(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[OperationsWorkbenchService, Depends(operations_workbench_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> OperationsIngestionJobListResponse:
    """跨知识库列出当前工作空间最近的入库任务。"""

    return OperationsIngestionJobListResponse(
        items=[
            OperationsIngestionJobResponse.from_domain(item)
            for item in service.list_ingestion_jobs(
                context,
                workspace_id=workspace_id,
                limit=limit,
            )
        ]
    )


@router.get(
    "/index-maintenance/requests",
    response_model=IndexMaintenanceRequestListResponse,
    operation_id="listOperationsIndexMaintenanceRequests",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_index_requests(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[OperationsWorkbenchService, Depends(operations_workbench_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> IndexMaintenanceRequestListResponse:
    """列出人工索引维护请求和 Worker 恢复状态。"""

    return IndexMaintenanceRequestListResponse(
        items=[
            IndexMaintenanceRequestResponse.from_domain(item)
            for item in service.list_index_requests(
                context,
                workspace_id=workspace_id,
                limit=limit,
            )
        ]
    )


@router.get(
    "/index-maintenance/runs",
    response_model=IndexMaintenanceRunListResponse,
    operation_id="listOperationsIndexMaintenanceRuns",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_index_runs(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[OperationsWorkbenchService, Depends(operations_workbench_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> IndexMaintenanceRunListResponse:
    """列出已完成的索引维护结果和可复算摘要。"""

    return IndexMaintenanceRunListResponse(
        items=[
            IndexMaintenanceRunResponse.from_domain(item)
            for item in service.list_index_runs(
                context,
                workspace_id=workspace_id,
                limit=limit,
            )
        ]
    )


@router.get(
    "/lifecycle-operations",
    response_model=LifecycleOperationListResponse,
    operation_id="listOperationsLifecycleHistory",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_lifecycle_operations(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[OperationsWorkbenchService, Depends(operations_workbench_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> LifecycleOperationListResponse:
    """列出导出、清除和保留期运行的统一历史摘要。"""

    return LifecycleOperationListResponse(
        items=[
            LifecycleOperationResponse.from_domain(item)
            for item in service.list_lifecycle_operations(
                context,
                workspace_id=workspace_id,
                limit=limit,
            )
        ]
    )


def _request_index_maintenance(
    command: Literal["inspection", "full_rebuild", "cleanup"],
    workspace_id: UUID,
    body: IndexMaintenanceCommandBody,
    context: RequestContext,
    service: OperationsWorkbenchService,
    idempotency_key: str,
) -> IndexMaintenanceRequestResponse:
    """复用三类静态授权路由的命令转换，权限仍由各自 API 注册项决定。"""

    return IndexMaintenanceRequestResponse.from_domain(
        service.request_index_maintenance(
            context,
            workspace_id=workspace_id,
            command=command,
            idempotency_key=idempotency_key,
            reason_code=body.reason_code,
            confirmation=body.confirmation,
        )
    )


@router.post(
    "/index-maintenance/inspections",
    response_model=IndexMaintenanceRequestResponse,
    operation_id="requestOperationsIndexInspection",
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
def request_index_inspection(
    workspace_id: UUID,
    body: IndexMaintenanceCommandBody,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[OperationsWorkbenchService, Depends(operations_workbench_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> IndexMaintenanceRequestResponse:
    """登记一次会修复安全候选或排队重建的工作空间索引巡检。"""

    return _request_index_maintenance(
        "inspection", workspace_id, body, context, service, idempotency_key
    )


@router.post(
    "/index-maintenance/rebuilds",
    response_model=IndexMaintenanceRequestResponse,
    operation_id="requestOperationsIndexRebuild",
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
def request_index_rebuild(
    workspace_id: UUID,
    body: IndexMaintenanceCommandBody,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[OperationsWorkbenchService, Depends(operations_workbench_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> IndexMaintenanceRequestResponse:
    """登记一次从当前发布事实创建新索引构建的命令。"""

    return _request_index_maintenance(
        "full_rebuild", workspace_id, body, context, service, idempotency_key
    )


@router.post(
    "/index-maintenance/cleanups",
    response_model=IndexMaintenanceRequestResponse,
    operation_id="requestOperationsIndexCleanup",
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
def request_index_cleanup(
    workspace_id: UUID,
    body: IndexMaintenanceCommandBody,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[OperationsWorkbenchService, Depends(operations_workbench_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> IndexMaintenanceRequestResponse:
    """登记一次只删除不可恢复且不可见 Chunk 的清理命令。"""

    return _request_index_maintenance(
        "cleanup", workspace_id, body, context, service, idempotency_key
    )
