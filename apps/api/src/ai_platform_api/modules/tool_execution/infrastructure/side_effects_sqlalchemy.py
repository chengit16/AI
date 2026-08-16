"""用 PostgreSQL 实现执行前幂等预留、合成副作用和原子任务收口。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditAuthorization, AuditRecord, IntegrationEvent
from ai_platform_backend.integration.sqlalchemy import SqlAlchemyAuditWriter, SqlAlchemyOutboxWriter
from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.tool_execution.domain.errors import (
    ToolConfirmationStaleError,
    ToolIdempotencyConflictError,
    ToolRunConflictError,
)
from ai_platform_api.modules.tool_execution.domain.side_effects import (
    SyntheticSideEffectCommand,
    SyntheticSideEffectReceipt,
    ToolIdempotencyRecord,
    ToolIdempotencyReservation,
    ToolIdempotencyState,
    ToolSideEffectExecutionResult,
    ToolSideEffectIdentity,
    side_effect_identity,
    side_effect_key_hash,
    synthetic_result_hash,
)
from ai_platform_api.modules.tool_execution.domain.tasks import ClaimedToolAttempt
from ai_platform_api.persistence.tables import (
    synthetic_tool_side_effects,
    tool_attempts,
    tool_calls,
    tool_confirmation_invalidations,
    tool_confirmations,
    tool_idempotency_records,
    tool_policy_decisions,
    tool_runs,
    tool_steps,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyToolSideEffectStore:
    """让幂等记录成为副作用前置门禁，并与任务、审计、Outbox 一致提交。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def reserve(
        self,
        claim: ClaimedToolAttempt,
        *,
        reserved_at: datetime,
    ) -> ToolIdempotencyReservation:
        """先返回匹配的既有记录，否则在确认和租约门禁内创建唯一预留。"""

        try:
            with self._session_factory() as session, session.begin():
                # 1. 锁定当前 ToolCall 快照后先查稳定键，让并发重复投递只有一个预留创建者。
                row = _claim_row(session, claim, for_update=True)
                existing = _record_by_key(session, claim)
                if existing is not None:
                    _require_existing_record(existing, claim)
                    return ToolIdempotencyReservation(_record(existing), created=False)

                # 新副作用只有在 ToolCall 已确认、租约有效且确认仍为当前事实时才可预留。
                # 2. 在同一事务内写入预留、推进 executing 并追加最小横切事实；
                # 事务提交后才允许调用 Adapter。
                _require_current_claim(row, claim, reserved_at, expected_call_state="confirmed")
                confirmation = _active_confirmation(session, claim, reserved_at)
                identity = side_effect_identity(claim, cast(str, confirmation["confirmation_hash"]))
                record = ToolIdempotencyRecord(
                    idempotency_record_id=uuid4(),
                    workspace_id=claim.workspace_id,
                    run_id=claim.run_id,
                    step_id=claim.step_id,
                    tool_call_id=claim.tool_call_id,
                    tool_id=claim.tool_id,
                    tool_version=claim.tool_version,
                    identity=identity,
                    state="reserved",
                    result_hash=None,
                    reserved_at=reserved_at,
                    completed_at=None,
                    error_code=None,
                )
                _insert_record(session, record, cast(UUID, confirmation["confirmation_id"]))
                session.execute(
                    update(tool_calls)
                    .where(tool_calls.c.tool_call_id == claim.tool_call_id)
                    .values(state="executing", updated_at=reserved_at)
                )
                _record_lifecycle(session, row, record, "tool.call.started", reserved_at)
                return ToolIdempotencyReservation(record, created=True)
        except (IntegrityError, DBAPIError) as error:
            raise ToolRunConflictError from error

    def complete_success(
        self,
        claim: ClaimedToolAttempt,
        receipt: SyntheticSideEffectReceipt,
        *,
        completed_at: datetime,
        reconciled: bool,
    ) -> ToolSideEffectExecutionResult:
        """把成功摘要、任务状态、审计和 Outbox 作为一个事务收口。"""

        try:
            with self._session_factory() as session, session.begin():
                # 1. 锁定调用与幂等事实，分别验证实时执行和结果未知对账两条合法路径。
                row = _claim_row(session, claim, for_update=True)
                stored = _locked_record(session, receipt.idempotency_record_id)
                record = _record(stored)
                if record.state == "succeeded":
                    return _execution_result(record, replayed=True)
                allowed_states = {"reserved", "outcome_unknown"} if reconciled else {"reserved"}
                if record.state not in allowed_states:
                    raise ToolRunConflictError
                if reconciled and record.state == "outcome_unknown":
                    _require_reconcilable_claim(row, claim)
                else:
                    _require_current_claim(
                        row,
                        claim,
                        completed_at,
                        expected_call_state="executing",
                    )
                _require_receipt(record, claim, receipt)

                # 2. 幂等终态、任务父级、审计和 Outbox 共用事务，任一失败都整体回滚。
                updated = _update_success_record(session, record, receipt, completed_at)
                _close_successful_claim(
                    session,
                    row,
                    claim,
                    completed_at,
                    reconciled=reconciled,
                )
                _record_lifecycle(
                    session,
                    row,
                    updated,
                    "tool.call.completed",
                    completed_at,
                    reconciled=reconciled,
                )
                return _execution_result(updated, replayed=reconciled)
        except (IntegrityError, DBAPIError) as error:
            raise ToolRunConflictError from error

    def complete_failure(
        self,
        claim: ClaimedToolAttempt,
        record_id: UUID,
        *,
        error_code: str,
        completed_at: datetime,
    ) -> ToolIdempotencyRecord:
        """稳定失败不会产生合成副作用，并与父级失败终态原子提交。"""

        try:
            with self._session_factory() as session, session.begin():
                row = _claim_row(session, claim, for_update=True)
                record = _record(_locked_record(session, record_id))
                if record.state == "failed":
                    return record
                if record.state != "reserved":
                    raise ToolRunConflictError
                _require_current_claim(row, claim, completed_at, expected_call_state="executing")
                updated = _update_terminal_record(
                    session,
                    record,
                    state="failed",
                    error_code=error_code,
                    completed_at=completed_at,
                )
                _close_failed_claim(session, row, claim, error_code, completed_at)
                _record_lifecycle(
                    session,
                    row,
                    updated,
                    "tool.call.failed",
                    completed_at,
                )
                return updated
        except (IntegrityError, DBAPIError) as error:
            raise ToolRunConflictError from error

    def mark_outcome_unknown(
        self,
        claim: ClaimedToolAttempt,
        record_id: UUID,
        *,
        occurred_at: datetime,
    ) -> ToolIdempotencyRecord:
        """关闭原 Attempt 并进入人工恢复；ToolCall 保留 executing 供只读对账收敛。"""

        try:
            with self._session_factory() as session, session.begin():
                row = _claim_row(session, claim, for_update=True)
                record = _record(_locked_record(session, record_id))
                if record.state == "outcome_unknown":
                    return record
                if record.state != "reserved":
                    raise ToolRunConflictError
                _require_current_claim(row, claim, occurred_at, expected_call_state="executing")
                updated = _update_terminal_record(
                    session,
                    record,
                    state="outcome_unknown",
                    error_code="TOOL_OUTCOME_UNKNOWN",
                    completed_at=occurred_at,
                )
                _close_unknown_claim(session, row, claim, occurred_at)
                _record_lifecycle(
                    session,
                    row,
                    updated,
                    "tool.call.outcome_unknown",
                    occurred_at,
                )
                return updated
        except (IntegrityError, DBAPIError) as error:
            raise ToolRunConflictError from error


class SqlAlchemySyntheticSideEffectAdapter:
    """只写不可变合成事实，以数据库唯一键提供最后一道零重复保证。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def execute(
        self,
        command: SyntheticSideEffectCommand,
        *,
        committed_at: datetime,
    ) -> SyntheticSideEffectReceipt:
        """提交一次摘要型合成副作用；同键同请求只返回既有事实。"""

        result_hash = synthetic_result_hash(command)
        values = {
            "side_effect_id": command.side_effect_id,
            "idempotency_record_id": command.idempotency_record_id,
            "workspace_id": command.workspace_id,
            "idempotency_key_hash": command.idempotency_key_hash,
            "request_hash": command.request_hash,
            "canonical_arguments_hash": command.canonical_arguments_hash,
            "result_hash": result_hash,
            "committed_at": committed_at,
        }
        try:
            with self._session_factory() as session, session.begin():
                inserted = (
                    session.execute(
                        postgresql_insert(synthetic_tool_side_effects)
                        .values(**values)
                        .on_conflict_do_nothing(
                            index_elements=[
                                synthetic_tool_side_effects.c.workspace_id,
                                synthetic_tool_side_effects.c.idempotency_key_hash,
                            ]
                        )
                        .returning(synthetic_tool_side_effects)
                    )
                    .mappings()
                    .one_or_none()
                )
                row = inserted or _effect_by_key(session, command)
                _require_effect_matches_command(row, command)
                return _receipt(row)
        except IntegrityError as error:
            raise ToolRunConflictError from error

    def reconcile(
        self,
        record: ToolIdempotencyRecord,
    ) -> SyntheticSideEffectReceipt | None:
        """只读取已提交合成事实；不存在时保持未知，不推断为可安全重试。"""

        with self._session_factory() as session:
            row = (
                session.execute(
                    select(synthetic_tool_side_effects).where(
                        synthetic_tool_side_effects.c.workspace_id == record.workspace_id,
                        synthetic_tool_side_effects.c.idempotency_key_hash
                        == record.identity.idempotency_key_hash,
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        if (
            row["idempotency_record_id"] != record.idempotency_record_id
            or row["request_hash"] != record.identity.request_hash
        ):
            raise ToolIdempotencyConflictError
        return _receipt(row)


def _claim_row(
    session: Session,
    claim: ClaimedToolAttempt,
    *,
    for_update: bool,
) -> RowMapping:
    """读取租约、调用、Run Trace 和当前 PDP，供所有事务执行一致复核。"""

    # 1. 始终选取 Step 最新 PDP，避免读取确认申请时已经被后续授权事实取代的旧决定。
    latest_policy = (
        select(tool_policy_decisions.c.decision_id)
        .where(tool_policy_decisions.c.step_id == claim.step_id)
        .order_by(tool_policy_decisions.c.evaluated_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    # 2. 将调用、租约、父级状态和追踪元数据拼成一个锁定快照，后续校验不跨事务重新读取。
    statement = (
        select(
            tool_calls,
            tool_calls.c.state.label("call_state"),
            tool_attempts.c.state.label("attempt_state"),
            tool_attempts.c.recovery_generation,
            tool_attempts.c.attempt_no,
            tool_attempts.c.worker_id,
            tool_attempts.c.lease_generation,
            tool_attempts.c.lease_expires_at,
            tool_steps.c.current_attempt_no,
            tool_steps.c.recovery_generation.label("step_recovery_generation"),
            tool_steps.c.state.label("step_state"),
            tool_steps.c.version.label("step_version"),
            tool_runs.c.state.label("run_state"),
            tool_runs.c.recovery_generation.label("run_recovery_generation"),
            tool_runs.c.version.label("run_version"),
            tool_runs.c.cancel_requested_at,
            tool_runs.c.requested_by_actor_id,
            tool_runs.c.requested_by_account_id,
            tool_runs.c.trace_id,
            tool_runs.c.traceparent,
            tool_policy_decisions.c.decision_id,
            tool_policy_decisions.c.permission_code,
            tool_policy_decisions.c.policy_version,
        )
        .join(tool_attempts, tool_attempts.c.attempt_id == tool_calls.c.attempt_id)
        .join(tool_steps, tool_steps.c.step_id == tool_calls.c.step_id)
        .join(tool_runs, tool_runs.c.run_id == tool_calls.c.run_id)
        .join(tool_policy_decisions, tool_policy_decisions.c.decision_id == latest_policy)
        .where(tool_calls.c.tool_call_id == claim.tool_call_id)
    )
    if for_update:
        statement = statement.with_for_update()
    row = session.execute(statement).mappings().one_or_none()
    if row is None:
        raise ToolRunConflictError
    return row


def _require_current_claim(
    row: RowMapping,
    claim: ClaimedToolAttempt,
    occurred_at: datetime,
    *,
    expected_call_state: str,
) -> None:
    if (
        row["attempt_id"] != claim.attempt_id
        or row["run_id"] != claim.run_id
        or row["step_id"] != claim.step_id
        or row["workspace_id"] != claim.workspace_id
        or row["tool_id"] != claim.tool_id
        or row["tool_version"] != claim.tool_version
        or row["canonical_arguments_hash"] != claim.canonical_arguments_hash
        or row["recovery_generation"] != claim.recovery_generation
        or row["step_recovery_generation"] != claim.recovery_generation
        or row["worker_id"] != claim.worker_id
        or row["lease_generation"] != claim.lease_generation
        or row["current_attempt_no"] != claim.attempt_no
        or row["attempt_state"] != "executing"
        or row["step_state"] != "running"
        or row["run_state"] != "running"
        or row["cancel_requested_at"] is not None
        or row["call_state"] != expected_call_state
        or occurred_at >= cast(datetime, row["lease_expires_at"])
        or claim.lease_expires_at != row["lease_expires_at"]
    ):
        raise ToolRunConflictError


def _require_reconcilable_claim(row: RowMapping, claim: ClaimedToolAttempt) -> None:
    """对账只接受原结果未知 Attempt，故意不要求已失效租约仍然有效。"""

    if (
        row["attempt_id"] != claim.attempt_id
        or row["run_id"] != claim.run_id
        or row["step_id"] != claim.step_id
        or row["workspace_id"] != claim.workspace_id
        or row["tool_id"] != claim.tool_id
        or row["tool_version"] != claim.tool_version
        or row["canonical_arguments_hash"] != claim.canonical_arguments_hash
        or row["recovery_generation"] != claim.recovery_generation
        or row["attempt_no"] != claim.attempt_no
        or row["lease_generation"] != claim.lease_generation
        or row["worker_id"] != claim.worker_id
        or row["attempt_state"] != "failed"
        or row["step_state"] != "manual_recovery"
        or row["run_state"] != "manual_recovery"
        or row["call_state"] != "executing"
    ):
        raise ToolRunConflictError


def _active_confirmation(
    session: Session,
    claim: ClaimedToolAttempt,
    occurred_at: datetime,
) -> RowMapping:
    """要求批准、未失效且最新 PDP 与批准后恢复证据完全一致。"""

    # 1. 先锁定与当前调用完整绑定的批准事实，过期、非批准或参数变化都按陈旧确认拒绝。
    confirmation = (
        session.execute(
            select(tool_confirmations)
            .where(
                tool_confirmations.c.workspace_id == claim.workspace_id,
                tool_confirmations.c.run_id == claim.run_id,
                tool_confirmations.c.step_id == claim.step_id,
                tool_confirmations.c.tool_id == claim.tool_id,
                tool_confirmations.c.tool_version == claim.tool_version,
                tool_confirmations.c.canonical_arguments_hash == claim.canonical_arguments_hash,
                tool_confirmations.c.state == "approved",
                tool_confirmations.c.expires_at > occurred_at,
            )
            .with_for_update(read=True)
        )
        .mappings()
        .one_or_none()
    )
    if confirmation is None:
        raise ToolConfirmationStaleError
    # 2. 再检查显式失效记录和批准后重新授权的最新 PDP，数据库 Trigger 会执行同一组核心约束。
    invalidated = session.scalar(
        select(func.count())
        .select_from(tool_confirmation_invalidations)
        .where(tool_confirmation_invalidations.c.confirmation_id == confirmation["confirmation_id"])
    )
    latest_policy = (
        session.execute(
            select(tool_policy_decisions)
            .where(tool_policy_decisions.c.step_id == claim.step_id)
            .order_by(tool_policy_decisions.c.evaluated_at.desc())
            .limit(1)
        )
        .mappings()
        .one()
    )
    if invalidated or not _policy_matches_confirmation(latest_policy, confirmation):
        raise ToolConfirmationStaleError
    return confirmation


def _policy_matches_confirmation(policy: RowMapping, confirmation: RowMapping) -> bool:
    return bool(
        policy["decision_id"] != confirmation["policy_decision_id"]
        and policy["permission_code"] == confirmation["permission_code"]
        and policy["policy_version"] == confirmation["policy_version"]
        and policy["resource_scope_hash"] == confirmation["resource_scope_hash"]
        and policy["field_mask_hash"] == confirmation["field_mask_hash"]
        and policy["evaluated_at"] >= confirmation["resolved_at"]
    )


def _record_by_key(session: Session, claim: ClaimedToolAttempt) -> RowMapping | None:
    return (
        session.execute(
            select(tool_idempotency_records)
            .where(
                tool_idempotency_records.c.workspace_id == claim.workspace_id,
                tool_idempotency_records.c.idempotency_key_hash == side_effect_key_hash(claim),
            )
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )


def _locked_record(session: Session, record_id: UUID) -> RowMapping:
    row = (
        session.execute(
            select(tool_idempotency_records)
            .where(tool_idempotency_records.c.idempotency_record_id == record_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ToolRunConflictError
    return row


def _require_existing_record(row: RowMapping, claim: ClaimedToolAttempt) -> None:
    identity = side_effect_identity(claim, cast(str, row["confirmation_hash"]))
    if (
        row["workspace_id"] != claim.workspace_id
        or row["run_id"] != claim.run_id
        or row["step_id"] != claim.step_id
        or row["tool_id"] != claim.tool_id
        or row["tool_version"] != claim.tool_version
        or row["idempotency_key_hash"] != identity.idempotency_key_hash
        or row["request_hash"] != identity.request_hash
    ):
        raise ToolIdempotencyConflictError


def _insert_record(
    session: Session,
    record: ToolIdempotencyRecord,
    confirmation_id: UUID,
) -> None:
    # 1. 正常执行关闭 Call 与 Attempt；对账保留原失败 Attempt 作为不可变历史。
    session.execute(
        insert(tool_idempotency_records).values(
            idempotency_record_id=record.idempotency_record_id,
            workspace_id=record.workspace_id,
            run_id=record.run_id,
            step_id=record.step_id,
            tool_call_id=record.tool_call_id,
            tool_id=record.tool_id,
            tool_version=record.tool_version,
            confirmation_id=confirmation_id,
            confirmation_hash=record.identity.confirmation_hash,
            idempotency_key_hash=record.identity.idempotency_key_hash,
            request_hash=record.identity.request_hash,
            state=record.state,
            result_hash=None,
            reserved_at=record.reserved_at,
            completed_at=None,
            error_code=None,
        )
    )


def _update_success_record(
    session: Session,
    record: ToolIdempotencyRecord,
    receipt: SyntheticSideEffectReceipt,
    completed_at: datetime,
) -> ToolIdempotencyRecord:
    row = (
        session.execute(
            update(tool_idempotency_records)
            .where(tool_idempotency_records.c.idempotency_record_id == record.idempotency_record_id)
            .values(
                state="succeeded",
                result_hash=receipt.result_hash,
                completed_at=completed_at,
                error_code=None,
            )
            .returning(tool_idempotency_records)
        )
        .mappings()
        .one()
    )
    return _record(row)


def _update_terminal_record(
    session: Session,
    record: ToolIdempotencyRecord,
    *,
    state: ToolIdempotencyState,
    error_code: str,
    completed_at: datetime,
) -> ToolIdempotencyRecord:
    row = (
        session.execute(
            update(tool_idempotency_records)
            .where(tool_idempotency_records.c.idempotency_record_id == record.idempotency_record_id)
            .values(
                state=state,
                result_hash=None,
                completed_at=completed_at,
                error_code=error_code,
            )
            .returning(tool_idempotency_records)
        )
        .mappings()
        .one()
    )
    return _record(row)


def _require_receipt(
    record: ToolIdempotencyRecord,
    claim: ClaimedToolAttempt,
    receipt: SyntheticSideEffectReceipt,
) -> None:
    if (
        receipt.idempotency_record_id != record.idempotency_record_id
        or receipt.workspace_id != record.workspace_id
        or receipt.idempotency_key_hash != record.identity.idempotency_key_hash
        or receipt.request_hash != record.identity.request_hash
        or receipt.canonical_arguments_hash != claim.canonical_arguments_hash
    ):
        raise ToolIdempotencyConflictError


def _close_successful_claim(
    session: Session,
    row: RowMapping,
    claim: ClaimedToolAttempt,
    completed_at: datetime,
    *,
    reconciled: bool,
) -> None:
    # 1. 先收敛调用事实；对账恢复不得把原失败 Attempt 改写为成功历史。
    session.execute(
        update(tool_calls)
        .where(tool_calls.c.tool_call_id == claim.tool_call_id)
        .values(state="succeeded", completed_at=completed_at, updated_at=completed_at)
    )
    if not reconciled:
        session.execute(
            update(tool_attempts)
            .where(tool_attempts.c.attempt_id == claim.attempt_id)
            .values(state="succeeded", completed_at=completed_at, error_code=None)
        )
    # 2. Step 完成后仅在全部顺序步骤已完成时关闭 Run，并清理人工恢复原因。
    session.execute(
        update(tool_steps)
        .where(tool_steps.c.step_id == claim.step_id)
        .values(
            state="completed",
            available_at=completed_at,
            updated_at=completed_at,
            version=cast(int, row["step_version"]) + 1,
        )
    )
    incomplete = session.scalar(
        select(func.count())
        .select_from(tool_steps)
        .where(tool_steps.c.run_id == claim.run_id, tool_steps.c.state != "completed")
    )
    incomplete_count = int(incomplete or 0)
    if incomplete_count == 0:
        session.execute(
            update(tool_runs)
            .where(tool_runs.c.run_id == claim.run_id)
            .values(
                state="completed",
                recovery_reason_code=None,
                recovery_required_at=None,
                updated_at=completed_at,
                completed_at=completed_at,
                version=cast(int, row["run_version"]) + 1,
            )
        )
    elif reconciled:
        # 结果未知对账成功后恢复父级运行态，否则多步 Run 会永久滞留在人工恢复。
        session.execute(
            update(tool_runs)
            .where(tool_runs.c.run_id == claim.run_id)
            .values(
                state="running",
                recovery_reason_code=None,
                recovery_required_at=None,
                updated_at=completed_at,
                version=cast(int, row["run_version"]) + 1,
            )
        )


def _close_unknown_claim(
    session: Session,
    row: RowMapping,
    claim: ClaimedToolAttempt,
    occurred_at: datetime,
) -> None:
    """保留调用可对账状态，同时关闭 Worker 租约并建立死信父级事实。"""

    session.execute(
        update(tool_attempts)
        .where(tool_attempts.c.attempt_id == claim.attempt_id)
        .values(
            state="failed",
            completed_at=occurred_at,
            error_code="TOOL_OUTCOME_UNKNOWN",
        )
    )
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
            recovery_reason_code="TOOL_OUTCOME_UNKNOWN",
            recovery_required_at=occurred_at,
            updated_at=occurred_at,
            version=cast(int, row["run_version"]) + 1,
        )
    )


def _close_failed_claim(
    session: Session,
    row: RowMapping,
    claim: ClaimedToolAttempt,
    error_code: str,
    completed_at: datetime,
) -> None:
    session.execute(
        update(tool_calls)
        .where(tool_calls.c.tool_call_id == claim.tool_call_id)
        .values(
            state="failed",
            completed_at=completed_at,
            updated_at=completed_at,
            error_code=error_code,
        )
    )
    session.execute(
        update(tool_attempts)
        .where(tool_attempts.c.attempt_id == claim.attempt_id)
        .values(state="failed", completed_at=completed_at, error_code=error_code)
    )
    session.execute(
        update(tool_steps)
        .where(tool_steps.c.step_id == claim.step_id)
        .values(
            state="failed",
            updated_at=completed_at,
            version=cast(int, row["step_version"]) + 1,
        )
    )
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


def _record_lifecycle(
    session: Session,
    source: RowMapping,
    record: ToolIdempotencyRecord,
    event_type: str,
    occurred_at: datetime,
    *,
    reconciled: bool = False,
) -> None:
    """只记录标识、状态和摘要；参数、结果正文及凭证不得进入横切事实。"""

    # 1. 先构造允许进入审计和事件的最小投影；正文、规范参数和凭证引用均不在白名单内。
    outcome = "succeeded" if record.state in {"reserved", "succeeded"} else "failed"
    authorization = AuditAuthorization(
        permission_code=cast(str, source["permission_code"]),
        policy_decision_id=cast(UUID, source["decision_id"]),
        policy_version=cast(int, source["policy_version"]),
    )
    attributes: dict[str, object] = {
        "idempotency_record_id": str(record.idempotency_record_id),
        "reconciled": reconciled,
        "state": record.state,
        "tool_id": str(record.tool_id),
        "tool_version": record.tool_version,
    }
    if record.result_hash is not None:
        attributes["result_hash"] = record.result_hash
    if record.error_code is not None:
        attributes["error_code"] = record.error_code
    # 2. 审计与 Outbox 复用调用方 Session，任一横切写入失败都会回滚当前业务状态变更。
    SqlAlchemyAuditWriter(session).add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=record.workspace_id,
            actor_id=cast(UUID, source["requested_by_actor_id"]),
            user_id=cast(UUID, source["requested_by_account_id"]),
            action=event_type,
            resource_type="tool_call",
            resource_id=record.tool_call_id,
            outcome=outcome,
            occurred_at=occurred_at,
            request_id=record.tool_call_id,
            trace_id=cast(str, source["trace_id"]),
            traceparent=cast(str, source["traceparent"]),
            authorization=authorization,
            attributes=attributes,
        )
    )
    SqlAlchemyOutboxWriter(session).add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type=event_type,
            workspace_id=record.workspace_id,
            aggregate_id=record.tool_call_id,
            aggregate_version=3 if reconciled else (1 if record.state == "reserved" else 2),
            occurred_at=occurred_at,
            trace_id=cast(str, source["trace_id"]),
            traceparent=cast(str, source["traceparent"]),
            actor_id=cast(UUID, source["requested_by_actor_id"]),
            user_id=cast(UUID, source["requested_by_account_id"]),
            request_id=record.tool_call_id,
            payload=attributes,
        )
    )


def _record(row: RowMapping) -> ToolIdempotencyRecord:
    return ToolIdempotencyRecord(
        idempotency_record_id=cast(UUID, row["idempotency_record_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        run_id=cast(UUID, row["run_id"]),
        step_id=cast(UUID, row["step_id"]),
        tool_call_id=cast(UUID, row["tool_call_id"]),
        tool_id=cast(UUID, row["tool_id"]),
        tool_version=cast(int, row["tool_version"]),
        identity=ToolSideEffectIdentity(
            idempotency_key_hash=cast(str, row["idempotency_key_hash"]),
            request_hash=cast(str, row["request_hash"]),
            confirmation_hash=cast(str, row["confirmation_hash"]),
        ),
        state=cast(ToolIdempotencyState, row["state"]),
        result_hash=cast(str | None, row["result_hash"]),
        reserved_at=cast(datetime, row["reserved_at"]),
        completed_at=cast(datetime | None, row["completed_at"]),
        error_code=cast(str | None, row["error_code"]),
    )


def _execution_result(
    record: ToolIdempotencyRecord,
    *,
    replayed: bool,
) -> ToolSideEffectExecutionResult:
    if record.result_hash is None:
        raise ToolRunConflictError
    return ToolSideEffectExecutionResult(record, record.result_hash, replayed)


def _effect_by_key(session: Session, command: SyntheticSideEffectCommand) -> RowMapping:
    row = (
        session.execute(
            select(synthetic_tool_side_effects).where(
                synthetic_tool_side_effects.c.workspace_id == command.workspace_id,
                synthetic_tool_side_effects.c.idempotency_key_hash == command.idempotency_key_hash,
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ToolRunConflictError
    return row


def _require_effect_matches_command(
    row: RowMapping,
    command: SyntheticSideEffectCommand,
) -> None:
    if (
        row["idempotency_record_id"] != command.idempotency_record_id
        or row["workspace_id"] != command.workspace_id
        or row["request_hash"] != command.request_hash
        or row["canonical_arguments_hash"] != command.canonical_arguments_hash
    ):
        raise ToolIdempotencyConflictError


def _receipt(row: RowMapping) -> SyntheticSideEffectReceipt:
    return SyntheticSideEffectReceipt(
        side_effect_id=cast(UUID, row["side_effect_id"]),
        idempotency_record_id=cast(UUID, row["idempotency_record_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        idempotency_key_hash=cast(str, row["idempotency_key_hash"]),
        request_hash=cast(str, row["request_hash"]),
        canonical_arguments_hash=cast(str, row["canonical_arguments_hash"]),
        result_hash=cast(str, row["result_hash"]),
        committed_at=cast(datetime, row["committed_at"]),
    )


__all__ = ["SqlAlchemySyntheticSideEffectAdapter", "SqlAlchemyToolSideEffectStore"]
