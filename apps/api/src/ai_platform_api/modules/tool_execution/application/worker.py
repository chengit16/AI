"""编排工具 Worker 的租约、有限重试、取消观察和人工恢复收口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ai_platform_api.modules.tool_execution.application.errors import ToolOutcomeUnknownError
from ai_platform_api.modules.tool_execution.application.results import ToolResultFactsService
from ai_platform_api.modules.tool_execution.application.tasks import ToolTaskService
from ai_platform_api.modules.tool_execution.domain.results import ToolAttemptOutcomeFacts
from ai_platform_api.modules.tool_execution.domain.tasks import AttemptResult, ClaimedToolAttempt


class ToolAttemptExecutor(Protocol):
    """在受控调用边缘执行一个已授权 Attempt，不拥有任务状态写入权。"""

    def execute(
        self,
        claim: ClaimedToolAttempt,
        control: ToolAttemptControl,
    ) -> ToolAttemptOutcomeFacts: ...


class ToolAttemptExecutionError(Exception):
    """携带稳定错误码和重试分类，不向任务事实暴露原始异常文本。"""

    def __init__(
        self,
        error_code: str,
        *,
        retryable: bool,
        duration_ms: int = 0,
        cost_microunits: int = 0,
        result_size_bytes: int = 0,
    ) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.retryable = retryable
        self.duration_ms = duration_ms
        self.cost_microunits = cost_microunits
        self.result_size_bytes = result_size_bytes


@dataclass(frozen=True)
class ToolWorkerBatchResult:
    """汇总一次有界扫描，不包含 Run、Step、Actor 或参数等高基数字段。"""

    claimed: int = 0
    succeeded: int = 0
    retried: int = 0
    failed: int = 0
    manual_recovery: int = 0
    cancelled: int = 0
    lost_claims: int = 0


class ToolAttemptControl:
    """向长调用提供续租和取消查询，Adapter 只能使用当前冻结 Claim。"""

    def __init__(self, tasks: ToolTaskService, claim: ClaimedToolAttempt) -> None:
        self._tasks = tasks
        self._claim = claim

    @property
    def claim(self) -> ClaimedToolAttempt:
        return self._claim

    def heartbeat(
        self,
        *,
        occurred_at: datetime,
        lease_seconds: int,
    ) -> bool:
        """续租成功后替换本地 Claim，后续结果必须使用最新截止时间。"""

        renewed = self._tasks.renew_lease(
            self._claim,
            renewed_at=occurred_at,
            lease_seconds=lease_seconds,
        )
        if renewed is None:
            return False
        self._claim = renewed
        return True

    def cancellation_requested(self, *, observed_at: datetime) -> bool:
        """查询并记录取消观察；底层 Adapter 据此尽力中止自身工作。"""

        return self._tasks.observe_cancellation(self._claim, observed_at=observed_at)


class ToolWorkerProcessor:
    """只在短事务外执行 Adapter，并把所有结果交回唯一任务 Store 收敛。"""

    def __init__(
        self,
        tasks: ToolTaskService,
        executor: ToolAttemptExecutor,
        *,
        worker_id: str,
        lease_seconds: int,
        retry_base_seconds: int,
    ) -> None:
        self._tasks = tasks
        self._executor = executor
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._retry_base_seconds = retry_base_seconds
        self._result_facts = ToolResultFactsService()

    def run_batch(
        self,
        *,
        limit: int,
        now: datetime | None = None,
    ) -> ToolWorkerBatchResult:
        """有界领取并执行当前可用 Step，同一批次不会绕过退避时间立即重试。"""

        if not 1 <= limit <= 100:
            raise ValueError("工具 Worker 批次必须位于 1 到 100 之间")
        current = now or datetime.now(UTC)
        counts = {
            "claimed": 0,
            "succeeded": 0,
            "retried": 0,
            "failed": 0,
            "manual_recovery": 0,
            "cancelled": 0,
            "lost_claims": 0,
        }
        for _ in range(limit):
            claim = self._tasks.claim_next(
                worker_id=self._worker_id,
                now=current,
                lease_seconds=self._lease_seconds,
            )
            if claim is None:
                break
            counts["claimed"] += 1
            counts[self._process(claim, occurred_at=current)] += 1
        return ToolWorkerBatchResult(**counts)

    def _process(self, claim: ClaimedToolAttempt, *, occurred_at: datetime) -> str:
        """按调用门禁执行一次 Attempt，异常只按稳定分类进入状态机。"""

        if not self._tasks.begin_attempt(claim, started_at=occurred_at):
            return "lost_claims"
        # 1. 每个 Attempt 都重新走授权和确认状态；副作用预留仍由 P4-08 服务在 executing 前完成。
        for state in ("authorized", "confirmed", "executing"):
            if not self._tasks.transition_call(claim, state, occurred_at=occurred_at):
                return "lost_claims"
        # 2. Adapter 在短事务外执行，所有成功、稳定失败和未知结果统一交回任务状态机收敛。
        control = ToolAttemptControl(self._tasks, claim)
        try:
            facts = self._executor.execute(claim, control)
        except ToolOutcomeUnknownError:
            facts = self._result_facts.failed(
                outcome="manual_recovery",
                duration_ms=0,
                cost_microunits=0,
                result_size_bytes=0,
                error_code="TOOL_OUTCOME_UNKNOWN",
            )
            result = self._tasks.require_manual_recovery(
                control.claim,
                facts=facts,
                error_code="TOOL_OUTCOME_UNKNOWN",
                occurred_at=occurred_at,
            )
        except ToolAttemptExecutionError as error:
            result = self._finish_failure(control.claim, error, occurred_at)
        except Exception:
            # 未分类异常只映射到稳定可重试码，堆栈和异常正文不能进入任务事实。
            result = self._finish_failure(
                control.claim,
                ToolAttemptExecutionError("TOOL_ADAPTER_UNAVAILABLE", retryable=True),
                occurred_at,
            )
        else:
            result = self._tasks.finish_attempt(
                control.claim,
                facts=facts,
                succeeded=True,
                completed_at=occurred_at,
            )
        return {
            "succeeded": "succeeded",
            "retry_wait": "retried",
            "failed": "failed",
            "manual_recovery": "manual_recovery",
            "ignored_late_result": "cancelled",
        }[result]

    def _finish_failure(
        self,
        claim: ClaimedToolAttempt,
        error: ToolAttemptExecutionError,
        occurred_at: datetime,
    ) -> AttemptResult:
        backoff = self._retry_base_seconds * 2 ** max(claim.attempt_no - 1, 0)
        facts = self._result_facts.failed(
            outcome="failed",
            duration_ms=error.duration_ms,
            cost_microunits=error.cost_microunits,
            result_size_bytes=error.result_size_bytes,
            error_code=error.error_code,
        )
        return self._tasks.finish_attempt(
            claim,
            facts=facts,
            succeeded=False,
            completed_at=occurred_at,
            error_code=error.error_code,
            retryable=error.retryable,
            next_attempt_at=occurred_at + timedelta(seconds=backoff),
        )


__all__ = [
    "ToolAttemptControl",
    "ToolAttemptExecutionError",
    "ToolAttemptExecutor",
    "ToolWorkerBatchResult",
    "ToolWorkerProcessor",
]
