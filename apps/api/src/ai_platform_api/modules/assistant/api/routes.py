"""映射私有会话、不可变消息、后台运行与可恢复 HTTP SSE 协议。"""

import json
import time
from collections.abc import Generator
from contextlib import suppress
from datetime import UTC, datetime
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request, status
from fastapi.responses import StreamingResponse

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.config import Settings, get_settings
from ai_platform_api.modules.assistant.api.schemas import (
    AssistantRunListResponse,
    AssistantRunResponse,
    AssistantSourceListResponse,
    AssistantSourceResponse,
    ConversationListResponse,
    ConversationResponse,
    CreateConversationRequest,
    CreateUserMessageRequest,
    CurrentMessageFeedbackResponse,
    MessageFeedbackResponse,
    MessageListResponse,
    MessagePartResponse,
    MessageResponse,
    SubmitMessageFeedbackRequest,
    UserMessageCreatedResponse,
)
from ai_platform_api.modules.assistant.application.errors import AssistantDeniedError
from ai_platform_api.modules.assistant.application.runner import AssistantRunExecutor
from ai_platform_api.modules.assistant.application.service import (
    AssistantConversationService,
    AssistantRun,
    Conversation,
    Message,
    MessageFeedback,
)
from ai_platform_api.modules.assistant.application.sources import (
    AssistantSourceService,
)
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.streaming.application.service import (
    StreamEvent,
    StreamReplay,
    StreamRunNotFoundError,
    TransactionalStreamService,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/conversations", tags=["助手会话"])


def assistant_conversation_service(request: Request) -> AssistantConversationService:
    """从应用容器解析助手会话服务，避免 Router 自行持有数据库依赖。"""

    service = getattr(request.app.state, "assistant_conversation_service", None)
    if not isinstance(service, AssistantConversationService):
        raise RuntimeError("助手会话服务尚未完成装配")
    return service


def assistant_run_executor(request: Request) -> AssistantRunExecutor:
    """从应用容器解析后台问答执行器，Router 不直接编排检索或模型调用。"""

    executor = getattr(request.app.state, "assistant_run_executor", None)
    if not isinstance(executor, AssistantRunExecutor):
        raise RuntimeError("助手运行执行器尚未完成装配")
    return executor


def streaming_service(request: Request) -> TransactionalStreamService:
    """从应用容器解析事务化流服务，SSE 生成器不会持有长期 Session。"""

    service = getattr(request.app.state, "streaming_service", None)
    if not isinstance(service, TransactionalStreamService):
        raise RuntimeError("流式事件服务尚未完成装配")
    return service


def assistant_source_service(request: Request) -> AssistantSourceService:
    """从应用容器解析来源服务，Router 不直接读取 Retrieval 私有表。"""

    service = getattr(request.app.state, "assistant_source_service", None)
    if not isinstance(service, AssistantSourceService):
        raise RuntimeError("助手来源服务尚未完成装配")
    return service


@router.post(
    "",
    response_model=ConversationResponse,
    operation_id="createAssistantConversation",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
def create_conversation(
    workspace_id: UUID,
    body: CreateConversationRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AssistantConversationService, Depends(assistant_conversation_service)],
) -> ConversationResponse:
    """创建当前账号私有会话；认证、授权和事务由统一服务执行。"""

    _require_workspace_path(context, workspace_id)
    return _conversation(service.create_conversation(context, title=body.title))


@router.get(
    "",
    response_model=ConversationListResponse,
    operation_id="listAssistantConversations",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_conversations(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AssistantConversationService, Depends(assistant_conversation_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ConversationListResponse:
    """列出当前账号创建的私有会话。"""

    _require_workspace_path(context, workspace_id)
    return ConversationListResponse(
        items=[_conversation(item) for item in service.list_conversations(context, limit=limit)]
    )


@router.get(
    "/{conversation_id}/messages",
    response_model=MessageListResponse,
    operation_id="listAssistantMessages",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def list_messages(
    workspace_id: UUID,
    conversation_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AssistantConversationService, Depends(assistant_conversation_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> MessageListResponse:
    """列出当前账号私有会话中的消息。"""

    _require_workspace_path(context, workspace_id)
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
    "/{conversation_id}/runs",
    response_model=AssistantRunListResponse,
    operation_id="listAssistantRuns",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def list_runs(
    workspace_id: UUID,
    conversation_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AssistantConversationService, Depends(assistant_conversation_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> AssistantRunListResponse:
    """列出会话运行，用于页面刷新后恢复活动流和终态。"""

    _require_workspace_path(context, workspace_id)
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
    "/{conversation_id}/messages",
    response_model=UserMessageCreatedResponse,
    operation_id="createAssistantUserMessage",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def create_user_message(
    workspace_id: UUID,
    conversation_id: UUID,
    body: CreateUserMessageRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AssistantConversationService, Depends(assistant_conversation_service)],
    executor: Annotated[AssistantRunExecutor, Depends(assistant_run_executor)],
) -> UserMessageCreatedResponse:
    """幂等创建用户消息并调度一次本地后台运行，重复调度由数据库状态拒绝。"""

    _require_workspace_path(context, workspace_id)
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
    "/{conversation_id}/runs/{run_id}/events",
    operation_id="streamAssistantRunEvents",
    response_class=StreamingResponse,
    responses={
        **error_responses(400, 401, 403, 404, 409, 410, 422, 500),
        200: {
            "description": "按严格序号返回可恢复的助手运行事件",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
    },
)
def stream_run_events(
    workspace_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    conversations: Annotated[
        AssistantConversationService,
        Depends(assistant_conversation_service),
    ],
    streams: Annotated[TransactionalStreamService, Depends(streaming_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    last_event_id: Annotated[UUID | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """授权后回放游标后的事件；重连路径只读取事实，不重新触发模型生成。"""

    _require_workspace_path(context, workspace_id)
    run = conversations.get_run_for_stream(
        context,
        conversation_id=conversation_id,
        run_id=run_id,
    )
    initial_replay = _initial_replay(streams, workspace_id, run, last_event_id)
    return StreamingResponse(
        _stream_frames(
            conversations,
            streams,
            context,
            run,
            last_event_id,
            initial_replay,
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
    "/{conversation_id}/runs/{run_id}/cancel",
    response_model=AssistantRunResponse,
    operation_id="cancelAssistantRun",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def cancel_run(
    workspace_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    conversations: Annotated[
        AssistantConversationService,
        Depends(assistant_conversation_service),
    ],
    streams: Annotated[TransactionalStreamService, Depends(streaming_service)],
) -> AssistantRunResponse:
    """取消当前账号的活动 Run，并尽力关闭对应可恢复流事实。"""

    _require_workspace_path(context, workspace_id)
    run = conversations.cancel_run(
        context,
        conversation_id=conversation_id,
        run_id=run_id,
    )
    if run.status == "cancelled":
        _close_cancelled_stream(streams, run)
    return _run(run)


@router.get(
    "/{conversation_id}/messages/{message_id}/sources",
    response_model=AssistantSourceListResponse,
    operation_id="listAssistantMessageSources",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def list_message_sources(
    workspace_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AssistantSourceService, Depends(assistant_source_service)],
) -> AssistantSourceListResponse:
    """按当前权限和版本重新验证后返回助手消息来源。"""

    _require_workspace_path(context, workspace_id)
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
    "/{conversation_id}/messages/{message_id}/feedback",
    response_model=CurrentMessageFeedbackResponse,
    operation_id="getAssistantMessageFeedback",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def get_message_feedback(
    workspace_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AssistantConversationService, Depends(assistant_conversation_service)],
) -> CurrentMessageFeedbackResponse:
    """读取当前账号对目标助手消息的最新反馈。"""

    _require_workspace_path(context, workspace_id)
    feedback = service.get_feedback(
        context,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    return CurrentMessageFeedbackResponse(
        item=_feedback(feedback) if feedback is not None else None
    )


@router.put(
    "/{conversation_id}/messages/{message_id}/feedback",
    response_model=MessageFeedbackResponse,
    operation_id="submitAssistantMessageFeedback",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def submit_message_feedback(
    workspace_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    body: SubmitMessageFeedbackRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AssistantConversationService, Depends(assistant_conversation_service)],
) -> MessageFeedbackResponse:
    """新增或修订人工反馈，不触发模型调用或自动评分。"""

    _require_workspace_path(context, workspace_id)
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
    "/{conversation_id}/archive",
    response_model=ConversationResponse,
    operation_id="archiveAssistantConversation",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def archive_conversation(
    workspace_id: UUID,
    conversation_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AssistantConversationService, Depends(assistant_conversation_service)],
) -> ConversationResponse:
    """归档当前账号创建且没有活动 Run 的会话。"""

    _require_workspace_path(context, workspace_id)
    return _conversation(service.archive_conversation(context, conversation_id=conversation_id))


def _conversation(value: Conversation) -> ConversationResponse:
    return ConversationResponse(
        conversation_id=value.conversation_id,
        workspace_id=value.workspace_id,
        created_by_account_id=value.created_by_account_id,
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
        agent_release_id=value.agent_release_id,
        runtime_config_version_id=value.runtime_config_version_id,
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


def _require_workspace_path(context: RequestContext, workspace_id: UUID) -> None:
    if context.workspace_id != workspace_id:
        raise AssistantDeniedError


def _initial_replay(
    streams: TransactionalStreamService,
    workspace_id: UUID,
    run: AssistantRun,
    last_event_id: UUID | None,
) -> StreamReplay | None:
    """在响应头发送前校验已有游标；尚未启动的 queued Run 允许等待首个事件。"""

    try:
        return streams.replay(
            workspace_id,
            run.run_id,
            last_event_id,
            now=datetime.now(UTC),
        )
    except StreamRunNotFoundError:
        if last_event_id is not None or run.status not in {"queued", "running"}:
            raise
        return None


def _close_cancelled_stream(
    streams: TransactionalStreamService,
    run: AssistantRun,
) -> None:
    """兼容 Run 尚未建流或已由执行器建流的竞态，并最终保存取消快照。"""

    if run.assistant_message_id is None:
        return
    now = datetime.now(UTC)
    with suppress(PlatformError):
        streams.start_run(
            run.workspace_id,
            run.conversation_id,
            run.assistant_message_id,
            run.run_id,
            now=now,
        )
    # 已存在的活动流仍需继续追加取消事件；已关闭流则由后续操作自然保持终态。
    try:
        streams.append(
            run.run_id,
            "message.failed",
            run.trace_id,
            run.traceparent,
            {"error_code": "RUN_CANCELLED"},
            now=now,
        )
        streams.finish(
            run.run_id,
            "cancelled",
            {"status": "cancelled", "error_code": "RUN_CANCELLED"},
            now=now,
        )
    except PlatformError:
        # 数据库 Run 已是安全终态；流被并发关闭时不能反向回滚取消结果。
        pass


def _stream_frames(
    conversations: AssistantConversationService,
    streams: TransactionalStreamService,
    context: RequestContext,
    run: AssistantRun,
    last_event_id: UUID | None,
    initial_replay: StreamReplay | None,
    *,
    heartbeat_seconds: int,
    poll_interval_ms: int,
) -> Generator[str, None, None]:
    """结合跨实例唤醒和短事务轮询产生 SSE 帧，等待期间不占数据库连接。"""

    replay = initial_replay
    cursor = last_event_id
    next_heartbeat = time.monotonic() + heartbeat_seconds
    subscription = streams.subscribe(run.run_id)
    try:
        while True:
            # 1. 每轮只在实际查询期间持有 Session；Run 未启动时复核助手终态后继续等待。
            if replay is None:
                try:
                    replay = streams.replay(
                        context.workspace_id,
                        run.run_id,
                        cursor,
                        now=datetime.now(UTC),
                    )
                except StreamRunNotFoundError:
                    current = conversations.get_run_for_stream(
                        context,
                        conversation_id=run.conversation_id,
                        run_id=run.run_id,
                    )
                    if current.status not in {"queued", "running"}:
                        return

            # 2. 按数据库序号输出完整协议事件；只在持久化事件后推进回放游标。
            if replay is not None:
                for event in replay.events:
                    yield _event_frame(event)
                    cursor = event.event_id
                if replay.final_run.status != "active":
                    if replay.snapshot_required:
                        yield _snapshot_frame(run, replay)
                    return
                replay = None

            # 3. 心跳不写库、不改游标；Valkey 仅提前结束等待，超时仍轮询 PostgreSQL。
            now = time.monotonic()
            if now >= next_heartbeat:
                yield ": heartbeat\n\n"
                next_heartbeat = now + heartbeat_seconds
            wait_seconds = poll_interval_ms / 1_000
            if subscription is None:
                time.sleep(wait_seconds)
            else:
                subscription.wait(wait_seconds)
    finally:
        if subscription is not None:
            subscription.close()


def _snapshot_frame(run: AssistantRun, replay: StreamReplay) -> str:
    """生成不推进 SSE 游标的恢复快照，客户端后续仍携带最后一个持久化事件 ID。"""

    stream_run = replay.final_run
    snapshot = StreamEvent(
        event_id=uuid5(
            NAMESPACE_URL,
            f"assistant-stream-snapshot:{stream_run.run_id}:{stream_run.last_sequence_no}",
        ),
        event_type="message.snapshot",
        workspace_id=stream_run.workspace_id,
        conversation_id=stream_run.conversation_id,
        message_id=stream_run.message_id,
        run_id=stream_run.run_id,
        sequence_no=max(1, stream_run.last_sequence_no),
        occurred_at=datetime.now(UTC),
        expires_at=stream_run.expires_at,
        trace_id=run.trace_id,
        traceparent=run.traceparent,
        payload=stream_run.final_payload or {"status": stream_run.status},
    )
    # 快照不是持久化事件，省略 SSE id 行，避免浏览器把派生 ID 当作下次回放游标。
    return _event_frame(snapshot, include_sse_id=False)


def _event_frame(event: StreamEvent, *, include_sse_id: bool = True) -> str:
    envelope = {
        "event_id": str(event.event_id),
        "event_type": event.event_type,
        "schema_version": 1,
        "workspace_id": str(event.workspace_id),
        "conversation_id": str(event.conversation_id),
        "message_id": str(event.message_id),
        "run_id": str(event.run_id),
        "sequence_no": event.sequence_no,
        "occurred_at": event.occurred_at.isoformat(),
        "expires_at": event.expires_at.isoformat(),
        "trace_id": event.trace_id,
        "payload": event.payload,
    }
    data = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    event_line = f"event: {event.event_type}\ndata: {data}\n\n"
    return f"id: {event.event_id}\n{event_line}" if include_sse_id else event_line
