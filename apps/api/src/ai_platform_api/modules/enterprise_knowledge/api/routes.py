"""映射企业分类、团队知识域和授权范围投影 HTTP 协议。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.enterprise_knowledge.api.dependencies import (
    enterprise_knowledge_service,
    trusted_request_context,
)
from ai_platform_api.modules.enterprise_knowledge.api.schemas import (
    CreateEnterpriseCategoryRequest,
    CreateTeamKnowledgeDomainRequest,
    EnterpriseCategoryResponse,
    EnterpriseDepartmentOptionResponse,
    EnterpriseDocumentOptionResponse,
    EnterpriseKnowledgeBaseOptionResponse,
    EnterpriseKnowledgePortalResponse,
    EnterpriseKnowledgeStatisticsResponse,
    EnterpriseMemberOptionResponse,
    RagPolicyResponse,
    ReplaceCategoryDocumentsRequest,
    ReplaceKnowledgeDomainScopeRequest,
    ResolvedKnowledgeDomainScopeResponse,
    TeamKnowledgeDomainResponse,
    UpdateEnterpriseCategoryRequest,
    UpdateTeamKnowledgeDomainRequest,
    VersionedArchiveRequest,
)
from ai_platform_api.modules.enterprise_knowledge.application.service import (
    EnterpriseKnowledgeService,
)
from ai_platform_api.modules.enterprise_knowledge.application.views import (
    EnterpriseCategoryResultView,
    EnterpriseKnowledgePortalView,
    ResolvedKnowledgeDomainScopeView,
    TeamKnowledgeDomainView,
)

router = APIRouter(prefix="/workspaces", tags=["企业知识治理"])


@router.get(
    "/{workspace_id}/enterprise-knowledge",
    response_model=EnterpriseKnowledgePortalResponse,
    operation_id="getEnterpriseKnowledgePortal",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def get_enterprise_knowledge_portal(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseKnowledgeService, Depends(enterprise_knowledge_service)],
) -> EnterpriseKnowledgePortalResponse:
    """读取企业知识门户统一快照，字段遮罩与空间隔离由后端执行。"""

    return _portal_response(service.get_portal(context, workspace_id=workspace_id))


@router.post(
    "/{workspace_id}/enterprise-categories",
    response_model=EnterpriseCategoryResponse,
    operation_id="createEnterpriseCategory",
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
def create_enterprise_category(
    workspace_id: UUID,
    body: CreateEnterpriseCategoryRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseKnowledgeService, Depends(enterprise_knowledge_service)],
) -> EnterpriseCategoryResponse:
    """创建企业分类及初始文档绑定，绑定不会修改文档权限。"""

    category = service.create_category(
        context,
        workspace_id=workspace_id,
        name=body.name,
        description=body.description,
        parent_category_id=body.parent_category_id,
        visibility=body.visibility,
        department_ids=tuple(body.department_ids),
        document_ids=tuple(body.document_ids),
    )
    return _category_view_response(category)


@router.put(
    "/{workspace_id}/enterprise-categories/{category_id}",
    response_model=EnterpriseCategoryResponse,
    operation_id="updateEnterpriseCategory",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def update_enterprise_category(
    workspace_id: UUID,
    category_id: UUID,
    body: UpdateEnterpriseCategoryRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseKnowledgeService, Depends(enterprise_knowledge_service)],
) -> EnterpriseCategoryResponse:
    """按版本更新分类层级和独立可见策略。"""

    category = service.update_category(
        context,
        workspace_id=workspace_id,
        category_id=category_id,
        expected_version=body.expected_version,
        name=body.name,
        description=body.description,
        parent_category_id=body.parent_category_id,
        visibility=body.visibility,
        department_ids=tuple(body.department_ids),
    )
    return _category_view_response(category)


@router.post(
    "/{workspace_id}/enterprise-categories/{category_id}/archive",
    response_model=EnterpriseCategoryResponse,
    operation_id="archiveEnterpriseCategory",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def archive_enterprise_category(
    workspace_id: UUID,
    category_id: UUID,
    body: VersionedArchiveRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseKnowledgeService, Depends(enterprise_knowledge_service)],
) -> EnterpriseCategoryResponse:
    """归档无活动子分类的分类，保留历史关系供审计导出。"""

    return _category_view_response(
        service.archive_category(
            context,
            workspace_id=workspace_id,
            category_id=category_id,
            expected_version=body.expected_version,
        )
    )


@router.put(
    "/{workspace_id}/enterprise-categories/{category_id}/documents",
    response_model=EnterpriseCategoryResponse,
    operation_id="replaceEnterpriseCategoryDocuments",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def replace_enterprise_category_documents(
    workspace_id: UUID,
    category_id: UUID,
    body: ReplaceCategoryDocumentsRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseKnowledgeService, Depends(enterprise_knowledge_service)],
) -> EnterpriseCategoryResponse:
    """整体替换分类文档关系，不复制文件、版本、解析产物或 Chunk。"""

    category = service.replace_category_documents(
        context,
        workspace_id=workspace_id,
        category_id=category_id,
        expected_version=body.expected_version,
        document_ids=tuple(body.document_ids),
    )
    return _category_view_response(category)


@router.post(
    "/{workspace_id}/team-knowledge-domains",
    response_model=TeamKnowledgeDomainResponse,
    operation_id="createTeamKnowledgeDomain",
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
def create_team_knowledge_domain(
    workspace_id: UUID,
    body: CreateTeamKnowledgeDomainRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseKnowledgeService, Depends(enterprise_knowledge_service)],
) -> TeamKnowledgeDomainResponse:
    """创建团队知识域、显式范围和初始 RAG 策略版本。"""

    return _domain_response(
        service.create_domain(
            context,
            workspace_id=workspace_id,
            name=body.name,
            description=body.description,
            member_ids=tuple(body.member_ids),
            department_ids=tuple(body.department_ids),
            knowledge_base_ids=tuple(body.knowledge_base_ids),
            rag_mode=body.rag_mode,
            top_k=body.top_k,
            minimum_score=body.minimum_score,
        )
    )


@router.put(
    "/{workspace_id}/team-knowledge-domains/{domain_id}",
    response_model=TeamKnowledgeDomainResponse,
    operation_id="updateTeamKnowledgeDomain",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def update_team_knowledge_domain(
    workspace_id: UUID,
    domain_id: UUID,
    body: UpdateTeamKnowledgeDomainRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseKnowledgeService, Depends(enterprise_knowledge_service)],
) -> TeamKnowledgeDomainResponse:
    """更新知识域元数据，RAG 参数变化时追加不可变策略版本。"""

    return _domain_response(
        service.update_domain(
            context,
            workspace_id=workspace_id,
            domain_id=domain_id,
            expected_version=body.expected_version,
            name=body.name,
            description=body.description,
            rag_mode=body.rag_mode,
            top_k=body.top_k,
            minimum_score=body.minimum_score,
        )
    )


@router.post(
    "/{workspace_id}/team-knowledge-domains/{domain_id}/archive",
    response_model=TeamKnowledgeDomainResponse,
    operation_id="archiveTeamKnowledgeDomain",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def archive_team_knowledge_domain(
    workspace_id: UUID,
    domain_id: UUID,
    body: VersionedArchiveRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseKnowledgeService, Depends(enterprise_knowledge_service)],
) -> TeamKnowledgeDomainResponse:
    """归档知识域，运行范围解析立即失败关闭为空集。"""

    return _domain_response(
        service.archive_domain(
            context,
            workspace_id=workspace_id,
            domain_id=domain_id,
            expected_version=body.expected_version,
        )
    )


@router.put(
    "/{workspace_id}/team-knowledge-domains/{domain_id}/scope",
    response_model=TeamKnowledgeDomainResponse,
    operation_id="replaceTeamKnowledgeDomainScope",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def replace_team_knowledge_domain_scope(
    workspace_id: UUID,
    domain_id: UUID,
    body: ReplaceKnowledgeDomainScopeRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseKnowledgeService, Depends(enterprise_knowledge_service)],
) -> TeamKnowledgeDomainResponse:
    """原子替换成员、部门和知识库范围，不产生中间授权态。"""

    return _domain_response(
        service.replace_domain_scope(
            context,
            workspace_id=workspace_id,
            domain_id=domain_id,
            expected_version=body.expected_version,
            member_ids=tuple(body.member_ids),
            department_ids=tuple(body.department_ids),
            knowledge_base_ids=tuple(body.knowledge_base_ids),
        )
    )


@router.get(
    "/{workspace_id}/team-knowledge-domains/{domain_id}/resolved-scope",
    response_model=ResolvedKnowledgeDomainScopeResponse,
    operation_id="resolveTeamKnowledgeDomainScope",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def resolve_team_knowledge_domain_scope(
    workspace_id: UUID,
    domain_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[EnterpriseKnowledgeService, Depends(enterprise_knowledge_service)],
) -> ResolvedKnowledgeDomainScopeResponse:
    """解释声明范围与当前授权的交集，空交集绝不回退到全企业。"""

    return _scope_response(
        service.resolve_domain_scope(context, workspace_id=workspace_id, domain_id=domain_id)
    )


def _portal_response(
    snapshot: EnterpriseKnowledgePortalView,
) -> EnterpriseKnowledgePortalResponse:
    return EnterpriseKnowledgePortalResponse(
        workspace_id=snapshot.workspace_id,
        workspace_name=snapshot.workspace_name,
        statistics=EnterpriseKnowledgeStatisticsResponse(**vars(snapshot.statistics)),
        categories=[_category_response(item) for item in snapshot.categories],
        domains=[_domain_response(item) for item in snapshot.domains],
        documents=[EnterpriseDocumentOptionResponse(**vars(item)) for item in snapshot.documents],
        knowledge_bases=[
            EnterpriseKnowledgeBaseOptionResponse(**vars(item)) for item in snapshot.knowledge_bases
        ],
        departments=[
            EnterpriseDepartmentOptionResponse(**vars(item)) for item in snapshot.departments
        ],
        members=[EnterpriseMemberOptionResponse(**vars(item)) for item in snapshot.members],
        generated_at=snapshot.generated_at,
    )


def _category_response(category: EnterpriseCategoryResultView) -> EnterpriseCategoryResponse:
    return EnterpriseCategoryResponse(
        category_id=category.category_id,
        parent_category_id=category.parent_category_id,
        name=category.name,
        description=category.description,
        visibility=category.visibility,
        department_ids=list(category.department_ids),
        document_ids=list(category.document_ids),
        status=category.status,
        created_at=category.created_at,
        updated_at=category.updated_at,
        version=category.version,
    )


def _category_view_response(view: EnterpriseCategoryResultView) -> EnterpriseCategoryResponse:
    return _category_response(view)


def _domain_response(domain: TeamKnowledgeDomainView) -> TeamKnowledgeDomainResponse:
    return TeamKnowledgeDomainResponse(
        domain_id=domain.domain_id,
        name=domain.name,
        description=domain.description,
        member_ids=list(domain.member_ids),
        department_ids=list(domain.department_ids),
        knowledge_base_ids=list(domain.knowledge_base_ids),
        rag_policy=RagPolicyResponse(**vars(domain.rag_policy)),
        status=domain.status,
        created_at=domain.created_at,
        updated_at=domain.updated_at,
        version=domain.version,
    )


def _scope_response(
    scope: ResolvedKnowledgeDomainScopeView,
) -> ResolvedKnowledgeDomainScopeResponse:
    return ResolvedKnowledgeDomainScopeResponse(
        domain_id=scope.domain_id,
        policy_version=scope.policy_version,
        actor_in_declared_scope=scope.actor_in_declared_scope,
        declared_knowledge_base_ids=list(scope.declared_knowledge_base_ids),
        authorized_knowledge_base_ids=list(scope.authorized_knowledge_base_ids),
        effective_knowledge_base_ids=list(scope.effective_knowledge_base_ids),
        empty_reason=scope.empty_reason,
    )
