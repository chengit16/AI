"""生成可复算的不可变 AgentRelease，并绑定测试与审批来源证据。"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_control.application.configuration import (
    parse_agent_configuration,
)
from ai_platform_api.modules.agent_control.application.errors import (
    AgentLifecycleConflictError,
    AgentNotFoundError,
    AgentReleaseApprovalRequiredError,
    AgentTestGateFailedError,
)
from ai_platform_api.modules.agent_control.application.support import (
    PUBLISH_RELEASE_OPERATION,
    browser_account,
    canonical_json,
    control_request,
    raise_write_conflict,
    record_change,
    replay_release,
    request_digest,
    require_custom_agent,
    require_idempotency_key,
    require_request_hash,
    require_resource_scope,
)
from ai_platform_api.modules.agent_control.domain.approval import AgentApprovalDecision
from ai_platform_api.modules.agent_control.domain.evaluation import (
    REQUIRED_EVALUATION_CHECKS,
    AgentEvaluationDatasetVersion,
    AgentEvaluationReport,
)
from ai_platform_api.modules.agent_control.domain.models import (
    AgentControlUnitOfWork,
    AgentDraftRevision,
    AgentRelease,
    AgentReleaseCandidate,
    AgentWriteConflictError,
)


def publish_agent_release(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    candidate_id: UUID,
    idempotency_key: str,
) -> AgentRelease:
    """把当前有效且已批准的候选原子固化为唯一不可变 Release。"""

    # 长函数保留原因: 锁顺序、证据复核、快照和候选终态必须共享同一事务视图。
    account_id = browser_account(context)
    require_idempotency_key(idempotency_key)
    request_hash = request_digest(
        {
            "operation": PUBLISH_RELEASE_OPERATION,
            "candidate_id": str(candidate_id),
        }
    )
    now = datetime.now(UTC)
    try:
        with unit_of_work_factory as unit_of_work:
            # 1. 同一主体与幂等键永远返回已绑定 Release，不重新生成版本或事件。
            request = unit_of_work.agents.get_request(
                context.workspace_id,
                context.actor_id,
                PUBLISH_RELEASE_OPERATION,
                idempotency_key,
            )
            if request is not None:
                require_request_hash(request, request_hash)
                return require_valid_release(
                    replay_release(unit_of_work, context.workspace_id, request)
                )

            # 2. 先按不可变身份确定 Agent，再遵循 Agent -> 候选的全局锁顺序。
            candidate_hint = unit_of_work.agents.get_candidate(
                context.workspace_id,
                candidate_id,
            )
            if candidate_hint is None:
                raise AgentNotFoundError
            require_resource_scope(context, candidate_hint.agent_id)
            agent = require_custom_agent(
                unit_of_work,
                context.workspace_id,
                candidate_hint.agent_id,
                for_update=True,
            )
            candidate = unit_of_work.agents.get_candidate(
                context.workspace_id,
                candidate_id,
                for_update=True,
            )
            if candidate is None or candidate.agent_id != agent.agent_id:
                raise AgentLifecycleConflictError

            # 3. 候选级唯一约束提供全局幂等；新幂等键只补请求映射，不复制发布事实。
            existing = unit_of_work.agents.get_release_by_candidate(
                context.workspace_id,
                candidate_id,
            )
            if candidate.status == "released":
                if existing is None:
                    raise AgentLifecycleConflictError
                _bind_replayed_release(
                    unit_of_work,
                    context,
                    existing,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    occurred_at=now,
                )
                unit_of_work.commit()
                return require_valid_release(existing)
            if agent.status != "active" or candidate.status != "approved" or existing is not None:
                raise AgentReleaseApprovalRequiredError

            # 4. 从不可变 revision、评估运行和审批终态构造完整快照，禁止回读当前草稿。
            revision = unit_of_work.agents.get_draft_revision(
                context.workspace_id,
                candidate.draft_id,
                candidate.draft_revision,
            )
            decision = unit_of_work.approval.get_decision_by_candidate(
                context.workspace_id,
                candidate_id,
                for_share=True,
            )
            revision = _require_source_revision(candidate, revision)
            decision = _require_approved_decision(candidate, decision)
            report = unit_of_work.evaluation.get_report(
                context.workspace_id,
                decision.binding.evaluation_run_id,
            )
            report = _require_passing_report(candidate, decision, report)
            dataset = unit_of_work.evaluation.get_dataset_version(
                context.workspace_id,
                report.run.dataset_version_id,
                for_share=True,
            )
            if dataset is None or dataset.dataset_version_id != report.run.dataset_version_id:
                raise AgentTestGateFailedError
            parsed, normalized_configuration, config_hash = parse_agent_configuration(
                revision.configuration
            )
            if (
                normalized_configuration != revision.configuration
                or config_hash != candidate.config_hash
            ):
                raise AgentLifecycleConflictError
            snapshot = _release_snapshot(candidate, revision, report, dataset, decision)
            snapshot_hash = release_snapshot_digest(snapshot)
            release = AgentRelease(
                release_id=uuid4(),
                agent_id=agent.agent_id,
                workspace_id=context.workspace_id,
                release_kind="custom",
                version=unit_of_work.agents.next_release_version(
                    context.workspace_id,
                    agent.agent_id,
                ),
                runtime_config_version_id=parsed.runtime_config_version_id,
                config_hash=candidate.config_hash,
                candidate_id=candidate.candidate_id,
                candidate_hash=candidate.candidate_hash,
                source_draft_id=candidate.draft_id,
                source_draft_revision=candidate.draft_revision,
                evaluation_run_id=report.run.evaluation_run_id,
                approval_binding_id=decision.binding.approval_binding_id,
                snapshot=snapshot,
                snapshot_hash=snapshot_hash,
                released_by_account_id=account_id,
                released_at=now,
            )
            unit_of_work.agents.add_release(release)
            released_candidate = replace(
                candidate,
                status="released",
                updated_at=now,
                version=candidate.version + 1,
            )
            if not unit_of_work.agents.save_candidate(
                released_candidate,
                expected_version=candidate.version,
            ):
                raise AgentLifecycleConflictError
            unit_of_work.agents.add_request(
                control_request(
                    context,
                    PUBLISH_RELEASE_OPERATION,
                    idempotency_key,
                    request_hash,
                    "release",
                    release.release_id,
                    release.version,
                    now,
                )
            )
            # 5. 事件只携带身份和摘要；Prompt、配置正文及审批条件不进入运营表面。
            record_change(
                unit_of_work,
                context,
                action="agent.release.published",
                event_type="agent.release.published",
                resource_id=agent.agent_id,
                aggregate_version=release.version,
                occurred_at=now,
                attributes={
                    "release_id": str(release.release_id),
                    "release_version": release.version,
                    "candidate_id": str(candidate.candidate_id),
                    "candidate_hash": candidate.candidate_hash,
                    "config_hash": candidate.config_hash,
                    "snapshot_hash": snapshot_hash,
                    "evaluation_run_id": str(report.run.evaluation_run_id),
                    "approval_binding_id": str(decision.binding.approval_binding_id),
                },
            )
            unit_of_work.commit()
            return release
    except AgentWriteConflictError as error:
        replayed = _recover_release(
            unit_of_work_factory,
            context,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replayed is not None:
            return replayed
        raise_write_conflict(error)


def release_snapshot_digest(snapshot: dict[str, object]) -> str:
    """计算 Release 快照的规范 JSON SHA-256，供写入和读取双向复算。"""

    return hashlib.sha256(canonical_json(snapshot)).hexdigest()


def require_valid_release(release: AgentRelease) -> AgentRelease:
    """读取时复核自定义 Release 的来源字段和快照摘要，损坏时失败关闭。"""

    snapshot = release.snapshot
    if (
        release.release_kind != "custom"
        or release.candidate_id is None
        or release.candidate_hash is None
        or release.source_draft_id is None
        or release.source_draft_revision is None
        or release.evaluation_run_id is None
        or release.approval_binding_id is None
        or snapshot is None
        or release.snapshot_hash is None
        or snapshot.get("source_draft_id") != str(release.source_draft_id)
        or snapshot.get("source_draft_revision") != release.source_draft_revision
        or release_snapshot_digest(snapshot) != release.snapshot_hash
    ):
        raise AgentLifecycleConflictError
    return release


def _require_source_revision(
    candidate: AgentReleaseCandidate,
    revision: AgentDraftRevision | None,
) -> AgentDraftRevision:
    if (
        revision is None
        or revision.agent_id != candidate.agent_id
        or revision.draft_id != candidate.draft_id
        or revision.revision != candidate.draft_revision
        or revision.config_hash != candidate.config_hash
    ):
        raise AgentLifecycleConflictError
    return revision


def _require_approved_decision(
    candidate: AgentReleaseCandidate,
    decision: AgentApprovalDecision | None,
) -> AgentApprovalDecision:
    if decision is None:
        raise AgentReleaseApprovalRequiredError
    binding = decision.binding
    if (
        decision.status != "approved"
        or decision.completed_at is None
        or binding.workspace_id != candidate.workspace_id
        or binding.candidate_id != candidate.candidate_id
        or binding.agent_id != candidate.agent_id
        or binding.candidate_hash != candidate.candidate_hash
        or binding.config_hash != candidate.config_hash
    ):
        raise AgentReleaseApprovalRequiredError
    return decision


def _require_passing_report(
    candidate: AgentReleaseCandidate,
    decision: AgentApprovalDecision,
    report: AgentEvaluationReport | None,
) -> AgentEvaluationReport:
    binding = decision.binding
    if report is None:
        raise AgentTestGateFailedError
    run = report.run
    checks = {item.check_code: item for item in report.checks}
    if (
        run.candidate_id != candidate.candidate_id
        or run.candidate_hash != candidate.candidate_hash
        or run.config_hash != candidate.config_hash
        or run.evaluation_policy_version_id != binding.evaluation_policy_version_id
        or run.result_hash != binding.evaluation_result_hash
        or run.status != "passed"
        or run.passed_cases != run.total_cases
        or len(report.checks) != len(REQUIRED_EVALUATION_CHECKS)
        or set(checks) != set(REQUIRED_EVALUATION_CHECKS)
        or any(checks[code].status != "passed" for code in REQUIRED_EVALUATION_CHECKS)
        or len(report.cases) != run.total_cases
        or any(item.outcome != "passed" for item in report.cases)
    ):
        raise AgentTestGateFailedError
    return report


def _release_snapshot(
    candidate: AgentReleaseCandidate,
    revision: AgentDraftRevision,
    report: AgentEvaluationReport,
    dataset: AgentEvaluationDatasetVersion,
    decision: AgentApprovalDecision,
) -> dict[str, object]:
    """按 `agent-control.v1` 固定字段构造不含自由审批条件和测试正文的快照。"""

    approved_at = decision.completed_at
    if approved_at is None:
        raise AgentReleaseApprovalRequiredError
    return {
        "snapshot_schema_version": 1,
        "source_draft_id": str(revision.draft_id),
        "source_draft_revision": revision.revision,
        "configuration": revision.configuration,
        "evaluation": {
            "evaluation_run_id": str(report.run.evaluation_run_id),
            "dataset_version": dataset.dataset_version,
            "evaluation_policy_version_id": str(report.run.evaluation_policy_version_id),
            "status": "passed",
            "required_checks": list(REQUIRED_EVALUATION_CHECKS),
            "completed_at": report.run.completed_at.isoformat(),
        },
        "approval": {
            "approval_instance_id": str(decision.binding.approval_instance_id),
            "approval_policy_version_id": str(decision.binding.approval_policy_version_id),
            "candidate_hash": candidate.candidate_hash,
            "status": "approved",
            "approved_at": approved_at.isoformat(),
        },
    }


def _bind_replayed_release(
    unit_of_work: AgentControlUnitOfWork,
    context: RequestContext,
    release: AgentRelease,
    *,
    idempotency_key: str,
    request_hash: str,
    occurred_at: datetime,
) -> None:
    """为已发布候选补充当前幂等键映射，不重复写审计与发布事件。"""

    unit_of_work.agents.add_request(
        control_request(
            context,
            PUBLISH_RELEASE_OPERATION,
            idempotency_key,
            request_hash,
            "release",
            release.release_id,
            release.version,
            occurred_at,
        )
    )


def _recover_release(
    unit_of_work_factory: AgentControlUnitOfWork,
    context: RequestContext,
    *,
    idempotency_key: str,
    request_hash: str,
) -> AgentRelease | None:
    """在唯一键竞争后恢复同一发布请求已经提交的 Release。"""

    with unit_of_work_factory as unit_of_work:
        request = unit_of_work.agents.get_request(
            context.workspace_id,
            context.actor_id,
            PUBLISH_RELEASE_OPERATION,
            idempotency_key,
        )
        if request is None:
            return None
        require_request_hash(request, request_hash)
        return require_valid_release(replay_release(unit_of_work, context.workspace_id, request))
