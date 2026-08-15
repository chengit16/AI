"""映射工作空间导出、业务数据清除和保留期执行 HTTP 协议。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.lifecycle.api.schemas import (
    LifecycleExportResponse,
    LifecyclePurgeBody,
    LifecyclePurgeResponse,
    RetentionRunResponse,
)
from ai_platform_api.modules.lifecycle.application.service import WorkspaceLifecycleService

router = APIRouter(prefix="/workspaces/{workspace_id}/lifecycle", tags=["数据生命周期"])


def lifecycle_service(request: Request) -> WorkspaceLifecycleService:
    """从组合根解析生命周期服务，Router 不直接操作多种存储。"""

    service = getattr(request.app.state, "workspace_lifecycle_service", None)
    if not isinstance(service, WorkspaceLifecycleService):
        raise RuntimeError("工作空间生命周期服务尚未完成装配")
    return service


@router.post(
    "/exports",
    response_model=LifecycleExportResponse,
    operation_id="createWorkspaceLifecycleExport",
    responses=error_responses(400, 401, 403, 409, 422, 503),
)
def create_export(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[WorkspaceLifecycleService, Depends(lifecycle_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> LifecycleExportResponse:
    """同步生成版本化导出包并返回可复算摘要。"""

    return LifecycleExportResponse.from_domain(
        service.export_workspace(
            context,
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
        )
    )


@router.post(
    "/purges",
    response_model=LifecyclePurgeResponse,
    operation_id="purgeWorkspaceBusinessData",
    responses=error_responses(400, 401, 403, 404, 409, 422, 503),
)
def purge_business_data(
    workspace_id: UUID,
    body: LifecyclePurgeBody,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[WorkspaceLifecycleService, Depends(lifecycle_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> LifecyclePurgeResponse:
    """保留可登录治理壳层，并清除业务数据与派生介质。"""

    purge, certificate = service.purge_workspace_business_data(
        context,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        confirmed_workspace_name=body.confirmed_workspace_name,
        reason_code=body.reason_code,
    )
    return LifecyclePurgeResponse.from_domain(purge, certificate)


@router.post(
    "/retention-runs",
    response_model=RetentionRunResponse,
    operation_id="executeWorkspaceRetention",
    responses=error_responses(400, 401, 403, 409, 422, 503),
)
def execute_retention(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[WorkspaceLifecycleService, Depends(lifecycle_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> RetentionRunResponse:
    """执行冻结保留期并返回各事实表的删除计数。"""

    return RetentionRunResponse.from_domain(
        service.execute_retention(
            context,
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
        )
    )
