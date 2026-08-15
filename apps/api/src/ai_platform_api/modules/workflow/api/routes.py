"""映射工作流草稿、校验、发布和冻结版本运行事实的 HTTP 协议。"""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request, status

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.application.fields import FieldProjectionService
from ai_platform_api.modules.identity.api.dependencies import (
    field_projection_service,
    trusted_request_context,
)
from ai_platform_api.modules.workflow.api.schemas import (
    CreateWorkflowRequest,
    CreateWorkflowRunRequest,
    PublishWorkflowRequest,
    UpdateWorkflowDraftRequest,
    WorkflowDefinitionResponse,
    WorkflowDetailResponse,
    WorkflowDraftResponse,
    WorkflowEdgeDocument,
    WorkflowGraphDocument,
    WorkflowGraphViolationResponse,
    WorkflowListResponse,
    WorkflowNodeDocument,
    WorkflowPublicationResponse,
    WorkflowPublishResponse,
    WorkflowRunListResponse,
    WorkflowRunResponse,
    WorkflowVersionResponse,
)
from ai_platform_api.modules.workflow.application.executor import WorkflowRunExecutor
from ai_platform_api.modules.workflow.application.service import (
    WorkflowDefinition,
    WorkflowDefinitionService,
    WorkflowDeniedError,
    WorkflowDraft,
    WorkflowGraph,
    WorkflowPublication,
    WorkflowRun,
    WorkflowVersion,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/workflows", tags=["工作流"])


def workflow_service(request: Request) -> WorkflowDefinitionService:
    """从应用容器解析工作流服务，Router 不直接访问版本或运行表。"""

    service = getattr(request.app.state, "workflow_definition_service", None)
    if not isinstance(service, WorkflowDefinitionService):
        raise RuntimeError("工作流服务尚未完成装配")
    return service


def workflow_run_executor(request: Request) -> WorkflowRunExecutor:
    """从应用容器解析受限执行器，Router 不直接调度节点 Adapter。"""

    executor = getattr(request.app.state, "workflow_run_executor", None)
    if not isinstance(executor, WorkflowRunExecutor):
        raise RuntimeError("工作流执行器尚未完成装配")
    return executor


@router.post(
    "",
    response_model=WorkflowDetailResponse,
    operation_id="createWorkflowDefinition",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
def create_workflow(
    workspace_id: UUID,
    body: CreateWorkflowRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[WorkflowDefinitionService, Depends(workflow_service)],
) -> WorkflowDetailResponse:
    """创建定义和首个草稿，草稿可以携带待修复的图校验错误。"""

    _require_workspace_path(context, workspace_id)
    workflow, draft = service.create(
        context,
        name=body.name,
        description=body.description,
        graph=body.graph.to_domain(),
    )
    return WorkflowDetailResponse(
        workflow=_workflow(workflow),
        draft=_draft(draft),
        publication=None,
    )


@router.get(
    "",
    response_model=WorkflowListResponse,
    operation_id="listWorkflowDefinitions",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_workflows(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[WorkflowDefinitionService, Depends(workflow_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> WorkflowListResponse:
    """列出当前 PDP 数据范围内的工作流定义。"""

    _require_workspace_path(context, workspace_id)
    return WorkflowListResponse(
        items=[_workflow(item) for item in service.list(context, limit=limit)]
    )


@router.get(
    "/{workflow_id}",
    response_model=WorkflowDetailResponse,
    operation_id="getWorkflowDefinition",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def get_workflow(
    workspace_id: UUID,
    workflow_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[WorkflowDefinitionService, Depends(workflow_service)],
) -> WorkflowDetailResponse:
    """读取定义、当前草稿和发布指针。"""

    _require_workspace_path(context, workspace_id)
    workflow, draft, publication = service.get(context, workflow_id=workflow_id)
    return WorkflowDetailResponse(
        workflow=_workflow(workflow),
        draft=_draft(draft),
        publication=_publication(publication) if publication is not None else None,
    )


@router.put(
    "/{workflow_id}/draft",
    response_model=WorkflowDraftResponse,
    operation_id="updateWorkflowDraft",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def update_workflow_draft(
    workspace_id: UUID,
    workflow_id: UUID,
    body: UpdateWorkflowDraftRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[WorkflowDefinitionService, Depends(workflow_service)],
) -> WorkflowDraftResponse:
    """按修订号替换草稿并返回最新图校验结果。"""

    _require_workspace_path(context, workspace_id)
    return _draft(
        service.update_draft(
            context,
            workflow_id=workflow_id,
            expected_revision=body.expected_revision,
            graph=body.graph.to_domain(),
        )
    )


@router.post(
    "/{workflow_id}/draft/validate",
    response_model=WorkflowDraftResponse,
    operation_id="validateWorkflowDraft",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def validate_workflow_draft(
    workspace_id: UUID,
    workflow_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[WorkflowDefinitionService, Depends(workflow_service)],
) -> WorkflowDraftResponse:
    """按当前图规则重新校验草稿，不改变草稿修订号。"""

    _require_workspace_path(context, workspace_id)
    return _draft(service.validate_draft(context, workflow_id=workflow_id))


@router.post(
    "/{workflow_id}/publish",
    response_model=WorkflowPublishResponse,
    operation_id="publishWorkflowVersion",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def publish_workflow(
    workspace_id: UUID,
    workflow_id: UUID,
    body: PublishWorkflowRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[WorkflowDefinitionService, Depends(workflow_service)],
) -> WorkflowPublishResponse:
    """冻结有效草稿为不可变版本并切换当前发布指针。"""

    _require_workspace_path(context, workspace_id)
    workflow, version, publication = service.publish(
        context,
        workflow_id=workflow_id,
        expected_revision=body.expected_revision,
    )
    return WorkflowPublishResponse(
        workflow=_workflow(workflow),
        version=_version(version),
        publication=_publication(publication),
    )


@router.post(
    "/{workflow_id}/runs",
    response_model=WorkflowRunResponse,
    operation_id="createWorkflowRun",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def create_workflow_run(
    workspace_id: UUID,
    workflow_id: UUID,
    body: CreateWorkflowRunRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[WorkflowDefinitionService, Depends(workflow_service)],
    executor: Annotated[WorkflowRunExecutor, Depends(workflow_run_executor)],
) -> WorkflowRunResponse:
    """幂等创建排队运行，并在响应提交后触发一次受限本地执行。"""

    _require_workspace_path(context, workspace_id)
    run = service.create_run(
        context,
        workflow_id=workflow_id,
        workflow_version_id=body.workflow_version_id,
        idempotency_key=idempotency_key,
        input_payload=body.input_payload,
    )
    # 重复幂等请求可能再次调度，但数据库 queued -> running 条件保证节点只执行一次。
    background_tasks.add_task(executor.execute, context, run.workflow_run_id)
    return _run(run)


@router.get(
    "/{workflow_id}/runs",
    response_model=WorkflowRunListResponse,
    operation_id="listWorkflowRuns",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def list_workflow_runs(
    workspace_id: UUID,
    workflow_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[WorkflowDefinitionService, Depends(workflow_service)],
    projection: Annotated[FieldProjectionService, Depends(field_projection_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> WorkflowRunListResponse:
    """列出最近运行；每条输入和输出在序列化前独立执行字段级投影。"""

    _require_workspace_path(context, workspace_id)
    return WorkflowRunListResponse(
        items=[
            _run(
                item,
                projection=projection,
                field_mask=context.authorized_field_mask,
            )
            for item in service.list_runs(context, workflow_id=workflow_id, limit=limit)
        ]
    )


@router.get(
    "/{workflow_id}/runs/{workflow_run_id}",
    response_model=WorkflowRunResponse,
    operation_id="getWorkflowRun",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def get_workflow_run(
    workspace_id: UUID,
    workflow_id: UUID,
    workflow_run_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[WorkflowDefinitionService, Depends(workflow_service)],
    projection: Annotated[FieldProjectionService, Depends(field_projection_service)],
) -> WorkflowRunResponse:
    """读取冻结版本和当前状态，不从当前草稿反推历史运行。"""

    _require_workspace_path(context, workspace_id)
    return _run(
        service.get_run(
            context,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
        ),
        projection=projection,
        field_mask=context.authorized_field_mask,
    )


def _require_workspace_path(context: RequestContext, workspace_id: UUID) -> None:
    if context.workspace_id != workspace_id:
        raise WorkflowDeniedError


def _workflow(value: WorkflowDefinition) -> WorkflowDefinitionResponse:
    return WorkflowDefinitionResponse(
        workflow_id=value.workflow_id,
        workspace_id=value.workspace_id,
        name=value.name,
        description=value.description,
        status=value.status,
        current_version_id=value.current_version_id,
        created_by_account_id=value.created_by_account_id,
        created_at=value.created_at,
        updated_at=value.updated_at,
        version=value.version,
    )


def _draft(value: WorkflowDraft) -> WorkflowDraftResponse:
    return WorkflowDraftResponse(
        workflow_id=value.workflow_id,
        workspace_id=value.workspace_id,
        revision=value.revision,
        graph=_graph(value.graph),
        graph_digest=value.graph_digest,
        validation_errors=[
            WorkflowGraphViolationResponse(
                code=item.code,
                node_id=item.node_id,
                edge_id=item.edge_id,
            )
            for item in value.validation_errors
        ],
        updated_by_account_id=value.updated_by_account_id,
        updated_at=value.updated_at,
    )


def _version(value: WorkflowVersion) -> WorkflowVersionResponse:
    return WorkflowVersionResponse(
        workflow_version_id=value.workflow_version_id,
        workflow_id=value.workflow_id,
        workspace_id=value.workspace_id,
        version_number=value.version_number,
        source_draft_revision=value.source_draft_revision,
        graph=_graph(value.graph),
        graph_digest=value.graph_digest,
        published_by_account_id=value.published_by_account_id,
        published_at=value.published_at,
    )


def _publication(value: WorkflowPublication) -> WorkflowPublicationResponse:
    return WorkflowPublicationResponse(
        workflow_id=value.workflow_id,
        workspace_id=value.workspace_id,
        workflow_version_id=value.workflow_version_id,
        generation=value.generation,
        published_by_account_id=value.published_by_account_id,
        published_at=value.published_at,
    )


def _run(
    value: WorkflowRun,
    *,
    projection: FieldProjectionService | None = None,
    field_mask: frozenset[str] = frozenset(),
) -> WorkflowRunResponse:
    """转换运行事实；读取接口在序列化前移除无权查看的输入与输出。"""

    visible_input: dict[str, object] | None = value.input_payload
    visible_output: dict[str, object] | None = value.output_payload
    if projection is not None:
        visible = projection.response(
            "workflow_instance",
            {"input": value.input_payload, "output": value.output_payload},
            field_mask,
        )
        visible_input = cast("dict[str, object] | None", visible.get("input"))
        visible_output = cast("dict[str, object] | None", visible.get("output"))
    return WorkflowRunResponse(
        workflow_run_id=value.workflow_run_id,
        workflow_id=value.workflow_id,
        workspace_id=value.workspace_id,
        workflow_version_id=value.workflow_version_id,
        requested_by_account_id=value.requested_by_account_id,
        status=value.status,
        input_payload=visible_input,
        output_payload=visible_output,
        executor_version=value.executor_version,
        steps_executed=value.steps_executed,
        model_calls=value.model_calls,
        retrieval_calls=value.retrieval_calls,
        output_bytes=value.output_bytes,
        created_at=value.created_at,
        updated_at=value.updated_at,
        completed_at=value.completed_at,
        error_code=value.error_code,
        version=value.version,
    )


def _graph(value: WorkflowGraph) -> WorkflowGraphDocument:
    return WorkflowGraphDocument(
        schema_version=value.schema_version,
        entry_node_id=value.entry_node_id,
        nodes=[
            WorkflowNodeDocument(
                node_id=item.node_id,
                node_type=item.node_type,
                name=item.name,
                config=item.config,
            )
            for item in value.nodes
        ],
        edges=[
            WorkflowEdgeDocument(
                edge_id=item.edge_id,
                source_node_id=item.source_node_id,
                target_node_id=item.target_node_id,
                condition_key=item.condition_key,
            )
            for item in value.edges
        ],
    )
