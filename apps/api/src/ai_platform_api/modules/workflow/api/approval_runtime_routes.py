"""映射审批实例查询、人工动作、到期处理和工作流恢复协议。"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.workflow.api.approval_runtime_schemas import (
    ApprovalActionRequest,
    ApprovalCommandResponse,
    ApprovalInstanceListResponse,
    ApprovalInstanceResponse,
    DueApprovalResponse,
    StartApprovalInstanceRequest,
    TransferApprovalRequest,
)
from ai_platform_api.modules.workflow.application.approval_runtime import (
    ApprovalCommandResult,
    ApprovalInstanceDenied,
    ApprovalInstanceService,
    ApprovalRuntimeCommand,
)
from ai_platform_api.modules.workflow.application.executor import WorkflowRunExecutor

router = APIRouter(
    prefix="/workspaces/{workspace_id}/approval-instances",
    tags=["审批实例"],
)


def approval_instance_service(request: Request) -> ApprovalInstanceService:
    """从应用容器解析审批运行服务，Router 不直接写入聚合或工作流表。"""

    service = getattr(request.app.state, "approval_instance_service", None)
    if not isinstance(service, ApprovalInstanceService):
        raise RuntimeError("审批运行服务尚未完成装配")
    return service


def workflow_run_executor(request: Request) -> WorkflowRunExecutor:
    """解析提交后恢复所需执行器，审批事务本身不持有模型或检索 Adapter。"""

    executor = getattr(request.app.state, "workflow_run_executor", None)
    if not isinstance(executor, WorkflowRunExecutor):
        raise RuntimeError("工作流执行器尚未完成装配")
    return executor


@router.post(
    "",
    response_model=ApprovalCommandResponse,
    operation_id="startApprovalInstance",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def start_approval_instance(
    workspace_id: UUID,
    body: StartApprovalInstanceRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ApprovalInstanceService, Depends(approval_instance_service)],
) -> ApprovalCommandResponse:
    """按当前策略和组织事实创建独立审批实例。"""

    _require_workspace_path(context, workspace_id)
    account_id = _browser_account(context)
    return _command_response(
        service.start(
            context,
            subject=body.to_domain(
                workspace_id=workspace_id,
                requester_account_id=account_id,
            ),
            idempotency_key=body.idempotency_key,
        )
    )


@router.get(
    "",
    response_model=ApprovalInstanceListResponse,
    operation_id="listApprovalInstances",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_approval_instances(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ApprovalInstanceService, Depends(approval_instance_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ApprovalInstanceListResponse:
    """列出申请人或历史审批人参与过的实例。"""

    _require_workspace_path(context, workspace_id)
    return ApprovalInstanceListResponse(
        items=[
            ApprovalInstanceResponse.from_domain(item)
            for item in service.list(context, limit=limit)
        ]
    )


@router.post(
    "/process-due",
    response_model=DueApprovalResponse,
    operation_id="processDueApprovalInstances",
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
def process_due_approval_instances(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ApprovalInstanceService, Depends(approval_instance_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> DueApprovalResponse:
    """以短事务处理当前工作空间到期提醒和超时动作。"""

    _require_workspace_path(context, workspace_id)
    return DueApprovalResponse(
        items=[_command_response(item) for item in service.process_due(context, limit=limit)]
    )


@router.get(
    "/{approval_instance_id}",
    response_model=ApprovalInstanceResponse,
    operation_id="getApprovalInstance",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def get_approval_instance(
    workspace_id: UUID,
    approval_instance_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ApprovalInstanceService, Depends(approval_instance_service)],
) -> ApprovalInstanceResponse:
    """读取参与者可见的冻结层级和当前指派。"""

    _require_workspace_path(context, workspace_id)
    return ApprovalInstanceResponse.from_domain(
        service.get(context, approval_instance_id=approval_instance_id)
    )


@router.post(
    "/{approval_instance_id}/approve",
    response_model=ApprovalCommandResponse,
    operation_id="approveApprovalInstance",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def approve_approval_instance(
    workspace_id: UUID,
    approval_instance_id: UUID,
    body: ApprovalActionRequest,
    background_tasks: BackgroundTasks,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ApprovalInstanceService, Depends(approval_instance_service)],
    executor: Annotated[WorkflowRunExecutor, Depends(workflow_run_executor)],
) -> ApprovalCommandResponse:
    """通过当前指派；最终层提交后才异步恢复关联工作流。"""

    return _act(
        workspace_id,
        approval_instance_id,
        "approve",
        body,
        background_tasks,
        context,
        service,
        executor,
    )


@router.post(
    "/{approval_instance_id}/reject",
    response_model=ApprovalCommandResponse,
    operation_id="rejectApprovalInstance",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def reject_approval_instance(
    workspace_id: UUID,
    approval_instance_id: UUID,
    body: ApprovalActionRequest,
    background_tasks: BackgroundTasks,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ApprovalInstanceService, Depends(approval_instance_service)],
    executor: Annotated[WorkflowRunExecutor, Depends(workflow_run_executor)],
) -> ApprovalCommandResponse:
    """驳回当前实例，并在同一事务终止关联工作流。"""

    return _act(
        workspace_id,
        approval_instance_id,
        "reject",
        body,
        background_tasks,
        context,
        service,
        executor,
    )


@router.post(
    "/{approval_instance_id}/transfer",
    response_model=ApprovalCommandResponse,
    operation_id="transferApprovalInstance",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def transfer_approval_instance(
    workspace_id: UUID,
    approval_instance_id: UUID,
    body: TransferApprovalRequest,
    background_tasks: BackgroundTasks,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ApprovalInstanceService, Depends(approval_instance_service)],
    executor: Annotated[WorkflowRunExecutor, Depends(workflow_run_executor)],
) -> ApprovalCommandResponse:
    """把当前指派转给新的活动成员，历史责任事实保持只追加可追溯。"""

    return _act(
        workspace_id,
        approval_instance_id,
        "transfer",
        body,
        background_tasks,
        context,
        service,
        executor,
        target_account_id=body.target_account_id,
    )


@router.post(
    "/{approval_instance_id}/withdraw",
    response_model=ApprovalCommandResponse,
    operation_id="withdrawApprovalInstance",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def withdraw_approval_instance(
    workspace_id: UUID,
    approval_instance_id: UUID,
    body: ApprovalActionRequest,
    background_tasks: BackgroundTasks,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ApprovalInstanceService, Depends(approval_instance_service)],
    executor: Annotated[WorkflowRunExecutor, Depends(workflow_run_executor)],
) -> ApprovalCommandResponse:
    """仅允许申请人撤回待审批实例，并原子取消关联工作流。"""

    return _act(
        workspace_id,
        approval_instance_id,
        "withdraw",
        body,
        background_tasks,
        context,
        service,
        executor,
    )


def _act(
    workspace_id: UUID,
    approval_instance_id: UUID,
    action: Literal["approve", "reject", "transfer", "withdraw"],
    body: ApprovalActionRequest,
    background_tasks: BackgroundTasks,
    context: RequestContext,
    service: ApprovalInstanceService,
    executor: WorkflowRunExecutor,
    *,
    target_account_id: UUID | None = None,
) -> ApprovalCommandResponse:
    """统一动作编排并保证恢复调度只发生在审批事务提交之后。"""

    _require_workspace_path(context, workspace_id)
    result = service.act(
        context,
        approval_instance_id=approval_instance_id,
        command=ApprovalRuntimeCommand(
            action,
            _browser_account(context),
            body.idempotency_key,
            target_account_id,
            body.reason_code,
        ),
    )
    if result.resume is not None:
        resume = result.resume
        resume_context = RequestContext.trusted(
            actor_id=resume.requester_account_id,
            user_id=resume.requester_account_id,
            workspace_id=resume.workspace_id,
            trace=TraceContext.continue_from(resume.traceparent),
            authentication_method="browser_session",
        )
        background_tasks.add_task(executor.execute, resume_context, resume.workflow_run_id)
    return _command_response(result)


def _command_response(result: ApprovalCommandResult) -> ApprovalCommandResponse:
    return ApprovalCommandResponse(
        instance=ApprovalInstanceResponse.from_domain(result.state),
        replayed=result.replayed,
    )


def _require_workspace_path(context: RequestContext, workspace_id: UUID) -> None:
    if context.workspace_id != workspace_id:
        raise ApprovalInstanceDenied


def _browser_account(context: RequestContext) -> UUID:
    if context.authentication_method != "browser_session" or context.user_id is None:
        raise ApprovalInstanceDenied
    if context.user_id != context.actor_id:
        raise ApprovalInstanceDenied
    return context.user_id
