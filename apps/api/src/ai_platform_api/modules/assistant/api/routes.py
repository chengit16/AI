"""映射私有会话、不可变消息与助手运行排队 HTTP 协议。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.assistant.api.schemas import (
    AssistantRunResponse,
    ConversationListResponse,
    ConversationResponse,
    CreateConversationRequest,
    CreateUserMessageRequest,
    MessageListResponse,
    MessagePartResponse,
    MessageResponse,
    UserMessageCreatedResponse,
)
from ai_platform_api.modules.assistant.application.errors import AssistantDeniedError
from ai_platform_api.modules.assistant.application.service import (
    AssistantConversationService,
    AssistantRun,
    Conversation,
    Message,
)
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context

router = APIRouter(prefix="/workspaces/{workspace_id}/conversations", tags=["助手会话"])


def assistant_conversation_service(request: Request) -> AssistantConversationService:
    """从应用容器解析助手会话服务，避免 Router 自行持有数据库依赖。"""

    service = getattr(request.app.state, "assistant_conversation_service", None)
    if not isinstance(service, AssistantConversationService):
        raise RuntimeError("助手会话服务尚未完成装配")
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
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AssistantConversationService, Depends(assistant_conversation_service)],
) -> UserMessageCreatedResponse:
    """幂等创建用户消息并返回冻结版本后的 queued Run。"""

    _require_workspace_path(context, workspace_id)
    submission = service.create_user_message(
        context,
        conversation_id=conversation_id,
        texts=tuple(part.text for part in body.parts),
        idempotency_key=idempotency_key,
    )
    return UserMessageCreatedResponse(
        message=_message(submission.message),
        run=_run(submission.run),
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


def _require_workspace_path(context: RequestContext, workspace_id: UUID) -> None:
    if context.workspace_id != workspace_id:
        raise AssistantDeniedError
