"""定义受限工作流执行预算、步骤事实和节点 Adapter 契约。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.workflow.domain.approval_runtime import ApprovalRuntimeState
from ai_platform_api.modules.workflow.domain.models import (
    WorkflowNode,
    WorkflowRun,
    WorkflowVersion,
)

WORKFLOW_EXECUTOR_VERSION = "workflow-executor-v1"
WorkflowStepStatus = Literal[
    "running",
    "waiting_approval",
    "succeeded",
    "skipped",
    "failed",
]
WorkflowAttemptStatus = Literal["running", "succeeded", "failed"]


@dataclass(frozen=True)
class WorkflowExecutionBudget:
    """冻结一次运行允许消耗的节点、模型、检索、时间和输出预算。"""

    max_steps: int = 100
    max_model_calls: int = 5
    max_retrieval_calls: int = 5
    max_elapsed_ms: int = 30_000
    max_step_output_bytes: int = 64 * 1024
    max_total_output_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        values = (
            self.max_steps,
            self.max_model_calls,
            self.max_retrieval_calls,
            self.max_elapsed_ms,
            self.max_step_output_bytes,
            self.max_total_output_bytes,
        )
        if min(values) < 1:
            raise ValueError("工作流执行预算必须全部为正整数")
        if self.max_model_calls > self.max_steps or self.max_retrieval_calls > self.max_steps:
            raise ValueError("专项调用预算不能超过总步骤预算")
        if self.max_step_output_bytes > self.max_total_output_bytes:
            raise ValueError("单步骤输出预算不能超过总输出预算")

    def document(self) -> dict[str, int]:
        """生成可持久化预算快照，运行中不得读取可变配置替换它。"""

        return asdict(self)


DEFAULT_WORKFLOW_EXECUTION_BUDGET = WorkflowExecutionBudget()


@dataclass(frozen=True)
class WorkflowExecutionClaim:
    """返回成功认领的 Run、冻结版本、预算和已有步骤快照。"""

    run: WorkflowRun
    version: WorkflowVersion
    budget: WorkflowExecutionBudget
    steps: tuple[WorkflowRunStep, ...] = ()


@dataclass(frozen=True)
class WorkflowRunStep:
    """记录一个节点在指定 Run 中的稳定步骤身份和当前执行状态。"""

    workflow_step_id: UUID
    workflow_run_id: UUID
    workspace_id: UUID
    node_id: str
    node_type: str
    sequence_no: int
    status: WorkflowStepStatus
    input_payload: dict[str, object] | None
    output_payload: dict[str, object] | None
    branch_key: str | None
    policy_decision_id: UUID | None
    policy_version: int | None
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None


@dataclass(frozen=True)
class WorkflowNodeAttempt:
    """只追加一次节点尝试事实，失败详情使用稳定错误码而非原始异常。"""

    workflow_attempt_id: UUID
    workflow_step_id: UUID
    workflow_run_id: UUID
    workspace_id: UUID
    attempt_no: int
    executor_version: str
    status: WorkflowAttemptStatus
    input_hash: str
    output_hash: str | None
    usage: dict[str, object]
    started_at: datetime
    completed_at: datetime | None
    error_code: str | None


@dataclass(frozen=True)
class WorkflowKnowledgeItem:
    """返回当前仍授权的知识分块和可追溯来源标识。"""

    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    content_hash: str
    content: str
    source_position: dict[str, object]
    security_level: SecurityLevel


@dataclass(frozen=True)
class WorkflowKnowledgeResult:
    """汇总有界知识检索结果及本次 PDP 决策事实。"""

    items: tuple[WorkflowKnowledgeItem, ...]
    policy_decision_id: UUID
    policy_version: int
    maximum_security_level: SecurityLevel


@dataclass(frozen=True)
class WorkflowModelResult:
    """返回模型正文和可审计用量，不暴露供应商凭据或原始异常。"""

    content: str
    input_tokens: int
    output_tokens: int
    estimated_cost_microunits: int
    degraded: bool


class WorkflowExecutionStore(Protocol):
    """以短事务认领 Run 并维护步骤、尝试和终态事实。"""

    def claim(
        self,
        context: RequestContext,
        workflow_run_id: UUID,
        *,
        executor_version: str,
        budget: WorkflowExecutionBudget,
        now: datetime,
    ) -> WorkflowExecutionClaim | None: ...

    def start_step(
        self,
        step: WorkflowRunStep,
        attempt: WorkflowNodeAttempt,
    ) -> None: ...

    def complete_step(
        self,
        workflow_step_id: UUID,
        workflow_attempt_id: UUID,
        *,
        output_payload: dict[str, object],
        branch_key: str | None,
        output_hash: str,
        usage: dict[str, object],
        now: datetime,
    ) -> None: ...

    def skip_step(self, step: WorkflowRunStep) -> None: ...

    def wait_for_approval(
        self,
        context: RequestContext,
        workflow_step_id: UUID,
        workflow_attempt_id: UUID,
        *,
        approval_state: ApprovalRuntimeState,
        output_payload: dict[str, object],
        output_hash: str,
        steps_executed: int,
        model_calls: int,
        retrieval_calls: int,
        output_bytes: int,
        now: datetime,
    ) -> None: ...

    def complete_run(
        self,
        context: RequestContext,
        workflow_run_id: UUID,
        *,
        output_payload: dict[str, object],
        steps_executed: int,
        model_calls: int,
        retrieval_calls: int,
        output_bytes: int,
        now: datetime,
    ) -> WorkflowRun: ...

    def fail_step_and_run(
        self,
        context: RequestContext,
        workflow_run_id: UUID,
        *,
        workflow_step_id: UUID | None,
        workflow_attempt_id: UUID | None,
        error_code: str,
        now: datetime,
    ) -> WorkflowRun: ...


class WorkflowKnowledgeRetriever(Protocol):
    """在当前文档权限和字段范围内执行只读知识检索。"""

    def retrieve(
        self,
        context: RequestContext,
        *,
        query: str,
        knowledge_base_ids: frozenset[UUID] | None,
        limit: int,
    ) -> WorkflowKnowledgeResult: ...


class WorkflowModelInvoker(Protocol):
    """通过冻结运行配置调用模型，不允许节点直接持有供应商 Adapter。"""

    def invoke(
        self,
        context: RequestContext,
        *,
        run: WorkflowRun,
        node: WorkflowNode,
        prompt: str,
        knowledge: tuple[WorkflowKnowledgeItem, ...],
        security_level: SecurityLevel,
    ) -> WorkflowModelResult: ...
