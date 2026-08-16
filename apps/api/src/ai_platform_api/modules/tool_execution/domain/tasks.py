"""定义受控工具任务状态事实和唯一持久化端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

ToolRunState = Literal[
    "pending",
    "planning",
    "running",
    "waiting_confirmation",
    "waiting_approval",
    "cancellation_requested",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
]
ToolStepState = Literal[
    "planned",
    "policy_checking",
    "waiting_confirmation",
    "waiting_approval",
    "ready",
    "running",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
]
ToolAttemptState = Literal[
    "leased",
    "executing",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
    "ignored_late_result",
]
ToolCallState = Literal[
    "proposed",
    "authorized",
    "confirmed",
    "executing",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
]
AttemptResult = Literal["succeeded", "failed", "ignored_late_result"]


@dataclass(frozen=True)
class ToolRunBudget:
    """冻结 Run 的步骤、尝试、执行时间和成本硬上限。"""

    max_steps: int
    max_attempts_per_step: int
    max_execution_seconds: int
    max_cost_microunits: int


@dataclass(frozen=True)
class ToolStepBudget:
    """冻结单步超时、尝试、结果大小和成本上限。"""

    timeout_seconds: int
    max_attempts: int
    max_result_bytes: int
    max_cost_microunits: int


@dataclass(frozen=True)
class ToolRun:
    """保存与可信主体、Service 和不可变 AgentRelease 绑定的普通工具任务。"""

    run_id: UUID
    workspace_id: UUID
    requested_by_actor_id: UUID
    requested_by_account_id: UUID
    service_id: UUID
    agent_release_id: UUID
    state: ToolRunState
    budget: ToolRunBudget
    cancel_requested_at: datetime | None
    deadline_at: datetime
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    version: int


@dataclass(frozen=True)
class ToolStep:
    """保存 Run 内不可变顺序、工具版本和规范参数摘要。"""

    step_id: UUID
    run_id: UUID
    workspace_id: UUID
    sequence_no: int
    tool_id: UUID
    tool_version: int
    canonical_arguments_hash: str
    budget: ToolStepBudget
    state: ToolStepState
    current_attempt_no: int | None
    created_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class ClaimedToolAttempt:
    """冻结 Worker 本次租约身份，写回时必须逐字段匹配数据库当前事实。"""

    attempt_id: UUID
    run_id: UUID
    step_id: UUID
    tool_call_id: UUID
    workspace_id: UUID
    tool_id: UUID
    tool_version: int
    canonical_arguments_hash: str
    attempt_no: int
    lease_generation: int
    worker_id: str
    lease_expires_at: datetime


class ToolTaskStore(Protocol):
    """独占 Run、Step、Attempt 和 ToolCall 的持久化状态转换。"""

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
    ) -> ToolRun: ...

    def get_run(self, workspace_id: UUID, run_id: UUID) -> ToolRun | None: ...

    def transition_run(
        self,
        workspace_id: UUID,
        run_id: UUID,
        target_state: ToolRunState,
        *,
        occurred_at: datetime,
    ) -> ToolRun: ...

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
    ) -> ToolStep: ...

    def transition_step(
        self,
        workspace_id: UUID,
        step_id: UUID,
        target_state: ToolStepState,
        *,
        occurred_at: datetime,
    ) -> ToolStep: ...

    def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimedToolAttempt | None: ...

    def begin_attempt(
        self,
        claim: ClaimedToolAttempt,
        *,
        started_at: datetime,
    ) -> bool: ...

    def transition_call(
        self,
        claim: ClaimedToolAttempt,
        target_state: ToolCallState,
        *,
        occurred_at: datetime,
    ) -> bool: ...

    def finish_attempt(
        self,
        claim: ClaimedToolAttempt,
        *,
        succeeded: bool,
        completed_at: datetime,
        error_code: str | None,
    ) -> AttemptResult: ...

    def request_cancellation(
        self,
        workspace_id: UUID,
        run_id: UUID,
        *,
        requested_at: datetime,
    ) -> ToolRun: ...
