"""编排 Agent 候选发布审批，并复用通用多级审批运行时。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_control.application.errors import (
    AgentLifecycleConflictError,
    AgentNotFoundError,
    AgentReleaseApprovalRequiredError,
    AgentTestGateFailedError,
)
from ai_platform_api.modules.agent_control.application.support import (
    browser_account,
    require_custom_agent,
    require_resource_scope,
)
from ai_platform_api.modules.agent_control.domain.approval import AgentApprovalBinding
from ai_platform_api.modules.agent_control.domain.evaluation import AgentEvaluationReport
from ai_platform_api.modules.agent_control.domain.models import (
    AgentControlUnitOfWork,
    AgentReleaseCandidate,
)
from ai_platform_api.modules.workflow.application.approval_runtime import (
    ApprovalInstanceService,
    ApprovalRuntimeState,
    ApprovalSubject,
)


@dataclass(frozen=True)
class AgentApprovalContext:
    """返回 Agent 审批绑定、审批运行快照和本次是否为幂等重放。"""

    binding: AgentApprovalBinding
    state: ApprovalRuntimeState
    replayed: bool


def request_agent_approval(
    unit_of_work_factory: AgentControlUnitOfWork,
    approval_instances: ApprovalInstanceService,
    context: RequestContext,
    *,
    candidate_id: UUID,
    idempotency_key: str,
) -> AgentApprovalContext:
    """用固定测试证据创建审批实例，并由审批事务原子推进候选状态。"""

    browser_account(context)
    candidate, report = _approval_inputs(unit_of_work_factory, context, candidate_id)
    subject = _approval_subject(candidate, report)
    result = approval_instances.start(
        context,
        subject=subject,
        idempotency_key=idempotency_key,
    )
    with unit_of_work_factory as unit_of_work:
        binding = unit_of_work.approval.get_by_candidate(context.workspace_id, candidate_id)
    if (
        binding is None
        or binding.approval_instance_id != result.state.instance.approval_instance_id
    ):
        raise AgentReleaseApprovalRequiredError
    return AgentApprovalContext(binding, result.state, result.replayed)


def get_agent_approval(
    unit_of_work_factory: AgentControlUnitOfWork,
    approval_instances: ApprovalInstanceService,
    context: RequestContext,
    *,
    candidate_id: UUID,
) -> AgentApprovalContext:
    """读取候选绑定的审批运行事实，不把审批条件字段正文返回给调用方。"""

    browser_account(context)
    with unit_of_work_factory as unit_of_work:
        candidate = unit_of_work.agents.get_candidate(context.workspace_id, candidate_id)
        if candidate is None:
            raise AgentNotFoundError
        require_resource_scope(context, candidate.agent_id)
        binding = unit_of_work.approval.get_by_candidate(context.workspace_id, candidate_id)
    if binding is None:
        raise AgentReleaseApprovalRequiredError
    state = approval_instances.get(
        context,
        approval_instance_id=binding.approval_instance_id,
    )
    return AgentApprovalContext(binding, state, True)


def _approval_inputs(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    candidate_id: UUID,
) -> tuple[AgentReleaseCandidate, AgentEvaluationReport]:
    """读取新申请或幂等重放所需的候选和不可变通过证据。"""

    with unit_of_work_factory as unit_of_work:
        # 1. 候选与 Agent 必须仍属于当前工作空间和资源授权范围。
        candidate = unit_of_work.agents.get_candidate(context.workspace_id, candidate_id)
        if candidate is None:
            raise AgentNotFoundError
        require_resource_scope(context, candidate.agent_id)
        agent = require_custom_agent(unit_of_work, context.workspace_id, candidate.agent_id)
        if agent.status != "active":
            raise AgentLifecycleConflictError
        # 2. 首次申请读取最新通过结果，幂等重放则固定读取原绑定结果。
        binding = unit_of_work.approval.get_by_candidate(context.workspace_id, candidate_id)
        if binding is not None:
            report = unit_of_work.evaluation.get_report(
                context.workspace_id,
                binding.evaluation_run_id,
            )
        else:
            report = unit_of_work.evaluation.get_latest_passing_report(
                context.workspace_id,
                candidate_id,
            )
        if (
            report is None
            or report.run.candidate_id != candidate.candidate_id
            or report.run.candidate_hash != candidate.candidate_hash
            or report.run.config_hash != candidate.config_hash
            or report.run.status != "passed"
            or (binding is None and candidate.status != "ready_for_approval")
            or (
                binding is not None
                and (
                    binding.candidate_hash != candidate.candidate_hash
                    or binding.config_hash != candidate.config_hash
                    or binding.evaluation_result_hash != report.run.result_hash
                )
            )
        ):
            raise AgentTestGateFailedError
        return candidate, report


def _approval_subject(
    candidate: AgentReleaseCandidate,
    report: AgentEvaluationReport,
) -> ApprovalSubject:
    """构造只含标识和摘要的高风险审批主题，不携带 Prompt 或回答正文。"""

    return ApprovalSubject(
        workspace_id=candidate.workspace_id,
        requester_account_id=candidate.created_by_account_id,
        resource_type="agent.release",
        operation="approve",
        resource_id=candidate.candidate_id,
        department_ids=(),
        security_level="INTERNAL",
        risk_level="high",
        fields={
            "candidate_hash": candidate.candidate_hash,
            "config_hash": candidate.config_hash,
            "draft_revision": candidate.draft_revision,
            "evaluation_run_id": str(report.run.evaluation_run_id),
            "evaluation_result_hash": report.run.result_hash,
            "evaluation_policy_version_id": str(report.run.evaluation_policy_version_id),
        },
    )
