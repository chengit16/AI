"""映射 AgentRelease 运营报告的只读工作空间接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_operations.api.schemas import AgentOperationsReportResponse
from ai_platform_api.modules.agent_operations.application.service import AgentOperationsService
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context

router = APIRouter(
    prefix="/workspaces/{workspace_id}/agent-release-operations",
    tags=["Agent Release 运营"],
)


def agent_operations_service(request: Request) -> AgentOperationsService:
    """从组合根解析运营服务，Router 不直接连接运行事实表。"""

    service = getattr(request.app.state, "agent_operations_service", None)
    if not isinstance(service, AgentOperationsService):
        raise RuntimeError("Agent Release 运营服务尚未完成装配")
    return service


@router.get(
    "",
    response_model=AgentOperationsReportResponse,
    operation_id="getAgentReleaseOperations",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def get_agent_release_operations(
    workspace_id: UUID,
    service_id: Annotated[UUID, Query()],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AgentOperationsService, Depends(agent_operations_service)],
    window_hours: Annotated[int, Query(ge=1, le=168)] = 24,
) -> AgentOperationsReportResponse:
    """按服务和时间窗口返回主版本与灰度或上一版本的脱敏对比。"""

    return AgentOperationsReportResponse.from_domain(
        service.get_report(
            context,
            workspace_id=workspace_id,
            service_id=service_id,
            window_hours=window_hours,
        )
    )
