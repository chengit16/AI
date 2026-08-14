"""映射审批策略版本管理和审批链预计算的 HTTP 协议。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.workflow.api.approval_schemas import (
    ApprovalChainResponse,
    ApprovalPolicyDefinitionDocument,
    ApprovalPolicyDetailResponse,
    ApprovalPolicyListResponse,
    ApprovalPolicyResponse,
    ApprovalPolicyVersionResponse,
    CreateApprovalPolicyRequest,
    PreviewApprovalChainRequest,
    ResolvedApprovalLevelResponse,
    ReviseApprovalPolicyRequest,
)
from ai_platform_api.modules.workflow.application.approvals import (
    ApprovalChain,
    ApprovalPolicy,
    ApprovalPolicyDenied,
    ApprovalPolicyService,
    ApprovalPolicyVersion,
    approval_definition_document,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/approval-policies",
    tags=["审批策略"],
)


def approval_policy_service(request: Request) -> ApprovalPolicyService:
    """从应用容器解析审批策略服务，Router 不直接读取组织和版本表。"""

    service = getattr(request.app.state, "approval_policy_service", None)
    if not isinstance(service, ApprovalPolicyService):
        raise RuntimeError("审批策略服务尚未完成装配")
    return service


@router.post(
    "",
    response_model=ApprovalPolicyDetailResponse,
    operation_id="createApprovalPolicy",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
def create_approval_policy(
    workspace_id: UUID,
    body: CreateApprovalPolicyRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ApprovalPolicyService, Depends(approval_policy_service)],
) -> ApprovalPolicyDetailResponse:
    """创建策略身份和首个不可变版本。"""

    _require_workspace_path(context, workspace_id)
    policy, version = service.create(
        context,
        name=body.name,
        definition=body.definition.to_domain(),
    )
    return _detail(policy, version)


@router.get(
    "",
    response_model=ApprovalPolicyListResponse,
    operation_id="listApprovalPolicies",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_approval_policies(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ApprovalPolicyService, Depends(approval_policy_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ApprovalPolicyListResponse:
    """列出 PDP 授权范围内的审批策略身份。"""

    _require_workspace_path(context, workspace_id)
    return ApprovalPolicyListResponse(
        items=[_policy(policy) for policy in service.list(context, limit=limit)]
    )


@router.post(
    "/preview-chain",
    response_model=ApprovalChainResponse,
    operation_id="previewApprovalChain",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def preview_approval_chain(
    workspace_id: UUID,
    body: PreviewApprovalChainRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ApprovalPolicyService, Depends(approval_policy_service)],
) -> ApprovalChainResponse:
    """按当前策略和组织事实预计算审批链，但不创建审批实例。"""

    _require_workspace_path(context, workspace_id)
    if context.user_id is None:
        raise ApprovalPolicyDenied
    chain = service.preview_chain(
        context,
        subject=body.to_domain(
            workspace_id=workspace_id,
            requester_account_id=context.user_id,
        ),
    )
    return _chain(chain)


@router.get(
    "/{approval_policy_id}",
    response_model=ApprovalPolicyDetailResponse,
    operation_id="getApprovalPolicy",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def get_approval_policy(
    workspace_id: UUID,
    approval_policy_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ApprovalPolicyService, Depends(approval_policy_service)],
) -> ApprovalPolicyDetailResponse:
    """读取策略身份和当前不可变版本。"""

    _require_workspace_path(context, workspace_id)
    return _detail(
        *service.get(context, approval_policy_id=approval_policy_id),
    )


@router.post(
    "/{approval_policy_id}/versions",
    response_model=ApprovalPolicyDetailResponse,
    operation_id="reviseApprovalPolicy",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def revise_approval_policy(
    workspace_id: UUID,
    approval_policy_id: UUID,
    body: ReviseApprovalPolicyRequest,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[ApprovalPolicyService, Depends(approval_policy_service)],
) -> ApprovalPolicyDetailResponse:
    """新增不可变版本并以乐观锁推进当前版本指针。"""

    _require_workspace_path(context, workspace_id)
    return _detail(
        *service.revise(
            context,
            approval_policy_id=approval_policy_id,
            expected_version=body.expected_version,
            definition=body.definition.to_domain(),
        )
    )


def _require_workspace_path(context: RequestContext, workspace_id: UUID) -> None:
    if context.workspace_id != workspace_id:
        raise ApprovalPolicyDenied


def _detail(
    policy: ApprovalPolicy,
    version: ApprovalPolicyVersion,
) -> ApprovalPolicyDetailResponse:
    return ApprovalPolicyDetailResponse(
        policy=_policy(policy),
        version=ApprovalPolicyVersionResponse(
            approval_policy_version_id=version.approval_policy_version_id,
            approval_policy_id=version.approval_policy_id,
            workspace_id=version.workspace_id,
            version_number=version.version_number,
            definition=ApprovalPolicyDefinitionDocument.model_validate(
                approval_definition_document(version.definition)
            ),
            definition_digest=version.definition_digest,
            created_by_account_id=version.created_by_account_id,
            created_at=version.created_at,
        ),
    )


def _policy(policy: ApprovalPolicy) -> ApprovalPolicyResponse:
    return ApprovalPolicyResponse(
        approval_policy_id=policy.approval_policy_id,
        workspace_id=policy.workspace_id,
        name=policy.name,
        status=policy.status,
        current_version_id=policy.current_version_id,
        created_by_account_id=policy.created_by_account_id,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
        version=policy.version,
    )


def _chain(chain: ApprovalChain) -> ApprovalChainResponse:
    return ApprovalChainResponse(
        workspace_id=chain.workspace_id,
        approval_policy_id=chain.approval_policy_id,
        approval_policy_version_id=chain.approval_policy_version_id,
        personal_owner_confirmation=chain.personal_owner_confirmation,
        levels=[
            ResolvedApprovalLevelResponse(
                sequence_no=level.sequence_no,
                mode=level.mode,
                approver_account_ids=list(level.approver_account_ids),
                reminder_after_minutes=level.reminder_after_minutes,
                timeout_after_minutes=level.timeout_after_minutes,
                timeout_action=level.timeout_action,
                fallback_approver_account_ids=list(level.fallback_approver_account_ids),
            )
            for level in chain.levels
        ],
        chain_digest=chain.chain_digest,
    )
