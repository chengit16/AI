"""定义模型候选意图、Release 工具允许列表和原子计划冻结端口。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from ai_platform_api.modules.tool_execution.domain.tasks import (
    ToolRun,
    ToolStep,
    ToolStepBudget,
)


@dataclass(frozen=True)
class CandidateToolIntent:
    """表示模型输出经严格解析后形成的候选调用，不具备执行权。"""

    tool_key: str
    tool_id: UUID
    tool_version: int
    arguments: Mapping[str, object]


@dataclass(frozen=True)
class ReleaseToolReference:
    """保存不可变 AgentRelease 明确允许的工具身份和权限。"""

    tool_id: UUID
    tool_version: int
    access_mode: str
    permission_code: str


@dataclass(frozen=True)
class ToolReleasePlan:
    """投影一个 Run 精确绑定 Release 的只读工具允许列表。"""

    workspace_id: UUID
    service_id: UUID
    agent_release_id: UUID
    release_snapshot_hash: str | None
    tools: tuple[ReleaseToolReference, ...]


@dataclass(frozen=True)
class ToolPolicyDecisionRecord:
    """保存 Step 进入 ready 前的当前策略决策最小证据。"""

    decision_id: UUID
    workspace_id: UUID
    run_id: UUID
    step_id: UUID
    tool_id: UUID
    tool_version: int
    canonical_arguments_hash: str
    permission_code: str
    policy_version: int
    resource_scope_hash: str
    field_mask_hash: str
    evaluated_at: datetime


@dataclass(frozen=True)
class FrozenToolPlanStep:
    """聚合即将原子写入的 Step 身份、预算和策略证据。"""

    step_id: UUID
    sequence_no: int
    tool_id: UUID
    tool_version: int
    canonical_arguments_hash: str
    budget: ToolStepBudget
    policy: ToolPolicyDecisionRecord


@dataclass(frozen=True)
class ToolExecutionPlan:
    """返回已经持久化且可由 Worker 按顺序领取的只读计划。"""

    run: ToolRun
    steps: tuple[ToolStep, ...]
    policies: tuple[ToolPolicyDecisionRecord, ...]


class ToolReleasePlanSource(Protocol):
    """只读取 Run 精确绑定的不可变 Release 工具允许列表。"""

    def get_bound(
        self,
        workspace_id: UUID,
        service_id: UUID,
        agent_release_id: UUID,
    ) -> ToolReleasePlan | None: ...


class ToolPlanStore(Protocol):
    """原子冻结全部 Step、预算、策略证据和 Run 运行状态。"""

    def get_run(self, workspace_id: UUID, run_id: UUID) -> ToolRun | None: ...

    def freeze_read_plan(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        steps: tuple[FrozenToolPlanStep, ...],
        frozen_at: datetime,
    ) -> ToolExecutionPlan: ...
