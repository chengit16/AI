"""映射工作空间审计、用量和 Outbox 运营 HTTP 协议。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.api.dependencies import (
    entitlement_service,
    trusted_request_context,
)
from ai_platform_api.modules.identity.application.entitlements import (
    EntitlementService,
    UsageMetric,
)
from ai_platform_api.modules.integration.api.schemas import (
    AuditRecordPageResponse,
    AuditRecordResponse,
    IntegrationInspectionResponse,
    OutboxEventPageResponse,
    OutboxEventResponse,
    OutboxReplayBody,
    OutboxReplayResponse,
    UsageReconciliationListResponse,
    UsageReconciliationResponse,
    UsageRecordPageResponse,
    UsageRecordResponse,
)
from ai_platform_api.modules.integration.application.operations import (
    IntegrationOperationsService,
    OutboxStatus,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/operations",
    tags=["运营记录"],
)


def operations_service(request: Request) -> IntegrationOperationsService:
    """从组合根解析运营服务，Router 不直接持有数据库 Session。"""

    service = getattr(request.app.state, "integration_operations_service", None)
    if not isinstance(service, IntegrationOperationsService):
        raise RuntimeError("集成运营服务尚未完成装配")
    return service


@router.get(
    "/audit-records",
    response_model=AuditRecordPageResponse,
    operation_id="listOperationsAuditRecords",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_audit_records(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[IntegrationOperationsService, Depends(operations_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    cursor: UUID | None = None,
    actor_id: UUID | None = None,
    action: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    resource_type: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    outcome: Literal["succeeded", "denied", "failed"] | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> AuditRecordPageResponse:
    """按工作空间和可选筛选返回审计记录。"""

    page = service.list_audit_records(
        context,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        outcome=outcome,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    return AuditRecordPageResponse(
        items=[AuditRecordResponse.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/usage-records",
    response_model=UsageRecordPageResponse,
    operation_id="listOperationsUsageRecords",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_usage_records(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EntitlementService, Depends(entitlement_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    cursor: UUID | None = None,
    metric: UsageMetric | None = None,
    period_key: Annotated[
        str | None,
        Query(pattern=r"^(?:lifetime|[0-9]{4}-[0-9]{2})$"),
    ] = None,
) -> UsageRecordPageResponse:
    """按计量项和周期分页读取不可变用量明细。"""

    page = service.list_usage_records(
        context,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
        metric=metric,
        period_key=period_key,
    )
    return UsageRecordPageResponse(
        items=[UsageRecordResponse.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/usage-reconciliation",
    response_model=UsageReconciliationListResponse,
    operation_id="getOperationsUsageReconciliation",
    responses=error_responses(400, 401, 403, 422, 500),
)
def get_usage_reconciliation(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EntitlementService, Depends(entitlement_service)],
) -> UsageReconciliationListResponse:
    """对账当前计数器、明细累计和最后结果，不自动改写账本。"""

    return UsageReconciliationListResponse(
        items=[
            UsageReconciliationResponse.from_domain(item)
            for item in service.reconcile_usage(context, workspace_id=workspace_id)
        ]
    )


@router.get(
    "/outbox-events",
    response_model=OutboxEventPageResponse,
    operation_id="listOperationsOutboxEvents",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_outbox_events(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[IntegrationOperationsService, Depends(operations_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    cursor: UUID | None = None,
    event_status: Annotated[OutboxStatus | None, Query(alias="status")] = None,
    event_type: Annotated[str | None, Query(min_length=5, max_length=255)] = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> OutboxEventPageResponse:
    """分页返回 Outbox 追踪元数据，响应结构不包含 Payload。"""

    page = service.list_outbox_events(
        context,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
        status=event_status,
        event_type=event_type,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    return OutboxEventPageResponse(
        items=[OutboxEventResponse.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/integration-inspection",
    response_model=IntegrationInspectionResponse,
    operation_id="getOperationsIntegrationInspection",
    responses=error_responses(400, 401, 403, 422, 500),
)
def get_integration_inspection(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[IntegrationOperationsService, Depends(operations_service)],
) -> IntegrationInspectionResponse:
    """汇总 Outbox 积压、Schema 兼容和消费者幂等状态。"""

    return IntegrationInspectionResponse.from_domain(
        service.inspect(context, workspace_id=workspace_id)
    )


@router.post(
    "/outbox-events/{event_id}/replay",
    response_model=OutboxReplayResponse,
    operation_id="replayOperationsOutboxEvent",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def replay_outbox_event(
    workspace_id: UUID,
    event_id: UUID,
    body: OutboxReplayBody,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[IntegrationOperationsService, Depends(operations_service)],
) -> OutboxReplayResponse:
    """保留原事件 ID 重排已发布或死信事件，并记录不可变人工请求。"""

    return OutboxReplayResponse.from_domain(
        service.replay_outbox_event(
            context,
            workspace_id=workspace_id,
            event_id=event_id,
            idempotency_key=body.idempotency_key,
            reason_code=body.reason_code,
        )
    )
