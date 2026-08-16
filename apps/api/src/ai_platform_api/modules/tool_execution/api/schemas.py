"""定义工具目录、Run、确认和进度的脱敏 HTTP Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ai_platform_api.modules.tool_execution.application.catalog import ToolDefinition
from ai_platform_api.modules.tool_execution.application.console import (
    ToolAttemptView,
    ToolConfirmationView,
    ToolRunDetail,
    ToolRunSummary,
    ToolStepView,
)
from ai_platform_api.modules.tool_execution.application.results import ToolProgressEvent


class ToolIntentRequest(BaseModel):
    """提交一个严格版本化的只读工具候选调用。"""

    model_config = ConfigDict(extra="forbid")

    tool_key: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
    tool_id: UUID
    tool_version: int = Field(ge=1)
    arguments: dict[str, object]


class CreateToolRunRequest(BaseModel):
    """创建并冻结只读工具计划；预算只能在平台硬上限内收窄。"""

    model_config = ConfigDict(extra="forbid")

    service_id: UUID
    agent_release_id: UUID
    tool_calls: list[ToolIntentRequest] = Field(min_length=1, max_length=50)
    max_attempts_per_step: int = Field(default=3, ge=1, le=5)
    max_execution_seconds: int = Field(default=300, ge=1, le=1800)


class ToolConfirmationActionRequest(BaseModel):
    """提供工具确认动作的稳定幂等键和可选原因码。"""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
    reason_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_.-]{0,127}$",
    )


class ToolCatalogItemResponse(BaseModel):
    """返回可执行工具的 Schema 与治理属性，不包含任何凭证值。"""

    model_config = ConfigDict(extra="forbid")

    tool_id: UUID
    tool_version: int
    tool_key: str
    display_name: str
    description: str
    access_mode: str
    risk_level: str
    input_schema: dict[str, object]
    permission_code: str
    credential_requirement: str
    timeout_seconds: int
    retry_mode: str
    synthetic: bool

    @classmethod
    def from_domain(cls, item: ToolDefinition) -> Self:
        return cls(
            tool_id=item.tool_id,
            tool_version=item.tool_version,
            tool_key=item.tool_key,
            display_name=item.display_name,
            description=item.description,
            access_mode=item.access_mode,
            risk_level=item.risk_level,
            input_schema=item.input_schema_document,
            permission_code=item.permission_code,
            credential_requirement=item.credential_requirement,
            timeout_seconds=item.timeout_seconds,
            retry_mode=item.retry_mode,
            synthetic=item.synthetic,
        )


class ToolCatalogResponse(BaseModel):
    """返回当前工作空间套餐与当前权限的目录交集。"""

    model_config = ConfigDict(extra="forbid")

    items: list[ToolCatalogItemResponse]


class ToolRunSummaryResponse(BaseModel):
    """返回 Run 状态、预算和低敏运营聚合。"""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    service_id: UUID
    service_name: str
    agent_release_id: UUID
    agent_release_version: int
    state: str
    max_steps: int
    max_attempts_per_step: int
    max_execution_seconds: int
    max_cost_microunits: int
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

    @classmethod
    def from_domain(cls, item: ToolRunSummary) -> Self:
        return cls(
            run_id=item.run_id,
            service_id=item.service_id,
            service_name=item.service_name,
            agent_release_id=item.agent_release_id,
            agent_release_version=item.agent_release_version,
            state=item.state,
            max_steps=item.budget.max_steps,
            max_attempts_per_step=item.budget.max_attempts_per_step,
            max_execution_seconds=item.budget.max_execution_seconds,
            max_cost_microunits=item.budget.max_cost_microunits,
            requested_by_account_id=item.requested_by_account_id,
            step_count=item.step_count,
            completed_step_count=item.completed_step_count,
            total_cost_microunits=item.total_cost_microunits,
            pending_confirmation_count=item.pending_confirmation_count,
            cancel_requested_at=item.cancel_requested_at,
            deadline_at=item.deadline_at,
            created_at=item.created_at,
            updated_at=item.updated_at,
            completed_at=item.completed_at,
            version=item.version,
        )


class ToolAttemptResponse(BaseModel):
    """返回一次 Attempt 的状态、用量和结果安全结论。"""

    model_config = ConfigDict(extra="forbid")

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

    @classmethod
    def from_domain(cls, item: ToolAttemptView) -> Self:
        return cls(**item.__dict__)


class ToolConfirmationResponse(BaseModel):
    """返回工具确认当前状态和浏览器可执行性。"""

    model_config = ConfigDict(extra="forbid")

    confirmation_id: UUID
    approval_instance_id: UUID
    mode: Literal["personal_owner", "enterprise_approval"]
    state: str
    risk_level: str
    confirmation_hash: str
    expires_at: datetime
    resolved_at: datetime | None
    can_respond: bool
    version: int

    @classmethod
    def from_domain(cls, item: ToolConfirmationView) -> Self:
        return cls(**item.__dict__)


class ToolStepResponse(BaseModel):
    """返回冻结 Step、工具属性、确认和 Attempt 历史。"""

    model_config = ConfigDict(extra="forbid")

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
    confirmation: ToolConfirmationResponse | None
    attempts: list[ToolAttemptResponse]

    @classmethod
    def from_domain(cls, item: ToolStepView) -> Self:
        return cls(
            step_id=item.step_id,
            sequence_no=item.sequence_no,
            tool_id=item.tool_id,
            tool_version=item.tool_version,
            tool_key=item.tool_key,
            display_name=item.display_name,
            access_mode=item.access_mode,
            risk_level=item.risk_level,
            canonical_arguments_hash=item.canonical_arguments_hash,
            state=item.state,
            timeout_seconds=item.timeout_seconds,
            max_attempts=item.max_attempts,
            max_result_bytes=item.max_result_bytes,
            current_attempt_no=item.current_attempt_no,
            created_at=item.created_at,
            updated_at=item.updated_at,
            confirmation=(
                ToolConfirmationResponse.from_domain(item.confirmation)
                if item.confirmation is not None
                else None
            ),
            attempts=[ToolAttemptResponse.from_domain(attempt) for attempt in item.attempts],
        )


class ToolRunDetailResponse(BaseModel):
    """返回一个 Run 的完整控制台投影。"""

    model_config = ConfigDict(extra="forbid")

    run: ToolRunSummaryResponse
    steps: list[ToolStepResponse]
    latest_cursor: int

    @classmethod
    def from_domain(cls, item: ToolRunDetail) -> Self:
        return cls(
            run=ToolRunSummaryResponse.from_domain(item.run),
            steps=[ToolStepResponse.from_domain(step) for step in item.steps],
            latest_cursor=item.latest_cursor,
        )


class ToolRunListResponse(BaseModel):
    """返回授权范围内的 Run 历史。"""

    model_config = ConfigDict(extra="forbid")

    items: list[ToolRunSummaryResponse]


class ToolProgressEventResponse(BaseModel):
    """定义 SSE 的稳定最小事件载荷。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    workspace_id: UUID
    run_id: UUID
    cursor: int
    event_type: str
    run_state: str
    step_id: UUID | None
    step_state: str | None
    attempt_id: UUID | None
    tool_call_id: UUID | None
    error_code: str | None
    occurred_at: datetime

    @classmethod
    def from_domain(cls, item: ToolProgressEvent) -> Self:
        return cls(
            workspace_id=item.workspace_id,
            run_id=item.run_id,
            cursor=item.cursor,
            event_type=item.event_type,
            run_state=item.run_state,
            step_id=item.step_id,
            step_state=item.step_state,
            attempt_id=item.attempt_id,
            tool_call_id=item.tool_call_id,
            error_code=item.error_code,
            occurred_at=item.occurred_at,
        )


__all__ = [
    "CreateToolRunRequest",
    "ToolCatalogItemResponse",
    "ToolCatalogResponse",
    "ToolConfirmationActionRequest",
    "ToolProgressEventResponse",
    "ToolRunDetailResponse",
    "ToolRunListResponse",
    "ToolRunSummaryResponse",
]
