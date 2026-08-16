"""映射工具目录、任务控制、确认审批与可恢复 SSE 协议。"""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import StreamingResponse

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.config import Settings, get_settings
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.tool_execution.api.schemas import (
    CreateToolRunRequest,
    ToolCatalogItemResponse,
    ToolCatalogResponse,
    ToolConfirmationActionRequest,
    ToolProgressEventResponse,
    ToolRunDetailResponse,
    ToolRunListResponse,
    ToolRunSummaryResponse,
)
from ai_platform_api.modules.tool_execution.application.catalog import ToolCatalogService
from ai_platform_api.modules.tool_execution.application.confirmations import (
    ToolCallBinding,
    ToolConfirmationService,
)
from ai_platform_api.modules.tool_execution.application.console import (
    ToolConfirmationView,
    ToolConsoleService,
    ToolRunDetail,
    ToolStepView,
)
from ai_platform_api.modules.tool_execution.application.errors import (
    ToolExecutionDeniedError,
    ToolRunConflictError,
)
from ai_platform_api.modules.tool_execution.application.planning import (
    ToolExecutionPlanningService,
    parse_candidate_tool_intents,
)
from ai_platform_api.modules.tool_execution.application.results import (
    ToolProgressPage,
    ToolProgressService,
)
from ai_platform_api.modules.tool_execution.application.tasks import ToolRunBudget, ToolTaskService
from ai_platform_api.modules.workflow.application.approval_runtime import (
    ApprovalInstanceService,
    ApprovalRuntimeCommand,
)

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["工具任务控制台"])
RUN_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "timed_out"})


def tool_catalog_service(request: Request) -> ToolCatalogService:
    """从组合根解析工具目录服务。"""

    service = getattr(request.app.state, "tool_catalog_service", None)
    if not isinstance(service, ToolCatalogService):
        raise RuntimeError("工具目录服务尚未完成装配")
    return service


def tool_task_service(request: Request) -> ToolTaskService:
    """从组合根解析工具任务唯一写入服务。"""

    service = getattr(request.app.state, "tool_task_service", None)
    if not isinstance(service, ToolTaskService):
        raise RuntimeError("工具任务服务尚未完成装配")
    return service


def tool_planning_service(request: Request) -> ToolExecutionPlanningService:
    """从组合根解析工具计划冻结服务。"""

    service = getattr(request.app.state, "tool_planning_service", None)
    if not isinstance(service, ToolExecutionPlanningService):
        raise RuntimeError("工具计划服务尚未完成装配")
    return service


def tool_console_service(request: Request) -> ToolConsoleService:
    """从组合根解析脱敏控制台投影服务。"""

    service = getattr(request.app.state, "tool_console_service", None)
    if not isinstance(service, ToolConsoleService):
        raise RuntimeError("工具控制台服务尚未完成装配")
    return service


def tool_progress_service(request: Request) -> ToolProgressService:
    """从组合根解析持久化进度回放服务。"""

    service = getattr(request.app.state, "tool_progress_service", None)
    if not isinstance(service, ToolProgressService):
        raise RuntimeError("工具进度服务尚未完成装配")
    return service


def tool_confirmation_service(request: Request) -> ToolConfirmationService:
    """从组合根解析批准后的当前策略复核服务。"""

    service = getattr(request.app.state, "tool_confirmation_service", None)
    if not isinstance(service, ToolConfirmationService):
        raise RuntimeError("工具确认服务尚未完成装配")
    return service


def approval_instance_service(request: Request) -> ApprovalInstanceService:
    """从组合根解析通用审批引擎，工具接口不建立第二套审批。"""

    service = getattr(request.app.state, "approval_instance_service", None)
    if not isinstance(service, ApprovalInstanceService):
        raise RuntimeError("审批运行服务尚未完成装配")
    return service


@router.get(
    "/tools",
    response_model=ToolCatalogResponse,
    operation_id="listAvailableTools",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_available_tools(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ToolCatalogService, Depends(tool_catalog_service)],
) -> ToolCatalogResponse:
    """返回平台注册、套餐和当前工具权限三层交集。"""

    _require_workspace_path(context, workspace_id)
    return ToolCatalogResponse(
        items=[
            ToolCatalogItemResponse.from_domain(item)
            for item in service.list_available_tools(context, workspace_id=workspace_id)
        ]
    )


@router.post(
    "/tool-runs",
    response_model=ToolRunDetailResponse,
    operation_id="createToolRun",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def create_tool_run(
    workspace_id: UUID,
    body: CreateToolRunRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    tasks: Annotated[ToolTaskService, Depends(tool_task_service)],
    planning: Annotated[ToolExecutionPlanningService, Depends(tool_planning_service)],
    console: Annotated[ToolConsoleService, Depends(tool_console_service)],
) -> ToolRunDetailResponse:
    """幂等创建 Run 并冻结完整只读计划，失败时关闭已创建的空 Run。"""

    # 1. 路径工作空间和幂等创建先收敛为 Run 事实，客户端预算只能在平台上限内收窄。
    _require_workspace_path(context, workspace_id)
    now = datetime.now(UTC)
    run = tasks.create_run(
        context,
        service_id=body.service_id,
        agent_release_id=body.agent_release_id,
        idempotency_key=idempotency_key,
        budget=ToolRunBudget(
            max_steps=len(body.tool_calls),
            max_attempts_per_step=body.max_attempts_per_step,
            max_execution_seconds=body.max_execution_seconds,
            max_cost_microunits=0,
        ),
        created_at=now,
    )
    # 2. 计划冻结跨独立事务执行；失败时必须尽力关闭空 Run，避免留下可误判为可执行的任务。
    if run.state == "pending":
        try:
            planning.freeze(
                context,
                run.run_id,
                parse_candidate_tool_intents(
                    {"tool_calls": [item.model_dump(mode="json") for item in body.tool_calls]}
                ),
                evaluated_at=now,
            )
        except Exception:
            # Run 创建和计划预检跨事务；任意失败都尽力把空 Run 收口，不能伪装为原子提交。
            with suppress(Exception):
                tasks.request_cancellation(context, run.run_id, requested_at=datetime.now(UTC))
            raise
    # 3. 从脱敏投影回读最终事实；幂等重放命中已关闭空 Run 时稳定返回冲突。
    detail = console.get_action_run(
        context,
        run_id=run.run_id,
        permission_code="tool.run.create",
    )
    if detail.run.state == "cancelled" and detail.run.step_count == 0:
        raise ToolRunConflictError
    return ToolRunDetailResponse.from_domain(detail)


@router.get(
    "/tool-runs",
    response_model=ToolRunListResponse,
    operation_id="listToolRuns",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_tool_runs(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ToolConsoleService, Depends(tool_console_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ToolRunListResponse:
    """列出 PDP 资源范围内的工具任务历史。"""

    _require_workspace_path(context, workspace_id)
    return ToolRunListResponse(
        items=[
            ToolRunSummaryResponse.from_domain(item)
            for item in service.list_runs(context, limit=limit)
        ]
    )


@router.get(
    "/tool-runs/{run_id}",
    response_model=ToolRunDetailResponse,
    operation_id="getToolRun",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def get_tool_run(
    workspace_id: UUID,
    run_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ToolConsoleService, Depends(tool_console_service)],
) -> ToolRunDetailResponse:
    """返回 Run、步骤、确认和 Attempt 的脱敏历史详情。"""

    _require_workspace_path(context, workspace_id)
    return ToolRunDetailResponse.from_domain(service.get_run(context, run_id=run_id))


@router.get(
    "/tool-runs/{run_id}/events",
    operation_id="streamToolRun",
    response_class=StreamingResponse,
    responses={
        **error_responses(400, 401, 403, 404, 409, 422, 500),
        200: {
            "description": "按连续整数游标返回可恢复工具进度",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
    },
)
def stream_tool_run(
    workspace_id: UUID,
    run_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    console: Annotated[ToolConsoleService, Depends(tool_console_service)],
    progress: Annotated[ToolProgressService, Depends(tool_progress_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID", ge=0)] = None,
) -> StreamingResponse:
    """只回放持久化进度；重连不会创建 Run、Step 或 Attempt。"""

    _require_workspace_path(context, workspace_id)
    detail = console.get_run(context, run_id=run_id)
    initial = progress.replay(context, run_id=run_id, after_cursor=last_event_id or 0)
    if last_event_id is not None and last_event_id > initial.latest_cursor:
        # 超前游标不能被当作“暂无新事件”，否则客户端会永久跳过尚未产生的持久化事实。
        raise ToolRunConflictError
    return StreamingResponse(
        stream_tool_frames(
            progress,
            context,
            run_id,
            initial,
            initial_cursor=last_event_id or 0,
            terminal=detail.run.state in RUN_TERMINAL_STATES,
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


@router.post(
    "/tool-runs/{run_id}/confirmations/{confirmation_id}/confirm",
    response_model=ToolRunDetailResponse,
    operation_id="confirmToolCall",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def confirm_tool_call(
    workspace_id: UUID,
    run_id: UUID,
    confirmation_id: UUID,
    body: ToolConfirmationActionRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    console: Annotated[ToolConsoleService, Depends(tool_console_service)],
    approvals: Annotated[ApprovalInstanceService, Depends(approval_instance_service)],
    confirmations: Annotated[ToolConfirmationService, Depends(tool_confirmation_service)],
) -> ToolRunDetailResponse:
    """通过当前确认指派；最终批准后重新执行 PDP 才恢复 Step。"""

    return _respond_to_confirmation(
        workspace_id,
        run_id,
        confirmation_id,
        "approve",
        body,
        context,
        console,
        approvals,
        confirmations,
        None,
    )


@router.post(
    "/tool-runs/{run_id}/confirmations/{confirmation_id}/reject",
    response_model=ToolRunDetailResponse,
    operation_id="rejectToolCall",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def reject_tool_call(
    workspace_id: UUID,
    run_id: UUID,
    confirmation_id: UUID,
    body: ToolConfirmationActionRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    console: Annotated[ToolConsoleService, Depends(tool_console_service)],
    approvals: Annotated[ApprovalInstanceService, Depends(approval_instance_service)],
    confirmations: Annotated[ToolConfirmationService, Depends(tool_confirmation_service)],
    tasks: Annotated[ToolTaskService, Depends(tool_task_service)],
) -> ToolRunDetailResponse:
    """驳回当前确认指派，并在最终拒绝时关闭未执行任务。"""

    return _respond_to_confirmation(
        workspace_id,
        run_id,
        confirmation_id,
        "reject",
        body,
        context,
        console,
        approvals,
        confirmations,
        tasks,
    )


@router.post(
    "/tool-runs/{run_id}/cancel",
    response_model=ToolRunDetailResponse,
    operation_id="cancelToolRun",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def cancel_tool_run(
    workspace_id: UUID,
    run_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    console: Annotated[ToolConsoleService, Depends(tool_console_service)],
    tasks: Annotated[ToolTaskService, Depends(tool_task_service)],
) -> ToolRunDetailResponse:
    """提交取消事实；终态幂等返回，活动 Adapter 只做尽力传播。"""

    _require_workspace_path(context, workspace_id)
    console.get_action_run(context, run_id=run_id, permission_code="tool.run.cancel")
    tasks.request_cancellation(context, run_id, requested_at=datetime.now(UTC))
    return ToolRunDetailResponse.from_domain(
        console.get_action_run(context, run_id=run_id, permission_code="tool.run.cancel")
    )


def _respond_to_confirmation(
    workspace_id: UUID,
    run_id: UUID,
    confirmation_id: UUID,
    action: Literal["approve", "reject"],
    body: ToolConfirmationActionRequest,
    context: RequestContext,
    console: ToolConsoleService,
    approvals: ApprovalInstanceService,
    confirmations: ToolConfirmationService,
    tasks: ToolTaskService | None,
) -> ToolRunDetailResponse:
    """把工具专用权限安全翻译为一个已核验的通用审批实例动作。"""

    # 1. 先按工具确认权限和精确确认资源复核，不能直接信任通用审批实例 ID。
    _require_workspace_path(context, workspace_id)
    detail = console.get_action_run(
        context,
        run_id=run_id,
        permission_code="tool.confirmation.respond",
        target_id=confirmation_id,
    )
    step, confirmation = _find_confirmation(detail, confirmation_id)
    # 2. 通用审批服务按审批实例复核资源范围；这里只加入已由工具 PDP 核验的单个绑定实例。
    approval_context = replace(
        context,
        authorized_resource_ids=frozenset({confirmation.approval_instance_id}),
    )
    result = approvals.act(
        approval_context,
        approval_instance_id=confirmation.approval_instance_id,
        command=ApprovalRuntimeCommand(
            action=action,
            actor_account_id=_browser_account(context),
            idempotency_key=body.idempotency_key,
            target_account_id=None,
            reason_code=body.reason_code,
        ),
    )
    # 3. 新产生的审批终态必须回到工具状态机：批准重新执行当前 PDP，驳回则收敛整个 Run。
    if confirmation.state == "pending" and result.state.instance.status == "approved":
        confirmations.resume(
            context,
            confirmation_id=confirmation.confirmation_id,
            expected_binding=ToolCallBinding(
                workspace_id=workspace_id,
                run_id=run_id,
                step_id=step.step_id,
                tool_id=step.tool_id,
                tool_version=step.tool_version,
                canonical_arguments_hash=step.canonical_arguments_hash,
            ),
        )
    elif (
        confirmation.state == "pending"
        and result.state.instance.status == "rejected"
        and tasks is not None
    ):
        tasks.request_cancellation(context, run_id, requested_at=datetime.now(UTC))
    # 4. 动作完成后重新读取脱敏事实，响应不拼接审批或参数私有表。
    return ToolRunDetailResponse.from_domain(
        console.get_action_run(
            context,
            run_id=run_id,
            permission_code="tool.confirmation.respond",
            target_id=confirmation_id,
        )
    )


def _find_confirmation(
    detail: ToolRunDetail,
    confirmation_id: UUID,
) -> tuple[ToolStepView, ToolConfirmationView]:
    for step in detail.steps:
        if step.confirmation is not None and step.confirmation.confirmation_id == confirmation_id:
            return step, step.confirmation
    raise ToolExecutionDeniedError


def stream_tool_frames(
    progress: ToolProgressService,
    context: RequestContext,
    run_id: UUID,
    initial: ToolProgressPage,
    *,
    initial_cursor: int,
    terminal: bool,
    heartbeat_seconds: int,
    poll_interval_ms: int,
) -> Generator[str, None, None]:
    """短轮询持久化事实并输出严格递增帧，空闲时只发送 SSE 注释心跳。"""

    cursor = initial_cursor
    page = initial
    last_heartbeat = time.monotonic()
    while True:
        for event in page.events:
            cursor = event.cursor
            payload = ToolProgressEventResponse.from_domain(event).model_dump_json()
            yield f"id: {event.cursor}\nevent: {event.event_type}\ndata: {payload}\n\n"
            terminal = event.run_state in RUN_TERMINAL_STATES
        if page.has_more:
            page = progress.replay(context, run_id=run_id, after_cursor=cursor)
            continue
        if terminal:
            return
        now = time.monotonic()
        if now - last_heartbeat >= heartbeat_seconds:
            yield ": heartbeat\n\n"
            last_heartbeat = now
        time.sleep(max(0.05, poll_interval_ms / 1000))
        page = progress.replay(context, run_id=run_id, after_cursor=cursor)


def _require_workspace_path(context: RequestContext, workspace_id: UUID) -> None:
    if context.workspace_id != workspace_id:
        raise ToolExecutionDeniedError


def _browser_account(context: RequestContext) -> UUID:
    if (
        context.authentication_method != "browser_session"
        or context.user_id is None
        or context.user_id != context.actor_id
    ):
        raise ToolExecutionDeniedError
    return context.user_id


__all__ = ["router", "stream_tool_frames"]
