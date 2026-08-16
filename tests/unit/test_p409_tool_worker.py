"""验证 P4-09 Worker 的异常分类、指数退避、续租和取消控制。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from ai_platform_api.modules.tool_execution.application.errors import ToolOutcomeUnknownError
from ai_platform_api.modules.tool_execution.application.tasks import ToolTaskService
from ai_platform_api.modules.tool_execution.application.worker import (
    ToolAttemptControl,
    ToolAttemptExecutionError,
    ToolWorkerProcessor,
)
from ai_platform_api.modules.tool_execution.domain.tasks import (
    AttemptResult,
    ClaimedToolAttempt,
)

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def _claim(*, attempt_no: int = 1) -> ClaimedToolAttempt:
    return ClaimedToolAttempt(
        attempt_id=uuid4(),
        run_id=uuid4(),
        step_id=uuid4(),
        tool_call_id=uuid4(),
        workspace_id=uuid4(),
        tool_id=uuid4(),
        tool_version=1,
        canonical_arguments_hash="a" * 64,
        recovery_generation=0,
        attempt_no=attempt_no,
        lease_generation=attempt_no,
        trigger="automatic" if attempt_no == 1 else "automatic_retry",
        worker_id="p409-worker",
        lease_expires_at=NOW + timedelta(seconds=30),
    )


class StubTasks:
    """保存 Worker 写回参数，避免单元测试复制数据库状态机。"""

    def __init__(self, claim: ClaimedToolAttempt, result: AttemptResult) -> None:
        self.pending: ClaimedToolAttempt | None = claim
        self.result = result
        self.finished: list[dict[str, object]] = []
        self.manual_recovery_calls = 0
        self.cancel_requested = False

    def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimedToolAttempt | None:
        del worker_id, now, lease_seconds
        claim, self.pending = self.pending, None
        return claim

    def begin_attempt(self, claim: ClaimedToolAttempt, *, started_at: datetime) -> bool:
        del claim, started_at
        return True

    def transition_call(
        self,
        claim: ClaimedToolAttempt,
        target_state: str,
        *,
        occurred_at: datetime,
    ) -> bool:
        del claim, target_state, occurred_at
        return True

    def finish_attempt(
        self,
        claim: ClaimedToolAttempt,
        *,
        succeeded: bool,
        completed_at: datetime,
        error_code: str | None = None,
        retryable: bool = False,
        next_attempt_at: datetime | None = None,
    ) -> AttemptResult:
        self.finished.append(
            {
                "claim": claim,
                "succeeded": succeeded,
                "completed_at": completed_at,
                "error_code": error_code,
                "retryable": retryable,
                "next_attempt_at": next_attempt_at,
            }
        )
        return self.result

    def require_manual_recovery(
        self,
        claim: ClaimedToolAttempt,
        *,
        error_code: str,
        occurred_at: datetime,
    ) -> AttemptResult:
        del claim, error_code, occurred_at
        self.manual_recovery_calls += 1
        return "manual_recovery"

    def renew_lease(
        self,
        claim: ClaimedToolAttempt,
        *,
        renewed_at: datetime,
        lease_seconds: int,
    ) -> ClaimedToolAttempt:
        return replace(
            claim,
            lease_expires_at=renewed_at + timedelta(seconds=lease_seconds),
        )

    def observe_cancellation(
        self,
        claim: ClaimedToolAttempt,
        *,
        observed_at: datetime,
    ) -> bool:
        del claim, observed_at
        return self.cancel_requested


class FailingExecutor:
    """按测试指定的稳定错误分类终止执行。"""

    def __init__(self, error: Exception | None) -> None:
        self.error = error

    def execute(self, claim: ClaimedToolAttempt, control: ToolAttemptControl) -> None:
        del claim, control
        if self.error is not None:
            raise self.error


def _processor(tasks: StubTasks, executor: FailingExecutor) -> ToolWorkerProcessor:
    return ToolWorkerProcessor(
        cast(ToolTaskService, tasks),
        executor,
        worker_id="p409-worker",
        lease_seconds=30,
        retry_base_seconds=2,
    )


def test_retryable_failure_uses_attempt_number_for_exponential_backoff() -> None:
    tasks = StubTasks(_claim(attempt_no=3), "retry_wait")
    processor = _processor(
        tasks,
        FailingExecutor(ToolAttemptExecutionError("TOOL_ADAPTER_UNAVAILABLE", retryable=True)),
    )

    result = processor.run_batch(limit=1, now=NOW)

    assert result.claimed == 1
    assert result.retried == 1
    assert tasks.finished[0]["retryable"] is True
    assert tasks.finished[0]["next_attempt_at"] == NOW + timedelta(seconds=8)


def test_unknown_outcome_never_enters_automatic_finish_path() -> None:
    tasks = StubTasks(_claim(), "manual_recovery")
    result = _processor(tasks, FailingExecutor(ToolOutcomeUnknownError())).run_batch(
        limit=1,
        now=NOW,
    )

    assert result.manual_recovery == 1
    assert tasks.manual_recovery_calls == 1
    assert tasks.finished == []


def test_unclassified_failure_is_mapped_to_stable_retryable_code() -> None:
    tasks = StubTasks(_claim(), "retry_wait")
    result = _processor(tasks, FailingExecutor(RuntimeError("不得进入任务事实"))).run_batch(
        limit=1,
        now=NOW,
    )

    assert result.retried == 1
    assert tasks.finished[0]["error_code"] == "TOOL_ADAPTER_UNAVAILABLE"
    assert tasks.finished[0]["retryable"] is True


def test_attempt_control_replaces_renewed_claim_and_observes_cancellation() -> None:
    tasks = StubTasks(_claim(), "succeeded")
    assert tasks.pending is not None
    control = ToolAttemptControl(cast(ToolTaskService, tasks), tasks.pending)

    assert control.heartbeat(occurred_at=NOW + timedelta(seconds=5), lease_seconds=20)
    assert control.claim.lease_expires_at == NOW + timedelta(seconds=25)
    assert not control.cancellation_requested(observed_at=NOW + timedelta(seconds=6))
    tasks.cancel_requested = True
    assert control.cancellation_requested(observed_at=NOW + timedelta(seconds=7))
