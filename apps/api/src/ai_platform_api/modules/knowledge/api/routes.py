"""映射知识库、文档版本、上传、发布和入库任务 HTTP 协议。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from starlette.concurrency import run_in_threadpool

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.config import Settings, get_settings
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.knowledge.api.schemas import (
    CreateDocumentRequest,
    CreateDocumentVersionRequest,
    CreateKnowledgeBaseRequest,
    CreateKnowledgeFolderRequest,
    CreateKnowledgeTagRequest,
    DocumentCreatedResponse,
    DocumentResponse,
    DocumentSourceResponse,
    DocumentUploadResponse,
    DocumentVersionCreatedResponse,
    DocumentVersionResponse,
    DocumentVersionUploadResponse,
    IngestionJobListResponse,
    IngestionJobResponse,
    KnowledgeBaseListResponse,
    KnowledgeBaseResponse,
    KnowledgeBaseSummaryResponse,
    KnowledgeDocumentBindingsRequest,
    KnowledgeDocumentFolderBindingResponse,
    KnowledgeDocumentListResponse,
    KnowledgeDocumentSummaryResponse,
    KnowledgeDocumentTagBindingResponse,
    KnowledgeFavoriteListResponse,
    KnowledgeFavoriteRequest,
    KnowledgeFavoriteResponse,
    KnowledgeFolderListResponse,
    KnowledgeFolderResponse,
    KnowledgeTagListResponse,
    KnowledgeTagResponse,
    KnowledgeTrashListResponse,
    MarkDocumentVersionReadyRequest,
    MoveKnowledgeFolderRequest,
    UpdateKnowledgeFolderRequest,
    UpdateKnowledgeTagRequest,
    UploadMetadataResponse,
)
from ai_platform_api.modules.knowledge.application.facts import (
    Document,
    DocumentSource,
    DocumentVersion,
    KnowledgeBase,
    KnowledgeDeniedError,
    KnowledgeFactService,
)
from ai_platform_api.modules.knowledge.application.management import (
    IngestionJob,
    KnowledgeDocumentSummary,
    KnowledgeManagementService,
)
from ai_platform_api.modules.knowledge.application.organization import (
    KnowledgeFolder,
    KnowledgeOrganizationService,
    KnowledgeTag,
)
from ai_platform_api.modules.knowledge.application.uploads import (
    KnowledgeUploadService,
    UploadFileTooLargeError,
)

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["知识事实"])


def knowledge_fact_service(request: Request) -> KnowledgeFactService:
    """处理知识事实服务；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    service = getattr(request.app.state, "knowledge_fact_service", None)
    if not isinstance(service, KnowledgeFactService):
        raise RuntimeError("知识事实服务尚未完成装配")
    return service


def knowledge_upload_service(request: Request) -> KnowledgeUploadService:
    """处理知识上传服务；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    service = getattr(request.app.state, "knowledge_upload_service", None)
    if not isinstance(service, KnowledgeUploadService):
        raise RuntimeError("知识上传服务尚未完成装配")
    return service


def knowledge_management_service(request: Request) -> KnowledgeManagementService:
    """处理知识管理服务；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    service = getattr(request.app.state, "knowledge_management_service", None)
    if not isinstance(service, KnowledgeManagementService):
        raise RuntimeError("知识管理服务尚未完成装配")
    return service


def knowledge_organization_service(request: Request) -> KnowledgeOrganizationService:
    """获取目录、标签、收藏和回收站服务；路由不直接访问 Repository。"""

    service = getattr(request.app.state, "knowledge_organization_service", None)
    if not isinstance(service, KnowledgeOrganizationService):
        raise RuntimeError("知识组织服务尚未完成装配")
    return service


@router.get(
    "/knowledge-folders",
    response_model=KnowledgeFolderListResponse,
    operation_id="listKnowledgeFolders",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_knowledge_folders(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
    include_deleted: Annotated[bool, Query()] = False,
) -> KnowledgeFolderListResponse:
    """列出工作空间目录；删除目录只在显式查询时返回。"""

    _require_workspace_path(context, workspace_id)
    return KnowledgeFolderListResponse(
        items=[
            _folder(item) for item in service.list_folders(context, include_deleted=include_deleted)
        ]
    )


@router.post(
    "/knowledge-folders",
    response_model=KnowledgeFolderResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createKnowledgeFolder",
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
def create_knowledge_folder(
    workspace_id: UUID,
    body: CreateKnowledgeFolderRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
) -> KnowledgeFolderResponse:
    """创建工作空间目录。"""

    _require_workspace_path(context, workspace_id)
    return _folder(
        service.create_folder(context, name=body.name, parent_folder_id=body.parent_folder_id)
    )


@router.patch(
    "/knowledge-folders/{folder_id}",
    response_model=KnowledgeFolderResponse,
    operation_id="renameKnowledgeFolder",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def rename_knowledge_folder(
    workspace_id: UUID,
    folder_id: UUID,
    body: UpdateKnowledgeFolderRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
) -> KnowledgeFolderResponse:
    """重命名工作空间目录。"""

    _require_workspace_path(context, workspace_id)
    return _folder(service.rename_folder(context, folder_id=folder_id, name=body.name))


@router.post(
    "/knowledge-folders/{folder_id}/move",
    response_model=KnowledgeFolderResponse,
    operation_id="moveKnowledgeFolder",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def move_knowledge_folder(
    workspace_id: UUID,
    folder_id: UUID,
    body: MoveKnowledgeFolderRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
) -> KnowledgeFolderResponse:
    """移动目录并拒绝跨空间或循环父子关系。"""

    _require_workspace_path(context, workspace_id)
    return _folder(
        service.move_folder(context, folder_id=folder_id, parent_folder_id=body.parent_folder_id)
    )


@router.delete(
    "/knowledge-folders/{folder_id}",
    response_model=KnowledgeFolderResponse,
    operation_id="deleteKnowledgeFolder",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def delete_knowledge_folder(
    workspace_id: UUID,
    folder_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
) -> KnowledgeFolderResponse:
    """软删除空目录，保留恢复入口。"""

    _require_workspace_path(context, workspace_id)
    return _folder(service.delete_folder(context, folder_id=folder_id))


@router.post(
    "/knowledge-folders/{folder_id}/restore",
    response_model=KnowledgeFolderResponse,
    operation_id="restoreKnowledgeFolder",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def restore_knowledge_folder(
    workspace_id: UUID,
    folder_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
) -> KnowledgeFolderResponse:
    """恢复目录；父目录必须仍处于活动状态。"""

    _require_workspace_path(context, workspace_id)
    return _folder(service.restore_folder(context, folder_id=folder_id))


@router.delete(
    "/knowledge-folders/{folder_id}/permanent",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="purgeKnowledgeFolder",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def purge_knowledge_folder(
    workspace_id: UUID,
    folder_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
) -> None:
    """永久删除已进入回收站且没有子项或文档绑定的目录。"""

    _require_workspace_path(context, workspace_id)
    service.permanently_delete_folder(context, folder_id=folder_id)


@router.get(
    "/knowledge-tags",
    response_model=KnowledgeTagListResponse,
    operation_id="listKnowledgeTags",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_knowledge_tags(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
    include_deleted: Annotated[bool, Query()] = False,
) -> KnowledgeTagListResponse:
    """列出工作空间标签。"""

    _require_workspace_path(context, workspace_id)
    return KnowledgeTagListResponse(
        items=[_tag(item) for item in service.list_tags(context, include_deleted=include_deleted)]
    )


@router.post(
    "/knowledge-tags",
    response_model=KnowledgeTagResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createKnowledgeTag",
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
def create_knowledge_tag(
    workspace_id: UUID,
    body: CreateKnowledgeTagRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
) -> KnowledgeTagResponse:
    """创建工作空间标签。"""

    _require_workspace_path(context, workspace_id)
    return _tag(service.create_tag(context, name=body.name, color=body.color))


@router.patch(
    "/knowledge-tags/{tag_id}",
    response_model=KnowledgeTagResponse,
    operation_id="updateKnowledgeTag",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def update_knowledge_tag(
    workspace_id: UUID,
    tag_id: UUID,
    body: UpdateKnowledgeTagRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
) -> KnowledgeTagResponse:
    """更新标签名称和展示颜色。"""

    _require_workspace_path(context, workspace_id)
    return _tag(service.rename_tag(context, tag_id=tag_id, name=body.name, color=body.color))


@router.delete(
    "/knowledge-tags/{tag_id}",
    response_model=KnowledgeTagResponse,
    operation_id="deleteKnowledgeTag",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def delete_knowledge_tag(
    workspace_id: UUID,
    tag_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
) -> KnowledgeTagResponse:
    """软删除标签，已有绑定在读取时隐藏。"""

    _require_workspace_path(context, workspace_id)
    return _tag(service.delete_tag(context, tag_id=tag_id))


@router.post(
    "/knowledge-tags/{tag_id}/restore",
    response_model=KnowledgeTagResponse,
    operation_id="restoreKnowledgeTag",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def restore_knowledge_tag(
    workspace_id: UUID,
    tag_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
) -> KnowledgeTagResponse:
    """恢复标签；同名活动标签存在时拒绝恢复。"""

    _require_workspace_path(context, workspace_id)
    return _tag(service.restore_tag(context, tag_id=tag_id))


@router.post(
    "/documents/{document_id}/folders",
    response_model=KnowledgeDocumentFolderBindingResponse,
    operation_id="bindKnowledgeDocumentFolders",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def bind_document_folders(
    workspace_id: UUID,
    document_id: UUID,
    body: KnowledgeDocumentBindingsRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
) -> KnowledgeDocumentFolderBindingResponse:
    """幂等绑定文档目录。"""

    _require_workspace_path(context, workspace_id)
    return KnowledgeDocumentFolderBindingResponse(
        items=[
            _folder(item)
            for item in service.bind_folder(context, document_id=document_id, folder_ids=body.ids)
        ]
    )


@router.delete(
    "/documents/{document_id}/folders/{folder_id}",
    response_model=KnowledgeDocumentFolderBindingResponse,
    operation_id="unbindKnowledgeDocumentFolder",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def unbind_document_folder(
    workspace_id: UUID,
    document_id: UUID,
    folder_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
) -> KnowledgeDocumentFolderBindingResponse:
    """幂等解绑文档目录。"""

    _require_workspace_path(context, workspace_id)
    return KnowledgeDocumentFolderBindingResponse(
        items=[
            _folder(item)
            for item in service.unbind_folder(context, document_id=document_id, folder_id=folder_id)
        ]
    )


@router.post(
    "/documents/{document_id}/tags",
    response_model=KnowledgeDocumentTagBindingResponse,
    operation_id="bindKnowledgeDocumentTags",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def bind_document_tags(
    workspace_id: UUID,
    document_id: UUID,
    body: KnowledgeDocumentBindingsRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
) -> KnowledgeDocumentTagBindingResponse:
    """幂等绑定文档标签。"""

    _require_workspace_path(context, workspace_id)
    return KnowledgeDocumentTagBindingResponse(
        items=[
            _tag(item)
            for item in service.bind_tag(context, document_id=document_id, tag_ids=body.ids)
        ]
    )


@router.delete(
    "/documents/{document_id}/tags/{tag_id}",
    response_model=KnowledgeDocumentTagBindingResponse,
    operation_id="unbindKnowledgeDocumentTag",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def unbind_document_tag(
    workspace_id: UUID,
    document_id: UUID,
    tag_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
) -> KnowledgeDocumentTagBindingResponse:
    """幂等解绑文档标签。"""

    _require_workspace_path(context, workspace_id)
    return KnowledgeDocumentTagBindingResponse(
        items=[
            _tag(item)
            for item in service.unbind_tag(context, document_id=document_id, tag_id=tag_id)
        ]
    )


@router.put(
    "/documents/{document_id}/favorite",
    response_model=KnowledgeFavoriteResponse,
    operation_id="setKnowledgeDocumentFavorite",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def set_document_favorite(
    workspace_id: UUID,
    document_id: UUID,
    body: KnowledgeFavoriteRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
) -> KnowledgeFavoriteResponse:
    """幂等设置文档收藏状态。"""

    _require_workspace_path(context, workspace_id)
    return KnowledgeFavoriteResponse(
        document_id=document_id,
        favorite=service.set_favorite(context, document_id=document_id, favorite=body.favorite),
    )


@router.get(
    "/favorites",
    response_model=KnowledgeFavoriteListResponse,
    operation_id="listKnowledgeFavorites",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_document_favorites(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> KnowledgeFavoriteListResponse:
    """列出当前用户的活动文档收藏。"""

    _require_workspace_path(context, workspace_id)
    return KnowledgeFavoriteListResponse(
        document_ids=list(service.list_favorites(context, limit=limit))
    )


@router.get(
    "/trash",
    response_model=KnowledgeTrashListResponse,
    operation_id="listKnowledgeTrash",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_knowledge_trash(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> KnowledgeTrashListResponse:
    """列出工作空间回收站文档。"""

    _require_workspace_path(context, workspace_id)
    return KnowledgeTrashListResponse(
        items=[_document(item) for item in service.list_trash(context, limit=limit)]
    )


@router.post(
    "/trash/documents/{document_id}/restore",
    response_model=DocumentResponse,
    operation_id="restoreKnowledgeDocument",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def restore_knowledge_document(
    workspace_id: UUID,
    document_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
) -> DocumentResponse:
    """恢复回收站文档。"""

    _require_workspace_path(context, workspace_id)
    return _document(service.restore_document(context, document_id=document_id))


@router.delete(
    "/trash/documents/{document_id}/permanent",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="purgeKnowledgeDocument",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def purge_knowledge_document(
    workspace_id: UUID,
    document_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeOrganizationService, Depends(knowledge_organization_service)],
) -> None:
    """永久清理已删除文档的数据库事实，并由 Outbox 驱动外部清理。"""

    _require_workspace_path(context, workspace_id)
    service.permanently_delete_document(context, document_id=document_id)


@router.get(
    "/knowledge-bases",
    response_model=KnowledgeBaseListResponse,
    operation_id="listKnowledgeBases",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_knowledge_bases(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeManagementService, Depends(knowledge_management_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> KnowledgeBaseListResponse:
    """列出知识库集合；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    _require_workspace_path(context, workspace_id)
    items = service.list_knowledge_bases(context, limit=limit)
    return KnowledgeBaseListResponse(items=[_knowledge_base_summary(item) for item in items])


@router.get(
    "/knowledge-bases/{knowledge_base_id}/documents",
    response_model=KnowledgeDocumentListResponse,
    operation_id="listKnowledgeDocuments",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def list_documents(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeManagementService, Depends(knowledge_management_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> KnowledgeDocumentListResponse:
    """列出文档集合；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    _require_workspace_path(context, workspace_id)
    return KnowledgeDocumentListResponse(
        items=[
            _document_summary(item)
            for item in service.list_documents(
                context,
                knowledge_base_id=knowledge_base_id,
                limit=limit,
            )
        ]
    )


@router.get(
    "/knowledge-bases/{knowledge_base_id}/ingestion-jobs",
    response_model=IngestionJobListResponse,
    operation_id="listKnowledgeIngestionJobs",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def list_ingestion_jobs(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeManagementService, Depends(knowledge_management_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> IngestionJobListResponse:
    """列出入库任务集合；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    _require_workspace_path(context, workspace_id)
    return IngestionJobListResponse(
        items=[
            _ingestion_job(item)
            for item in service.list_ingestion_jobs(
                context,
                knowledge_base_id=knowledge_base_id,
                limit=limit,
            )
        ]
    )


@router.post(
    "/ingestion-jobs/{ingestion_job_id}/retry",
    response_model=IngestionJobResponse,
    operation_id="retryKnowledgeIngestionJob",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def retry_ingestion_job(
    workspace_id: UUID,
    ingestion_job_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeManagementService, Depends(knowledge_management_service)],
) -> IngestionJobResponse:
    """重试入库任务；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    _require_workspace_path(context, workspace_id)
    return _ingestion_job(service.retry_ingestion_job(context, ingestion_job_id=ingestion_job_id))


@router.post(
    "/ingestion-jobs/{ingestion_job_id}/cancel",
    response_model=IngestionJobResponse,
    operation_id="cancelKnowledgeIngestionJob",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def cancel_ingestion_job(
    workspace_id: UUID,
    ingestion_job_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeManagementService, Depends(knowledge_management_service)],
) -> IngestionJobResponse:
    """取消非终态入库任务；状态机、审计和 Worker 失租由应用服务保证。"""

    _require_workspace_path(context, workspace_id)
    return _ingestion_job(service.cancel_ingestion_job(context, ingestion_job_id=ingestion_job_id))


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
    """创建知识库；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

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
    """删除知识库；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

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
    """创建文档；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    _require_workspace_path(context, workspace_id)
    document, version, source = service.create_document(
        context,
        knowledge_base_id=knowledge_base_id,
        title=body.title,
        source_kind=body.source_kind,
        source_name=body.source_name,
        original_object_key=None,
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


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/upload",
    response_model=DocumentUploadResponse,
    operation_id="uploadKnowledgeDocument",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 413, 415, 422, 500, 503),
)
async def upload_document(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeUploadService, Depends(knowledge_upload_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File(description="待安全检查的文档原件")],
    title: Annotated[str, Form(min_length=1, max_length=255)],
    visibility: Annotated[Literal["private", "workspace", "departments"] | None, Form()] = None,
    department_ids: Annotated[list[UUID] | None, Form()] = None,
    security_level: Annotated[
        Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"] | None, Form()
    ] = None,
    permission_labels: Annotated[list[str] | None, Form()] = None,
) -> DocumentUploadResponse:
    """上传文档；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    _require_workspace_path(context, workspace_id)
    result = await run_in_threadpool(
        service.upload_document,
        context,
        knowledge_base_id=knowledge_base_id,
        title=title,
        file_name=file.filename or "",
        declared_media_type=file.content_type,
        content=await _read_bounded_upload(file, settings.upload_max_file_size_bytes),
        visibility=visibility,
        department_ids=frozenset(department_ids) if department_ids is not None else None,
        security_level=security_level,
        permission_labels=frozenset(permission_labels or ()),
    )
    return DocumentUploadResponse(
        document=_document(result.document),
        document_version=_document_version(result.document_version),
        source=_source(result.source),
        upload=UploadMetadataResponse(
            media_type=result.media_type,
            size_bytes=result.size_bytes,
            content_hash=result.content_hash,
        ),
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
    """删除文档；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

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
    """创建文档版本；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    _require_workspace_path(context, workspace_id)
    version, source = service.create_document_version(
        context,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        source_kind=body.source_kind,
        source_name=body.source_name,
        original_object_key=None,
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
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/versions/upload",
    response_model=DocumentVersionUploadResponse,
    operation_id="uploadKnowledgeDocumentVersion",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 413, 415, 422, 500, 503),
)
async def upload_document_version(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[KnowledgeUploadService, Depends(knowledge_upload_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File(description="待安全检查的新版本原件")],
) -> DocumentVersionUploadResponse:
    """上传文档版本；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    _require_workspace_path(context, workspace_id)
    result = await run_in_threadpool(
        service.upload_document_version,
        context,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        file_name=file.filename or "",
        declared_media_type=file.content_type,
        content=await _read_bounded_upload(file, settings.upload_max_file_size_bytes),
    )
    return DocumentVersionUploadResponse(
        document_version=_document_version(result.document_version),
        source=_source(result.source),
        upload=UploadMetadataResponse(
            media_type=result.media_type,
            size_bytes=result.size_bytes,
            content_hash=result.content_hash,
        ),
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
    """标记文档版本就绪；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

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
    """发布文档版本；仅转换协议数据，认证授权和事务由应用服务统一执行。"""

    _require_workspace_path(context, workspace_id)
    return _document_version(
        service.publish_document_version(
            context,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            document_version_id=document_version_id,
        )
    )


def _folder(value: KnowledgeFolder) -> KnowledgeFolderResponse:
    """将目录领域对象映射为不暴露持久化细节的响应。"""

    return KnowledgeFolderResponse(
        folder_id=value.folder_id,
        workspace_id=value.workspace_id,
        name=value.name,
        parent_folder_id=value.parent_folder_id,
        created_by_account_id=value.created_by_account_id,
        created_at=value.created_at,
        updated_at=value.updated_at,
        status=value.status,
        deleted_at=value.deleted_at,
        version=value.version,
        is_default=value.is_default,
    )


def _tag(value: KnowledgeTag) -> KnowledgeTagResponse:
    """将标签领域对象映射为稳定响应。"""

    return KnowledgeTagResponse(
        tag_id=value.tag_id,
        workspace_id=value.workspace_id,
        name=value.name,
        color=value.color,
        created_by_account_id=value.created_by_account_id,
        created_at=value.created_at,
        updated_at=value.updated_at,
        status=value.status,
        deleted_at=value.deleted_at,
        version=value.version,
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


def _knowledge_base_summary(value: KnowledgeBase) -> KnowledgeBaseSummaryResponse:
    return KnowledgeBaseSummaryResponse(
        knowledge_base_id=value.knowledge_base_id,
        name=value.name,
        description=value.description,
        default_visibility=value.default_visibility,
        default_security_level=value.default_security_level,
        updated_at=value.updated_at,
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


def _document_summary(value: KnowledgeDocumentSummary) -> KnowledgeDocumentSummaryResponse:
    return KnowledgeDocumentSummaryResponse(
        document_id=value.document.document_id,
        title=value.document.title,
        visibility=value.document.visibility,
        security_level=value.document.security_level,
        updated_at=value.document.updated_at,
        latest_version=_document_version(value.latest_version),
        source_id=value.source_id,
        source_kind=value.source_kind,
        source_name=value.source_name,
        current_document_version_id=value.current_document_version_id,
        folder_id=value.folder_id,
        tag_ids=list(value.tag_ids),
        is_favorite=value.is_favorite,
    )


def _ingestion_job(value: IngestionJob) -> IngestionJobResponse:
    return IngestionJobResponse(
        ingestion_job_id=value.ingestion_job_id,
        knowledge_base_id=value.knowledge_base_id,
        document_id=value.document_id,
        document_version_id=value.document_version_id,
        source_id=value.source_id,
        source_name=value.source_name,
        source_media_type=value.source_media_type,
        status=value.status,
        attempt_count=value.attempt_count,
        max_attempts=value.max_attempts,
        available_at=value.available_at,
        started_at=value.started_at,
        completed_at=value.completed_at,
        failure_stage=value.failure_stage,
        error_code=value.error_code,
        error_message=value.error_message,
        parsed_content_hash=value.parsed_content_hash,
        parser_name=value.parser_name,
        ocr_used=value.ocr_used,
        page_count=value.page_count,
        block_count=value.block_count,
        can_retry_manually=value.can_retry_manually,
        can_cancel=value.can_cancel,
        manual_retry_count=value.manual_retry_count,
        last_retried_at=value.last_retried_at,
        cancelled_at=value.cancelled_at,
        created_at=value.created_at,
        updated_at=value.updated_at,
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


async def _read_bounded_upload(file: UploadFile, max_size_bytes: int) -> bytes:
    chunks: list[bytes] = []
    received = 0
    while True:
        chunk = await file.read(min(1024 * 1024, max_size_bytes + 1 - received))
        if not chunk:
            return b"".join(chunks)
        received += len(chunk)
        if received > max_size_bytes:
            raise UploadFileTooLargeError
        chunks.append(chunk)
