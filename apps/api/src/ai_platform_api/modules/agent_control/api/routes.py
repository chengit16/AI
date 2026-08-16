"""映射 Agent 列表、草稿、测试、审批和发布控制台协议。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status

from ai_platform_api.common.api_errors import error_responses
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_control.api.schemas import (
    AgentApprovalResponse,
    AgentCandidateControlResponse,
    AgentCandidateListResponse,
    AgentCandidateResponse,
    AgentDetailResponse,
    AgentDraftResponse,
    AgentEvaluationCheckResponse,
    AgentEvaluationResponse,
    AgentListResponse,
    AgentReleaseListResponse,
    AgentReleaseResponse,
    AgentResponse,
    ArchiveAgentRequest,
    CreateAgentRequest,
    RequestAgentReleaseRequest,
    UpdateAgentDraftRequest,
)
from ai_platform_api.modules.agent_control.application.errors import AgentDeniedError
from ai_platform_api.modules.agent_control.application.service import (
    Agent,
    AgentApprovalDecision,
    AgentCandidateControlView,
    AgentControlService,
    AgentDraft,
    AgentEvaluationReport,
    AgentRelease,
    AgentReleaseCandidate,
)
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context

router = APIRouter(prefix="/workspaces/{workspace_id}/agents", tags=["Agent 控制台"])


def agent_control_service(request: Request) -> AgentControlService:
    """从组合根解析 Agent 控制面，Router 不直接读取候选或发布表。"""

    service = getattr(request.app.state, "agent_control_service", None)
    if not isinstance(service, AgentControlService):
        raise RuntimeError("Agent 控制面尚未完成装配")
    return service


@router.get(
    "",
    response_model=AgentListResponse,
    operation_id="listAgents",
    responses=error_responses(400, 401, 403, 422, 500),
)
def list_agents(
    workspace_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AgentControlService, Depends(agent_control_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> AgentListResponse:
    """列出当前空间的自定义 Agent 与当前草稿。"""

    _require_workspace_path(context, workspace_id)
    return AgentListResponse(
        items=[_detail(agent, draft) for agent, draft in service.list_agents(context, limit=limit)]
    )


@router.post(
    "",
    response_model=AgentDetailResponse,
    operation_id="createAgent",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 409, 422, 500),
)
def create_agent(
    workspace_id: UUID,
    body: CreateAgentRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AgentControlService, Depends(agent_control_service)],
) -> AgentDetailResponse:
    """原子创建 Agent 和首个草稿，配置引用由服务端完整复核。"""

    _require_workspace_path(context, workspace_id)
    agent, draft = service.create_agent(
        context,
        name=body.name,
        description=body.description,
        configuration=body.configuration,
        use_starter_configuration=body.use_starter_configuration,
        idempotency_key=idempotency_key,
    )
    return _detail(agent, draft)


@router.put(
    "/{agent_id}/draft",
    response_model=AgentDraftResponse,
    operation_id="updateAgentDraft",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def update_agent_draft(
    workspace_id: UUID,
    agent_id: UUID,
    body: UpdateAgentDraftRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AgentControlService, Depends(agent_control_service)],
) -> AgentDraftResponse:
    """按 revision 写入新草稿，旧候选和审批资格由应用层同步失效。"""

    _require_workspace_path(context, workspace_id)
    return _draft(
        service.update_draft(
            context,
            agent_id=agent_id,
            expected_revision=body.expected_revision,
            configuration=body.configuration,
            idempotency_key=idempotency_key,
        )
    )


@router.post(
    "/{agent_id}/archive",
    response_model=AgentResponse,
    operation_id="archiveAgent",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def archive_agent(
    workspace_id: UUID,
    agent_id: UUID,
    body: ArchiveAgentRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AgentControlService, Depends(agent_control_service)],
) -> AgentResponse:
    """归档自定义 Agent，既有 Release 与服务历史保持可追溯。"""

    _require_workspace_path(context, workspace_id)
    return _agent(
        service.archive_agent(
            context,
            agent_id=agent_id,
            expected_version=body.expected_version,
            idempotency_key=idempotency_key,
        )
    )


@router.post(
    "/{agent_id}/release-requests",
    response_model=AgentCandidateResponse,
    operation_id="requestAgentRelease",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def request_agent_release(
    workspace_id: UUID,
    agent_id: UUID,
    body: RequestAgentReleaseRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AgentControlService, Depends(agent_control_service)],
) -> AgentCandidateResponse:
    """冻结当前草稿为候选，但不伪造测试、审批或发布结论。"""

    _require_workspace_path(context, workspace_id)
    return _candidate(
        service.request_release_candidate(
            context,
            agent_id=agent_id,
            expected_revision=body.expected_revision,
            idempotency_key=idempotency_key,
        )
    )


@router.get(
    "/{agent_id}/release-requests",
    response_model=AgentCandidateListResponse,
    operation_id="listAgentEvaluations",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def list_agent_evaluations(
    workspace_id: UUID,
    agent_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AgentControlService, Depends(agent_control_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AgentCandidateListResponse:
    """列出候选、最近评估与审批摘要，刷新不会丢失失败状态。"""

    _require_workspace_path(context, workspace_id)
    return AgentCandidateListResponse(
        items=[
            _candidate_control(item)
            for item in service.list_candidate_controls(context, agent_id=agent_id, limit=limit)
        ]
    )


@router.post(
    "/{agent_id}/release-requests/{candidate_id}/evaluations",
    response_model=AgentEvaluationResponse,
    operation_id="runAgentEvaluation",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def run_agent_evaluation(
    workspace_id: UUID,
    agent_id: UUID,
    candidate_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AgentControlService, Depends(agent_control_service)],
) -> AgentEvaluationResponse:
    """运行平台固定五类规则，浏览器不能提交或篡改执行观测。"""

    _require_workspace_path(context, workspace_id)
    _require_candidate_agent(service, context, agent_id, candidate_id)
    return _evaluation(service.run_release_gate_evaluation(context, candidate_id=candidate_id))


@router.post(
    "/{agent_id}/release-requests/{candidate_id}/approval",
    response_model=AgentApprovalResponse,
    operation_id="approveAgentRelease",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def approve_agent_release(
    workspace_id: UUID,
    agent_id: UUID,
    candidate_id: UUID,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AgentControlService, Depends(agent_control_service)],
) -> AgentApprovalResponse:
    """发起个人所有者确认或企业多级审批，实际决策仍由通用审批接口执行。"""

    _require_workspace_path(context, workspace_id)
    _require_candidate_agent(service, context, agent_id, candidate_id)
    approval = service.request_approval(
        context,
        candidate_id=candidate_id,
        idempotency_key=idempotency_key,
    )
    return AgentApprovalResponse(
        approval_instance_id=approval.binding.approval_instance_id,
        status=approval.state.instance.status,
        personal_owner_confirmation=approval.binding.personal_owner_confirmation,
        completed_at=approval.state.instance.completed_at,
    )


@router.post(
    "/{agent_id}/release-requests/{candidate_id}/publish",
    response_model=AgentReleaseResponse,
    operation_id="publishAgentRelease",
    responses=error_responses(400, 401, 403, 404, 409, 422, 500),
)
def publish_agent_release(
    workspace_id: UUID,
    agent_id: UUID,
    candidate_id: UUID,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AgentControlService, Depends(agent_control_service)],
) -> AgentReleaseResponse:
    """发布已通过测试和审批的候选，返回不可变 Release 摘要。"""

    _require_workspace_path(context, workspace_id)
    _require_candidate_agent(service, context, agent_id, candidate_id)
    return _release(
        service.publish_release(
            context,
            candidate_id=candidate_id,
            idempotency_key=idempotency_key,
        )
    )


@router.get(
    "/{agent_id}/releases",
    response_model=AgentReleaseListResponse,
    operation_id="listAgentReleases",
    responses=error_responses(400, 401, 403, 404, 422, 500),
)
def list_agent_releases(
    workspace_id: UUID,
    agent_id: UUID,
    context: Annotated[RequestContext, Depends(trusted_request_context)],
    service: Annotated[AgentControlService, Depends(agent_control_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AgentReleaseListResponse:
    """列出已校验 Release，不暴露 Prompt 或完整快照正文。"""

    _require_workspace_path(context, workspace_id)
    return AgentReleaseListResponse(
        items=[
            _release(item)
            for item in service.list_releases(context, agent_id=agent_id, limit=limit)
        ]
    )


def _require_candidate_agent(
    service: AgentControlService,
    context: RequestContext,
    agent_id: UUID,
    candidate_id: UUID,
) -> None:
    """用候选列表复核路径 Agent，阻止跨 Agent 组合标识。"""

    candidates = service.list_candidate_controls(context, agent_id=agent_id, limit=100)
    if not any(item.candidate.candidate_id == candidate_id for item in candidates):
        raise AgentDeniedError


def _require_workspace_path(context: RequestContext, workspace_id: UUID) -> None:
    if context.workspace_id != workspace_id:
        raise AgentDeniedError


def _detail(agent: Agent, draft: AgentDraft) -> AgentDetailResponse:
    return AgentDetailResponse(agent=_agent(agent), draft=_draft(draft))


def _agent(value: Agent) -> AgentResponse:
    return AgentResponse(
        agent_id=value.agent_id,
        workspace_id=value.workspace_id,
        agent_key=value.agent_key,
        name=value.name,
        description=value.description,
        status=value.status,
        created_by_account_id=value.created_by_account_id,
        created_at=value.created_at,
        updated_at=value.updated_at,
        version=value.version,
    )


def _draft(value: AgentDraft) -> AgentDraftResponse:
    return AgentDraftResponse(
        draft_id=value.draft_id,
        agent_id=value.agent_id,
        revision=value.revision,
        status=value.status,
        configuration=value.configuration,
        config_hash=value.config_hash,
        updated_at=value.updated_at,
    )


def _candidate(value: AgentReleaseCandidate) -> AgentCandidateResponse:
    return AgentCandidateResponse(
        candidate_id=value.candidate_id,
        agent_id=value.agent_id,
        draft_revision=value.draft_revision,
        candidate_hash=value.candidate_hash,
        config_hash=value.config_hash,
        status=value.status,
        created_at=value.created_at,
        updated_at=value.updated_at,
        version=value.version,
    )


def _evaluation(value: AgentEvaluationReport) -> AgentEvaluationResponse:
    run = value.run
    return AgentEvaluationResponse(
        evaluation_run_id=run.evaluation_run_id,
        candidate_id=run.candidate_id,
        status=run.status,
        evaluator_version=run.evaluator_version,
        evidence_level=run.evidence_level,
        total_cases=run.total_cases,
        passed_cases=run.passed_cases,
        failed_cases=run.failed_cases,
        timeout_cases=run.timeout_cases,
        skipped_cases=run.skipped_cases,
        result_hash=run.result_hash,
        completed_at=run.completed_at,
        checks=[
            AgentEvaluationCheckResponse(
                check_code=item.check_code,
                status=item.status,
                case_count=item.case_count,
                passed_count=item.passed_count,
                score_bps=item.score_bps,
            )
            for item in value.checks
        ],
    )


def _approval(value: AgentApprovalDecision) -> AgentApprovalResponse:
    return AgentApprovalResponse(
        approval_instance_id=value.binding.approval_instance_id,
        status=value.status,
        personal_owner_confirmation=value.binding.personal_owner_confirmation,
        completed_at=value.completed_at,
    )


def _candidate_control(value: AgentCandidateControlView) -> AgentCandidateControlResponse:
    return AgentCandidateControlResponse(
        candidate=_candidate(value.candidate),
        evaluation=_evaluation(value.evaluation) if value.evaluation is not None else None,
        approval=_approval(value.approval) if value.approval is not None else None,
    )


def _release(value: AgentRelease) -> AgentReleaseResponse:
    return AgentReleaseResponse(
        release_id=value.release_id,
        agent_id=value.agent_id,
        version=value.version,
        candidate_id=value.candidate_id,
        source_draft_revision=value.source_draft_revision,
        config_hash=value.config_hash,
        snapshot_hash=value.snapshot_hash,
        released_by_account_id=value.released_by_account_id,
        released_at=value.released_at,
    )
