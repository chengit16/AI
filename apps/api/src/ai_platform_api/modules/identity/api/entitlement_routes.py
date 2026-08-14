"""映射空间权益、功能开关和套餐用量治理 HTTP 协议。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.api.dependencies import (
    entitlement_service,
    trusted_request_context,
)
from ai_platform_api.modules.identity.api.entitlement_schemas import (
    EntitlementResponse,
    OpenApiFeatureRequest,
    QuotaResponse,
)
from ai_platform_api.modules.identity.application.entitlements import (
    EntitlementService,
    EntitlementSnapshot,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/entitlements", tags=["工作空间套餐"])


def _response(snapshot: EntitlementSnapshot) -> EntitlementResponse:
    return EntitlementResponse(
        workspace_id=snapshot.workspace_id,
        workspace_status=snapshot.workspace_status,
        plan_code=snapshot.plan_code,
        entitlement_version=snapshot.entitlement_version,
        open_api_allowed=snapshot.open_api_allowed,
        open_api_enabled=snapshot.open_api_enabled,
        public_publish_allowed=snapshot.public_publish_allowed,
        quotas=[
            QuotaResponse(
                metric=quota.metric,
                period_key=quota.period_key,
                used_value=quota.used_value,
                limit_value=quota.limit_value,
                remaining_value=quota.remaining_value,
            )
            for quota in snapshot.quotas
        ],
    )


@router.get(
    "",
    response_model=EntitlementResponse,
    operation_id="getWorkspaceEntitlement",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def get_entitlement(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EntitlementService, Depends(entitlement_service)],
) -> EntitlementResponse:
    """获取权益；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    return _response(service.get_snapshot(context, workspace_id=workspace_id))


@router.post(
    "/features/open-api",
    response_model=EntitlementResponse,
    operation_id="setWorkspaceOpenApiFeature",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def set_open_api_feature(
    workspace_id: UUID,
    body: OpenApiFeatureRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EntitlementService, Depends(entitlement_service)],
) -> EntitlementResponse:
    """设置开放API功能；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    return _response(
        service.set_open_api_enabled(
            context,
            workspace_id=workspace_id,
            enabled=body.enabled,
        )
    )
