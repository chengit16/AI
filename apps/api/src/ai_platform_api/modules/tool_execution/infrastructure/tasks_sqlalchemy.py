"""用 PostgreSQL 行锁、租约和追加式 Attempt 实现工具任务唯一写入权。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditAuthorization, AuditRecord, IntegrationEvent
from ai_platform_backend.integration.sqlalchemy import SqlAlchemyAuditWriter, SqlAlchemyOutboxWriter
from sqlalchemy import Row, and_, exists, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.tool_execution.domain.errors import (
    ToolExecutionDeniedError,
    ToolRetryNotAllowedError,
    ToolRunBudgetExceededError,
    ToolRunConflictError,
    ToolRunTerminalError,
)
from ai_platform_api.modules.tool_execution.domain.planning import (
    FrozenToolPlanStep,
    ToolExecutionPlan,
    ToolPolicyDecisionRecord,
)
from ai_platform_api.modules.tool_execution.domain.results import (
    ToolAttemptOutcomeFacts,
    ToolUsageOutcome,
)
from ai_platform_api.modules.tool_execution.domain.tasks import (
    AttemptResult,
    ClaimedToolAttempt,
    ToolAttemptTrigger,
    ToolCallState,
    ToolRun,
    ToolRunBudget,
    ToolRunState,
    ToolStep,
    ToolStepBudget,
    ToolStepState,
)
from ai_platform_api.modules.tool_execution.infrastructure.results_sqlalchemy import (
    append_attempt_outcome,
    append_progress_event,
)
from ai_platform_api.persistence.tables import (
    agent_tool_definitions,
    tool_attempts,
    tool_calls,
    tool_idempotency_records,
    tool_policy_decisions,
    tool_runs,
    tool_steps,
)

SessionFactory = Callable[[], Session]
RUN_TERMINAL = frozenset({"completed", "failed", "cancelled", "timed_out"})
STEP_TERMINAL = frozenset({"completed", "failed", "cancelled", "timed_out"})
ATTEMPT_TERMINAL = frozenset(
    {"succeeded", "failed", "cancelled", "timed_out", "ignored_late_result"}
)
CALL_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "timed_out"})
RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"planning", "cancellation_requested"}),
    "planning": frozenset({"running"}),
    "running": frozenset(
        {
            "waiting_confirmation",
            "waiting_approval",
            "completed",
            "failed",
            "cancellation_requested",
            "manual_recovery",
            "timed_out",
        }
    ),
    "waiting_confirmation": frozenset({"running"}),
    "waiting_approval": frozenset({"running"}),
    "cancellation_requested": frozenset({"cancelled"}),
    "manual_recovery": frozenset({"running", "completed", "cancellation_requested"}),
}
STEP_TRANSITIONS: dict[str, frozenset[str]] = {
    "planned": frozenset({"policy_checking", "cancelled"}),
    "policy_checking": frozenset({"ready", "waiting_confirmation", "waiting_approval"}),
    "waiting_confirmation": frozenset({"ready"}),
    "waiting_approval": frozenset({"ready"}),
    "retry_wait": frozenset({"running", "cancelled"}),
    "manual_recovery": frozenset({"ready", "completed", "cancelled"}),
}
CALL_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"authorized"}),
    "authorized": frozenset({"confirmed", "cancelled"}),
    "confirmed": frozenset({"executing"}),
}


class SqlAlchemyToolTaskStore:
    """以工具任务表为唯一事实源，拒绝失租、跨空间和终态覆盖。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def create_run(
        self,
        *,
        run_id: UUID,
        workspace_id: UUID,
        actor_id: UUID,
        account_id: UUID,
        service_id: UUID,
        agent_release_id: UUID,
        idempotency_key: str,
        request_hash: str,
        budget: ToolRunBudget,
        trace_id: str,
        traceparent: str,
        created_at: datetime,
    ) -> ToolRun:
        """创建 Run 或返回同 Actor 的同载荷重放，异载荷使用同键时失败关闭。"""

        # 1. 先冻结全部语义字段和绝对截止时间，重放不能改写首次请求身份。
        values = {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "requested_by_actor_id": actor_id,
            "requested_by_account_id": account_id,
            "service_id": service_id,
            "agent_release_id": agent_release_id,
            "state": "pending",
            "max_steps": budget.max_steps,
            "max_attempts_per_step": budget.max_attempts_per_step,
            "max_execution_seconds": budget.max_execution_seconds,
            "max_cost_microunits": budget.max_cost_microunits,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "trace_id": trace_id,
            "traceparent": traceparent,
            "cancel_requested_at": None,
            "deadline_at": created_at + timedelta(seconds=budget.max_execution_seconds),
            "created_at": created_at,
            "updated_at": created_at,
            "completed_at": None,
            "recovery_generation": 0,
            "recovery_reason_code": None,
            "recovery_required_at": None,
            "last_recovered_by_actor_id": None,
            "last_recovered_at": None,
            "version": 1,
        }
        # 2. 唯一键竞争只产生一个 Run；冲突后读取已提交事实并核对载荷摘要。
        with self._session_factory() as session, session.begin():
            statement = (
                postgresql_insert(tool_runs)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=[
                        tool_runs.c.workspace_id,
                        tool_runs.c.requested_by_actor_id,
                        tool_runs.c.idempotency_key,
                    ]
                )
            )
            try:
                inserted = session.execute(statement.returning(tool_runs)).mappings().one_or_none()
            except IntegrityError as error:
                raise ToolExecutionDeniedError from error
            if inserted is not None:
                append_progress_event(
                    session,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    event_type="tool.run.created",
                    run_state="pending",
                    step_id=None,
                    step_state=None,
                    attempt_id=None,
                    tool_call_id=None,
                    error_code=None,
                    occurred_at=created_at,
                )
                return _run(inserted)
            # 3. 冲突只允许同载荷重放；不同请求摘要不能借幂等键读取或改写既有 Run。
            existing = (
                session.execute(
                    select(tool_runs).where(
                        tool_runs.c.workspace_id == workspace_id,
                        tool_runs.c.requested_by_actor_id == actor_id,
                        tool_runs.c.idempotency_key == idempotency_key,
                    )
                )
                .mappings()
                .one()
            )
            if existing["request_hash"] != request_hash:
                raise ToolRunConflictError
            return _run(existing)

    def get_run(self, workspace_id: UUID, run_id: UUID) -> ToolRun | None:
        with self._session_factory() as session:
            row = (
                session.execute(
                    select(tool_runs).where(
                        tool_runs.c.workspace_id == workspace_id,
                        tool_runs.c.run_id == run_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
        return _run(row) if row is not None else None

    def freeze_read_plan(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        steps: tuple[FrozenToolPlanStep, ...],
        frozen_at: datetime,
    ) -> ToolExecutionPlan:
        """在一次事务中冻结全部只读 Step、预算、策略证据和 Run 状态。"""

        try:
            with self._session_factory() as session, session.begin():
                # 1. 锁定 Run 并复核全部聚合预算，计划预检后的并发推进必须失败关闭。
                run = _locked_run(session, workspace_id, run_id)
                _require_frozen_plan(run, steps, frozen_at)
                planning = _advance_run(
                    session,
                    run,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    target_state="planning",
                    occurred_at=frozen_at,
                )
                append_progress_event(
                    session,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    event_type="tool.run.state_changed",
                    run_state="planning",
                    step_id=None,
                    step_state=None,
                    attempt_id=None,
                    tool_call_id=None,
                    error_code=None,
                    occurred_at=frozen_at,
                )

                # 2. 每个 Step 先进入策略检查，再写入匹配证据；数据库 Trigger 才允许 ready。
                persisted_steps = tuple(
                    _freeze_step(
                        session,
                        run,
                        frozen,
                        workspace_id=workspace_id,
                        run_id=run_id,
                        frozen_at=frozen_at,
                    )
                    for frozen in steps
                )
                for step in persisted_steps:
                    append_progress_event(
                        session,
                        workspace_id=workspace_id,
                        run_id=run_id,
                        event_type="tool.step.state_changed",
                        run_state="planning",
                        step_id=step.step_id,
                        step_state=step.state,
                        attempt_id=None,
                        tool_call_id=None,
                        error_code=None,
                        occurred_at=frozen_at,
                    )

                # 3. 全部 Step 可执行后再发布 Run；任何异常由事务回滚到原始 pending 状态。
                running = _advance_run(
                    session,
                    planning,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    target_state="running",
                    occurred_at=frozen_at,
                )
                append_progress_event(
                    session,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    event_type="tool.run.state_changed",
                    run_state="running",
                    step_id=None,
                    step_state=None,
                    attempt_id=None,
                    tool_call_id=None,
                    error_code=None,
                    occurred_at=frozen_at,
                )
                return ToolExecutionPlan(
                    run=_run(running),
                    steps=persisted_steps,
                    policies=tuple(item.policy for item in steps),
                )
        except IntegrityError as error:
            raise ToolRunConflictError from error

    def transition_run(
        self,
        workspace_id: UUID,
        run_id: UUID,
        target_state: ToolRunState,
        *,
        occurred_at: datetime,
    ) -> ToolRun:
        with self._session_factory() as session, session.begin():
            # 1. 锁定 Run 并校验状态图；进入运行或完成前额外复核子级事实。
            row = _locked_run(session, workspace_id, run_id)
            _require_state_transition(row["state"], target_state, RUN_TRANSITIONS, RUN_TERMINAL)
            if target_state == "running":
                _require_run_has_steps(session, run_id)
            if target_state == "completed":
                _require_all_steps_completed(session, run_id)
            values: dict[str, object] = {
                "state": target_state,
                "updated_at": occurred_at,
                "version": cast(int, row["version"]) + 1,
            }
            if target_state in RUN_TERMINAL:
                values["completed_at"] = occurred_at
            # 2. 版本条件更新和进度事件共用事务，竞争推进不能产生虚假 SSE 事实。
            updated = (
                session.execute(
                    update(tool_runs)
                    .where(
                        tool_runs.c.run_id == run_id,
                        tool_runs.c.workspace_id == workspace_id,
                        tool_runs.c.version == row["version"],
                    )
                    .values(**values)
                    .returning(tool_runs)
                )
                .mappings()
                .one_or_none()
            )
            if updated is None:
                raise ToolRunConflictError
            append_progress_event(
                session,
                workspace_id=workspace_id,
                run_id=run_id,
                event_type="tool.run.state_changed",
                run_state=target_state,
                step_id=None,
                step_state=None,
                attempt_id=None,
                tool_call_id=None,
                error_code=None,
                occurred_at=occurred_at,
            )
            return _run(updated)

    def append_step(
        self,
        *,
        step_id: UUID,
        workspace_id: UUID,
        run_id: UUID,
        tool_id: UUID,
        tool_version: int,
        canonical_arguments_hash: str,
        created_at: datetime,
    ) -> ToolStep:
        """在 planning Run 内追加顺序 Step，并执行冻结步骤预算。"""

        with self._session_factory() as session, session.begin():
            # 1. 锁定父 Run 后分配连续序号，多个计划写入方不能越过 max_steps。
            run = _locked_run(session, workspace_id, run_id)
            if run["state"] != "planning":
                _raise_for_closed_state(cast(str, run["state"]))
            sequence_no = (
                int(
                    session.scalar(
                        select(func.count())
                        .select_from(tool_steps)
                        .where(tool_steps.c.run_id == run_id)
                    )
                    or 0
                )
                + 1
            )
            if sequence_no > cast(int, run["max_steps"]):
                raise ToolRunBudgetExceededError
            # 2. 只保存工具版本与参数摘要，数据库复合外键继续保护版本和空间绑定。
            try:
                row = (
                    session.execute(
                        insert(tool_steps)
                        .values(
                            step_id=step_id,
                            run_id=run_id,
                            workspace_id=workspace_id,
                            sequence_no=sequence_no,
                            tool_id=tool_id,
                            tool_version=tool_version,
                            canonical_arguments_hash=canonical_arguments_hash,
                            timeout_seconds=min(
                                30,
                                cast(int, run["max_execution_seconds"]),
                            ),
                            max_attempts=cast(int, run["max_attempts_per_step"]),
                            max_result_bytes=262_144,
                            max_cost_microunits=0,
                            state="planned",
                            recovery_generation=0,
                            current_attempt_no=None,
                            available_at=created_at,
                            next_attempt_trigger="automatic",
                            created_at=created_at,
                            updated_at=created_at,
                            version=1,
                        )
                        .returning(tool_steps)
                    )
                    .mappings()
                    .one()
                )
            except IntegrityError as error:
                raise ToolRunConflictError from error
            append_progress_event(
                session,
                workspace_id=workspace_id,
                run_id=run_id,
                event_type="tool.step.state_changed",
                run_state=cast(str, run["state"]),
                step_id=step_id,
                step_state="planned",
                attempt_id=None,
                tool_call_id=None,
                error_code=None,
                occurred_at=created_at,
            )
            return _step(row)

    def transition_step(
        self,
        workspace_id: UUID,
        step_id: UUID,
        target_state: ToolStepState,
        *,
        occurred_at: datetime,
    ) -> ToolStep:
        with self._session_factory() as session, session.begin():
            # 1. 锁定当前空间的 Step，状态图先拒绝终态覆盖和越级迁移。
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
                raise ToolExecutionDeniedError
            _require_state_transition(row["state"], target_state, STEP_TRANSITIONS, STEP_TERMINAL)
            # 2. 携带旧版本执行条件更新，竞争写入只能有一个提交者成功。
            updated = (
                session.execute(
                    update(tool_steps)
                    .where(
                        tool_steps.c.step_id == step_id,
                        tool_steps.c.workspace_id == workspace_id,
                        tool_steps.c.version == row["version"],
                    )
                    .values(
                        state=target_state,
                        updated_at=occurred_at,
                        version=cast(int, row["version"]) + 1,
                    )
                    .returning(tool_steps)
                )
                .mappings()
                .one_or_none()
            )
            if updated is None:
                raise ToolRunConflictError
            run_state = cast(
                str,
                session.scalar(
                    select(tool_runs.c.state).where(tool_runs.c.run_id == row["run_id"])
                ),
            )
            append_progress_event(
                session,
                workspace_id=workspace_id,
                run_id=cast(UUID, row["run_id"]),
                event_type="tool.step.state_changed",
                run_state=run_state,
                step_id=step_id,
                step_state=target_state,
                attempt_id=None,
                tool_call_id=None,
                error_code=None,
                occurred_at=occurred_at,
            )
            return _step(updated)

    def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimedToolAttempt | None:
        with self._session_factory() as session, session.begin():
            # 1. 总时限优先于租约恢复，先关闭已经越过绝对截止时间的 Run。
            _expire_due_runs(session, now)
            # 2. Worker 重启没有异常回调，领取前稳定收敛过期租约。
            _expire_stale_attempts(session, now)
            # 3. 只锁定顺序已满足且退避到期的 Step，多 Worker 使用 SKIP LOCKED 竞争。
            row = _select_claimable_step(session, now)
            if row is None:
                return None
            # 4. Step、Attempt 与 ToolCall 在同一事务建立，队列重复投递不会留下半事实。
            return _claim_step(
                session,
                row,
                worker_id=worker_id,
                now=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )

    def begin_attempt(
        self,
        claim: ClaimedToolAttempt,
        *,
        started_at: datetime,
    ) -> bool:
        with self._session_factory() as session, session.begin():
            row = _locked_claim(session, claim)
            if row is None or not _claim_is_current(row, claim, started_at):
                return False
            if row["attempt_state"] != "leased":
                return bool(row["attempt_state"] == "executing")
            session.execute(
                update(tool_attempts)
                .where(tool_attempts.c.attempt_id == claim.attempt_id)
                .values(state="executing", started_at=started_at)
            )
            return True

    def renew_lease(
        self,
        claim: ClaimedToolAttempt,
        *,
        renewed_at: datetime,
        lease_seconds: int,
    ) -> ClaimedToolAttempt | None:
        """续租不得超过 Step 冻结时限和 Run 总截止时间。"""

        with self._session_factory() as session, session.begin():
            _expire_due_runs(session, renewed_at)
            _expire_stale_attempts(session, renewed_at)
            row = _locked_claim(session, claim)
            if row is None or not _claim_is_current(row, claim, renewed_at):
                return None
            if row["attempt_state"] not in {"leased", "executing"}:
                return None

            # 续租上限同时受单步相对时限和 Run 绝对截止时间约束，心跳不能延长冻结预算。
            upper_bound = min(
                cast(datetime, row["deadline_at"]),
                cast(datetime, row["lease_started_at"])
                + timedelta(seconds=cast(int, row["timeout_seconds"])),
            )
            expires_at = min(renewed_at + timedelta(seconds=lease_seconds), upper_bound)
            if expires_at <= renewed_at:
                _expire_claim(session, row, renewed_at)
                return None
            session.execute(text("SET LOCAL ai_platform.tool_lease_write = 'on'"))
            updated = session.execute(
                update(tool_attempts)
                .where(
                    tool_attempts.c.attempt_id == claim.attempt_id,
                    tool_attempts.c.lease_expires_at == claim.lease_expires_at,
                )
                .values(lease_expires_at=expires_at)
                .returning(tool_attempts.c.lease_expires_at)
            ).scalar_one_or_none()
            if updated is None:
                return None
            return _claim_from_row(row, claim, lease_expires_at=updated)

    def observe_cancellation(
        self,
        claim: ClaimedToolAttempt,
        *,
        observed_at: datetime,
    ) -> bool:
        """仅当前执行者可以记录取消观察，返回值表示是否存在取消事实。"""

        with self._session_factory() as session, session.begin():
            row = _locked_claim(session, claim)
            if row is None or row["attempt_state"] not in {"leased", "executing"}:
                return False
            if not _claim_identity_matches(row, claim):
                return False
            cancellation_requested = bool(
                row["run_state"] == "cancellation_requested"
                or row["cancel_requested_at"] is not None
            )
            if cancellation_requested and row["cancel_observed_at"] is None:
                session.execute(text("SET LOCAL ai_platform.tool_lease_write = 'on'"))
                session.execute(
                    update(tool_attempts)
                    .where(tool_attempts.c.attempt_id == claim.attempt_id)
                    .values(cancel_observed_at=observed_at)
                )
            return cancellation_requested

    def transition_call(
        self,
        claim: ClaimedToolAttempt,
        target_state: ToolCallState,
        *,
        occurred_at: datetime,
    ) -> bool:
        with self._session_factory() as session, session.begin():
            row = _locked_claim(session, claim)
            if row is None or not _claim_is_current(row, claim, occurred_at):
                return False
            if row["attempt_state"] != "executing" or row["run_state"] != "running":
                return False
            current = cast(str, row["call_state"])
            if current == target_state:
                return True
            _require_state_transition(current, target_state, CALL_TRANSITIONS, CALL_TERMINAL)
            session.execute(
                update(tool_calls)
                .where(tool_calls.c.tool_call_id == claim.tool_call_id)
                .values(state=target_state, updated_at=occurred_at)
            )
            if target_state == "executing":
                append_progress_event(
                    session,
                    workspace_id=claim.workspace_id,
                    run_id=claim.run_id,
                    event_type="tool.call.started",
                    run_state="running",
                    step_id=claim.step_id,
                    step_state="running",
                    attempt_id=claim.attempt_id,
                    tool_call_id=claim.tool_call_id,
                    error_code=None,
                    occurred_at=occurred_at,
                )
            return True

    def finish_attempt(
        self,
        claim: ClaimedToolAttempt,
        *,
        facts: ToolAttemptOutcomeFacts,
        succeeded: bool,
        completed_at: datetime,
        error_code: str | None,
        retryable: bool,
        next_attempt_at: datetime | None,
    ) -> AttemptResult:
        with self._session_factory() as session, session.begin():
            _expire_due_runs(session, completed_at)
            _expire_stale_attempts(session, completed_at)
            row = _locked_claim(session, claim)
            if row is None:
                return "ignored_late_result"
            # 1. 取消优先于 Adapter 返回；旧 Worker 只关闭历史 Attempt，不能提交成功。
            if row["run_state"] == "cancellation_requested":
                append_attempt_outcome(
                    session,
                    row=row,
                    claim=claim,
                    facts=facts,
                    effective_outcome="ignored_late_result",
                    run_state="cancelled",
                    step_state="cancelled",
                    event_type="tool.call.failed",
                    error_code="TOOL_LATE_RESULT_IGNORED",
                    recorded_at=completed_at,
                )
                _close_cancelled_claim(session, row, claim, completed_at)
                return "ignored_late_result"
            if not _claim_is_current(row, claim, completed_at):
                return "ignored_late_result"
            if row["attempt_state"] != "executing" or row["call_state"] != "executing":
                return "ignored_late_result"
            # 2. 成功和稳定失败直接收口；只有 safe_read 且预算未耗尽时才能进入退避重试。
            if not succeeded and retryable:
                if not _is_safe_read(row):
                    retryable = False
                elif cast(int, row["attempt_no"]) < cast(int, row["max_attempts"]):
                    if next_attempt_at is None or next_attempt_at <= completed_at:
                        raise ToolRunConflictError
                    append_attempt_outcome(
                        session,
                        row=row,
                        claim=claim,
                        facts=facts,
                        effective_outcome="failed",
                        run_state="running",
                        step_state="retry_wait",
                        event_type="tool.call.failed",
                        error_code=error_code or "TOOL_ADAPTER_UNAVAILABLE",
                        recorded_at=completed_at,
                    )
                    _close_for_retry(
                        session,
                        row,
                        claim,
                        completed_at=completed_at,
                        error_code=error_code or "TOOL_ADAPTER_UNAVAILABLE",
                        next_attempt_at=next_attempt_at,
                        trigger="automatic_retry",
                    )
                    return "retry_wait"
                else:
                    append_attempt_outcome(
                        session,
                        row=row,
                        claim=claim,
                        facts=facts,
                        effective_outcome="manual_recovery",
                        run_state="manual_recovery",
                        step_state="manual_recovery",
                        event_type="tool.call.failed",
                        error_code=error_code or "TOOL_ADAPTER_UNAVAILABLE",
                        recorded_at=completed_at,
                    )
                    _close_for_manual_recovery(
                        session,
                        row,
                        claim,
                        error_code=error_code or "TOOL_ADAPTER_UNAVAILABLE",
                        occurred_at=completed_at,
                    )
                    return "manual_recovery"

            # 3. 非重试结论先写运营事实，再由数据库约束下的状态机原子关闭所有层级。
            outcome: AttemptResult = "succeeded" if succeeded else "failed"
            effective_outcome: ToolUsageOutcome = "succeeded" if succeeded else "failed"
            append_attempt_outcome(
                session,
                row=row,
                claim=claim,
                facts=facts,
                effective_outcome=effective_outcome,
                run_state="running" if succeeded else "failed",
                step_state="completed" if succeeded else "failed",
                event_type="tool.call.completed" if succeeded else "tool.call.failed",
                error_code=error_code,
                recorded_at=completed_at,
            )
            _close_current_attempt(
                session,
                row,
                claim,
                outcome=outcome,
                completed_at=completed_at,
                error_code=error_code,
            )
            return outcome

    def require_manual_recovery(
        self,
        claim: ClaimedToolAttempt,
        *,
        facts: ToolAttemptOutcomeFacts,
        error_code: str,
        occurred_at: datetime,
    ) -> AttemptResult:
        """把结果未知或不能自动接管的调用稳定转入人工恢复。"""

        with self._session_factory() as session, session.begin():
            # 1. 总时限和既有人工恢复优先，已经失效的 Claim 不能创建第二条用量事实。
            _expire_due_runs(session, occurred_at)
            row = _locked_claim(session, claim)
            if row is None:
                return "ignored_late_result"
            if row["run_state"] == "manual_recovery" and row["step_state"] == "manual_recovery":
                return "manual_recovery"
            if row["run_state"] == "cancellation_requested":
                append_attempt_outcome(
                    session,
                    row=row,
                    claim=claim,
                    facts=facts,
                    effective_outcome="ignored_late_result",
                    run_state="cancelled",
                    step_state="cancelled",
                    event_type="tool.call.failed",
                    error_code="TOOL_LATE_RESULT_IGNORED",
                    recorded_at=occurred_at,
                )
                _close_cancelled_claim(session, row, claim, occurred_at)
                return "ignored_late_result"
            if not _claim_is_current(row, claim, occurred_at):
                return "ignored_late_result"
            # 2. 当前结果未知样本先写用量和失败进度，再把父级稳定移入人工恢复。
            append_attempt_outcome(
                session,
                row=row,
                claim=claim,
                facts=facts,
                effective_outcome="manual_recovery",
                run_state="manual_recovery",
                step_state="manual_recovery",
                event_type="tool.call.failed",
                error_code=error_code,
                recorded_at=occurred_at,
            )
            _close_for_manual_recovery(
                session,
                row,
                claim,
                error_code=error_code,
                occurred_at=occurred_at,
            )
            return "manual_recovery"

    def recover_manually(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        actor_id: UUID,
        account_id: UUID,
        request_id: UUID,
        trace_id: str,
        traceparent: str,
        authorization: AuditAuthorization | None,
        recovered_at: datetime,
    ) -> ToolRun:
        """开启下一恢复代际，并把恢复动作和事件放入同一事务。"""

        try:
            with self._session_factory() as session, session.begin():
                # 1. 锁定死信 Run，先处理总时限、未知副作用和三代上限。
                run = _locked_run(session, workspace_id, run_id)
                if run["state"] != "manual_recovery":
                    _raise_for_closed_state(cast(str, run["state"]))
                if recovered_at >= cast(datetime, run["deadline_at"]):
                    _time_out_run(session, run, recovered_at)
                    return _run(_locked_run(session, workspace_id, run_id))
                if run["recovery_reason_code"] == "TOOL_OUTCOME_UNKNOWN":
                    raise ToolRetryNotAllowedError
                generation = cast(int, run["recovery_generation"]) + 1
                if generation > 3:
                    raise ToolRunBudgetExceededError
                step = _manual_recovery_step(session, run_id)

                # 2. 受控递增 Run/Step 代际，并在同一事务记录操作者审计与状态事件。
                session.execute(text("SET LOCAL ai_platform.tool_recovery_write = 'on'"))
                session.execute(
                    update(tool_steps)
                    .where(tool_steps.c.step_id == step["step_id"])
                    .values(
                        state="ready",
                        recovery_generation=generation,
                        current_attempt_no=None,
                        available_at=recovered_at,
                        next_attempt_trigger="manual_recovery",
                        updated_at=recovered_at,
                        version=cast(int, step["version"]) + 1,
                    )
                )
                updated = (
                    session.execute(
                        update(tool_runs)
                        .where(
                            tool_runs.c.run_id == run_id,
                            tool_runs.c.workspace_id == workspace_id,
                        )
                        .values(
                            state="running",
                            recovery_generation=generation,
                            recovery_reason_code=None,
                            recovery_required_at=None,
                            last_recovered_by_actor_id=actor_id,
                            last_recovered_at=recovered_at,
                            updated_at=recovered_at,
                            version=cast(int, run["version"]) + 1,
                        )
                        .returning(tool_runs)
                    )
                    .mappings()
                    .one()
                )
                # 3. 恢复后的 Step/Run 进度与操作者审计、Outbox 共用同一事务提交。
                append_progress_event(
                    session,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    event_type="tool.step.state_changed",
                    run_state="running",
                    step_id=cast(UUID, step["step_id"]),
                    step_state="ready",
                    attempt_id=None,
                    tool_call_id=None,
                    error_code=None,
                    occurred_at=recovered_at,
                )
                append_progress_event(
                    session,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    event_type="tool.run.state_changed",
                    run_state="running",
                    step_id=None,
                    step_state=None,
                    attempt_id=None,
                    tool_call_id=None,
                    error_code=None,
                    occurred_at=recovered_at,
                )
                _record_manual_recovery(
                    session,
                    updated,
                    actor_id=actor_id,
                    account_id=account_id,
                    request_id=request_id,
                    trace_id=trace_id,
                    traceparent=traceparent,
                    authorization=authorization,
                    occurred_at=recovered_at,
                )
                return _run(updated)
        except IntegrityError as error:
            raise ToolRunConflictError from error

    def request_cancellation(
        self,
        workspace_id: UUID,
        run_id: UUID,
        *,
        requested_at: datetime,
    ) -> ToolRun:
        with self._session_factory() as session, session.begin():
            row = _locked_run(session, workspace_id, run_id)
            if row["state"] in RUN_TERMINAL:
                return _run(row)
            if row["state"] == "cancellation_requested":
                return _run(row)
            if row["state"] not in {
                "pending",
                "running",
                "waiting_confirmation",
                "waiting_approval",
                "manual_recovery",
            }:
                raise ToolRunConflictError

            # 1. 先提交取消事实，后续领取条件立即失效；未运行步骤同步关闭。
            session.execute(
                update(tool_runs)
                .where(tool_runs.c.run_id == run_id)
                .values(
                    state="cancellation_requested",
                    cancel_requested_at=requested_at,
                    recovery_reason_code=None,
                    recovery_required_at=None,
                    updated_at=requested_at,
                    version=cast(int, row["version"]) + 1,
                )
            )
            append_progress_event(
                session,
                workspace_id=workspace_id,
                run_id=run_id,
                event_type="tool.run.cancellation_requested",
                run_state="cancellation_requested",
                step_id=None,
                step_state=None,
                attempt_id=None,
                tool_call_id=None,
                error_code=None,
                occurred_at=requested_at,
            )
            session.execute(
                update(tool_steps)
                .where(
                    tool_steps.c.run_id == run_id,
                    tool_steps.c.state.in_(
                        (
                            "planned",
                            "policy_checking",
                            "waiting_confirmation",
                            "waiting_approval",
                            "ready",
                            "retry_wait",
                            "manual_recovery",
                        )
                    ),
                )
                .values(
                    state="cancelled",
                    available_at=requested_at,
                    updated_at=requested_at,
                    version=tool_steps.c.version + 1,
                )
            )
            active = _active_claim_row(session, run_id)
            # 2. 尚未开始的租约可立即取消；执行中的调用等待回调并按迟到结果收敛。
            if active is not None and active["attempt_state"] == "leased":
                cancelled = _attempt_recovery_row(session, cast(UUID, active["attempt_id"]))
                _close_cancelled_row(session, cancelled, requested_at)
                active = None
            if active is None:
                _finish_run_cancellation(session, run_id, requested_at)
            # 3. 从当前事务回读收敛后的 Run，保证返回状态与进度事件在同一次提交中一致。
            current = (
                session.execute(select(tool_runs).where(tool_runs.c.run_id == run_id))
                .mappings()
                .one()
            )
            return _run(current)


def _select_claimable_step(session: Session, now: datetime) -> Row[Any] | None:
    previous = tool_steps.alias("previous_tool_steps")
    return session.execute(
        select(
            tool_steps,
            tool_runs.c.deadline_at,
            agent_tool_definitions.c.access_mode,
            agent_tool_definitions.c.risk_level,
        )
        .join(tool_runs, tool_runs.c.run_id == tool_steps.c.run_id)
        .join(
            agent_tool_definitions,
            and_(
                agent_tool_definitions.c.tool_id == tool_steps.c.tool_id,
                agent_tool_definitions.c.tool_version == tool_steps.c.tool_version,
            ),
        )
        .where(
            tool_steps.c.state.in_(("ready", "retry_wait")),
            tool_steps.c.available_at <= now,
            tool_runs.c.state == "running",
            tool_runs.c.cancel_requested_at.is_(None),
            tool_runs.c.deadline_at > now,
            or_(
                tool_steps.c.current_attempt_no.is_(None),
                tool_steps.c.current_attempt_no < tool_steps.c.max_attempts,
            ),
            ~exists(
                select(1).where(
                    previous.c.run_id == tool_steps.c.run_id,
                    previous.c.sequence_no < tool_steps.c.sequence_no,
                    previous.c.state != "completed",
                )
            ),
        )
        .order_by(tool_steps.c.created_at, tool_steps.c.sequence_no)
        .limit(1)
        .with_for_update(of=tool_steps, skip_locked=True)
    ).one_or_none()


def _claim_step(
    session: Session,
    row: Row[Any],
    *,
    worker_id: str,
    now: datetime,
    lease_expires_at: datetime,
) -> ClaimedToolAttempt:
    # 1. 在已锁定 Step 上生成不可复用的 Attempt、ToolCall 和租约代际。
    lease_expires_at = min(
        lease_expires_at,
        cast(datetime, row.deadline_at),
        now + timedelta(seconds=cast(int, row.timeout_seconds)),
    )
    recovery_generation = cast(int, row.recovery_generation)
    attempt_no = cast(int | None, row.current_attempt_no) or 0
    attempt_no += 1
    attempt_id = uuid4()
    tool_call_id = uuid4()
    # 2. 先推进 Step 并插入租约 Attempt；延迟约束在提交时核对当前 Attempt。
    session.execute(
        update(tool_steps)
        .where(tool_steps.c.step_id == row.step_id, tool_steps.c.state.in_(("ready", "retry_wait")))
        .values(
            state="running",
            current_attempt_no=attempt_no,
            updated_at=now,
            version=tool_steps.c.version + 1,
        )
    )
    session.execute(
        insert(tool_attempts).values(
            attempt_id=attempt_id,
            run_id=row.run_id,
            step_id=row.step_id,
            workspace_id=row.workspace_id,
            recovery_generation=recovery_generation,
            attempt_no=attempt_no,
            lease_generation=recovery_generation * 10 + attempt_no,
            trigger=row.next_attempt_trigger,
            state="leased",
            worker_id=worker_id,
            lease_started_at=now,
            lease_expires_at=lease_expires_at,
            started_at=now,
            completed_at=None,
            error_code=None,
            cancel_observed_at=None,
        )
    )
    # 3. ToolCall 与 Attempt 同事务绑定，返回给 Worker 的 Claim 冻结完整租约身份。
    session.execute(
        insert(tool_calls).values(
            tool_call_id=tool_call_id,
            run_id=row.run_id,
            step_id=row.step_id,
            attempt_id=attempt_id,
            workspace_id=row.workspace_id,
            tool_id=row.tool_id,
            tool_version=row.tool_version,
            canonical_arguments_hash=row.canonical_arguments_hash,
            access_mode=row.access_mode,
            risk_level=row.risk_level,
            credential_ref=None,
            state="proposed",
            created_at=now,
            updated_at=now,
            completed_at=None,
            error_code=None,
        )
    )
    return ClaimedToolAttempt(
        attempt_id=attempt_id,
        run_id=row.run_id,
        step_id=row.step_id,
        tool_call_id=tool_call_id,
        workspace_id=row.workspace_id,
        tool_id=row.tool_id,
        tool_version=row.tool_version,
        canonical_arguments_hash=row.canonical_arguments_hash,
        recovery_generation=recovery_generation,
        attempt_no=attempt_no,
        lease_generation=recovery_generation * 10 + attempt_no,
        trigger=cast(ToolAttemptTrigger, row.next_attempt_trigger),
        worker_id=worker_id,
        lease_expires_at=lease_expires_at,
    )


def _locked_claim(
    session: Session,
    claim: ClaimedToolAttempt,
) -> RowMapping | None:
    return (
        session.execute(
            select(
                tool_attempts.c.attempt_id,
                tool_attempts.c.run_id,
                tool_attempts.c.step_id,
                tool_attempts.c.workspace_id,
                tool_attempts.c.recovery_generation,
                tool_attempts.c.attempt_no,
                tool_attempts.c.lease_generation,
                tool_attempts.c.trigger,
                tool_attempts.c.worker_id,
                tool_attempts.c.lease_started_at,
                tool_attempts.c.lease_expires_at,
                tool_attempts.c.cancel_observed_at,
                tool_attempts.c.state.label("attempt_state"),
                tool_steps.c.recovery_generation.label("step_recovery_generation"),
                tool_steps.c.current_attempt_no,
                tool_steps.c.timeout_seconds,
                tool_steps.c.max_attempts,
                tool_steps.c.state.label("step_state"),
                tool_steps.c.version.label("step_version"),
                tool_runs.c.deadline_at,
                tool_runs.c.cancel_requested_at,
                tool_runs.c.state.label("run_state"),
                tool_runs.c.version.label("run_version"),
                tool_calls.c.tool_call_id,
                tool_calls.c.tool_id,
                tool_calls.c.tool_version,
                tool_calls.c.canonical_arguments_hash,
                tool_calls.c.access_mode,
                tool_calls.c.risk_level,
                tool_calls.c.state.label("call_state"),
                agent_tool_definitions.c.retry_mode,
            )
            .join(tool_steps, tool_steps.c.step_id == tool_attempts.c.step_id)
            .join(tool_runs, tool_runs.c.run_id == tool_attempts.c.run_id)
            .join(tool_calls, tool_calls.c.attempt_id == tool_attempts.c.attempt_id)
            .join(
                agent_tool_definitions,
                and_(
                    agent_tool_definitions.c.tool_id == tool_calls.c.tool_id,
                    agent_tool_definitions.c.tool_version == tool_calls.c.tool_version,
                ),
            )
            .where(
                tool_attempts.c.attempt_id == claim.attempt_id,
                tool_attempts.c.run_id == claim.run_id,
                tool_attempts.c.step_id == claim.step_id,
                tool_attempts.c.workspace_id == claim.workspace_id,
                tool_calls.c.tool_call_id == claim.tool_call_id,
            )
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )


def _claim_is_current(
    row: RowMapping,
    claim: ClaimedToolAttempt,
    occurred_at: datetime,
) -> bool:
    return bool(
        _claim_identity_matches(row, claim)
        and row["lease_generation"] == claim.lease_generation
        and row["current_attempt_no"] == claim.attempt_no
        and row["step_recovery_generation"] == claim.recovery_generation
        and row["lease_expires_at"] > occurred_at
        and row["deadline_at"] > occurred_at
        and row["run_state"] not in RUN_TERMINAL
        and row["step_state"] not in STEP_TERMINAL
    )


def _claim_identity_matches(row: RowMapping, claim: ClaimedToolAttempt) -> bool:
    """比较不可复用的租约身份，续租只允许截止时间发生变化。"""

    return bool(
        row["attempt_id"] == claim.attempt_id
        and row["run_id"] == claim.run_id
        and row["step_id"] == claim.step_id
        and row["workspace_id"] == claim.workspace_id
        and row["recovery_generation"] == claim.recovery_generation
        and row["attempt_no"] == claim.attempt_no
        and row["lease_generation"] == claim.lease_generation
        and row["trigger"] == claim.trigger
        and row["worker_id"] == claim.worker_id
        and row["tool_call_id"] == claim.tool_call_id
    )


def _expire_due_runs(session: Session, now: datetime) -> None:
    """有界关闭超过绝对截止时间的 Run，避免无活动租约时永久停留。"""

    rows = (
        session.execute(
            select(tool_runs)
            .where(
                tool_runs.c.state.in_(("running", "manual_recovery")),
                tool_runs.c.deadline_at <= now,
            )
            .order_by(tool_runs.c.deadline_at)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        .mappings()
        .all()
    )
    for row in rows:
        _time_out_run(session, row, now)


def _expire_stale_attempts(session: Session, now: datetime) -> None:
    """将 Worker 重启遗留租约收敛为安全重试、人工恢复或总超时。"""

    attempt_ids = session.scalars(
        select(tool_attempts.c.attempt_id)
        .where(
            tool_attempts.c.state.in_(("leased", "executing")),
            tool_attempts.c.lease_expires_at <= now,
        )
        .order_by(tool_attempts.c.lease_expires_at)
        .limit(100)
        .with_for_update(skip_locked=True)
    ).all()
    for attempt_id in attempt_ids:
        row = _attempt_recovery_row(session, attempt_id)
        if row["run_state"] in RUN_TERMINAL or row["attempt_state"] in ATTEMPT_TERMINAL:
            continue
        # 取消和总截止时间均高于恢复决定，旧 Worker 的迟到结果只能留下历史终态。
        if row["run_state"] == "cancellation_requested":
            _close_cancelled_row(session, row, now)
            continue
        if cast(datetime, row["deadline_at"]) <= now:
            _time_out_run(session, row, now)
            continue
        _expire_claim(session, row, now)


def _expire_claim(session: Session, row: RowMapping, now: datetime) -> None:
    """按工具重试安全性处理一条已锁定的过期租约。"""

    claim = _claim_from_recovery_row(row)
    if _is_safe_read(row) and cast(int, row["attempt_no"]) < cast(int, row["max_attempts"]):
        append_attempt_outcome(
            session,
            row=row,
            claim=claim,
            facts=_derived_outcome_facts(row, "timed_out", "TOOL_ADAPTER_UNAVAILABLE", now),
            effective_outcome="timed_out",
            run_state="running",
            step_state="retry_wait",
            event_type="tool.call.failed",
            error_code="TOOL_ADAPTER_UNAVAILABLE",
            recorded_at=now,
        )
        _close_for_retry(
            session,
            row,
            claim,
            completed_at=now,
            error_code="TOOL_ADAPTER_UNAVAILABLE",
            next_attempt_at=now + timedelta(seconds=1),
            trigger="lease_recovery",
            timed_out=True,
        )
        return
    reason = _lease_recovery_reason(session, row)
    append_attempt_outcome(
        session,
        row=row,
        claim=claim,
        facts=_derived_outcome_facts(row, "timed_out", reason, now),
        effective_outcome="timed_out",
        run_state="manual_recovery",
        step_state="manual_recovery",
        event_type="tool.call.failed",
        error_code=reason,
        recorded_at=now,
    )
    _close_for_manual_recovery(
        session,
        row,
        claim,
        error_code=reason,
        occurred_at=now,
        attempt_state="timed_out",
        keep_call_executing=reason == "TOOL_OUTCOME_UNKNOWN",
    )


def _attempt_recovery_row(session: Session, attempt_id: UUID) -> RowMapping:
    """锁定恢复判断所需的最小完整快照。"""

    return (
        session.execute(
            select(
                tool_attempts,
                tool_attempts.c.state.label("attempt_state"),
                tool_steps.c.current_attempt_no,
                tool_steps.c.recovery_generation.label("step_recovery_generation"),
                tool_steps.c.max_attempts,
                tool_steps.c.timeout_seconds,
                tool_steps.c.version.label("step_version"),
                tool_steps.c.state.label("step_state"),
                tool_runs.c.deadline_at,
                tool_runs.c.cancel_requested_at,
                tool_runs.c.version.label("run_version"),
                tool_runs.c.state.label("run_state"),
                tool_calls.c.tool_call_id,
                tool_calls.c.tool_id,
                tool_calls.c.tool_version,
                tool_calls.c.canonical_arguments_hash,
                tool_calls.c.access_mode,
                tool_calls.c.risk_level,
                tool_calls.c.state.label("call_state"),
                agent_tool_definitions.c.retry_mode,
            )
            .join(tool_steps, tool_steps.c.step_id == tool_attempts.c.step_id)
            .join(tool_runs, tool_runs.c.run_id == tool_attempts.c.run_id)
            .join(tool_calls, tool_calls.c.attempt_id == tool_attempts.c.attempt_id)
            .join(
                agent_tool_definitions,
                and_(
                    agent_tool_definitions.c.tool_id == tool_calls.c.tool_id,
                    agent_tool_definitions.c.tool_version == tool_calls.c.tool_version,
                ),
            )
            .where(tool_attempts.c.attempt_id == attempt_id)
            .with_for_update()
        )
        .mappings()
        .one()
    )


def _close_for_retry(
    session: Session,
    row: RowMapping,
    claim: ClaimedToolAttempt,
    *,
    completed_at: datetime,
    error_code: str,
    next_attempt_at: datetime,
    trigger: ToolAttemptTrigger,
    timed_out: bool = False,
) -> None:
    """关闭本代 Attempt，并让同一恢复代际在退避后领取下一次尝试。"""

    outcome = "timed_out" if timed_out else "failed"
    if row["call_state"] not in CALL_TERMINAL:
        session.execute(
            update(tool_calls)
            .where(tool_calls.c.tool_call_id == claim.tool_call_id)
            .values(
                state=outcome,
                completed_at=completed_at,
                updated_at=completed_at,
                error_code=error_code,
            )
        )
    session.execute(
        update(tool_attempts)
        .where(tool_attempts.c.attempt_id == claim.attempt_id)
        .values(state=outcome, completed_at=completed_at, error_code=error_code)
    )
    session.execute(
        update(tool_steps)
        .where(tool_steps.c.step_id == claim.step_id)
        .values(
            state="retry_wait",
            available_at=next_attempt_at,
            next_attempt_trigger=trigger,
            updated_at=completed_at,
            version=cast(int, row["step_version"]) + 1,
        )
    )


def _close_for_manual_recovery(
    session: Session,
    row: RowMapping,
    claim: ClaimedToolAttempt,
    *,
    error_code: str,
    occurred_at: datetime,
    attempt_state: str = "failed",
    keep_call_executing: bool = False,
) -> None:
    """把不可自动处理的事实移入死信状态，保留原 Attempt 不可变历史。"""

    # 1. 原 Call 与 Attempt 先关闭；未知副作用例外保留 executing Call 给只读对账。
    if not keep_call_executing and row["call_state"] not in CALL_TERMINAL:
        call_state = "timed_out" if attempt_state == "timed_out" else "failed"
        session.execute(
            update(tool_calls)
            .where(tool_calls.c.tool_call_id == claim.tool_call_id)
            .values(
                state=call_state,
                completed_at=occurred_at,
                updated_at=occurred_at,
                error_code=error_code,
            )
        )
    if row["attempt_state"] not in ATTEMPT_TERMINAL:
        session.execute(
            update(tool_attempts)
            .where(tool_attempts.c.attempt_id == claim.attempt_id)
            .values(state=attempt_state, completed_at=occurred_at, error_code=error_code)
        )
    # 2. 父级统一进入人工恢复并冻结原因，Worker 领取条件立即失效。
    session.execute(
        update(tool_steps)
        .where(tool_steps.c.step_id == claim.step_id)
        .values(
            state="manual_recovery",
            available_at=occurred_at,
            updated_at=occurred_at,
            version=cast(int, row["step_version"]) + 1,
        )
    )
    session.execute(
        update(tool_runs)
        .where(tool_runs.c.run_id == claim.run_id)
        .values(
            state="manual_recovery",
            recovery_reason_code=error_code,
            recovery_required_at=occurred_at,
            updated_at=occurred_at,
            version=cast(int, row["run_version"]) + 1,
        )
    )


def _is_safe_read(row: RowMapping) -> bool:
    return bool(row["access_mode"] == "read" and row["retry_mode"] == "safe_read")


def _lease_recovery_reason(session: Session, row: RowMapping) -> str:
    """写调用一旦存在执行前预留，就只能按未知副作用进入只读对账。"""

    if row["access_mode"] == "write" and row["call_state"] == "executing":
        reserved = session.scalar(
            select(func.count())
            .select_from(tool_idempotency_records)
            .where(
                tool_idempotency_records.c.tool_call_id == row["tool_call_id"],
                tool_idempotency_records.c.state.in_(("reserved", "outcome_unknown")),
            )
        )
        if int(reserved or 0) > 0:
            return "TOOL_OUTCOME_UNKNOWN"
    return "TOOL_ADAPTER_UNAVAILABLE" if _is_safe_read(row) else "TOOL_RETRY_NOT_ALLOWED"


def _claim_from_recovery_row(row: RowMapping) -> ClaimedToolAttempt:
    return ClaimedToolAttempt(
        attempt_id=cast(UUID, row["attempt_id"]),
        run_id=cast(UUID, row["run_id"]),
        step_id=cast(UUID, row["step_id"]),
        tool_call_id=cast(UUID, row["tool_call_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        tool_id=cast(UUID, row["tool_id"]),
        tool_version=cast(int, row["tool_version"]),
        canonical_arguments_hash=cast(str, row["canonical_arguments_hash"]),
        recovery_generation=cast(int, row["recovery_generation"]),
        attempt_no=cast(int, row["attempt_no"]),
        lease_generation=cast(int, row["lease_generation"]),
        trigger=cast(ToolAttemptTrigger, row["trigger"]),
        worker_id=cast(str, row["worker_id"]),
        lease_expires_at=cast(datetime, row["lease_expires_at"]),
    )


def _claim_from_row(
    row: RowMapping,
    claim: ClaimedToolAttempt,
    *,
    lease_expires_at: datetime,
) -> ClaimedToolAttempt:
    """续租只替换截止时间，其余冻结身份继续使用调用方 Claim。"""

    del row
    return ClaimedToolAttempt(
        attempt_id=claim.attempt_id,
        run_id=claim.run_id,
        step_id=claim.step_id,
        tool_call_id=claim.tool_call_id,
        workspace_id=claim.workspace_id,
        tool_id=claim.tool_id,
        tool_version=claim.tool_version,
        canonical_arguments_hash=claim.canonical_arguments_hash,
        recovery_generation=claim.recovery_generation,
        attempt_no=claim.attempt_no,
        lease_generation=claim.lease_generation,
        trigger=claim.trigger,
        worker_id=claim.worker_id,
        lease_expires_at=lease_expires_at,
    )


def _close_cancelled_row(session: Session, row: RowMapping, occurred_at: datetime) -> None:
    """在无 Claim 对象的租约扫描中复用取消优先规则。"""

    # 1. leased 表示尚未调用，executing 则只能记为迟到；两者都必须保留一条用量事实。
    claim = _claim_from_recovery_row(row)
    outcome: ToolUsageOutcome = (
        "cancelled" if row["attempt_state"] == "leased" else "ignored_late_result"
    )
    error_code = "TOOL_CANCELLED" if outcome == "cancelled" else "TOOL_LATE_RESULT_IGNORED"
    append_attempt_outcome(
        session,
        row=row,
        claim=claim,
        facts=_derived_outcome_facts(row, outcome, error_code, occurred_at),
        effective_outcome=outcome,
        run_state="cancelled",
        step_state="cancelled",
        event_type="tool.call.failed",
        error_code=error_code,
        recorded_at=occurred_at,
    )
    # 2. 运营事实成功后才关闭 Call、Attempt、Step 和 Run，避免终态与证据脱节。
    if row["call_state"] not in CALL_TERMINAL:
        session.execute(
            update(tool_calls)
            .where(tool_calls.c.tool_call_id == row["tool_call_id"])
            .values(state="cancelled", completed_at=occurred_at, updated_at=occurred_at)
        )
    session.execute(
        update(tool_attempts)
        .where(tool_attempts.c.attempt_id == row["attempt_id"])
        .values(
            state="cancelled" if row["attempt_state"] == "leased" else "ignored_late_result",
            completed_at=occurred_at,
        )
    )
    session.execute(
        update(tool_steps)
        .where(tool_steps.c.step_id == row["step_id"])
        .values(
            state="cancelled",
            updated_at=occurred_at,
            version=cast(int, row["step_version"]) + 1,
        )
    )
    _finish_run_cancellation(session, cast(UUID, row["run_id"]), occurred_at)


def _time_out_run(session: Session, source: RowMapping, occurred_at: datetime) -> None:
    """关闭 Run 下全部未终止事实，总截止时间不允许被人工恢复绕过。"""

    run_id = cast(UUID, source["run_id"])
    run = (
        session.execute(select(tool_runs).where(tool_runs.c.run_id == run_id).with_for_update())
        .mappings()
        .one()
    )
    if run["state"] in RUN_TERMINAL:
        return
    # 1. 先为所有活动 Attempt 写超时用量，确保后续批量终止不能剔除失败样本。
    active_attempt_ids = session.scalars(
        select(tool_attempts.c.attempt_id).where(
            tool_attempts.c.run_id == run_id,
            tool_attempts.c.state.in_(("leased", "executing")),
        )
    ).all()
    for attempt_id in active_attempt_ids:
        active = _attempt_recovery_row(session, attempt_id)
        claim = _claim_from_recovery_row(active)
        append_attempt_outcome(
            session,
            row=active,
            claim=claim,
            facts=_derived_outcome_facts(
                active,
                "timed_out",
                "TOOL_RUN_DEADLINE_EXCEEDED",
                occurred_at,
            ),
            effective_outcome="timed_out",
            run_state="timed_out",
            step_state="timed_out",
            event_type="tool.call.failed",
            error_code="TOOL_RUN_DEADLINE_EXCEEDED",
            recorded_at=occurred_at,
        )
    # 2. 再关闭所有未终止 Call 和活动 Attempt，迟到 Worker 随即失去写资格。
    session.execute(
        update(tool_calls)
        .where(tool_calls.c.run_id == run_id, ~tool_calls.c.state.in_(CALL_TERMINAL))
        .values(
            state="timed_out",
            completed_at=occurred_at,
            updated_at=occurred_at,
            error_code="TOOL_RUN_DEADLINE_EXCEEDED",
        )
    )
    # 3. 最后关闭 Step 和 Run 并发布超时进度，人工恢复不能延长冻结的绝对期限。
    session.execute(
        update(tool_attempts)
        .where(tool_attempts.c.run_id == run_id, tool_attempts.c.state.in_(("leased", "executing")))
        .values(
            state="timed_out",
            completed_at=occurred_at,
            error_code="TOOL_RUN_DEADLINE_EXCEEDED",
        )
    )
    session.execute(
        update(tool_steps)
        .where(tool_steps.c.run_id == run_id, ~tool_steps.c.state.in_(STEP_TERMINAL))
        .values(
            state="timed_out",
            available_at=occurred_at,
            updated_at=occurred_at,
            version=tool_steps.c.version + 1,
        )
    )
    session.execute(
        update(tool_runs)
        .where(tool_runs.c.run_id == run_id)
        .values(
            state="timed_out",
            recovery_reason_code=None,
            recovery_required_at=None,
            updated_at=occurred_at,
            completed_at=occurred_at,
            version=cast(int, run["version"]) + 1,
        )
    )
    append_progress_event(
        session,
        workspace_id=cast(UUID, run["workspace_id"]),
        run_id=run_id,
        event_type="tool.run.state_changed",
        run_state="timed_out",
        step_id=None,
        step_state=None,
        attempt_id=None,
        tool_call_id=None,
        error_code="TOOL_RUN_DEADLINE_EXCEEDED",
        occurred_at=occurred_at,
    )


def _manual_recovery_step(session: Session, run_id: UUID) -> RowMapping:
    rows = (
        session.execute(
            select(tool_steps)
            .where(tool_steps.c.run_id == run_id, tool_steps.c.state == "manual_recovery")
            .with_for_update()
        )
        .mappings()
        .all()
    )
    if len(rows) != 1:
        raise ToolRunConflictError
    return rows[0]


def _record_manual_recovery(
    session: Session,
    run: RowMapping,
    *,
    actor_id: UUID,
    account_id: UUID,
    request_id: UUID,
    trace_id: str,
    traceparent: str,
    authorization: AuditAuthorization | None,
    occurred_at: datetime,
) -> None:
    """只记录恢复代际和目标状态，不把参数、结果或凭证写入横切事实。"""

    attributes: dict[str, object] = {
        "recovery_generation": cast(int, run["recovery_generation"]),
        "state": "running",
    }
    SqlAlchemyAuditWriter(session).add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=cast(UUID, run["workspace_id"]),
            actor_id=actor_id,
            user_id=account_id,
            action="tool.run.state_changed",
            resource_type="tool_run",
            resource_id=cast(UUID, run["run_id"]),
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=request_id,
            trace_id=trace_id,
            traceparent=traceparent,
            authorization=authorization,
            attributes=attributes,
        )
    )
    SqlAlchemyOutboxWriter(session).add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type="tool.run.state_changed",
            workspace_id=cast(UUID, run["workspace_id"]),
            aggregate_id=cast(UUID, run["run_id"]),
            aggregate_version=cast(int, run["version"]),
            occurred_at=occurred_at,
            trace_id=trace_id,
            traceparent=traceparent,
            actor_id=actor_id,
            user_id=account_id,
            request_id=request_id,
            payload=attributes,
        )
    )


def _close_current_attempt(
    session: Session,
    row: RowMapping,
    claim: ClaimedToolAttempt,
    *,
    outcome: AttemptResult,
    completed_at: datetime,
    error_code: str | None,
) -> None:
    session.execute(
        update(tool_calls)
        .where(tool_calls.c.tool_call_id == claim.tool_call_id)
        .values(
            state=outcome,
            completed_at=completed_at,
            updated_at=completed_at,
            error_code=error_code,
        )
    )
    session.execute(
        update(tool_attempts)
        .where(tool_attempts.c.attempt_id == claim.attempt_id)
        .values(state=outcome, completed_at=completed_at, error_code=error_code)
    )
    session.execute(
        update(tool_steps)
        .where(tool_steps.c.step_id == claim.step_id)
        .values(
            state="completed" if outcome == "succeeded" else "failed",
            updated_at=completed_at,
            version=cast(int, row["step_version"]) + 1,
        )
    )
    if outcome == "failed":
        session.execute(
            update(tool_runs)
            .where(tool_runs.c.run_id == claim.run_id)
            .values(
                state="failed",
                updated_at=completed_at,
                completed_at=completed_at,
                version=cast(int, row["run_version"]) + 1,
            )
        )


def _close_cancelled_claim(
    session: Session,
    row: RowMapping,
    claim: ClaimedToolAttempt,
    completed_at: datetime,
) -> None:
    if row["call_state"] not in CALL_TERMINAL:
        session.execute(
            update(tool_calls)
            .where(tool_calls.c.tool_call_id == claim.tool_call_id)
            .values(state="cancelled", completed_at=completed_at, updated_at=completed_at)
        )
    if row["attempt_state"] not in ATTEMPT_TERMINAL:
        session.execute(
            update(tool_attempts)
            .where(tool_attempts.c.attempt_id == claim.attempt_id)
            .values(state="ignored_late_result", completed_at=completed_at)
        )
    session.execute(
        update(tool_steps)
        .where(tool_steps.c.step_id == claim.step_id)
        .values(
            state="cancelled",
            updated_at=completed_at,
            version=cast(int, row["step_version"]) + 1,
        )
    )
    _finish_run_cancellation(session, claim.run_id, completed_at)


def _cancel_leased_claim(session: Session, row: RowMapping, occurred_at: datetime) -> None:
    session.execute(
        update(tool_calls)
        .where(tool_calls.c.tool_call_id == row["tool_call_id"])
        .values(state="cancelled", completed_at=occurred_at, updated_at=occurred_at)
    )
    session.execute(
        update(tool_attempts)
        .where(tool_attempts.c.attempt_id == row["attempt_id"])
        .values(state="cancelled", completed_at=occurred_at)
    )
    session.execute(
        update(tool_steps)
        .where(tool_steps.c.step_id == row["step_id"])
        .values(
            state="cancelled",
            updated_at=occurred_at,
            version=cast(int, row["step_version"]) + 1,
        )
    )


def _active_claim_row(session: Session, run_id: UUID) -> RowMapping | None:
    return (
        session.execute(
            select(
                tool_attempts.c.attempt_id,
                tool_attempts.c.step_id,
                tool_attempts.c.state.label("attempt_state"),
                tool_steps.c.version.label("step_version"),
                tool_calls.c.tool_call_id,
            )
            .join(tool_steps, tool_steps.c.step_id == tool_attempts.c.step_id)
            .join(tool_calls, tool_calls.c.attempt_id == tool_attempts.c.attempt_id)
            .where(
                tool_attempts.c.run_id == run_id,
                tool_attempts.c.state.in_(("leased", "executing")),
            )
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )


def _finish_run_cancellation(session: Session, run_id: UUID, occurred_at: datetime) -> None:
    updated = (
        session.execute(
            update(tool_runs)
            .where(tool_runs.c.run_id == run_id, tool_runs.c.state == "cancellation_requested")
            .values(
                state="cancelled",
                updated_at=occurred_at,
                completed_at=occurred_at,
                version=tool_runs.c.version + 1,
            )
            .returning(tool_runs.c.workspace_id)
        )
        .mappings()
        .one_or_none()
    )
    if updated is not None:
        append_progress_event(
            session,
            workspace_id=cast(UUID, updated["workspace_id"]),
            run_id=run_id,
            event_type="tool.run.cancelled",
            run_state="cancelled",
            step_id=None,
            step_state=None,
            attempt_id=None,
            tool_call_id=None,
            error_code=None,
            occurred_at=occurred_at,
        )


def _derived_outcome_facts(
    row: RowMapping,
    outcome: ToolUsageOutcome,
    error_code: str,
    occurred_at: datetime,
) -> ToolAttemptOutcomeFacts:
    """为租约、取消和总超时生成零正文、零成本但保留耗时的运营事实。"""

    started_at = cast(datetime, row["lease_started_at"])
    duration_ms = max(0, int((occurred_at - started_at).total_seconds() * 1000))
    return ToolAttemptOutcomeFacts(
        outcome=outcome,
        duration_ms=duration_ms,
        cost_microunits=0,
        result_size_bytes=0,
        error_code=error_code,
        safe_result=None,
    )


def _locked_run(session: Session, workspace_id: UUID, run_id: UUID) -> RowMapping:
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
        raise ToolExecutionDeniedError
    return row


def _require_frozen_plan(
    run: RowMapping,
    steps: tuple[FrozenToolPlanStep, ...],
    frozen_at: datetime,
) -> None:
    """复核原子写入边界，避免绕过 Application 后写入超预算或错位证据。"""

    state = cast(str, run["state"])
    if state != "pending":
        _raise_for_closed_state(state)
    if (
        frozen_at >= cast(datetime, run["deadline_at"])
        or not steps
        or len(steps) > cast(int, run["max_steps"])
        or sum(item.budget.max_cost_microunits for item in steps)
        > cast(int, run["max_cost_microunits"])
    ):
        raise ToolRunBudgetExceededError
    for expected_sequence, step in enumerate(steps, start=1):
        budget = step.budget
        policy = step.policy
        if (
            step.sequence_no != expected_sequence
            or budget.timeout_seconds < 1
            or budget.timeout_seconds > cast(int, run["max_execution_seconds"])
            or not 1 <= budget.max_attempts <= cast(int, run["max_attempts_per_step"])
            or not 1 <= budget.max_result_bytes <= 262_144
            or budget.max_cost_microunits < 0
            or policy.workspace_id != run["workspace_id"]
            or policy.run_id != run["run_id"]
            or policy.step_id != step.step_id
            or policy.tool_id != step.tool_id
            or policy.tool_version != step.tool_version
            or policy.canonical_arguments_hash != step.canonical_arguments_hash
            or policy.policy_version < 1
        ):
            raise ToolRunConflictError


def _advance_run(
    session: Session,
    row: RowMapping,
    *,
    workspace_id: UUID,
    run_id: UUID,
    target_state: ToolRunState,
    occurred_at: datetime,
) -> RowMapping:
    """使用旧版本条件推进已锁定 Run，并返回后续写入使用的新版本事实。"""

    updated = (
        session.execute(
            update(tool_runs)
            .where(
                tool_runs.c.run_id == run_id,
                tool_runs.c.workspace_id == workspace_id,
                tool_runs.c.version == row["version"],
            )
            .values(
                state=target_state,
                updated_at=occurred_at,
                version=cast(int, row["version"]) + 1,
            )
            .returning(tool_runs)
        )
        .mappings()
        .one_or_none()
    )
    if updated is None:
        raise ToolRunConflictError
    return updated


def _freeze_step(
    session: Session,
    run: RowMapping,
    frozen: FrozenToolPlanStep,
    *,
    workspace_id: UUID,
    run_id: UUID,
    frozen_at: datetime,
) -> ToolStep:
    """保存一个 Step 及其当前允许决策，状态转换顺序由数据库重复验证。"""

    budget = frozen.budget
    try:
        # 1. 先保存不可变身份和预算，再进入 policy_checking，禁止直接插入 ready 状态。
        planned = (
            session.execute(
                insert(tool_steps)
                .values(
                    step_id=frozen.step_id,
                    run_id=run_id,
                    workspace_id=workspace_id,
                    sequence_no=frozen.sequence_no,
                    tool_id=frozen.tool_id,
                    tool_version=frozen.tool_version,
                    canonical_arguments_hash=frozen.canonical_arguments_hash,
                    timeout_seconds=budget.timeout_seconds,
                    max_attempts=budget.max_attempts,
                    max_result_bytes=budget.max_result_bytes,
                    max_cost_microunits=budget.max_cost_microunits,
                    state="planned",
                    recovery_generation=0,
                    current_attempt_no=None,
                    available_at=frozen_at,
                    next_attempt_trigger="automatic",
                    created_at=frozen_at,
                    updated_at=frozen_at,
                    version=1,
                )
                .returning(tool_steps)
            )
            .mappings()
            .one()
        )
        checking = (
            session.execute(
                update(tool_steps)
                .where(tool_steps.c.step_id == frozen.step_id, tool_steps.c.version == 1)
                .values(state="policy_checking", updated_at=frozen_at, version=2)
                .returning(tool_steps)
            )
            .mappings()
            .one()
        )
        # 2. 策略最小证据先于 ready 写入，Trigger 会再次核对身份、权限和评估时点。
        _insert_policy_decision(session, frozen.policy)
        ready = (
            session.execute(
                update(tool_steps)
                .where(tool_steps.c.step_id == frozen.step_id, tool_steps.c.version == 2)
                .values(state="ready", updated_at=frozen_at, version=3)
                .returning(tool_steps)
            )
            .mappings()
            .one()
        )
    except IntegrityError as error:
        raise ToolRunConflictError from error
    if planned["run_id"] != run["run_id"] or checking["state"] != "policy_checking":
        raise ToolRunConflictError
    return _step(ready)


def _insert_policy_decision(
    session: Session,
    policy: ToolPolicyDecisionRecord,
) -> None:
    """只保存授权最小证据，资源 ID、字段名和完整策略正文不进入工具事实。"""

    session.execute(
        insert(tool_policy_decisions).values(
            decision_id=policy.decision_id,
            workspace_id=policy.workspace_id,
            run_id=policy.run_id,
            step_id=policy.step_id,
            tool_id=policy.tool_id,
            tool_version=policy.tool_version,
            canonical_arguments_hash=policy.canonical_arguments_hash,
            permission_code=policy.permission_code,
            policy_version=policy.policy_version,
            resource_scope_hash=policy.resource_scope_hash,
            field_mask_hash=policy.field_mask_hash,
            evaluated_at=policy.evaluated_at,
        )
    )


def _require_run_has_steps(session: Session, run_id: UUID) -> None:
    count = session.scalar(
        select(func.count()).select_from(tool_steps).where(tool_steps.c.run_id == run_id)
    )
    if int(count or 0) < 1:
        raise ToolRunConflictError


def _require_all_steps_completed(session: Session, run_id: UUID) -> None:
    total = int(
        session.scalar(
            select(func.count()).select_from(tool_steps).where(tool_steps.c.run_id == run_id)
        )
        or 0
    )
    incomplete = int(
        session.scalar(
            select(func.count())
            .select_from(tool_steps)
            .where(
                tool_steps.c.run_id == run_id,
                tool_steps.c.state != "completed",
            )
        )
        or 0
    )
    if total < 1 or incomplete > 0:
        raise ToolRunConflictError


def _require_state_transition(
    current: object,
    target: object,
    transitions: dict[str, frozenset[str]],
    terminal: frozenset[str],
) -> None:
    current_value = cast(str, current)
    target_value = cast(str, target)
    if current_value in terminal:
        raise ToolRunTerminalError
    if target_value not in transitions.get(current_value, frozenset()):
        raise ToolRunConflictError


def _raise_for_closed_state(state: str) -> None:
    if state in RUN_TERMINAL:
        raise ToolRunTerminalError
    raise ToolRunConflictError


def _run(row: RowMapping) -> ToolRun:
    return ToolRun(
        run_id=cast(UUID, row["run_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        requested_by_actor_id=cast(UUID, row["requested_by_actor_id"]),
        requested_by_account_id=cast(UUID, row["requested_by_account_id"]),
        service_id=cast(UUID, row["service_id"]),
        agent_release_id=cast(UUID, row["agent_release_id"]),
        state=cast(ToolRunState, row["state"]),
        budget=ToolRunBudget(
            max_steps=cast(int, row["max_steps"]),
            max_attempts_per_step=cast(int, row["max_attempts_per_step"]),
            max_execution_seconds=cast(int, row["max_execution_seconds"]),
            max_cost_microunits=cast(int, row["max_cost_microunits"]),
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


def _step(row: RowMapping) -> ToolStep:
    return ToolStep(
        step_id=cast(UUID, row["step_id"]),
        run_id=cast(UUID, row["run_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        sequence_no=cast(int, row["sequence_no"]),
        tool_id=cast(UUID, row["tool_id"]),
        tool_version=cast(int, row["tool_version"]),
        canonical_arguments_hash=cast(str, row["canonical_arguments_hash"]),
        budget=ToolStepBudget(
            timeout_seconds=cast(int, row["timeout_seconds"]),
            max_attempts=cast(int, row["max_attempts"]),
            max_result_bytes=cast(int, row["max_result_bytes"]),
            max_cost_microunits=cast(int, row["max_cost_microunits"]),
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
