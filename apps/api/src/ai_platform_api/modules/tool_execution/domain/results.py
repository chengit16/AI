"""定义工具安全结果、进度与追加式用量成本事实。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

ToolSafetyCheckCode = Literal["schema", "size", "sensitive_fields", "prompt_injection"]
ToolSafetyCheckStatus = Literal["passed", "failed"]
ToolSafeResultStatus = Literal["accepted", "rejected"]
ToolUsageOutcome = Literal[
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
    "ignored_late_result",
    "manual_recovery",
]
ToolProgressEventType = Literal[
    "tool.run.created",
    "tool.run.state_changed",
    "tool.step.state_changed",
    "tool.call.confirmation_requested",
    "tool.call.confirmation_resolved",
    "tool.call.started",
    "tool.call.completed",
    "tool.call.failed",
    "tool.run.cancellation_requested",
    "tool.run.cancelled",
]


@dataclass(frozen=True)
class ToolSafetyCheck:
    """保存一项固定结果检查的稳定结论，不携带命中正文。"""

    check_code: ToolSafetyCheckCode
    status: ToolSafetyCheckStatus


@dataclass(frozen=True)
class ToolSafeResult:
    """保存可审计的结果摘要；原始结果正文只停留在受控调用边缘。"""

    result_id: UUID
    tool_call_id: UUID
    workspace_id: UUID
    output_schema_hash: str
    content_hash: str
    result_size_bytes: int
    status: ToolSafeResultStatus
    safety_checks: tuple[ToolSafetyCheck, ...]
    eligible_for_model_context: bool
    credential_exposure_detected: bool
    recorded_at: datetime


@dataclass(frozen=True)
class ToolAttemptOutcomeFacts:
    """把一次 Attempt 的安全结果、耗时和成本绑定为原子收口输入。"""

    outcome: ToolUsageOutcome
    duration_ms: int
    cost_microunits: int
    result_size_bytes: int
    error_code: str | None
    safe_result: ToolSafeResult | None


@dataclass(frozen=True)
class ToolUsageRecord:
    """记录每次 Attempt 的追加式用量，失败、取消和迟到样本同样保留。"""

    usage_record_id: UUID
    workspace_id: UUID
    run_id: UUID
    step_id: UUID
    attempt_id: UUID
    tool_call_id: UUID
    tool_id: UUID
    tool_version: int
    access_mode: Literal["read", "write"]
    risk_level: Literal["low", "medium", "high", "critical"]
    outcome: ToolUsageOutcome
    duration_ms: int
    result_size_bytes: int
    cost_microunits: int
    error_code: str | None
    recorded_at: datetime


@dataclass(frozen=True)
class ToolProgressEvent:
    """保存 Run 内单调游标事件，SSE 只回放状态和最小事实引用。"""

    progress_event_id: UUID
    workspace_id: UUID
    run_id: UUID
    cursor: int
    event_type: ToolProgressEventType
    run_state: str
    step_id: UUID | None
    step_state: str | None
    attempt_id: UUID | None
    tool_call_id: UUID | None
    error_code: str | None
    occurred_at: datetime


@dataclass(frozen=True)
class ToolProgressPage:
    """返回一次有界断点回放，并指明是否仍有后续事实。"""

    events: tuple[ToolProgressEvent, ...]
    latest_cursor: int
    has_more: bool


class ToolProgressStore(Protocol):
    """按可信工作空间读取 Run 进度；不存在与跨空间使用同一空结论。"""

    def replay(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        after_cursor: int,
        limit: int,
    ) -> ToolProgressPage | None: ...


__all__ = [
    "ToolAttemptOutcomeFacts",
    "ToolProgressEvent",
    "ToolProgressEventType",
    "ToolProgressPage",
    "ToolProgressStore",
    "ToolSafeResult",
    "ToolSafeResultStatus",
    "ToolSafetyCheck",
    "ToolSafetyCheckCode",
    "ToolSafetyCheckStatus",
    "ToolUsageOutcome",
    "ToolUsageRecord",
]
