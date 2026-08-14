"""验证 P1F-02 受限节点执行、分支、预算和审批等待语义。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.authorization.domain.policy import (
    PolicyDecision,
    PolicyRequest,
    ResourceScope,
)
from ai_platform_api.modules.workflow.application.executor import WorkflowRunExecutor
from ai_platform_api.modules.workflow.domain.execution import (
    WorkflowExecutionBudget,
    WorkflowExecutionClaim,
    WorkflowKnowledgeItem,
    WorkflowKnowledgeResult,
    WorkflowModelResult,
    WorkflowNodeAttempt,
    WorkflowRunStep,
)
from ai_platform_api.modules.workflow.domain.models import (
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
    WorkflowVersion,
)

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000901")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000901")
WORKFLOW_ID = UUID("a0000000-0000-4000-8000-000000000901")
VERSION_ID = UUID("a1000000-0000-4000-8000-000000000901")
RUN_ID = UUID("a2000000-0000-4000-8000-000000000901")
RUNTIME_ID = UUID("a3000000-0000-4000-8000-000000000901")
TRACE = TraceContext("a" * 32, "b" * 16)


class MemoryExecutionStore:
    """模拟数据库条件认领和单向状态转换，供执行器单元测试观察事实。"""

    def __init__(self, run: WorkflowRun, version: WorkflowVersion) -> None:
        self.run = run
        self.version = version
        self.steps: dict[UUID, WorkflowRunStep] = {}
        self.attempts: dict[UUID, WorkflowNodeAttempt] = {}

    def claim(
        self,
        context: RequestContext,
        workflow_run_id: UUID,
        *,
        executor_version: str,
        budget: WorkflowExecutionBudget,
        now: datetime,
    ) -> WorkflowExecutionClaim | None:
        if (
            self.run.workflow_run_id != workflow_run_id
            or self.run.status != "queued"
            or context.user_id != self.run.requested_by_account_id
        ):
            return None
        self.run = replace(
            self.run,
            status="running",
            executor_version=executor_version,
            execution_budget=budget.document(),
            updated_at=now,
            version=self.run.version + 1,
        )
        return WorkflowExecutionClaim(self.run, self.version, budget)

    def start_step(self, step: WorkflowRunStep, attempt: WorkflowNodeAttempt) -> None:
        self.steps[step.workflow_step_id] = step
        self.attempts[attempt.workflow_attempt_id] = attempt

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
    ) -> None:
        self.steps[workflow_step_id] = replace(
            self.steps[workflow_step_id],
            status="succeeded",
            output_payload=output_payload,
            branch_key=branch_key,
            completed_at=now,
        )
        self.attempts[workflow_attempt_id] = replace(
            self.attempts[workflow_attempt_id],
            status="succeeded",
            output_hash=output_hash,
            usage=usage,
            completed_at=now,
        )

    def skip_step(self, step: WorkflowRunStep) -> None:
        self.steps[step.workflow_step_id] = step

    def wait_for_approval(
        self,
        context: RequestContext,
        workflow_step_id: UUID,
        workflow_attempt_id: UUID,
        *,
        output_payload: dict[str, object],
        output_hash: str,
        now: datetime,
    ) -> None:
        del context
        self.steps[workflow_step_id] = replace(
            self.steps[workflow_step_id],
            status="waiting_approval",
            output_payload=output_payload,
        )
        self.attempts[workflow_attempt_id] = replace(
            self.attempts[workflow_attempt_id],
            status="succeeded",
            output_hash=output_hash,
            completed_at=now,
        )
        self.run = replace(
            self.run,
            status="waiting_approval",
            updated_at=now,
            version=self.run.version + 1,
        )

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
    ) -> WorkflowRun:
        del context, workflow_run_id
        self.run = replace(
            self.run,
            status="succeeded",
            output_payload=output_payload,
            steps_executed=steps_executed,
            model_calls=model_calls,
            retrieval_calls=retrieval_calls,
            output_bytes=output_bytes,
            updated_at=now,
            completed_at=now,
            version=self.run.version + 1,
        )
        return self.run

    def fail_step_and_run(
        self,
        context: RequestContext,
        workflow_run_id: UUID,
        *,
        workflow_step_id: UUID | None,
        workflow_attempt_id: UUID | None,
        error_code: str,
        now: datetime,
    ) -> WorkflowRun:
        del context, workflow_run_id
        if workflow_step_id is not None:
            self.steps[workflow_step_id] = replace(
                self.steps[workflow_step_id],
                status="failed",
                completed_at=now,
                error_code=error_code,
            )
        if workflow_attempt_id is not None:
            self.attempts[workflow_attempt_id] = replace(
                self.attempts[workflow_attempt_id],
                status="failed",
                completed_at=now,
                error_code=error_code,
            )
        self.run = replace(
            self.run,
            status="failed",
            error_code=error_code,
            updated_at=now,
            completed_at=now,
            version=self.run.version + 1,
        )
        return self.run


class AllowPolicy:
    """记录每个节点重新决策的请求并始终返回工作空间授权。"""

    def __init__(self) -> None:
        self.requests: list[PolicyRequest] = []

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        self.requests.append(request)
        return PolicyDecision(
            uuid4(),
            "allow",
            request.permission_code,
            request.context.workspace_id,
            ResourceScope(workspace=True),
            frozenset(),
            7,
            0,
            "synthetic_allow",
            "INTERNAL",
        )


class SyntheticKnowledge:
    """返回单条全合成证据，避免单元测试依赖真实索引。"""

    def retrieve(
        self,
        context: RequestContext,
        *,
        query: str,
        knowledge_base_ids: frozenset[UUID] | None,
        limit: int,
    ) -> WorkflowKnowledgeResult:
        del context, knowledge_base_ids, limit
        return WorkflowKnowledgeResult(
            (
                WorkflowKnowledgeItem(
                    uuid4(),
                    uuid4(),
                    uuid4(),
                    "c" * 64,
                    f"合成证据: {query}",
                    {"page": 1},
                    "INTERNAL",
                ),
            ),
            uuid4(),
            7,
            "INTERNAL",
        )


class SyntheticModels:
    """记录模型收到的受限提示词和证据，不执行真实供应商调用。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[WorkflowKnowledgeItem, ...]]] = []

    def invoke(
        self,
        context: RequestContext,
        *,
        run: WorkflowRun,
        node: WorkflowNode,
        prompt: str,
        knowledge: tuple[WorkflowKnowledgeItem, ...],
        security_level: SecurityLevel,
    ) -> WorkflowModelResult:
        del context, run, node, security_level
        self.calls.append((prompt, knowledge))
        return WorkflowModelResult("合成模型结果", 12, 5, 100, False)


def request_context() -> RequestContext:
    return RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TRACE,
        authentication_method="browser_session",
    )


def run_for(
    graph: WorkflowGraph, *, input_payload: dict[str, object]
) -> tuple[WorkflowRun, WorkflowVersion]:
    now = datetime.now(UTC)
    run = WorkflowRun(
        RUN_ID,
        WORKFLOW_ID,
        WORKSPACE_ID,
        VERSION_ID,
        ACCOUNT_ID,
        "queued",
        "synthetic-executor-run",
        "d" * 64,
        input_payload,
        TRACE.trace_id,
        TRACE.traceparent,
        now,
        now,
        None,
        None,
        1,
    )
    version = WorkflowVersion(
        VERSION_ID,
        WORKFLOW_ID,
        WORKSPACE_ID,
        1,
        1,
        graph,
        "e" * 64,
        ACCOUNT_ID,
        now,
    )
    return run, version


def test_condition_selects_one_branch_and_rechecks_each_active_node() -> None:
    graph = WorkflowGraph(
        1,
        "trigger",
        (
            WorkflowNode("trigger", "trigger", "触发", {}),
            WorkflowNode(
                "condition",
                "condition",
                "条件",
                {"source_path": "input.approved", "operator": "eq", "expected": True},
            ),
            WorkflowNode("accepted", "result", "通过", {"source_path": "steps.condition.matched"}),
            WorkflowNode("rejected", "result", "拒绝", {"source_path": "steps.condition.matched"}),
        ),
        (
            WorkflowEdge("e1", "trigger", "condition"),
            WorkflowEdge("e2", "condition", "accepted", "true"),
            WorkflowEdge("e3", "condition", "rejected", "false"),
        ),
    )
    run, version = run_for(graph, input_payload={"approved": True})
    store = MemoryExecutionStore(run, version)
    policy = AllowPolicy()
    executor = WorkflowRunExecutor(store, policy, SyntheticKnowledge(), SyntheticModels())

    result = executor.execute(request_context(), RUN_ID)

    assert result.status == "succeeded"
    assert store.run.output_payload == {"results": {"accepted": True}}
    assert sorted(step.status for step in store.steps.values()) == [
        "skipped",
        "succeeded",
        "succeeded",
        "succeeded",
    ]
    assert len(policy.requests) == 3
    assert executor.execute(request_context(), RUN_ID).claimed is False


def test_knowledge_and_model_nodes_use_bounded_context_and_usage() -> None:
    graph = WorkflowGraph(
        1,
        "trigger",
        (
            WorkflowNode("trigger", "trigger", "触发", {}),
            WorkflowNode(
                "knowledge",
                "knowledge_retrieval",
                "知识检索",
                {"query_template": "{{input.question}}", "max_results": 2},
            ),
            WorkflowNode(
                "model",
                "model",
                "模型",
                {
                    "runtime_config_version_id": str(RUNTIME_ID),
                    "prompt_template": "请回答: {{input.question}}",
                    "context_node_ids": ["knowledge"],
                    "max_output_tokens": 128,
                },
            ),
            WorkflowNode("result", "result", "结果", {"source_path": "steps.model.content"}),
        ),
        (
            WorkflowEdge("e1", "trigger", "knowledge"),
            WorkflowEdge("e2", "knowledge", "model"),
            WorkflowEdge("e3", "model", "result"),
        ),
    )
    run, version = run_for(graph, input_payload={"question": "合成问题"})
    store = MemoryExecutionStore(run, version)
    models = SyntheticModels()
    executor = WorkflowRunExecutor(store, AllowPolicy(), SyntheticKnowledge(), models)

    result = executor.execute(request_context(), RUN_ID)

    assert result.status == "succeeded"
    assert models.calls[0][0] == "请回答: 合成问题"
    assert models.calls[0][1][0].content == "合成证据: 合成问题"
    assert store.run.output_payload == {"results": {"result": "合成模型结果"}}
    assert store.run.model_calls == 1
    assert store.run.retrieval_calls == 1
    model_attempt = next(
        attempt
        for attempt in store.attempts.values()
        if store.steps[attempt.workflow_step_id].node_type == "model"
    )
    assert model_attempt.usage["output_tokens"] == 5


def test_approval_node_pauses_without_executing_result() -> None:
    graph = WorkflowGraph(
        1,
        "trigger",
        (
            WorkflowNode("trigger", "trigger", "触发", {}),
            WorkflowNode(
                "approval",
                "approval",
                "审批",
                {"risk_level": "high", "subject_path": "input.request_id"},
            ),
            WorkflowNode("result", "result", "结果", {}),
        ),
        (
            WorkflowEdge("e1", "trigger", "approval"),
            WorkflowEdge("e2", "approval", "result"),
        ),
    )
    run, version = run_for(graph, input_payload={"request_id": "synthetic-001"})
    store = MemoryExecutionStore(run, version)

    result = WorkflowRunExecutor(
        store,
        AllowPolicy(),
        SyntheticKnowledge(),
        SyntheticModels(),
    ).execute(request_context(), RUN_ID)

    assert result.status == "waiting_approval"
    assert store.run.status == "waiting_approval"
    assert [step.node_id for step in store.steps.values()] == ["trigger", "approval"]


def test_invalid_config_and_output_budget_fail_closed() -> None:
    invalid_graph = WorkflowGraph(
        1,
        "trigger",
        (
            WorkflowNode("trigger", "trigger", "触发", {"script": "return true"}),
            WorkflowNode("result", "result", "结果", {}),
        ),
        (WorkflowEdge("e1", "trigger", "result"),),
    )
    run, version = run_for(invalid_graph, input_payload={})
    invalid_store = MemoryExecutionStore(run, version)
    invalid = WorkflowRunExecutor(
        invalid_store,
        AllowPolicy(),
        SyntheticKnowledge(),
        SyntheticModels(),
    ).execute(request_context(), RUN_ID)
    assert invalid.error_code == "WORKFLOW_NODE_CONFIG_INVALID"
    assert invalid_store.run.status == "failed"

    valid_graph = WorkflowGraph(
        1,
        "trigger",
        (
            WorkflowNode("trigger", "trigger", "触发", {}),
            WorkflowNode("result", "result", "结果", {"source_path": "input.large"}),
        ),
        (WorkflowEdge("e1", "trigger", "result"),),
    )
    run, version = run_for(valid_graph, input_payload={"large": "x" * 2_000})
    budget_store = MemoryExecutionStore(run, version)
    exceeded = WorkflowRunExecutor(
        budget_store,
        AllowPolicy(),
        SyntheticKnowledge(),
        SyntheticModels(),
        budget=WorkflowExecutionBudget(
            max_steps=2,
            max_model_calls=1,
            max_retrieval_calls=1,
            max_elapsed_ms=30_000,
            max_step_output_bytes=2_500,
            max_total_output_bytes=3_000,
        ),
    ).execute(request_context(), RUN_ID)
    assert exceeded.error_code == "WORKFLOW_BUDGET_EXCEEDED"
    assert budget_store.run.status == "failed"
    failed_step = next(step for step in budget_store.steps.values() if step.status == "failed")
    failed_attempt = next(
        attempt for attempt in budget_store.attempts.values() if attempt.status == "failed"
    )
    assert failed_step.node_id == "result"
    assert failed_attempt.workflow_step_id == failed_step.workflow_step_id


def test_model_budget_blocks_call_before_exceeding_frozen_limit() -> None:
    graph = WorkflowGraph(
        1,
        "trigger",
        (
            WorkflowNode("trigger", "trigger", "触发", {}),
            WorkflowNode(
                "model_1",
                "model",
                "模型一",
                {
                    "runtime_config_version_id": str(RUNTIME_ID),
                    "prompt_template": "第一次: {{input.question}}",
                },
            ),
            WorkflowNode(
                "model_2",
                "model",
                "模型二",
                {
                    "runtime_config_version_id": str(RUNTIME_ID),
                    "prompt_template": "第二次: {{input.question}}",
                },
            ),
            WorkflowNode("result", "result", "结果", {"source_path": "steps.model_2.content"}),
        ),
        (
            WorkflowEdge("e1", "trigger", "model_1"),
            WorkflowEdge("e2", "model_1", "model_2"),
            WorkflowEdge("e3", "model_2", "result"),
        ),
    )
    run, version = run_for(graph, input_payload={"question": "合成预算问题"})
    store = MemoryExecutionStore(run, version)
    models = SyntheticModels()
    result = WorkflowRunExecutor(
        store,
        AllowPolicy(),
        SyntheticKnowledge(),
        models,
        budget=WorkflowExecutionBudget(
            max_steps=4,
            max_model_calls=1,
            max_retrieval_calls=1,
            max_elapsed_ms=30_000,
            max_step_output_bytes=64 * 1024,
            max_total_output_bytes=256 * 1024,
        ),
    ).execute(request_context(), RUN_ID)

    assert result.error_code == "WORKFLOW_BUDGET_EXCEEDED"
    assert len(models.calls) == 1
    assert {step.node_id for step in store.steps.values()} == {"trigger", "model_1"}
