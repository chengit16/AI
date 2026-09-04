"""映射企业大脑独立会话、消息、运行和可恢复 SSE 协议。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.config import Settings, get_settings
from ai_platform_api.modules.assistant.api.routes import (
    close_cancelled_stream,
    initial_stream_replay,
    stream_run_frames,
    streaming_service,
)
from ai_platform_api.modules.assistant.api.schemas import (
    AssistantRunListResponse,
    AssistantRunResponse,
    AssistantSourceListResponse,
    AssistantSourceResponse,
    CreateEnterpriseBrainConversationRequest,
    CreateEnterpriseBrainReportRequest,
    CreateUserMessageRequest,
    CurrentMessageFeedbackResponse,
    EnterpriseBrainConversationListResponse,
    EnterpriseBrainConversationResponse,
    EnterpriseBrainOverviewResponse,
    EnterpriseBrainReportListResponse,
    EnterpriseBrainReportResponse,
    MessageFeedbackResponse,
    MessageListResponse,
    MessagePartResponse,
    MessageResponse,
    SubmitMessageFeedbackRequest,
    UserMessageCreatedResponse,
)
from ai_platform_api.modules.assistant.application.enterprise_brain import (
    AssistantRun,
    Conversation,
    EnterpriseBrainConversationService,
    EnterpriseBrainReport,
    Message,
    MessageFeedback,
)
from ai_platform_api.modules.assistant.application.errors import AssistantDeniedError
from ai_platform_api.modules.assistant.application.runner import AssistantRunExecutor
from ai_platform_api.modules.assistant.application.sources import AssistantSourceService
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.streaming.application.service import TransactionalStreamService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/enterprise-brain",
    tags=["AI 企业大脑"],
)


def enterprise_brain_service(request: Request) -> EnterpriseBrainConversationService:
    """从组合根取得企业大脑服务，Router 不直接访问数据库。"""

    service = getattr(request.app.state, "enterprise_brain_conversation_service", None)
    if not isinstance(service, EnterpriseBrainConversationService):
        raise RuntimeError("企业大脑服务尚未完成装配")
    return service


def enterprise_brain_executor(request: Request) -> AssistantRunExecutor:
    """取得复用现有 RAG 和模型链路的企业大脑运行器。"""

    executor = getattr(request.app.state, "enterprise_brain_run_executor", None)
    if not isinstance(executor, AssistantRunExecutor):
        raise RuntimeError("企业大脑运行器尚未完成装配")
    return executor


def enterprise_brain_sources(request: Request) -> AssistantSourceService:
    """取得验证企业会话归属的来源服务。"""

    service = getattr(request.app.state, "enterprise_brain_source_service", None)
    if not isinstance(service, AssistantSourceService):
        raise RuntimeError("企业大脑来源服务尚未完成装配")
    return service


@router.get(
    "",
    response_model=EnterpriseBrainOverviewResponse,
    operation_id="getEnterpriseBrainOverview",
    responses=error_responses(400, 401, 403, 422, 500),
)
def get_overview(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseBrainConversationService, Depends(enterprise_brain_service)],
) -> EnterpriseBrainOverviewResponse:
    """返回企业大脑页面聚合统计，不返回问答正文或对象键。"""

    _require_workspace(context, workspace_id)
    overview = service.get_overview(context)
    return EnterpriseBrainOverviewResponse(
        workspace_id=overview.workspace_id,
        workspace_name=overview.workspace_name,
        generated_at=overview.generated_at,
        window_started_at=overview.window_started_at,
        window_ended_at=overview.window_ended_at,
        active_conversation_count=overview.active_conversation_count,
        archived_conversation_count=overview.archived_conversation_count,
        run_count_30d=overview.run_count_30d,
        completed_run_count_30d=overview.completed_run_count_30d,
        failed_run_count_30d=overview.failed_run_count_30d,
        cancelled_run_count_30d=overview.cancelled_run_count_30d,
        token_count_30d=overview.token_count_30d,
        estimated_cost_microunits_30d=overview.estimated_cost_microunits_30d,
        knowledge_domain_ids=list(overview.knowledge_domain_ids),
    )


@router.post(
    "/conversations",
    response_model=EnterpriseBrainConversationResponse,
    operation_id="createEnterpriseBrainConversation",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def create_conversation(
    workspace_id: UUID,
    body: CreateEnterpriseBrainConversationRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseBrainConversationService, Depends(enterprise_brain_service)],
) -> EnterpriseBrainConversationResponse:
    """选择当前获权团队知识域创建企业大脑会话。"""

    _require_workspace(context, workspace_id)
    return _conversation(
        service.create_conversation(
            context,
            knowledge_domain_id=body.knowledge_domain_id,
            title=body.title,
        )
    )


@router.get(
    "/conversations",
    response_model=EnterpriseBrainConversationListResponse,
    operation_id="listEnterpriseBrainConversations",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_conversations(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseBrainConversationService, Depends(enterprise_brain_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> EnterpriseBrainConversationListResponse:
    """只列出当前账号自己的企业大脑会话。"""

    _require_workspace(context, workspace_id)
    return EnterpriseBrainConversationListResponse(
        items=[_conversation(item) for item in service.list_conversations(context, limit=limit)]
    )


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=MessageListResponse,
    operation_id="listEnterpriseBrainMessages",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def list_messages(
    workspace_id: UUID,
    conversation_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseBrainConversationService, Depends(enterprise_brain_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> MessageListResponse:
    """返回当前账号企业大脑会话的消息历史。"""

    _require_workspace(context, workspace_id)
    return MessageListResponse(
        items=[
            _message(item)
            for item in service.list_messages(
                context,
                conversation_id=conversation_id,
                limit=limit,
            )
        ]
    )


@router.get(
    "/conversations/{conversation_id}/runs",
    response_model=AssistantRunListResponse,
    operation_id="listEnterpriseBrainRuns",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def list_runs(
    workspace_id: UUID,
    conversation_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseBrainConversationService, Depends(enterprise_brain_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> AssistantRunListResponse:
    """列出企业大脑运行及其冻结知识域范围。"""

    _require_workspace(context, workspace_id)
    return AssistantRunListResponse(
        items=[
            _run(item)
            for item in service.list_runs(
                context,
                conversation_id=conversation_id,
                limit=limit,
            )
        ]
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=UserMessageCreatedResponse,
    operation_id="createEnterpriseBrainMessage",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def create_message(
    workspace_id: UUID,
    conversation_id: UUID,
    body: CreateUserMessageRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseBrainConversationService, Depends(enterprise_brain_service)],
    executor: Annotated[AssistantRunExecutor, Depends(enterprise_brain_executor)],
) -> UserMessageCreatedResponse:
    """重新鉴权知识域并排队一次企业问答。"""

    _require_workspace(context, workspace_id)
    if body.attachment_ids:
        raise AssistantDeniedError
    submission = service.create_user_message(
        context,
        conversation_id=conversation_id,
        texts=tuple(part.text for part in body.parts),
        idempotency_key=idempotency_key,
    )
    background_tasks.add_task(executor.execute, context, submission.run.run_id)
    return UserMessageCreatedResponse(
        message=_message(submission.message),
        run=_run(submission.run),
    )


@router.get(
    "/conversations/{conversation_id}/runs/{run_id}/events",
    operation_id="streamEnterpriseBrainRunEvents",
    response_class=StreamingResponse,
    responses={
        **error_responses(400, 401, 403, 404, 409, 410, 422, 500),
        200: {
            "description": "按严格序号返回可恢复的企业大脑运行事件",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
    },
)
def stream_events(
    workspace_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    conversations: Annotated[
        EnterpriseBrainConversationService,
        Depends(enterprise_brain_service),
    ],
    streams: Annotated[TransactionalStreamService, Depends(streaming_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    last_event_id: Annotated[UUID | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """授权后复用现有 SSE 回放，不重新触发模型生成。"""

    _require_workspace(context, workspace_id)
    run = conversations.get_run_for_stream(
        context,
        conversation_id=conversation_id,
        run_id=run_id,
    )
    replay = initial_stream_replay(streams, workspace_id, run, last_event_id)
    return StreamingResponse(
        stream_run_frames(
            streams,
            context,
            run,
            lambda: conversations.get_run_for_stream(
                context,
                conversation_id=conversation_id,
                run_id=run_id,
            ),
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


@router.post(
    "/conversations/{conversation_id}/runs/{run_id}/cancel",
    response_model=AssistantRunResponse,
    operation_id="cancelEnterpriseBrainRun",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def cancel_run(
    workspace_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    conversations: Annotated[
        EnterpriseBrainConversationService,
        Depends(enterprise_brain_service),
    ],
    streams: Annotated[TransactionalStreamService, Depends(streaming_service)],
) -> AssistantRunResponse:
    """取消自己的企业大脑活动 Run。"""

    _require_workspace(context, workspace_id)
    run = conversations.cancel_run(
        context,
        conversation_id=conversation_id,
        run_id=run_id,
    )
    if run.status == "cancelled":
        close_cancelled_stream(streams, run)
    return _run(run)


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/sources",
    response_model=AssistantSourceListResponse,
    operation_id="listEnterpriseBrainMessageSources",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def list_sources(
    workspace_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AssistantSourceService, Depends(enterprise_brain_sources)],
) -> AssistantSourceListResponse:
    """重新鉴权后返回当前仍可读取的企业来源。"""

    _require_workspace(context, workspace_id)
    return AssistantSourceListResponse(
        items=[
            AssistantSourceResponse(
                rank=item.rank,
                document_id=item.document_id,
                document_version_id=item.document_version_id,
                chunk_id=item.chunk_id,
                content_hash=item.content_hash,
                quote=item.quote,
                source_position=dict(item.source_position),
                document_title=item.document_title,
                source_kind=item.source_kind,
                source_name=item.source_name,
                conflict_detected=item.conflict_detected,
            )
            for item in service.list_sources(
                context,
                conversation_id=conversation_id,
                message_id=message_id,
            )
        ]
    )


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    response_model=CurrentMessageFeedbackResponse,
    operation_id="getEnterpriseBrainMessageFeedback",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def get_feedback(
    workspace_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseBrainConversationService, Depends(enterprise_brain_service)],
) -> CurrentMessageFeedbackResponse:
    """读取当前账号对企业大脑回答的反馈。"""

    _require_workspace(context, workspace_id)
    feedback = service.get_feedback(
        context,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    return CurrentMessageFeedbackResponse(
        item=_feedback(feedback) if feedback is not None else None
    )


@router.put(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    response_model=MessageFeedbackResponse,
    operation_id="submitEnterpriseBrainMessageFeedback",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def submit_feedback(
    workspace_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    body: SubmitMessageFeedbackRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseBrainConversationService, Depends(enterprise_brain_service)],
) -> MessageFeedbackResponse:
    """新增或修订企业大脑回答反馈。"""

    _require_workspace(context, workspace_id)
    return _feedback(
        service.submit_feedback(
            context,
            conversation_id=conversation_id,
            message_id=message_id,
            rating=body.rating,
            issue_codes=tuple(body.issue_codes),
            comment=body.comment,
        )
    )


@router.post(
    "/conversations/{conversation_id}/archive",
    response_model=EnterpriseBrainConversationResponse,
    operation_id="archiveEnterpriseBrainConversation",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def archive_conversation(
    workspace_id: UUID,
    conversation_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseBrainConversationService, Depends(enterprise_brain_service)],
) -> EnterpriseBrainConversationResponse:
    """归档当前账号没有活动 Run 的企业大脑会话。"""

    _require_workspace(context, workspace_id)
    return _conversation(service.archive_conversation(context, conversation_id=conversation_id))


@router.post(
    "/reports",
    response_model=EnterpriseBrainReportResponse,
    operation_id="createEnterpriseBrainReport",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def create_report(
    workspace_id: UUID,
    body: CreateEnterpriseBrainReportRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseBrainConversationService, Depends(enterprise_brain_service)],
) -> EnterpriseBrainReportResponse:
    """从当前账号自己的已完成回答生成不可变 Markdown 报告。"""

    _require_workspace(context, workspace_id)
    return _report(
        service.create_report(
            context,
            message_id=body.message_id,
            template=body.template,
            title=body.title,
            idempotency_key=idempotency_key,
        )
    )


@router.get(
    "/reports",
    response_model=EnterpriseBrainReportListResponse,
    operation_id="listEnterpriseBrainReports",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_reports(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseBrainConversationService, Depends(enterprise_brain_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> EnterpriseBrainReportListResponse:
    """列出当前账号创建的企业大脑报告。"""

    _require_workspace(context, workspace_id)
    return EnterpriseBrainReportListResponse(
        items=[_report(item) for item in service.list_reports(context, limit=limit)]
    )


@router.get(
    "/reports/{report_id}",
    response_model=EnterpriseBrainReportResponse,
    operation_id="getEnterpriseBrainReport",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def get_report(
    workspace_id: UUID,
    report_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseBrainConversationService, Depends(enterprise_brain_service)],
) -> EnterpriseBrainReportResponse:
    """读取当前账号自己的企业大脑报告正文。"""

    _require_workspace(context, workspace_id)
    return _report(service.get_report(context, report_id=report_id))


@router.get(
    "/reports/{report_id}/download",
    operation_id="downloadEnterpriseBrainReport",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def download_report(
    workspace_id: UUID,
    report_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseBrainConversationService, Depends(enterprise_brain_service)],
) -> Response:
    """下载 Markdown 报告；响应不暴露内部对象键或存储路径。"""

    _require_workspace(context, workspace_id)
    report = service.get_report(context, report_id=report_id)
    return Response(
        content=report.content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{report.report_id}.md"'},
    )


def _conversation(value: Conversation) -> EnterpriseBrainConversationResponse:
    if value.knowledge_domain_id is None or value.knowledge_domain_policy_version is None:
        raise RuntimeError("企业大脑会话缺少知识域快照")
    return EnterpriseBrainConversationResponse(
        conversation_id=value.conversation_id,
        workspace_id=value.workspace_id,
        created_by_account_id=value.created_by_account_id,
        knowledge_domain_id=value.knowledge_domain_id,
        knowledge_domain_policy_version=value.knowledge_domain_policy_version,
        title=value.title,
        status=value.status,
        created_at=value.created_at,
        updated_at=value.updated_at,
        version=value.version,
    )


def _message(value: Message) -> MessageResponse:
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


def _run(value: AssistantRun) -> AssistantRunResponse:
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
        knowledge_base_ids=(
            sorted(value.knowledge_base_ids, key=str)
            if value.knowledge_base_ids is not None
            else None
        ),
        document_ids=(
            sorted(value.document_ids, key=str) if value.document_ids is not None else None
        ),
        attachment_ids=list(value.attachment_ids),
        knowledge_domain_id=value.knowledge_domain_id,
        knowledge_domain_policy_version=value.knowledge_domain_policy_version,
        status=value.status,
        trace_id=value.trace_id,
        created_at=value.created_at,
        updated_at=value.updated_at,
        completed_at=value.completed_at,
        error_code=value.error_code,
    )


def _feedback(value: MessageFeedback) -> MessageFeedbackResponse:
    return MessageFeedbackResponse(
        feedback_id=value.feedback_id,
        workspace_id=value.workspace_id,
        conversation_id=value.conversation_id,
        message_id=value.message_id,
        run_id=value.run_id,
        rating=value.rating,
        issue_codes=list(value.issue_codes),
        comment=value.comment,
        created_at=value.created_at,
        updated_at=value.updated_at,
        version=value.version,
    )


def _report(value: EnterpriseBrainReport) -> EnterpriseBrainReportResponse:
    """将报告领域对象映射为稳定 HTTP 响应。"""

    return EnterpriseBrainReportResponse(
        report_id=value.report_id,
        workspace_id=value.workspace_id,
        created_by_account_id=value.created_by_account_id,
        conversation_id=value.conversation_id,
        message_id=value.message_id,
        run_id=value.run_id,
        template=value.template,
        title=value.title,
        content=value.content,
        content_sha256=value.content_sha256,
        citation_count=value.citation_count,
        idempotency_key=value.idempotency_key,
        created_at=value.created_at,
    )


def _require_workspace(context: RequestContext, workspace_id: UUID) -> None:
    if context.workspace_id != workspace_id:
        raise AssistantDeniedError
