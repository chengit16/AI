"""用 PostgreSQL 行锁、租约和追加式 Attempt 实现工具任务唯一写入权。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import Row, and_, exists, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.tool_execution.domain.errors import (
    ToolExecutionDeniedError,
    ToolRunBudgetExceededError,
    ToolRunConflictError,
    ToolRunTerminalError,
)
from ai_platform_api.modules.tool_execution.domain.planning import (
    FrozenToolPlanStep,
    ToolExecutionPlan,
    ToolPolicyDecisionRecord,
)
from ai_platform_api.modules.tool_execution.domain.tasks import (
    AttemptResult,
    ClaimedToolAttempt,
    ToolCallState,
    ToolRun,
    ToolRunBudget,
    ToolRunState,
    ToolStep,
    ToolStepBudget,
    ToolStepState,
)
from ai_platform_api.persistence.tables import (
    agent_tool_definitions,
    tool_attempts,
    tool_calls,
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
            "timed_out",
        }
    ),
    "waiting_confirmation": frozenset({"running"}),
    "waiting_approval": frozenset({"running"}),
    "cancellation_requested": frozenset({"cancelled"}),
}
STEP_TRANSITIONS: dict[str, frozenset[str]] = {
    "planned": frozenset({"policy_checking", "cancelled"}),
    "policy_checking": frozenset({"ready", "waiting_confirmation", "waiting_approval"}),
    "waiting_confirmation": frozenset({"ready"}),
    "waiting_approval": frozenset({"ready"}),
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
                return _run(inserted)
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

                # 3. 全部 Step 可执行后再发布 Run；任何异常由事务回滚到原始 pending 状态。
                running = _advance_run(
                    session,
                    planning,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    target_state="running",
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
                            current_attempt_no=None,
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
            return _step(updated)

    def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimedToolAttempt | None:
        with self._session_factory() as session, session.begin():
            # 1. Worker 重启没有异常回调，领取前先稳定关闭过期租约和父级事实。
            _expire_stale_attempts(session, now)
            # 2. 只锁定顺序已满足的 ready Step，多 Worker 使用 SKIP LOCKED 竞争。
            row = _select_claimable_step(session, now)
            if row is None:
                return None
            # 3. Step、Attempt 与 ToolCall 在同一事务建立，队列重复投递不会留下半事实。
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
            return True

    def finish_attempt(
        self,
        claim: ClaimedToolAttempt,
        *,
        succeeded: bool,
        completed_at: datetime,
        error_code: str | None,
    ) -> AttemptResult:
        with self._session_factory() as session, session.begin():
            _expire_stale_attempts(session, completed_at)
            row = _locked_claim(session, claim)
            if row is None:
                return "ignored_late_result"
            # 1. 取消优先于 Adapter 返回；旧 Worker 只关闭历史 Attempt，不能提交成功。
            if row["run_state"] == "cancellation_requested":
                _close_cancelled_claim(session, row, claim, completed_at)
                return "ignored_late_result"
            if not _claim_is_current(row, claim, completed_at):
                return "ignored_late_result"
            if row["attempt_state"] != "executing" or row["call_state"] != "executing":
                return "ignored_late_result"
            # 2. 当前有效租约才可原子关闭 Call、Attempt、Step；失败同步关闭 Run。
            outcome: AttemptResult = "succeeded" if succeeded else "failed"
            _close_current_attempt(
                session,
                row,
                claim,
                outcome=outcome,
                completed_at=completed_at,
                error_code=error_code,
            )
            return outcome

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
            if row["state"] not in {"pending", "running"}:
                raise ToolRunConflictError

            # 1. 先提交取消事实，后续领取条件立即失效；未运行步骤同步关闭。
            session.execute(
                update(tool_runs)
                .where(tool_runs.c.run_id == run_id)
                .values(
                    state="cancellation_requested",
                    cancel_requested_at=requested_at,
                    updated_at=requested_at,
                    version=cast(int, row["version"]) + 1,
                )
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
                        )
                    ),
                )
                .values(
                    state="cancelled",
                    updated_at=requested_at,
                    version=tool_steps.c.version + 1,
                )
            )
            active = _active_claim_row(session, run_id)
            # 2. 尚未开始的租约可立即取消；执行中的调用等待回调并按迟到结果收敛。
            if active is not None and active["attempt_state"] == "leased":
                _cancel_leased_claim(session, active, requested_at)
                active = None
            if active is None:
                _finish_run_cancellation(session, run_id, requested_at)
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
            tool_steps.c.state == "ready",
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
    attempt_no = cast(int | None, row.current_attempt_no) or 0
    attempt_no += 1
    attempt_id = uuid4()
    tool_call_id = uuid4()
    # 2. 三类事实同事务提交；延迟约束在提交时确认 current_attempt_no 对应真实 Attempt。
    session.execute(
        update(tool_steps)
        .where(tool_steps.c.step_id == row.step_id, tool_steps.c.state == "ready")
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
            attempt_no=attempt_no,
            lease_generation=attempt_no,
            state="leased",
            worker_id=worker_id,
            lease_started_at=now,
            lease_expires_at=lease_expires_at,
            started_at=now,
            completed_at=None,
            error_code=None,
        )
    )
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
        attempt_no=attempt_no,
        lease_generation=attempt_no,
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
                tool_attempts.c.attempt_no,
                tool_attempts.c.lease_generation,
                tool_attempts.c.worker_id,
                tool_attempts.c.lease_expires_at,
                tool_attempts.c.state.label("attempt_state"),
                tool_steps.c.current_attempt_no,
                tool_steps.c.state.label("step_state"),
                tool_steps.c.version.label("step_version"),
                tool_runs.c.state.label("run_state"),
                tool_runs.c.version.label("run_version"),
                tool_calls.c.tool_call_id,
                tool_calls.c.state.label("call_state"),
            )
            .join(tool_steps, tool_steps.c.step_id == tool_attempts.c.step_id)
            .join(tool_runs, tool_runs.c.run_id == tool_attempts.c.run_id)
            .join(tool_calls, tool_calls.c.attempt_id == tool_attempts.c.attempt_id)
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
        row["attempt_no"] == claim.attempt_no
        and row["lease_generation"] == claim.lease_generation
        and row["worker_id"] == claim.worker_id
        and row["current_attempt_no"] == claim.attempt_no
        and row["lease_expires_at"] > occurred_at
        and row["run_state"] not in RUN_TERMINAL
        and row["step_state"] not in STEP_TERMINAL
    )


def _expire_stale_attempts(session: Session, now: datetime) -> None:
    """有界关闭过期租约；本节点不重试，P4-09 再引入安全恢复代际。"""

    # 1. 使用 SKIP LOCKED 有界领取过期 Attempt，多个 Worker 重启扫描互不阻塞。
    rows = session.execute(
        select(tool_attempts.c.attempt_id)
        .where(
            tool_attempts.c.state.in_(("leased", "executing")),
            tool_attempts.c.lease_expires_at <= now,
        )
        .order_by(tool_attempts.c.lease_expires_at)
        .limit(100)
        .with_for_update(skip_locked=True)
    ).all()
    for (attempt_id,) in rows:
        row = (
            session.execute(
                select(
                    tool_attempts.c.attempt_id,
                    tool_attempts.c.run_id,
                    tool_attempts.c.step_id,
                    tool_attempts.c.state.label("attempt_state"),
                    tool_steps.c.version.label("step_version"),
                    tool_calls.c.tool_call_id,
                    tool_calls.c.state.label("call_state"),
                    tool_runs.c.version.label("run_version"),
                    tool_runs.c.state.label("run_state"),
                )
                .join(tool_steps, tool_steps.c.step_id == tool_attempts.c.step_id)
                .join(tool_calls, tool_calls.c.attempt_id == tool_attempts.c.attempt_id)
                .join(tool_runs, tool_runs.c.run_id == tool_attempts.c.run_id)
                .where(tool_attempts.c.attempt_id == attempt_id)
                .with_for_update()
            )
            .mappings()
            .one()
        )
        if row["run_state"] in RUN_TERMINAL:
            continue
        # 2. 取消事实优先于租约超时；执行中结果只进入迟到终态，未执行租约直接取消。
        if row["run_state"] == "cancellation_requested":
            session.execute(
                update(tool_calls)
                .where(tool_calls.c.tool_call_id == row["tool_call_id"])
                .values(state="cancelled", completed_at=now, updated_at=now)
            )
            session.execute(
                update(tool_attempts)
                .where(tool_attempts.c.attempt_id == attempt_id)
                .values(
                    state=(
                        "cancelled" if row["attempt_state"] == "leased" else "ignored_late_result"
                    ),
                    completed_at=now,
                )
            )
            session.execute(
                update(tool_steps)
                .where(tool_steps.c.step_id == row["step_id"])
                .values(
                    state="cancelled",
                    updated_at=now,
                    version=cast(int, row["step_version"]) + 1,
                )
            )
            _finish_run_cancellation(session, cast(UUID, row["run_id"]), now)
            continue
        # 3. 普通过期租约按 Attempt、Call、Step、Run 顺序关闭，旧 Worker 不再拥有写资格。
        session.execute(
            update(tool_attempts)
            .where(tool_attempts.c.attempt_id == attempt_id)
            .values(state="timed_out", completed_at=now, error_code="TOOL_ADAPTER_UNAVAILABLE")
        )
        if row["call_state"] not in CALL_TERMINAL:
            session.execute(
                update(tool_calls)
                .where(tool_calls.c.tool_call_id == row["tool_call_id"])
                .values(
                    state="timed_out",
                    completed_at=now,
                    updated_at=now,
                    error_code="TOOL_ADAPTER_UNAVAILABLE",
                )
            )
        session.execute(
            update(tool_steps)
            .where(tool_steps.c.step_id == row["step_id"])
            .values(
                state="timed_out",
                updated_at=now,
                version=cast(int, row["step_version"]) + 1,
            )
        )
        session.execute(
            update(tool_runs)
            .where(tool_runs.c.run_id == row["run_id"])
            .values(
                state="timed_out",
                updated_at=now,
                completed_at=now,
                version=cast(int, row["run_version"]) + 1,
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
    session.execute(
        update(tool_runs)
        .where(tool_runs.c.run_id == run_id, tool_runs.c.state == "cancellation_requested")
        .values(
            state="cancelled",
            updated_at=occurred_at,
            completed_at=occurred_at,
            version=tool_runs.c.version + 1,
        )
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
                    current_attempt_no=None,
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
        current_attempt_no=cast(int | None, row["current_attempt_no"]),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
        version=cast(int, row["version"]),
    )
