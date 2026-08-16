"""定义工具控制台脱敏投影与只读存储接口。

该模块只描述控制台可消费的最小事实以及 PostgreSQL Adapter 必须满足的查询契约；
不负责浏览器身份授权、HTTP 映射、事务提交或任何工具任务写入。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from ai_platform_api.modules.tool_execution.domain.confirmations import ToolConfirmationMode
from ai_platform_api.modules.tool_execution.domain.tasks import ToolRunBudget


@dataclass(frozen=True)
class ToolRunSummary:
    """表示控制台可见的 Run 摘要，不包含参数、结果正文或 Trace。"""

    run_id: UUID
    service_id: UUID
    service_name: str
    agent_release_id: UUID
    agent_release_version: int
    state: str
    budget: ToolRunBudget
    requested_by_account_id: UUID
    step_count: int
    completed_step_count: int
    total_cost_microunits: int
    pending_confirmation_count: int
    cancel_requested_at: datetime | None
    deadline_at: datetime
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    version: int


@dataclass(frozen=True)
class ToolAttemptView:
    """投影一次 Attempt 的状态和用量，不暴露 Worker、凭证或结果正文。"""

    attempt_id: UUID
    tool_call_id: UUID
    attempt_no: int
    recovery_generation: int
    trigger: str
    state: str
    call_state: str
    error_code: str | None
    duration_ms: int | None
    result_size_bytes: int | None
    cost_microunits: int | None
    result_status: str | None
    started_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class ToolConfirmationView:
    """投影确认绑定和当前有效状态，不返回审批条件或参数正文。"""

    confirmation_id: UUID
    approval_instance_id: UUID
    mode: ToolConfirmationMode
    state: str
    risk_level: str
    confirmation_hash: str
    expires_at: datetime
    resolved_at: datetime | None
    can_respond: bool
    version: int


@dataclass(frozen=True)
class ToolStepView:
    """表示一个冻结步骤、工具治理属性和最近执行历史。"""

    step_id: UUID
    sequence_no: int
    tool_id: UUID
    tool_version: int
    tool_key: str
    display_name: str
    access_mode: str
    risk_level: str
    canonical_arguments_hash: str
    state: str
    timeout_seconds: int
    max_attempts: int
    max_result_bytes: int
    current_attempt_no: int | None
    created_at: datetime
    updated_at: datetime
    confirmation: ToolConfirmationView | None
    attempts: tuple[ToolAttemptView, ...]


@dataclass(frozen=True)
class ToolRunDetail:
    """组合 Run 摘要、步骤与 SSE 最新游标。"""

    run: ToolRunSummary
    steps: tuple[ToolStepView, ...]
    latest_cursor: int


class ToolConsoleStore(Protocol):
    """只读取控制台投影；实现必须隔离工作空间且不得取得任务写入权。"""

    def list_runs(
        self,
        *,
        workspace_id: UUID,
        account_id: UUID,
        workspace_scope: bool,
        resource_ids: frozenset[UUID],
        limit: int,
    ) -> tuple[ToolRunSummary, ...]: ...

    def get_run(
        self,
        *,
        workspace_id: UUID,
        account_id: UUID,
        run_id: UUID,
    ) -> ToolRunDetail | None: ...


__all__ = [
    "ToolAttemptView",
    "ToolConfirmationView",
    "ToolConsoleStore",
    "ToolRunDetail",
    "ToolRunSummary",
    "ToolStepView",
]
