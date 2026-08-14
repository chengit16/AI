from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.knowledge.api.schemas import (
    CreateDocumentRequest,
    CreateDocumentVersionRequest,
    CreateKnowledgeBaseRequest,
    DocumentCreatedResponse,
    DocumentResponse,
    DocumentSourceResponse,
    DocumentVersionCreatedResponse,
    DocumentVersionResponse,
    KnowledgeBaseResponse,
    MarkDocumentVersionReadyRequest,
)
from ai_platform_api.modules.knowledge.application.facts import (
    Document,
    DocumentSource,
    DocumentVersion,
    KnowledgeBase,
    KnowledgeDeniedError,
    KnowledgeFactService,
)

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["知识事实"])


def knowledge_fact_service(request: Request) -> KnowledgeFactService:
    service = getattr(request.app.state, "knowledge_fact_service", None)
    if not isinstance(service, KnowledgeFactService):
        raise RuntimeError("知识事实服务尚未完成装配")
    return service


@router.post(
    "/knowledge-bases",
    response_model=KnowledgeBaseResponse,
    operation_id="createKnowledgeBase",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
def create_knowledge_base(
    workspace_id: UUID,
    body: CreateKnowledgeBaseRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeFactService, Depends(knowledge_fact_service)],
) -> KnowledgeBaseResponse:
    _require_workspace_path(context, workspace_id)
    return _knowledge_base(
        service.create_knowledge_base(
            context,
            name=body.name,
            description=body.description,
            default_visibility=body.default_visibility,
            department_ids=frozenset(body.department_ids),
            default_security_level=body.default_security_level,
        )
    )


@router.delete(
    "/knowledge-bases/{knowledge_base_id}",
    response_model=KnowledgeBaseResponse,
    operation_id="deleteKnowledgeBase",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def delete_knowledge_base(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeFactService, Depends(knowledge_fact_service)],
) -> KnowledgeBaseResponse:
    _require_workspace_path(context, workspace_id)
    return _knowledge_base(
        service.delete_knowledge_base(context, knowledge_base_id=knowledge_base_id)
    )


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents",
    response_model=DocumentCreatedResponse,
    operation_id="createKnowledgeDocument",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def create_document(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    body: CreateDocumentRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeFactService, Depends(knowledge_fact_service)],
) -> DocumentCreatedResponse:
    _require_workspace_path(context, workspace_id)
    document, version, source = service.create_document(
        context,
        knowledge_base_id=knowledge_base_id,
        title=body.title,
        source_kind=body.source_kind,
        source_name=body.source_name,
        original_object_key=body.original_object_key,
        source_path=body.source_path,
        source_url=body.source_url,
        external_source_id=body.external_source_id,
        captured_at=body.captured_at,
        visibility=body.visibility,
        department_ids=(
            frozenset(body.department_ids) if body.department_ids is not None else None
        ),
        security_level=body.security_level,
        permission_labels=frozenset(body.permission_labels),
    )
    return DocumentCreatedResponse(
        document=_document(document),
        document_version=_document_version(version),
        source=_source(source),
    )


@router.delete(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}",
    response_model=DocumentResponse,
    operation_id="deleteKnowledgeDocument",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def delete_document(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeFactService, Depends(knowledge_fact_service)],
) -> DocumentResponse:
    _require_workspace_path(context, workspace_id)
    return _document(
        service.delete_document(
            context,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
    )


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/versions",
    response_model=DocumentVersionCreatedResponse,
    operation_id="createKnowledgeDocumentVersion",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def create_document_version(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
    body: CreateDocumentVersionRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeFactService, Depends(knowledge_fact_service)],
) -> DocumentVersionCreatedResponse:
    _require_workspace_path(context, workspace_id)
    version, source = service.create_document_version(
        context,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        source_kind=body.source_kind,
        source_name=body.source_name,
        original_object_key=body.original_object_key,
        source_path=body.source_path,
        source_url=body.source_url,
        external_source_id=body.external_source_id,
        captured_at=body.captured_at,
    )
    return DocumentVersionCreatedResponse(
        document_version=_document_version(version),
        source=_source(source),
    )


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/versions/{document_version_id}/ready",
    response_model=DocumentVersionResponse,
    operation_id="markKnowledgeDocumentVersionReady",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def mark_document_version_ready(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
    document_version_id: UUID,
    body: MarkDocumentVersionReadyRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeFactService, Depends(knowledge_fact_service)],
) -> DocumentVersionResponse:
    _require_workspace_path(context, workspace_id)
    return _document_version(
        service.mark_document_version_ready(
            context,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            document_version_id=document_version_id,
            content_hash=body.content_hash,
        )
    )


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/versions/{document_version_id}/publish",
    response_model=DocumentVersionResponse,
    operation_id="publishKnowledgeDocumentVersion",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def publish_document_version(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
    document_version_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeFactService, Depends(knowledge_fact_service)],
) -> DocumentVersionResponse:
    _require_workspace_path(context, workspace_id)
    return _document_version(
        service.publish_document_version(
            context,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            document_version_id=document_version_id,
        )
    )


def _knowledge_base(value: KnowledgeBase) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(
        knowledge_base_id=value.knowledge_base_id,
        workspace_id=value.workspace_id,
        name=value.name,
        description=value.description,
        default_visibility=value.default_visibility,
        department_ids=sorted(value.department_ids, key=lambda item: item.int),
        default_security_level=value.default_security_level,
        status=value.status,
        created_by_account_id=value.created_by_account_id,
        created_at=value.created_at,
        updated_at=value.updated_at,
        deleted_at=value.deleted_at,
        version=value.version,
    )


def _document(value: Document) -> DocumentResponse:
    return DocumentResponse(
        document_id=value.document_id,
        workspace_id=value.workspace_id,
        knowledge_base_id=value.knowledge_base_id,
        title=value.title,
        visibility=value.visibility,
        department_ids=sorted(value.department_ids, key=lambda item: item.int),
        security_level=value.security_level,
        permission_labels=sorted(value.permission_labels),
        status=value.status,
        created_by_account_id=value.created_by_account_id,
        created_at=value.created_at,
        updated_at=value.updated_at,
        deleted_at=value.deleted_at,
        version=value.version,
    )


def _document_version(value: DocumentVersion) -> DocumentVersionResponse:
    return DocumentVersionResponse(
        document_version_id=value.document_version_id,
        workspace_id=value.workspace_id,
        document_id=value.document_id,
        version_number=value.version_number,
        status=value.status,
        content_hash=value.content_hash,
        created_by_account_id=value.created_by_account_id,
        created_at=value.created_at,
        published_at=value.published_at,
        record_version=value.record_version,
    )


def _source(value: DocumentSource) -> DocumentSourceResponse:
    # 来源地址和对象键是字段级敏感事实，写入响应不回显这些定位信息。
    return DocumentSourceResponse(
        source_id=value.source_id,
        source_kind=value.source_kind,
        source_name=value.source_name,
        captured_at=value.captured_at,
        created_at=value.created_at,
    )


def _require_workspace_path(context: RequestContext, workspace_id: UUID) -> None:
    if workspace_id != context.workspace_id:
        raise KnowledgeDeniedError
