"""映射工作空间导出、业务数据清除和保留期执行 HTTP 协议。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.lifecycle.api.schemas import (
    LegalHoldBody,
    LegalHoldReleaseBody,
    LegalHoldReleaseResponse,
    LegalHoldResponse,
    LifecycleComplianceProofResponse,
    LifecycleExportResponse,
    LifecyclePurgeBody,
    LifecyclePurgeResponse,
    RegulatoryPolicyResponse,
    RetentionRunResponse,
)
from ai_platform_api.modules.lifecycle.application.compliance import RegulatoryComplianceService
from ai_platform_api.modules.lifecycle.application.service import WorkspaceLifecycleService

router = APIRouter(prefix="/workspaces/{workspace_id}/lifecycle", tags=["数据生命周期"])


def lifecycle_service(request: Request) -> WorkspaceLifecycleService:
    """从组合根解析生命周期服务，Router 不直接操作多种存储。"""

    service = getattr(request.app.state, "workspace_lifecycle_service", None)
    if not isinstance(service, WorkspaceLifecycleService):
        raise RuntimeError("工作空间生命周期服务尚未完成装配")
    return service


def regulatory_compliance_service(request: Request) -> RegulatoryComplianceService:
    """从组合根解析法规治理服务，协议层不接受法域或审核结论。"""

    service = getattr(request.app.state, "regulatory_compliance_service", None)
    if not isinstance(service, RegulatoryComplianceService):
        raise RuntimeError("法规治理服务尚未完成装配")
    return service


@router.post(
    "/exports",
    response_model=LifecycleExportResponse,
    operation_id="createWorkspaceLifecycleExport",
    responses=error_responses(400, 401, 403, 409, 422, 503),
)
def create_export(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[WorkspaceLifecycleService, Depends(lifecycle_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> LifecycleExportResponse:
    """同步生成版本化导出包并返回可复算摘要。"""

    return LifecycleExportResponse.from_domain(
        service.export_workspace(
            context,
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
        )
    )


@router.post(
    "/purges",
    response_model=LifecyclePurgeResponse,
    operation_id="purgeWorkspaceBusinessData",
    responses=error_responses(400, 401, 403, 404, 409, 422, 503),
)
def purge_business_data(
    workspace_id: UUID,
    body: LifecyclePurgeBody,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[WorkspaceLifecycleService, Depends(lifecycle_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> LifecyclePurgeResponse:
    """保留可登录治理壳层，并清除业务数据与派生介质。"""

    purge, certificate = service.purge_workspace_business_data(
        context,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        confirmed_workspace_name=body.confirmed_workspace_name,
        reason_code=body.reason_code,
    )
    return LifecyclePurgeResponse.from_domain(purge, certificate)


@router.post(
    "/retention-runs",
    response_model=RetentionRunResponse,
    operation_id="executeWorkspaceRetention",
    responses=error_responses(400, 401, 403, 409, 422, 503),
)
def execute_retention(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[WorkspaceLifecycleService, Depends(lifecycle_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> RetentionRunResponse:
    """执行冻结保留期并返回各事实表的删除计数。"""

    return RetentionRunResponse.from_domain(
        service.execute_retention(
            context,
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
        )
    )


@router.post(
    "/regulatory-policies",
    response_model=RegulatoryPolicyResponse,
    operation_id="publishWorkspaceRegulatoryPolicy",
    responses=error_responses(400, 401, 403, 404, 409, 422, 503),
)
def publish_regulatory_policy(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[RegulatoryComplianceService, Depends(regulatory_compliance_service)],
) -> RegulatoryPolicyResponse:
    """仅触发受信配置源发布，调用方不能提交法域和外部审核结果。"""

    return RegulatoryPolicyResponse.from_domain(
        service.publish_policy(context, workspace_id=workspace_id)
    )


@router.post(
    "/legal-holds",
    response_model=LegalHoldResponse,
    operation_id="activateWorkspaceLegalHold",
    responses=error_responses(400, 401, 403, 409, 422, 503),
)
def activate_legal_hold(
    workspace_id: UUID,
    body: LegalHoldBody,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[RegulatoryComplianceService, Depends(regulatory_compliance_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> LegalHoldResponse:
    """激活工作空间级法律保留，案件正文不进入请求或持久化。"""

    return LegalHoldResponse.from_domain(
        service.activate_legal_hold(
            context,
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
            case_reference_digest=body.case_reference_digest,
            reason_code=body.reason_code,
        )
    )


@router.post(
    "/legal-holds/{legal_hold_id}/release",
    response_model=LegalHoldReleaseResponse,
    operation_id="releaseWorkspaceLegalHold",
    responses=error_responses(400, 401, 403, 404, 409, 422, 503),
)
def release_legal_hold(
    workspace_id: UUID,
    legal_hold_id: UUID,
    body: LegalHoldReleaseBody,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[RegulatoryComplianceService, Depends(regulatory_compliance_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> LegalHoldReleaseResponse:
    """以独立权限和只追加证据解除法律保留。"""

    return LegalHoldReleaseResponse.from_domain(
        service.release_legal_hold(
            context,
            legal_hold_id,
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
            release_evidence_digest=body.release_evidence_digest,
            reason_code=body.reason_code,
        )
    )


@router.get(
    "/compliance-proofs",
    response_model=list[LifecycleComplianceProofResponse],
    operation_id="listWorkspaceLifecycleComplianceProofs",
    responses=error_responses(401, 403, 422, 503),
)
def list_compliance_proofs(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[RegulatoryComplianceService, Depends(regulatory_compliance_service)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[LifecycleComplianceProofResponse]:
    """按独立权限读取证明，不查询或返回业务、案件与法规正文。"""

    return [
        LifecycleComplianceProofResponse.from_domain(proof)
        for proof in service.list_compliance_proofs(
            context,
            workspace_id=workspace_id,
            limit=limit,
        )
    ]
