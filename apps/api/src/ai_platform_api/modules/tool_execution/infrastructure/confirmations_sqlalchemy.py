"""持久化工具确认及追加式失效事实，并原子恢复 Run 与 Step。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, func, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from ai_platform_api.modules.tool_execution.domain.confirmations import (
    ToolCallBinding,
    ToolConfirmation,
    ToolConfirmationInvalidationReason,
    ToolConfirmationInvalidationState,
    ToolConfirmationMode,
    ToolConfirmationRecordedState,
    ToolConfirmationRequestBasis,
    ToolConfirmationResumeResult,
)
from ai_platform_api.modules.tool_execution.domain.errors import (
    ToolConfirmationRequiredError,
    ToolConfirmationStaleError,
    ToolRunConflictError,
)
from ai_platform_api.modules.tool_execution.domain.planning import ToolPolicyDecisionRecord
from ai_platform_api.modules.tool_execution.domain.tasks import (
    ToolAttemptTrigger,
    ToolRun,
    ToolRunBudget,
    ToolRunState,
    ToolStep,
    ToolStepBudget,
    ToolStepState,
)
from ai_platform_api.modules.tool_execution.infrastructure.planning_sqlalchemy import (
    insert_tool_policy_decision,
)
from ai_platform_api.persistence.tables import (
    approval_instances,
    tool_confirmation_invalidations,
    tool_confirmations,
    tool_runs,
    tool_steps,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyToolConfirmationStore:
    """以 Confirmation、Step 和 Run 行锁原子处理失效与恢复。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def get_request_basis(
        self,
        workspace_id: UUID,
        step_id: UUID,
    ) -> ToolConfirmationRequestBasis | None:
        with self._session_factory() as session:
            step_row = (
                session.execute(
                    select(tool_steps).where(
                        tool_steps.c.workspace_id == workspace_id,
                        tool_steps.c.step_id == step_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            run_row = (
                session.execute(
                    select(tool_runs).where(
                        tool_runs.c.workspace_id == workspace_id,
                        tool_runs.c.run_id == step_row["run_id"],
                    )
                )
                .mappings()
                .one_or_none()
                if step_row is not None
                else None
            )
        if step_row is None or run_row is None:
            return None
        return ToolConfirmationRequestBasis(_run(run_row), _step(step_row))

    def get_by_request_idempotency(
        self,
        workspace_id: UUID,
        requester_account_id: UUID,
        idempotency_key: str,
    ) -> ToolConfirmation | None:
        statement = self._confirmation_statement().where(
            tool_confirmations.c.workspace_id == workspace_id,
            approval_instances.c.requester_account_id == requester_account_id,
            approval_instances.c.idempotency_key == idempotency_key,
        )
        with self._session_factory() as session:
            row = session.execute(statement).mappings().one_or_none()
        return _confirmation(row) if row is not None else None

    def get_by_approval_instance(
        self,
        workspace_id: UUID,
        approval_instance_id: UUID,
    ) -> ToolConfirmation | None:
        statement = self._confirmation_statement().where(
            tool_confirmations.c.workspace_id == workspace_id,
            tool_confirmations.c.approval_instance_id == approval_instance_id,
        )
        with self._session_factory() as session:
            row = session.execute(statement).mappings().one_or_none()
        return _confirmation(row) if row is not None else None

    def get_confirmation(
        self,
        workspace_id: UUID,
        confirmation_id: UUID,
    ) -> ToolConfirmation | None:
        statement = self._confirmation_statement().where(
            tool_confirmations.c.workspace_id == workspace_id,
            tool_confirmations.c.confirmation_id == confirmation_id,
        )
        with self._session_factory() as session:
            row = session.execute(statement).mappings().one_or_none()
        return _confirmation(row) if row is not None else None

    def invalidate(
        self,
        workspace_id: UUID,
        confirmation_id: UUID,
        *,
        state: ToolConfirmationInvalidationState,
        reason: ToolConfirmationInvalidationReason,
        occurred_at: datetime,
    ) -> ToolConfirmation:
        """追加一次批准后失效事实，原审批终态保持不变。"""

        with self._session_factory() as session, session.begin():
            row = self._locked_confirmation(session, workspace_id, confirmation_id)
            existing = (
                session.execute(
                    select(tool_confirmation_invalidations).where(
                        tool_confirmation_invalidations.c.confirmation_id == confirmation_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if existing["state"] != state or existing["reason_code"] != reason:
                    raise ToolRunConflictError
            elif row["state"] != "approved":
                raise ToolConfirmationRequiredError
            else:
                try:
                    session.execute(
                        insert(tool_confirmation_invalidations).values(
                            invalidation_id=uuid4(),
                            confirmation_id=confirmation_id,
                            workspace_id=workspace_id,
                            run_id=row["run_id"],
                            step_id=row["step_id"],
                            state=state,
                            reason_code=reason,
                            occurred_at=occurred_at,
                        )
                    )
                except IntegrityError as error:
                    raise ToolRunConflictError from error
            return self._project_in_session(session, workspace_id, confirmation_id)

    def expire(
        self,
        workspace_id: UUID,
        confirmation_id: UUID,
        *,
        occurred_at: datetime,
    ) -> ToolConfirmation:
        """关闭超过 Run 冻结期限的待确认或已批准事实。"""

        with self._session_factory() as session, session.begin():
            # 1. 锁定确认并复核绝对截止时间；提前调用不能伪造过期事实。
            row = self._locked_confirmation(session, workspace_id, confirmation_id)
            if occurred_at < cast(datetime, row["expires_at"]):
                raise ToolRunConflictError
            invalidation = (
                session.execute(
                    select(tool_confirmation_invalidations).where(
                        tool_confirmation_invalidations.c.confirmation_id == confirmation_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            # 2. 待确认事实更新原始终态，已批准事实只追加失效记录，保留审批审计原貌。
            if invalidation is not None:
                if invalidation["state"] != "expired":
                    raise ToolRunConflictError
            elif row["state"] == "pending":
                self._update_pending_state(
                    session,
                    row,
                    state="expired",
                    actor_id=None,
                    occurred_at=occurred_at,
                )
            elif row["state"] == "approved":
                session.execute(
                    insert(tool_confirmation_invalidations).values(
                        invalidation_id=uuid4(),
                        confirmation_id=confirmation_id,
                        workspace_id=workspace_id,
                        run_id=row["run_id"],
                        step_id=row["step_id"],
                        state="expired",
                        reason_code="expired",
                        occurred_at=occurred_at,
                    )
                )
            elif row["state"] != "expired":
                raise ToolRunConflictError
            return self._project_in_session(session, workspace_id, confirmation_id)

    def resume(
        self,
        workspace_id: UUID,
        confirmation_id: UUID,
        *,
        expected_binding: ToolCallBinding,
        policy: ToolPolicyDecisionRecord,
        resumed_at: datetime,
    ) -> ToolConfirmationResumeResult:
        """在一个事务内追加当前 PDP 证据，并恢复 Step 和无其他等待项的 Run。"""

        with self._session_factory() as session, session.begin():
            # 1. 固定锁顺序为 Confirmation、Step、Run，避免确认恢复与取消路径形成环形等待。
            confirmation_row = self._locked_confirmation(session, workspace_id, confirmation_id)
            step_row = self._locked_step(session, workspace_id, expected_binding.step_id)
            run_row = self._locked_run(session, workspace_id, expected_binding.run_id)
            invalidated = session.scalar(
                select(func.count())
                .select_from(tool_confirmation_invalidations)
                .where(tool_confirmation_invalidations.c.confirmation_id == confirmation_id)
            )
            confirmation = _confirmation_from_recorded(confirmation_row)
            expected_waiting = _waiting_state(confirmation.mode)
            if (
                confirmation.recorded_state != "approved"
                or invalidated
                or resumed_at >= confirmation.expires_at
                or confirmation.binding != expected_binding
                or step_row["state"] != expected_waiting
                or run_row["state"] != expected_waiting
                or not _policy_matches_confirmation(policy, confirmation)
            ):
                raise ToolConfirmationStaleError

            # 2. 新策略证据与 Step 使用同一时点，数据库 Trigger 会重新核对写工具确认和策略版本。
            try:
                insert_tool_policy_decision(session, policy)
                updated_step = (
                    session.execute(
                        update(tool_steps)
                        .where(
                            tool_steps.c.step_id == confirmation.binding.step_id,
                            tool_steps.c.workspace_id == workspace_id,
                            tool_steps.c.version == step_row["version"],
                        )
                        .values(
                            state="ready",
                            updated_at=resumed_at,
                            version=cast(int, step_row["version"]) + 1,
                        )
                        .returning(tool_steps)
                    )
                    .mappings()
                    .one_or_none()
                )
                if updated_step is None:
                    raise ToolRunConflictError
                # 3. 只有当前 Run 已无其他等待步骤时才恢复运行，避免越过并行审批门禁。
                waiting_count = session.scalar(
                    select(func.count())
                    .select_from(tool_steps)
                    .where(
                        tool_steps.c.run_id == confirmation.binding.run_id,
                        tool_steps.c.state.in_(("waiting_confirmation", "waiting_approval")),
                    )
                )
                updated_run_row = run_row
                if int(waiting_count or 0) == 0:
                    candidate_run = (
                        session.execute(
                            update(tool_runs)
                            .where(
                                tool_runs.c.run_id == confirmation.binding.run_id,
                                tool_runs.c.workspace_id == workspace_id,
                                tool_runs.c.version == run_row["version"],
                            )
                            .values(
                                state="running",
                                updated_at=resumed_at,
                                version=cast(int, run_row["version"]) + 1,
                            )
                            .returning(tool_runs)
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if candidate_run is None:
                        raise ToolRunConflictError
                    updated_run_row = candidate_run
            except IntegrityError as error:
                raise ToolRunConflictError from error
            return ToolConfirmationResumeResult(
                confirmation,
                _run(updated_run_row),
                _step(updated_step),
                policy,
            )

    def _confirmation_statement(self) -> Select[Any]:
        return (
            select(
                tool_confirmations,
                tool_confirmation_invalidations.c.state.label("invalidation_state"),
                tool_confirmation_invalidations.c.reason_code.label("invalidation_reason"),
                tool_confirmation_invalidations.c.occurred_at.label("invalidated_at"),
            )
            .join(
                approval_instances,
                approval_instances.c.approval_instance_id
                == tool_confirmations.c.approval_instance_id,
            )
            .outerjoin(
                tool_confirmation_invalidations,
                tool_confirmation_invalidations.c.confirmation_id
                == tool_confirmations.c.confirmation_id,
            )
        )

    def _locked_confirmation(
        self,
        session: Session,
        workspace_id: UUID,
        confirmation_id: UUID,
    ) -> RowMapping:
        row = (
            session.execute(
                select(tool_confirmations)
                .where(
                    tool_confirmations.c.workspace_id == workspace_id,
                    tool_confirmations.c.confirmation_id == confirmation_id,
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ToolRunConflictError
        return row

    def _locked_step(self, session: Session, workspace_id: UUID, step_id: UUID) -> RowMapping:
        row = (
            session.execute(
                select(tool_steps)
                .where(
                    tool_steps.c.workspace_id == workspace_id,
                    tool_steps.c.step_id == step_id,
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ToolRunConflictError
        return row

    def _locked_run(self, session: Session, workspace_id: UUID, run_id: UUID) -> RowMapping:
        row = (
            session.execute(
                select(tool_runs)
                .where(tool_runs.c.workspace_id == workspace_id, tool_runs.c.run_id == run_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ToolRunConflictError
        return row

    def _project_in_session(
        self,
        session: Session,
        workspace_id: UUID,
        confirmation_id: UUID,
    ) -> ToolConfirmation:
        row = (
            session.execute(
                self._confirmation_statement().where(
                    tool_confirmations.c.workspace_id == workspace_id,
                    tool_confirmations.c.confirmation_id == confirmation_id,
                )
            )
            .mappings()
            .one()
        )
        return _confirmation(row)

    def _update_pending_state(
        self,
        session: Session,
        row: RowMapping,
        *,
        state: Literal["expired"],
        actor_id: UUID | None,
        occurred_at: datetime,
    ) -> None:
        result = cast(
            CursorResult[Any],
            session.execute(
                update(tool_confirmations)
                .where(
                    tool_confirmations.c.confirmation_id == row["confirmation_id"],
                    tool_confirmations.c.version == row["version"],
                )
                .values(
                    state=state,
                    confirmed_by_actor_id=actor_id,
                    resolved_at=occurred_at,
                    updated_at=occurred_at,
                    version=cast(int, row["version"]) + 1,
                )
            ),
        )
        if result.rowcount != 1:
            raise ToolRunConflictError


def _policy_matches_confirmation(
    policy: ToolPolicyDecisionRecord,
    confirmation: ToolConfirmation,
) -> bool:
    binding = confirmation.binding
    return (
        policy.workspace_id == binding.workspace_id
        and policy.run_id == binding.run_id
        and policy.step_id == binding.step_id
        and policy.tool_id == binding.tool_id
        and policy.tool_version == binding.tool_version
        and policy.canonical_arguments_hash == binding.canonical_arguments_hash
        and policy.permission_code == confirmation.permission_code
        and policy.policy_version == confirmation.policy_version
        and policy.resource_scope_hash == confirmation.resource_scope_hash
        and policy.field_mask_hash == confirmation.field_mask_hash
    )


def _waiting_state(
    mode: ToolConfirmationMode,
) -> Literal["waiting_confirmation", "waiting_approval"]:
    return "waiting_confirmation" if mode == "personal_owner" else "waiting_approval"


def _confirmation(row: Mapping[Any, Any]) -> ToolConfirmation:
    return ToolConfirmation(
        confirmation_id=cast(UUID, row["confirmation_id"]),
        approval_instance_id=cast(UUID, row["approval_instance_id"]),
        binding=_binding(row),
        mode=cast(ToolConfirmationMode, row["mode"]),
        policy_decision_id=cast(UUID, row["policy_decision_id"]),
        permission_code=cast(str, row["permission_code"]),
        policy_version=cast(int, row["policy_version"]),
        resource_scope_hash=cast(str, row["resource_scope_hash"]),
        field_mask_hash=cast(str, row["field_mask_hash"]),
        policy_evaluated_at=cast(datetime, row["policy_evaluated_at"]),
        risk_level=cast(Literal["high", "critical"], row["risk_level"]),
        confirmation_hash=cast(str, row["confirmation_hash"]),
        subject_digest=cast(str, row["subject_digest"]),
        chain_digest=cast(str, row["chain_digest"]),
        recorded_state=cast(ToolConfirmationRecordedState, row["state"]),
        confirmed_by_actor_id=cast(UUID | None, row["confirmed_by_actor_id"]),
        expires_at=cast(datetime, row["expires_at"]),
        resolved_at=cast(datetime | None, row["resolved_at"]),
        invalidation_state=cast(
            ToolConfirmationInvalidationState | None,
            row.get("invalidation_state"),
        ),
        invalidation_reason=cast(
            ToolConfirmationInvalidationReason | None,
            row.get("invalidation_reason"),
        ),
        invalidated_at=cast(datetime | None, row.get("invalidated_at")),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
        version=cast(int, row["version"]),
    )


def _confirmation_from_recorded(row: Mapping[Any, Any]) -> ToolConfirmation:
    document = dict(row)
    document.update(
        invalidation_state=None,
        invalidation_reason=None,
        invalidated_at=None,
    )
    return _confirmation(document)


def _binding(row: Mapping[Any, Any]) -> ToolCallBinding:
    return ToolCallBinding(
        workspace_id=cast(UUID, row["workspace_id"]),
        run_id=cast(UUID, row["run_id"]),
        step_id=cast(UUID, row["step_id"]),
        tool_id=cast(UUID, row["tool_id"]),
        tool_version=cast(int, row["tool_version"]),
        canonical_arguments_hash=cast(str, row["canonical_arguments_hash"]),
    )


def _run(row: Mapping[Any, Any]) -> ToolRun:
    return ToolRun(
        run_id=cast(UUID, row["run_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        requested_by_actor_id=cast(UUID, row["requested_by_actor_id"]),
        requested_by_account_id=cast(UUID, row["requested_by_account_id"]),
        service_id=cast(UUID, row["service_id"]),
        agent_release_id=cast(UUID, row["agent_release_id"]),
        state=cast(ToolRunState, row["state"]),
        budget=ToolRunBudget(
            cast(int, row["max_steps"]),
            cast(int, row["max_attempts_per_step"]),
            cast(int, row["max_execution_seconds"]),
            cast(int, row["max_cost_microunits"]),
        ),
        cancel_requested_at=cast(datetime | None, row["cancel_requested_at"]),
        deadline_at=cast(datetime, row["deadline_at"]),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
        completed_at=cast(datetime | None, row["completed_at"]),
        recovery_generation=cast(int, row["recovery_generation"]),
        recovery_reason_code=cast(str | None, row["recovery_reason_code"]),
        recovery_required_at=cast(datetime | None, row["recovery_required_at"]),
        last_recovered_by_actor_id=cast(UUID | None, row["last_recovered_by_actor_id"]),
        last_recovered_at=cast(datetime | None, row["last_recovered_at"]),
        version=cast(int, row["version"]),
    )


def _step(row: Mapping[Any, Any]) -> ToolStep:
    return ToolStep(
        step_id=cast(UUID, row["step_id"]),
        run_id=cast(UUID, row["run_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        sequence_no=cast(int, row["sequence_no"]),
        tool_id=cast(UUID, row["tool_id"]),
        tool_version=cast(int, row["tool_version"]),
        canonical_arguments_hash=cast(str, row["canonical_arguments_hash"]),
        budget=ToolStepBudget(
            cast(int, row["timeout_seconds"]),
            cast(int, row["max_attempts"]),
            cast(int, row["max_result_bytes"]),
            cast(int, row["max_cost_microunits"]),
        ),
        state=cast(ToolStepState, row["state"]),
        recovery_generation=cast(int, row["recovery_generation"]),
        current_attempt_no=cast(int | None, row["current_attempt_no"]),
        available_at=cast(datetime, row["available_at"]),
        next_attempt_trigger=cast(ToolAttemptTrigger, row["next_attempt_trigger"]),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
        version=cast(int, row["version"]),
    )


__all__ = [
    "SqlAlchemyToolConfirmationStore",
]
