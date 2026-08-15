"""把 Agent 候选审批证据接入通用审批事务，不复制审批状态机。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal, cast
from uuid import UUID, uuid5

from sqlalchemy import CursorResult, func, insert, select, update
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from ai_platform_api.modules.agent_control.domain.approval import (
    PERSONAL_OWNER_APPROVAL_POLICY_VERSION_ID,
    AgentApprovalBinding,
    AgentApprovalDecision,
    AgentApprovalRepository,
)
from ai_platform_api.modules.agent_control.domain.models import (
    AgentReleaseCandidate,
    AgentReleaseCandidateStatus,
)
from ai_platform_api.modules.workflow.domain.approval_runtime import (
    ApprovalRuntimeState,
    ApprovalRuntimeStateError,
    ApprovalRuntimeTransition,
    ApprovalRuntimeValidationError,
    ApprovalSubjectEvent,
    ApprovalSubjectLifecycle,
    approval_subject_digest,
)
from ai_platform_api.modules.workflow.domain.approvals import ApprovalSubject
from ai_platform_api.persistence.tables import (
    agent_approval_bindings,
    agent_drafts,
    agent_evaluation_case_results,
    agent_evaluation_check_results,
    agent_evaluation_runs,
    agent_release_candidates,
    approval_instances,
)

_AGENT_APPROVAL_NAMESPACE = UUID("a5000000-0000-4000-8000-000000000306")
_SUBJECT_FIELDS = frozenset(
    {
        "candidate_hash",
        "config_hash",
        "draft_revision",
        "evaluation_run_id",
        "evaluation_result_hash",
        "evaluation_policy_version_id",
    }
)


class SqlAlchemyAgentApprovalRepository(AgentApprovalRepository):
    """读取 Agent 模块拥有的不可变审批绑定。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_candidate(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> AgentApprovalBinding | None:
        row = self._session.execute(
            select(agent_approval_bindings).where(
                agent_approval_bindings.c.workspace_id == workspace_id,
                agent_approval_bindings.c.candidate_id == candidate_id,
            )
        ).one_or_none()
        return _binding(row) if row is not None else None

    def get_by_instance(
        self,
        workspace_id: UUID,
        approval_instance_id: UUID,
    ) -> AgentApprovalBinding | None:
        row = self._session.execute(
            select(agent_approval_bindings).where(
                agent_approval_bindings.c.workspace_id == workspace_id,
                agent_approval_bindings.c.approval_instance_id == approval_instance_id,
            )
        ).one_or_none()
        return _binding(row) if row is not None else None

    def get_decision_by_candidate(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentApprovalDecision | None:
        """在发布事务内读取绑定的审批终态和完成时间。"""

        statement = (
            select(
                agent_approval_bindings,
                approval_instances.c.status.label("decision_status"),
                approval_instances.c.completed_at.label("decision_completed_at"),
            )
            .join(
                approval_instances,
                (
                    approval_instances.c.approval_instance_id
                    == agent_approval_bindings.c.approval_instance_id
                )
                & (approval_instances.c.workspace_id == agent_approval_bindings.c.workspace_id),
            )
            .where(
                agent_approval_bindings.c.workspace_id == workspace_id,
                agent_approval_bindings.c.candidate_id == candidate_id,
            )
        )
        if for_share:
            statement = statement.with_for_update(read=True, of=approval_instances)
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return AgentApprovalDecision(
            binding=_binding(row),
            status=cast(
                "Literal['pending', 'approved', 'rejected', 'withdrawn']",
                row.decision_status,
            ),
            completed_at=row.decision_completed_at,
        )


class SqlAlchemyAgentApprovalSubjectLifecycle(ApprovalSubjectLifecycle):
    """在审批实例事务内绑定通过证据，并同步 Agent 候选终态。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def bind(
        self,
        state: ApprovalRuntimeState,
        subject: ApprovalSubject,
    ) -> ApprovalSubjectEvent | None:
        """验证发布主题并原子写入审批绑定和 `approval_pending` 状态。"""

        if subject.resource_type != "agent.release":
            return None
        instance = state.instance
        candidate_id, evaluation_run_id, evaluation_policy_version_id = _subject_ids(subject)
        if (
            subject.operation != "approve"
            or instance.resource_id != candidate_id
            or instance.subject_digest != approval_subject_digest(subject)
        ):
            raise ApprovalRuntimeValidationError

        # 1. 候选必须仍是当前草稿 revision，且只能由冻结候选的申请人发起审批。
        candidate = self._candidate(subject.workspace_id, candidate_id, for_update=True)
        current_draft = self._session.execute(
            select(agent_drafts).where(
                agent_drafts.c.workspace_id == subject.workspace_id,
                agent_drafts.c.draft_id == candidate.draft_id,
            )
        ).one_or_none()
        if (
            candidate.status != "ready_for_approval"
            or candidate.created_by_account_id != subject.requester_account_id
            or current_draft is None
            or current_draft.revision != candidate.draft_revision
            or current_draft.config_hash != candidate.config_hash
            or subject.fields["candidate_hash"] != candidate.candidate_hash
            or subject.fields["config_hash"] != candidate.config_hash
            or subject.fields["draft_revision"] != candidate.draft_revision
        ):
            raise ApprovalRuntimeStateError

        # 2. 通过结果、五类检查和零失败用例必须与主题中的摘要完全一致。
        evaluation = self._session.execute(
            select(agent_evaluation_runs).where(
                agent_evaluation_runs.c.workspace_id == subject.workspace_id,
                agent_evaluation_runs.c.evaluation_run_id == evaluation_run_id,
                agent_evaluation_runs.c.candidate_id == candidate_id,
                agent_evaluation_runs.c.evaluation_policy_version_id
                == evaluation_policy_version_id,
            )
        ).one_or_none()
        if (
            evaluation is None
            or evaluation.status != "passed"
            or evaluation.candidate_hash != candidate.candidate_hash
            or evaluation.config_hash != candidate.config_hash
            or evaluation.result_hash != subject.fields["evaluation_result_hash"]
            or evaluation.passed_cases != evaluation.total_cases
            or not self._evaluation_complete(evaluation_run_id, subject.workspace_id)
        ):
            raise ApprovalRuntimeStateError

        # 3. 个人空间使用固定内置策略标识，企业空间绑定审批实例实际选择的策略版本。
        approval_policy_version_id = _approval_policy_version(state)
        binding = AgentApprovalBinding(
            approval_binding_id=uuid5(
                _AGENT_APPROVAL_NAMESPACE,
                f"binding:{candidate_id}:{instance.approval_instance_id}",
            ),
            workspace_id=subject.workspace_id,
            candidate_id=candidate_id,
            agent_id=candidate.agent_id,
            approval_instance_id=instance.approval_instance_id,
            evaluation_run_id=evaluation_run_id,
            evaluation_policy_version_id=evaluation_policy_version_id,
            approval_policy_version_id=approval_policy_version_id,
            candidate_hash=candidate.candidate_hash,
            config_hash=candidate.config_hash,
            evaluation_result_hash=evaluation.result_hash,
            subject_digest=instance.subject_digest,
            chain_digest=instance.chain_digest,
            personal_owner_confirmation=instance.personal_owner_confirmation,
            created_at=instance.created_at,
        )
        self._session.execute(insert(agent_approval_bindings).values(**_binding_values(binding)))
        updated = replace(
            candidate,
            status="approval_pending",
            updated_at=instance.created_at,
            version=candidate.version + 1,
        )
        self._save_candidate(updated, expected_version=candidate.version)
        return _subject_event("agent.approval.requested", binding, updated)

    def apply_transition(
        self,
        previous: ApprovalRuntimeState,
        transition: ApprovalRuntimeTransition,
    ) -> ApprovalSubjectEvent | None:
        """把审批终态同步为候选通过、驳回或失效；非终态动作不改变候选。"""

        instance = transition.state.instance
        if previous.instance.resource_type != "agent.release" or instance.status == "pending":
            return None
        # 1. 终态同步前重新核对不可变绑定和候选来源，防止审批上下文被替换。
        binding_row = self._session.execute(
            select(agent_approval_bindings).where(
                agent_approval_bindings.c.workspace_id == instance.workspace_id,
                agent_approval_bindings.c.approval_instance_id == instance.approval_instance_id,
            )
        ).one_or_none()
        if binding_row is None:
            raise ApprovalRuntimeStateError
        binding = _binding(binding_row)
        candidate = self._candidate(instance.workspace_id, binding.candidate_id, for_update=True)
        _require_binding_matches(binding, previous, candidate)
        # 草稿变化已经把候选标记为 superseded；历史审批仍可结束，但不能复活旧候选。
        if candidate.status == "superseded":
            return None
        if candidate.status != "approval_pending":
            raise ApprovalRuntimeStateError

        # 2. 通过只对当前 revision 生效；驳回、撤回和陈旧审批保持各自终态语义。
        next_status = self._candidate_terminal_status(candidate, instance.status)
        updated = replace(
            candidate,
            status=next_status,
            updated_at=transition.action.occurred_at,
            version=candidate.version + 1,
        )
        self._save_candidate(updated, expected_version=candidate.version)
        event_type = {
            "approved": "agent.approval.approved",
            "rejected": "agent.approval.rejected",
            "superseded": "agent.approval.superseded",
        }[next_status]
        return _subject_event(event_type, binding, updated)

    def _candidate(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
        *,
        for_update: bool,
    ) -> AgentReleaseCandidate:
        statement = select(agent_release_candidates).where(
            agent_release_candidates.c.workspace_id == workspace_id,
            agent_release_candidates.c.candidate_id == candidate_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None:
            raise ApprovalRuntimeStateError
        return _candidate(row)

    def _evaluation_complete(self, evaluation_run_id: UUID, workspace_id: UUID) -> bool:
        passed_checks = self._session.scalar(
            select(func.count())
            .select_from(agent_evaluation_check_results)
            .where(
                agent_evaluation_check_results.c.evaluation_run_id == evaluation_run_id,
                agent_evaluation_check_results.c.workspace_id == workspace_id,
                agent_evaluation_check_results.c.status == "passed",
            )
        )
        failed_cases = self._session.scalar(
            select(func.count())
            .select_from(agent_evaluation_case_results)
            .where(
                agent_evaluation_case_results.c.evaluation_run_id == evaluation_run_id,
                agent_evaluation_case_results.c.workspace_id == workspace_id,
                agent_evaluation_case_results.c.outcome != "passed",
            )
        )
        return passed_checks == 5 and failed_cases == 0

    def _candidate_terminal_status(
        self,
        candidate: AgentReleaseCandidate,
        approval_status: str,
    ) -> AgentReleaseCandidateStatus:
        if approval_status == "rejected":
            return "rejected"
        if approval_status == "withdrawn":
            return "superseded"
        if approval_status != "approved":
            raise ApprovalRuntimeStateError
        current_draft = self._session.execute(
            select(agent_drafts).where(
                agent_drafts.c.workspace_id == candidate.workspace_id,
                agent_drafts.c.draft_id == candidate.draft_id,
            )
        ).one_or_none()
        if (
            current_draft is None
            or current_draft.revision != candidate.draft_revision
            or current_draft.config_hash != candidate.config_hash
        ):
            return "superseded"
        return "approved"

    def _save_candidate(
        self,
        candidate: AgentReleaseCandidate,
        *,
        expected_version: int,
    ) -> None:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(agent_release_candidates)
                .where(
                    agent_release_candidates.c.workspace_id == candidate.workspace_id,
                    agent_release_candidates.c.candidate_id == candidate.candidate_id,
                    agent_release_candidates.c.version == expected_version,
                )
                .values(
                    status=candidate.status,
                    updated_at=candidate.updated_at,
                    version=candidate.version,
                )
            ),
        )
        if result.rowcount != 1:
            raise ApprovalRuntimeStateError


def _subject_ids(subject: ApprovalSubject) -> tuple[UUID, UUID, UUID]:
    """严格解析无额外字段的 Agent 审批主题，避免自由字段绕过摘要绑定。"""

    if subject.resource_id is None or set(subject.fields) != _SUBJECT_FIELDS:
        raise ApprovalRuntimeValidationError
    draft_revision = subject.fields["draft_revision"]
    if (
        not isinstance(draft_revision, int)
        or isinstance(draft_revision, bool)
        or draft_revision < 1
    ):
        raise ApprovalRuntimeValidationError
    try:
        return (
            subject.resource_id,
            UUID(str(subject.fields["evaluation_run_id"])),
            UUID(str(subject.fields["evaluation_policy_version_id"])),
        )
    except (TypeError, ValueError) as error:
        raise ApprovalRuntimeValidationError from error


def _approval_policy_version(state: ApprovalRuntimeState) -> UUID:
    instance = state.instance
    if instance.personal_owner_confirmation:
        if (
            instance.approval_policy_id is not None
            or instance.approval_policy_version_id is not None
        ):
            raise ApprovalRuntimeStateError
        return PERSONAL_OWNER_APPROVAL_POLICY_VERSION_ID
    if instance.approval_policy_id is None or instance.approval_policy_version_id is None:
        raise ApprovalRuntimeStateError
    return instance.approval_policy_version_id


def _require_binding_matches(
    binding: AgentApprovalBinding,
    state: ApprovalRuntimeState,
    candidate: AgentReleaseCandidate,
) -> None:
    instance = state.instance
    expected_policy_version = _approval_policy_version(state)
    if (
        binding.workspace_id != instance.workspace_id
        or binding.approval_instance_id != instance.approval_instance_id
        or binding.candidate_id != candidate.candidate_id
        or binding.agent_id != candidate.agent_id
        or binding.candidate_hash != candidate.candidate_hash
        or binding.config_hash != candidate.config_hash
        or binding.subject_digest != instance.subject_digest
        or binding.chain_digest != instance.chain_digest
        or binding.approval_policy_version_id != expected_policy_version
        or binding.personal_owner_confirmation != instance.personal_owner_confirmation
    ):
        raise ApprovalRuntimeStateError


def _subject_event(
    event_type: str,
    binding: AgentApprovalBinding,
    candidate: AgentReleaseCandidate,
) -> ApprovalSubjectEvent:
    return ApprovalSubjectEvent(
        event_type=event_type,
        resource_type="agent_release_candidate",
        resource_id=candidate.candidate_id,
        aggregate_id=candidate.agent_id,
        aggregate_version=candidate.version,
        attributes={
            "candidate_id": str(candidate.candidate_id),
            "candidate_hash": candidate.candidate_hash,
            "evaluation_run_id": str(binding.evaluation_run_id),
            "approval_policy_version_id": str(binding.approval_policy_version_id),
            "status": candidate.status,
        },
    )


def _binding(row: Row[Any]) -> AgentApprovalBinding:
    return AgentApprovalBinding(
        row.approval_binding_id,
        row.workspace_id,
        row.candidate_id,
        row.agent_id,
        row.approval_instance_id,
        row.evaluation_run_id,
        row.evaluation_policy_version_id,
        row.approval_policy_version_id,
        row.candidate_hash,
        row.config_hash,
        row.evaluation_result_hash,
        row.subject_digest,
        row.chain_digest,
        row.personal_owner_confirmation,
        row.created_at,
    )


def _binding_values(binding: AgentApprovalBinding) -> dict[str, object]:
    return {
        "approval_binding_id": binding.approval_binding_id,
        "workspace_id": binding.workspace_id,
        "candidate_id": binding.candidate_id,
        "agent_id": binding.agent_id,
        "approval_instance_id": binding.approval_instance_id,
        "evaluation_run_id": binding.evaluation_run_id,
        "evaluation_policy_version_id": binding.evaluation_policy_version_id,
        "approval_policy_version_id": binding.approval_policy_version_id,
        "candidate_hash": binding.candidate_hash,
        "config_hash": binding.config_hash,
        "evaluation_result_hash": binding.evaluation_result_hash,
        "subject_digest": binding.subject_digest,
        "chain_digest": binding.chain_digest,
        "personal_owner_confirmation": binding.personal_owner_confirmation,
        "created_at": binding.created_at,
    }


def _candidate(row: Row[Any]) -> AgentReleaseCandidate:
    return AgentReleaseCandidate(
        candidate_id=row.candidate_id,
        agent_id=row.agent_id,
        draft_id=row.draft_id,
        workspace_id=row.workspace_id,
        draft_revision=row.draft_revision,
        candidate_hash=row.candidate_hash,
        config_hash=row.config_hash,
        status=cast(AgentReleaseCandidateStatus, row.status),
        created_by_account_id=row.created_by_account_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        version=row.version,
    )
