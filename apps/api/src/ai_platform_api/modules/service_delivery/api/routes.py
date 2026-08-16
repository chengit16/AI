"""映射三类已发布服务共享的创建、HTTP 快照和可恢复 SSE 协议。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request, status
from fastapi.responses import StreamingResponse

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.config import Settings, get_settings
from ai_platform_api.modules.assistant.api.routes import (
    assistant_run_executor,
    initial_stream_replay,
    stream_run_frames,
    streaming_service,
)
from ai_platform_api.modules.assistant.api.schemas import (
    AssistantRunResponse,
    MessagePartResponse,
    MessageResponse,
)
from ai_platform_api.modules.assistant.application.runner import AssistantRunExecutor
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.service_delivery.api.schemas import (
    InvokePublishedServiceRequest,
    PublishedServiceInvocationResponse,
)
from ai_platform_api.modules.service_delivery.application.errors import (
    ServiceInvocationDeniedError,
)
from ai_platform_api.modules.service_delivery.application.service import (
    ServiceInvocationMessageView,
    ServiceInvocationRunView,
    ServiceInvocationService,
    ServiceInvocationView,
    invocation_view,
)
from ai_platform_api.modules.streaming.application.service import TransactionalStreamService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/services/{service_id}/invocations",
    tags=["服务调用"],
)


def service_invocation_service(request: Request) -> ServiceInvocationService:
    """从应用容器解析统一服务调用用例，Router 不直接访问发布或用量表。"""

    service = getattr(request.app.state, "service_invocation_service", None)
    if not isinstance(service, ServiceInvocationService):
        raise RuntimeError("服务调用用例尚未完成装配")
    return service


@router.post(
    "",
    response_model=PublishedServiceInvocationResponse,
    operation_id="invokePublishedService",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 422, 429, 500, 503),
)
def invoke_published_service(
    workspace_id: UUID,
    service_id: UUID,
    body: InvokePublishedServiceRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ServiceInvocationService, Depends(service_invocation_service)],
    executor: Annotated[AssistantRunExecutor, Depends(assistant_run_executor)],
) -> PublishedServiceInvocationResponse:
    """创建已发布服务 Run；响应后三类出口均由同一执行器消费。"""

    _require_workspace_path(context, workspace_id)
    submission = service.invoke(
        context,
        service_id=service_id,
        texts=tuple(part.text for part in body.parts),
        idempotency_key=idempotency_key,
    )
    background_tasks.add_task(executor.execute, context, submission.run.run_id)
    return _response(invocation_view(submission), workspace_id, service_id)


@router.get(
    "/{run_id}",
    response_model=PublishedServiceInvocationResponse,
    operation_id="getPublishedServiceInvocation",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def get_published_service_invocation(
    workspace_id: UUID,
    service_id: UUID,
    run_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ServiceInvocationService, Depends(service_invocation_service)],
) -> PublishedServiceInvocationResponse:
    """读取当前 Actor 的调用快照，HTTP 轮询与 SSE 观察同一 Run。"""

    _require_workspace_path(context, workspace_id)
    return _response(
        invocation_view(
            service.get_invocation(context, service_id=service_id, run_id=run_id),
        ),
        workspace_id,
        service_id,
    )


@router.get(
    "/{run_id}/events",
    operation_id="streamPublishedServiceInvocationEvents",
    response_class=StreamingResponse,
    responses={
        **error_responses(400, 401, 403, 404, 409, 410, 422, 500),
        200: {
            "description": "按严格序号返回可恢复的已发布服务运行事件",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
    },
)
def stream_published_service_invocation_events(
    workspace_id: UUID,
    service_id: UUID,
    run_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ServiceInvocationService, Depends(service_invocation_service)],
    streams: Annotated[TransactionalStreamService, Depends(streaming_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    last_event_id: Annotated[UUID | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """按调用 Actor 授权后回放事件，重连不会创建 Run 或再次调用模型。"""

    _require_workspace_path(context, workspace_id)
    run = service.get_run(context, service_id=service_id, run_id=run_id)
    replay = initial_stream_replay(streams, workspace_id, run, last_event_id)
    return StreamingResponse(
        stream_run_frames(
            streams,
            context,
            run,
            lambda: service.get_run(context, service_id=service_id, run_id=run_id),
            last_event_id,
            replay,
            heartbeat_seconds=settings.stream_heartbeat_seconds,
            poll_interval_ms=settings.stream_poll_interval_ms,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _response(
    invocation: ServiceInvocationView,
    workspace_id: UUID,
    service_id: UUID,
) -> PublishedServiceInvocationResponse:
    run = invocation.run
    return PublishedServiceInvocationResponse(
        input_message=_message(invocation.input_message),
        output_message=(
            _message(invocation.output_message) if invocation.output_message is not None else None
        ),
        run=_run(run),
        event_stream_path=(
            f"/api/v1/workspaces/{workspace_id}/services/{service_id}/"
            f"invocations/{run.run_id}/events"
        ),
    )


def _message(value: ServiceInvocationMessageView) -> MessageResponse:
    return MessageResponse(
        message_id=value.message_id,
        workspace_id=value.workspace_id,
        conversation_id=value.conversation_id,
        role=value.role,
        status=value.status,
        parts=[
            MessagePartResponse(
                part_id=part.part_id,
                sequence_no=part.sequence_no,
                type=part.part_type,
                text=part.text,
            )
            for part in value.parts
        ],
        created_by_account_id=value.created_by_account_id,
        created_at=value.created_at,
        updated_at=value.updated_at,
        version=value.version,
    )


def _run(value: ServiceInvocationRunView) -> AssistantRunResponse:
    return AssistantRunResponse(
        run_id=value.run_id,
        workspace_id=value.workspace_id,
        conversation_id=value.conversation_id,
        user_message_id=value.user_message_id,
        assistant_message_id=value.assistant_message_id,
        service_id=value.service_id,
        service_route_id=value.service_route_id,
        service_route_version=value.service_route_version,
        agent_release_id=value.agent_release_id,
        runtime_config_version_id=value.runtime_config_version_id,
        status=value.status,
        trace_id=value.trace_id,
        created_at=value.created_at,
        updated_at=value.updated_at,
        completed_at=value.completed_at,
        error_code=value.error_code,
    )


def _require_workspace_path(context: RequestContext, workspace_id: UUID) -> None:
    if context.workspace_id != workspace_id:
        raise ServiceInvocationDeniedError
