"""编排合成副作用的执行前预留、唯一提交、重放和人工对账边界。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from ai_platform_api.modules.tool_execution.application.errors import (
    ToolAdapterUnavailableError,
    ToolIdempotencyConflictError,
    ToolOutcomeUnknownError,
    ToolRunConflictError,
)
from ai_platform_api.modules.tool_execution.domain.side_effects import (
    SyntheticSideEffectAdapter,
    SyntheticSideEffectCommand,
    ToolIdempotencyRecord,
    ToolIdempotencyReservation,
    ToolSideEffectExecutionResult,
    ToolSideEffectStore,
)
from ai_platform_api.modules.tool_execution.domain.tasks import ClaimedToolAttempt


class ToolSideEffectService:
    """保证幂等预留先于 Adapter，并禁止对未知结果做自动重放。"""

    def __init__(
        self,
        store: ToolSideEffectStore,
        adapter: SyntheticSideEffectAdapter,
    ) -> None:
        self._store = store
        self._adapter = adapter

    def execute(
        self,
        claim: ClaimedToolAttempt,
        *,
        occurred_at: datetime | None = None,
    ) -> ToolSideEffectExecutionResult:
        """执行一次合成写调用；重复投递只能重放已提交摘要。"""

        now = occurred_at or datetime.now(UTC)
        # 1. 先持久化稳定幂等身份；已有终态只重放摘要，持有中的调用不允许被当前投递接管。
        reservation = self._store.reserve(claim, reserved_at=now)
        existing = _existing_result(reservation)
        if existing is not None:
            return existing

        # 2. Adapter 位于数据库事务之外；按“明确失败”与“结果未知”分别收口，未知结果绝不自动重放。
        command = _command(reservation.record, claim)
        try:
            receipt = self._adapter.execute(command, committed_at=now)
        except ToolOutcomeUnknownError:
            self._store.mark_outcome_unknown(
                claim,
                reservation.record.idempotency_record_id,
                occurred_at=now,
            )
            raise
        except ToolIdempotencyConflictError:
            self._store.complete_failure(
                claim,
                reservation.record.idempotency_record_id,
                error_code="IDEMPOTENCY_CONFLICT",
                completed_at=now,
            )
            raise
        except Exception:
            self._store.complete_failure(
                claim,
                reservation.record.idempotency_record_id,
                error_code="TOOL_ADAPTER_UNAVAILABLE",
                completed_at=now,
            )
            raise ToolAdapterUnavailableError from None
        # 3. 只有可验证回执才能与任务、审计和 Outbox 在同一事务中提交成功终态。
        return self._store.complete_success(
            claim,
            receipt,
            completed_at=now,
            reconciled=False,
        )

    def reconcile(
        self,
        claim: ClaimedToolAttempt,
        *,
        reconciled_at: datetime | None = None,
    ) -> ToolSideEffectExecutionResult:
        """只查询合成事实关闭未知结果，不再次调用副作用执行入口。"""

        now = reconciled_at or datetime.now(UTC)
        reservation = self._store.reserve(claim, reserved_at=now)
        if reservation.record.state == "succeeded":
            return _replayed_result(reservation.record)
        if reservation.record.state not in {"reserved", "outcome_unknown"}:
            raise ToolRunConflictError
        receipt = self._adapter.reconcile(reservation.record)
        if receipt is None:
            raise ToolOutcomeUnknownError
        return self._store.complete_success(
            claim,
            receipt,
            completed_at=now,
            reconciled=True,
        )


def _existing_result(
    reservation: ToolIdempotencyReservation,
) -> ToolSideEffectExecutionResult | None:
    if reservation.created:
        return None
    record = reservation.record
    if record.state == "succeeded":
        return _replayed_result(record)
    if record.state == "outcome_unknown":
        raise ToolOutcomeUnknownError
    if record.state == "failed":
        if record.error_code == "IDEMPOTENCY_CONFLICT":
            raise ToolIdempotencyConflictError
        raise ToolAdapterUnavailableError
    # 已预留但尚未结束表示另一个 Worker 仍可能持有 Adapter 调用权，当前投递不能接管。
    raise ToolRunConflictError


def _command(
    record: ToolIdempotencyRecord,
    claim: ClaimedToolAttempt,
) -> SyntheticSideEffectCommand:
    return SyntheticSideEffectCommand(
        side_effect_id=uuid4(),
        idempotency_record_id=record.idempotency_record_id,
        workspace_id=record.workspace_id,
        idempotency_key_hash=record.identity.idempotency_key_hash,
        request_hash=record.identity.request_hash,
        canonical_arguments_hash=claim.canonical_arguments_hash,
    )


def _replayed_result(record: ToolIdempotencyRecord) -> ToolSideEffectExecutionResult:
    if record.result_hash is None:
        raise ToolRunConflictError
    return ToolSideEffectExecutionResult(record, record.result_hash, replayed=True)


__all__ = ["ToolSideEffectService"]
