"""按冻结 DAG 执行受限节点，并在每一步重新校验权限与预算。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from ai_platform_backend.observability import observed_operation

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.fields import (
    SECURITY_LEVEL_RANK,
    SecurityLevel,
)
from ai_platform_api.modules.authorization.domain.policy import (
    PolicyDecision,
    PolicyDecisionPoint,
    PolicyRequest,
    ResourceReference,
)
from ai_platform_api.modules.model_gateway.application.context import (
    AuthorizedModelContextBuilder,
)
from ai_platform_api.modules.model_gateway.application.runtime_gateway import (
    RuntimeModelGatewayService,
)
from ai_platform_api.modules.model_gateway.domain.models import (
    ModelMessage,
    ModelRequest,
)
from ai_platform_api.modules.model_gateway.domain.runtime import (
    RuntimeConfigurationReader,
)
from ai_platform_api.modules.workflow.domain.approval_runtime import (
    ApprovalRuntimeState,
    create_approval_runtime,
)
from ai_platform_api.modules.workflow.domain.approvals import (
    ApprovalChain,
    ApprovalRiskLevel,
    ApprovalSubject,
)
from ai_platform_api.modules.workflow.domain.execution import (
    DEFAULT_WORKFLOW_EXECUTION_BUDGET,
    WORKFLOW_EXECUTOR_VERSION,
    WorkflowExecutionBudget,
    WorkflowExecutionStore,
    WorkflowKnowledgeItem,
    WorkflowKnowledgeRetriever,
    WorkflowModelInvoker,
    WorkflowModelResult,
    WorkflowNodeAttempt,
    WorkflowRunStep,
)
from ai_platform_api.modules.workflow.domain.models import (
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
)

_TEMPLATE_PATTERN = re.compile(r"{{\s*([a-z][a-z0-9_.-]{0,255})\s*}}")
_PATH_PATTERN = re.compile(r"^(?:input|steps)(?:\.[a-zA-Z0-9_-]+)+$")
_CONDITION_OPERATORS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "exists"})
_SECURITY_LEVELS: tuple[SecurityLevel, ...] = (
    "PUBLIC",
    "INTERNAL",
    "CONFIDENTIAL",
    "RESTRICTED",
)


class WorkflowNodeConfigError(PlatformError):
    """节点配置不满足固定 Schema，禁止在运行期解释任意表达式。"""

    error_code = "WORKFLOW_NODE_CONFIG_INVALID"
    http_status = 422


class WorkflowBudgetExceededError(PlatformError):
    """运行达到冻结预算后失败关闭，不允许通过重试扩展资源上限。"""

    error_code = "WORKFLOW_BUDGET_EXCEEDED"
    http_status = 409


class WorkflowNodeExecutionError(PlatformError):
    """节点 Adapter 返回无效结果或发生未知失败时使用稳定错误码。"""

    error_code = "WORKFLOW_NODE_EXECUTION_FAILED"
    http_status = 500


class WorkflowNodeDeniedError(PlatformError):
    """运行中权限被撤销时立即停止后续节点。"""

    error_code = "POLICY_DENIED"
    http_status = 403


@dataclass(frozen=True)
class WorkflowRunExecutionResult:
    """返回本次调度是否取得执行权及数据库中的最终或等待状态。"""

    claimed: bool
    status: str | None
    error_code: str | None = None


@dataclass(frozen=True)
class _NodeOutcome:
    """统一六类节点的输出、分支、等待和用量语义。"""

    output: dict[str, object]
    branch_key: str | None = None
    waiting_approval: bool = False
    usage: dict[str, object] | None = None
    approval_state: ApprovalRuntimeState | None = None


@dataclass
class _ExecutionState:
    """保存单次调度的计数和活动事实标识，数据库 Run 仍是跨请求事实来源。"""

    steps_executed: int = 0
    model_calls: int = 0
    retrieval_calls: int = 0
    output_bytes: int = 0
    active_step_id: UUID | None = None
    active_attempt_id: UUID | None = None


class WorkflowApprovalResolver(Protocol):
    """按当前组织与冻结策略版本解析审批链，执行器不直接读取策略表。"""

    def preview_chain(
        self,
        context: RequestContext,
        *,
        subject: ApprovalSubject,
    ) -> ApprovalChain: ...


class GovernedWorkflowModelInvoker:
    """复用模型网关、安全上下文和冻结配置执行 Model 节点。"""

    def __init__(
        self,
        configurations: RuntimeConfigurationReader,
        gateway: RuntimeModelGatewayService,
        context_builder: AuthorizedModelContextBuilder,
    ) -> None:
        self._configurations = configurations
        self._gateway = gateway
        self._context_builder = context_builder

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
        """把检索正文作为非可信证据包装，节点不能绕过网关直接选择 Provider。"""

        # 1. 只读取节点冻结的运行配置，并把节点 Token 上限限制在平台策略以内。
        runtime_config_version_id = _uuid_config(node, "runtime_config_version_id")
        configuration = self._configurations.get(runtime_config_version_id)
        if configuration is None:
            raise WorkflowNodeConfigError
        requested_tokens = _integer_config(
            node,
            "max_output_tokens",
            default=configuration.policy.max_output_tokens,
            minimum=1,
            maximum=configuration.policy.max_output_tokens,
        )
        # 2. 知识正文按非可信证据进入安全上下文，最终调用仍由模型网关执行数据政策和成本门禁。
        messages: list[ModelMessage] = [
            ModelMessage("system", configuration.system_prompt_template)
        ]
        for item in knowledge:
            messages.append(
                self._context_builder.build_untrusted_evidence_message(
                    resource_type="chunk",
                    payload={
                        "content": item.content,
                        "source_position": item.source_position,
                    },
                    field_mask=context.authorized_field_mask,
                )
            )
        messages.append(self._context_builder.build_user_message(prompt))
        result = self._gateway.invoke(
            ModelRequest(
                invocation_id=uuid5(
                    NAMESPACE_URL,
                    f"workflow-run:{run.workflow_run_id}:node:{node.node_id}",
                ),
                workspace_id=run.workspace_id,
                trace_id=run.trace_id,
                traceparent=run.traceparent,
                task_type="workflow.model",
                messages=tuple(messages),
                required_capabilities=frozenset({"generation"}),
                max_output_tokens=requested_tokens,
                external_data_allowed=_boolean_config(
                    node,
                    "external_data_allowed",
                    default=False,
                ),
                security_level=security_level,
            ),
            runtime_config_version_id=runtime_config_version_id,
        )
        return WorkflowModelResult(
            content=result.content.strip(),
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            estimated_cost_microunits=result.estimated_cost_microunits,
            degraded=result.degraded,
        )


class WorkflowRunExecutor:
    """认领排队 Run，按确定性拓扑执行固定节点集合并保存每步事实。"""

    def __init__(
        self,
        store: WorkflowExecutionStore,
        policy: PolicyDecisionPoint,
        knowledge: WorkflowKnowledgeRetriever,
        models: WorkflowModelInvoker,
        *,
        approvals: WorkflowApprovalResolver | None = None,
        budget: WorkflowExecutionBudget = DEFAULT_WORKFLOW_EXECUTION_BUDGET,
    ) -> None:
        self._store = store
        self._policy = policy
        self._knowledge = knowledge
        self._models = models
        self._approvals = approvals
        self._budget = budget

    @observed_operation(component="workflow", operation="execute")
    def execute(
        self,
        context: RequestContext,
        workflow_run_id: UUID,
    ) -> WorkflowRunExecutionResult:
        """执行一次非持久调度；数据库条件认领保证重复调用不会重复执行节点。"""

        # 1. 条件认领同时取得冻结版本、历史步骤和累计预算，审批恢复不会重置执行事实。
        claim = self._store.claim(
            context,
            workflow_run_id,
            executor_version=WORKFLOW_EXECUTOR_VERSION,
            budget=self._budget,
            now=datetime.now(UTC),
        )
        if claim is None:
            return WorkflowRunExecutionResult(False, None)
        state = _ExecutionState(
            steps_executed=claim.run.steps_executed,
            model_calls=claim.run.model_calls,
            retrieval_calls=claim.run.retrieval_calls,
            output_bytes=claim.run.output_bytes,
        )
        # 2. 节点异常统一收敛为稳定错误码，并由 Store 原子关闭当前 Step、Attempt 与 Run。
        try:
            _validate_executable_graph(claim.version.graph)
            status = self._execute_graph(
                context,
                claim.run,
                claim.version.graph,
                claim.budget,
                claim.steps,
                state,
            )
            return WorkflowRunExecutionResult(True, status)
        except Exception as error:
            error_code = _stable_error_code(error)
            failed = self._store.fail_step_and_run(
                context,
                workflow_run_id,
                workflow_step_id=state.active_step_id,
                workflow_attempt_id=state.active_attempt_id,
                error_code=error_code,
                now=datetime.now(UTC),
            )
            return WorkflowRunExecutionResult(True, failed.status, error_code)

    def _execute_graph(
        self,
        context: RequestContext,
        run: WorkflowRun,
        graph: WorkflowGraph,
        budget: WorkflowExecutionBudget,
        existing_steps: tuple[WorkflowRunStep, ...],
        state: _ExecutionState,
    ) -> str:
        """按拓扑顺序传播活动边；未被分支选择的节点会留下 skipped 事实。"""

        # 长函数保留原因: 活动边传播、步骤事实和预算计数必须在同一顺序循环中保持一致。
        started = monotonic()
        nodes = {node.node_id: node for node in graph.nodes}
        incoming, outgoing = _edge_maps(graph)
        outputs: dict[str, dict[str, object]] = {}
        active_nodes = {graph.entry_node_id}
        selected_edges: set[str] = set()
        result_outputs: dict[str, object] = {}
        steps_by_node = {step.node_id: step for step in existing_steps}
        if len(steps_by_node) != len(existing_steps) or any(
            node_id not in nodes for node_id in steps_by_node
        ):
            raise WorkflowNodeExecutionError
        # 1. 先回放已提交步骤以重建活动边和输出；恢复执行不能再次调用历史模型或检索节点。
        for sequence_no, node_id in enumerate(_topological_order(graph), start=1):
            node = nodes[node_id]
            existing = steps_by_node.get(node_id)
            if existing is not None:
                if existing.sequence_no != sequence_no:
                    raise WorkflowNodeExecutionError
                if existing.status == "skipped":
                    if node_id in active_nodes:
                        raise WorkflowNodeExecutionError
                    continue
                if (
                    existing.status != "succeeded"
                    or existing.output_payload is None
                    or node_id not in active_nodes
                ):
                    raise WorkflowNodeExecutionError
                outputs[node_id] = existing.output_payload
                if node.node_type == "result":
                    result_outputs[node_id] = existing.output_payload.get("value")
                for edge in _select_edges(node, outgoing[node_id], existing.branch_key):
                    selected_edges.add(edge.edge_id)
                    active_nodes.add(edge.target_node_id)
                continue
            if node_id not in active_nodes:
                self._store.skip_step(_skipped_step(run, node, sequence_no, datetime.now(UTC)))
                continue
            _require_next_node_budget(state, budget, started, node.node_type)
            decision = self._node_policy_decision(context, run, node)
            dependencies = _dependency_outputs(node_id, incoming, selected_edges, outputs)
            step_input = {"input": run.input_payload, "dependencies": dependencies}
            state.active_step_id = uuid4()
            state.active_attempt_id = uuid4()
            now = datetime.now(UTC)
            self._store.start_step(
                WorkflowRunStep(
                    workflow_step_id=state.active_step_id,
                    workflow_run_id=run.workflow_run_id,
                    workspace_id=run.workspace_id,
                    node_id=node.node_id,
                    node_type=node.node_type,
                    sequence_no=sequence_no,
                    status="running",
                    input_payload=step_input,
                    output_payload=None,
                    branch_key=None,
                    policy_decision_id=decision.decision_id,
                    policy_version=decision.policy_version,
                    started_at=now,
                    completed_at=None,
                    error_code=None,
                ),
                WorkflowNodeAttempt(
                    workflow_attempt_id=state.active_attempt_id,
                    workflow_step_id=state.active_step_id,
                    workflow_run_id=run.workflow_run_id,
                    workspace_id=run.workspace_id,
                    attempt_no=1,
                    executor_version=WORKFLOW_EXECUTOR_VERSION,
                    status="running",
                    input_hash=_payload_hash(step_input),
                    output_hash=None,
                    usage={},
                    started_at=now,
                    completed_at=None,
                    error_code=None,
                ),
            )

            # 2. Adapter 只接收已冻结节点和上游输出，任何任意脚本或外部写动作均无执行入口。
            outcome = self._execute_node(context, run, node, dependencies, outputs, state)
            output_hash, output_size = _bounded_output(outcome.output, budget)
            state.steps_executed += 1
            state.output_bytes += output_size
            _require_budget(state, budget, started)
            if outcome.waiting_approval:
                if outcome.approval_state is None:
                    raise WorkflowNodeExecutionError
                self._store.wait_for_approval(
                    context,
                    state.active_step_id,
                    state.active_attempt_id,
                    approval_state=outcome.approval_state,
                    output_payload=outcome.output,
                    output_hash=output_hash,
                    steps_executed=state.steps_executed,
                    model_calls=state.model_calls,
                    retrieval_calls=state.retrieval_calls,
                    output_bytes=state.output_bytes,
                    now=datetime.now(UTC),
                )
                state.active_step_id = None
                state.active_attempt_id = None
                return "waiting_approval"
            self._store.complete_step(
                state.active_step_id,
                state.active_attempt_id,
                output_payload=outcome.output,
                branch_key=outcome.branch_key,
                output_hash=output_hash,
                usage=outcome.usage or {},
                now=datetime.now(UTC),
            )
            outputs[node_id] = outcome.output
            if node.node_type == "result":
                result_outputs[node_id] = outcome.output.get("value")

            # 3. Condition 只激活匹配分支或 default，其余节点仍按拓扑留下 skipped 事实。
            for edge in _select_edges(node, outgoing[node_id], outcome.branch_key):
                selected_edges.add(edge.edge_id)
                active_nodes.add(edge.target_node_id)
            state.active_step_id = None
            state.active_attempt_id = None

        if not result_outputs:
            raise WorkflowNodeExecutionError
        final_output: dict[str, object] = {"results": result_outputs}
        _, final_size = _bounded_output(final_output, budget)
        if final_size > budget.max_total_output_bytes:
            raise WorkflowBudgetExceededError
        completed = self._store.complete_run(
            context,
            run.workflow_run_id,
            output_payload=final_output,
            steps_executed=state.steps_executed,
            model_calls=state.model_calls,
            retrieval_calls=state.retrieval_calls,
            output_bytes=state.output_bytes,
            now=datetime.now(UTC),
        )
        return completed.status

    def _node_policy_decision(
        self,
        context: RequestContext,
        run: WorkflowRun,
        node: WorkflowNode,
    ) -> PolicyDecision:
        risk_level = node.config.get("risk_level", "normal")
        decision = self._policy.decide(
            PolicyRequest(
                context,
                "workflow.run.create",
                ResourceReference(
                    "workflow_definition",
                    run.workflow_id,
                    run.workspace_id,
                    {
                        "account_id": run.requested_by_account_id,
                        "node_type": node.node_type,
                        "risk_level": risk_level,
                    },
                ),
            )
        )
        if not decision.allowed:
            raise WorkflowNodeDeniedError
        return decision

    def _execute_node(
        self,
        context: RequestContext,
        run: WorkflowRun,
        node: WorkflowNode,
        dependencies: dict[str, dict[str, object]],
        outputs: dict[str, dict[str, object]],
        state: _ExecutionState,
    ) -> _NodeOutcome:
        # 1. Trigger、Condition 和 Result 都是确定性的本地转换，不提供脚本解释或任意动作入口。
        root: dict[str, object] = {"input": run.input_payload, "steps": outputs}
        if node.node_type == "trigger":
            return _NodeOutcome({"input": run.input_payload})
        if node.node_type == "condition":
            matched = _evaluate_condition(node, root)
            return _NodeOutcome({"matched": matched}, "true" if matched else "false")
        # 2. 只有检索与模型节点能触发受控 Adapter，调用次数由冻结预算在进入本方法前预留。
        if node.node_type == "knowledge_retrieval":
            state.retrieval_calls += 1
            query = _render_template(_string_config(node, "query_template"), root)
            result = self._knowledge.retrieve(
                context,
                query=query,
                knowledge_base_ids=_uuid_set_config(node, "knowledge_base_ids"),
                limit=_integer_config(node, "max_results", default=3, minimum=1, maximum=5),
            )
            return _NodeOutcome(
                _knowledge_output(query, result.items, result.maximum_security_level),
                usage={"retrieval_items": len(result.items)},
            )
        if node.node_type == "model":
            state.model_calls += 1
            prompt = _render_template(_string_config(node, "prompt_template"), root)
            knowledge = _knowledge_context(node, outputs)
            security_level = _maximum_security_level(knowledge)
            model = self._models.invoke(
                context,
                run=run,
                node=node,
                prompt=prompt,
                knowledge=knowledge,
                security_level=security_level,
            )
            return _NodeOutcome(
                {"content": model.content, "degraded": model.degraded},
                usage={
                    "input_tokens": model.input_tokens,
                    "output_tokens": model.output_tokens,
                    "estimated_cost_microunits": model.estimated_cost_microunits,
                },
            )
        # 3. Approval 只冻结审批实例并暂停 Run；Result 仍是无外部副作用的确定性投影。
        if node.node_type == "approval":
            if self._approvals is None or state.active_step_id is None:
                raise WorkflowNodeConfigError
            subject_value = _optional_path_value(node, "subject_path", root)
            fields = _approval_fields(subject_value)
            approval_subject = ApprovalSubject(
                run.workspace_id,
                run.requested_by_account_id,
                _optional_string_config(node, "resource_type", "workflow.approval"),
                _optional_string_config(node, "operation", "submit"),
                run.workflow_run_id,
                _uuid_tuple_config(node, "department_ids"),
                _approval_security_level(node),
                cast(ApprovalRiskLevel, node.config.get("risk_level", "normal")),
                fields,
            )
            chain = self._approvals.preview_chain(context, subject=approval_subject)
            approval_state = create_approval_runtime(
                chain,
                approval_subject,
                idempotency_key=f"workflow:{run.workflow_run_id}:{node.node_id}",
                trace_id=run.trace_id,
                traceparent=run.traceparent,
                now=datetime.now(UTC),
                workflow_run_id=run.workflow_run_id,
                workflow_step_id=state.active_step_id,
            )
            return _NodeOutcome(
                {
                    "risk_level": node.config.get("risk_level", "normal"),
                    "approval_instance_id": str(approval_state.instance.approval_instance_id),
                    "chain_digest": approval_state.instance.chain_digest,
                    "dependency_node_ids": sorted(dependencies),
                },
                waiting_approval=True,
                approval_state=approval_state,
            )
        if node.node_type == "result":
            source_path = node.config.get("source_path")
            value = _path_value(root, source_path) if isinstance(source_path, str) else dependencies
            return _NodeOutcome({"value": value})
        raise WorkflowNodeConfigError


def _validate_executable_graph(graph: WorkflowGraph) -> None:
    """验证节点配置和分支语义；只接受固定字段，拒绝表达式、脚本和外部动作。"""

    outgoing: dict[str, list[WorkflowEdge]] = {node.node_id: [] for node in graph.nodes}
    for edge in graph.edges:
        outgoing[edge.source_node_id].append(edge)
    for node in graph.nodes:
        allowed, required = _config_contract(node.node_type)
        if set(node.config) - allowed or not required.issubset(node.config):
            raise WorkflowNodeConfigError
        if node.node_type == "condition":
            _validate_condition_config(node)
            keys = [edge.condition_key for edge in outgoing[node.node_id]]
            if not keys or any(key not in {"true", "false", "default"} for key in keys):
                raise WorkflowNodeConfigError
            if len(keys) != len(set(keys)):
                raise WorkflowNodeConfigError
        elif any(edge.condition_key is not None for edge in outgoing[node.node_id]):
            raise WorkflowNodeConfigError
        _validate_node_values(node)


def _config_contract(node_type: str) -> tuple[set[str], set[str]]:
    contracts = {
        "trigger": (set(), set()),
        "condition": ({"source_path", "operator", "expected"}, {"source_path", "operator"}),
        "knowledge_retrieval": (
            {"query_template", "knowledge_base_ids", "max_results"},
            {"query_template"},
        ),
        "model": (
            {
                "runtime_config_version_id",
                "prompt_template",
                "context_node_ids",
                "max_output_tokens",
                "external_data_allowed",
            },
            {"runtime_config_version_id", "prompt_template"},
        ),
        "approval": (
            {
                "risk_level",
                "subject_path",
                "resource_type",
                "operation",
                "department_ids",
                "security_level",
            },
            set(),
        ),
        "result": ({"source_path"}, set()),
    }
    try:
        return contracts[node_type]
    except KeyError as error:
        raise WorkflowNodeConfigError from error


def _validate_node_values(node: WorkflowNode) -> None:
    if node.node_type in {"knowledge_retrieval", "model"}:
        key = "query_template" if node.node_type == "knowledge_retrieval" else "prompt_template"
        _string_config(node, key)
    if node.node_type == "knowledge_retrieval":
        _uuid_set_config(node, "knowledge_base_ids")
        _integer_config(node, "max_results", default=3, minimum=1, maximum=5)
    if node.node_type == "model":
        _uuid_config(node, "runtime_config_version_id")
        _integer_config(node, "max_output_tokens", default=1, minimum=1, maximum=16_384)
        _boolean_config(node, "external_data_allowed", default=False)
        context_node_ids = node.config.get("context_node_ids", [])
        if not isinstance(context_node_ids, list) or any(
            not isinstance(item, str) for item in context_node_ids
        ):
            raise WorkflowNodeConfigError
    if node.node_type == "approval":
        if node.config.get("risk_level", "normal") not in {"normal", "high", "critical"}:
            raise WorkflowNodeConfigError
        _optional_path_config(node, "subject_path")
        _optional_string_config(node, "resource_type", "workflow.approval")
        _optional_string_config(node, "operation", "submit")
        _uuid_tuple_config(node, "department_ids")
        _approval_security_level(node)
    if node.node_type == "result":
        _optional_path_config(node, "source_path")


def _validate_condition_config(node: WorkflowNode) -> None:
    _required_path_config(node, "source_path")
    operator = node.config.get("operator")
    if operator not in _CONDITION_OPERATORS:
        raise WorkflowNodeConfigError
    if operator != "exists" and "expected" not in node.config:
        raise WorkflowNodeConfigError


def _topological_order(graph: WorkflowGraph) -> tuple[str, ...]:
    incoming, outgoing = _edge_maps(graph)
    indegree = {node_id: len(edges) for node_id, edges in incoming.items()}
    ready = sorted(node_id for node_id, count in indegree.items() if count == 0)
    result: list[str] = []
    while ready:
        node_id = ready.pop(0)
        result.append(node_id)
        for edge in outgoing[node_id]:
            indegree[edge.target_node_id] -= 1
            if indegree[edge.target_node_id] == 0:
                ready.append(edge.target_node_id)
                ready.sort()
    if len(result) != len(graph.nodes):
        raise WorkflowNodeConfigError
    return tuple(result)


def _edge_maps(
    graph: WorkflowGraph,
) -> tuple[dict[str, list[WorkflowEdge]], dict[str, list[WorkflowEdge]]]:
    incoming: dict[str, list[WorkflowEdge]] = {node.node_id: [] for node in graph.nodes}
    outgoing: dict[str, list[WorkflowEdge]] = {node.node_id: [] for node in graph.nodes}
    for edge in sorted(graph.edges, key=lambda item: item.edge_id):
        incoming[edge.target_node_id].append(edge)
        outgoing[edge.source_node_id].append(edge)
    return incoming, outgoing


def _select_edges(
    node: WorkflowNode,
    outgoing: list[WorkflowEdge],
    branch_key: str | None,
) -> tuple[WorkflowEdge, ...]:
    if node.node_type != "condition":
        return tuple(outgoing)
    selected = tuple(edge for edge in outgoing if edge.condition_key == branch_key)
    if selected:
        return selected
    return tuple(edge for edge in outgoing if edge.condition_key == "default")


def _dependency_outputs(
    node_id: str,
    incoming: dict[str, list[WorkflowEdge]],
    selected_edges: set[str],
    outputs: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    return {
        edge.source_node_id: outputs[edge.source_node_id]
        for edge in incoming[node_id]
        if edge.edge_id in selected_edges and edge.source_node_id in outputs
    }


def _evaluate_condition(node: WorkflowNode, root: dict[str, object]) -> bool:
    actual = _path_value(root, cast(str, node.config["source_path"]))
    operator = cast(str, node.config["operator"])
    expected = node.config.get("expected")
    if operator == "exists":
        return actual is not None
    if operator == "eq":
        return actual == expected
    if operator == "ne":
        return actual != expected
    if operator in {"gt", "gte", "lt", "lte"}:
        if not isinstance(actual, (int, float)) or isinstance(actual, bool):
            return False
        if not isinstance(expected, (int, float)) or isinstance(expected, bool):
            return False
        return {
            "gt": actual > expected,
            "gte": actual >= expected,
            "lt": actual < expected,
            "lte": actual <= expected,
        }[operator]
    if operator == "in":
        return isinstance(expected, list) and actual in expected
    if operator == "contains":
        return isinstance(actual, (str, list, dict)) and expected in actual
    raise WorkflowNodeConfigError


def _render_template(template: str, root: dict[str, object]) -> str:
    if len(template) > 8_000:
        raise WorkflowNodeConfigError

    def replace(match: re.Match[str]) -> str:
        value = _path_value(root, match.group(1))
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    rendered = _TEMPLATE_PATTERN.sub(replace, template).strip()
    if not rendered or len(rendered) > 16_000 or "{{" in rendered or "}}" in rendered:
        raise WorkflowNodeConfigError
    return rendered


def _path_value(root: dict[str, object], path: object) -> object:
    if not isinstance(path, str) or not _PATH_PATTERN.fullmatch(path):
        raise WorkflowNodeConfigError
    value: object = root
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise WorkflowNodeConfigError
        value = value[part]
    return value


def _optional_path_value(
    node: WorkflowNode,
    key: str,
    root: dict[str, object],
) -> object | None:
    path = node.config.get(key)
    return _path_value(root, path) if path is not None else None


def _required_path_config(node: WorkflowNode, key: str) -> str:
    value = node.config.get(key)
    if not isinstance(value, str) or not _PATH_PATTERN.fullmatch(value):
        raise WorkflowNodeConfigError
    return value


def _optional_path_config(node: WorkflowNode, key: str) -> str | None:
    if key not in node.config:
        return None
    return _required_path_config(node, key)


def _string_config(node: WorkflowNode, key: str) -> str:
    value = node.config.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > 8_000:
        raise WorkflowNodeConfigError
    return value


def _optional_string_config(node: WorkflowNode, key: str, default: str) -> str:
    value = node.config.get(key, default)
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise WorkflowNodeConfigError
    return value.strip()


def _uuid_tuple_config(node: WorkflowNode, key: str) -> tuple[UUID, ...]:
    value = node.config.get(key, [])
    if not isinstance(value, list) or len(value) > 100:
        raise WorkflowNodeConfigError
    try:
        result = tuple(sorted((UUID(str(item)) for item in value), key=lambda item: item.int))
    except (TypeError, ValueError) as error:
        raise WorkflowNodeConfigError from error
    if len(result) != len(set(result)):
        raise WorkflowNodeConfigError
    return result


def _approval_security_level(node: WorkflowNode) -> SecurityLevel:
    value = node.config.get("security_level", "PUBLIC")
    if value not in _SECURITY_LEVELS:
        raise WorkflowNodeConfigError
    return cast(SecurityLevel, value)


def _approval_fields(value: object | None) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise WorkflowNodeConfigError
        return cast(dict[str, object], value)
    return {"value": value}


def _integer_config(
    node: WorkflowNode,
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = node.config.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise WorkflowNodeConfigError
    return value


def _boolean_config(node: WorkflowNode, key: str, *, default: bool) -> bool:
    value = node.config.get(key, default)
    if not isinstance(value, bool):
        raise WorkflowNodeConfigError
    return value


def _uuid_config(node: WorkflowNode, key: str) -> UUID:
    value = node.config.get(key)
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as error:
        raise WorkflowNodeConfigError from error


def _uuid_set_config(node: WorkflowNode, key: str) -> frozenset[UUID] | None:
    value = node.config.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not value or len(value) > 20:
        raise WorkflowNodeConfigError
    try:
        result = frozenset(UUID(str(item)) for item in value)
    except (TypeError, ValueError) as error:
        raise WorkflowNodeConfigError from error
    if len(result) != len(value):
        raise WorkflowNodeConfigError
    return result


def _knowledge_context(
    node: WorkflowNode,
    outputs: dict[str, dict[str, object]],
) -> tuple[WorkflowKnowledgeItem, ...]:
    node_ids = node.config.get("context_node_ids", [])
    if not isinstance(node_ids, list):
        raise WorkflowNodeConfigError
    items: list[WorkflowKnowledgeItem] = []
    for node_id in node_ids:
        if not isinstance(node_id, str):
            raise WorkflowNodeConfigError
        output = outputs.get(node_id)
        if output is None or output.get("kind") != "knowledge":
            raise WorkflowNodeConfigError
        for raw in cast(list[dict[str, object]], output.get("items", [])):
            items.append(
                WorkflowKnowledgeItem(
                    chunk_id=UUID(str(raw["chunk_id"])),
                    document_id=UUID(str(raw["document_id"])),
                    document_version_id=UUID(str(raw["document_version_id"])),
                    content_hash=str(raw["content_hash"]),
                    content=str(raw["content"]),
                    source_position=cast(dict[str, object], raw["source_position"]),
                    security_level=cast(SecurityLevel, raw["security_level"]),
                )
            )
    return tuple(items)


def _knowledge_output(
    query: str,
    items: tuple[WorkflowKnowledgeItem, ...],
    maximum_security_level: SecurityLevel,
) -> dict[str, object]:
    return {
        "kind": "knowledge",
        "query": query,
        "maximum_security_level": maximum_security_level,
        "items": [
            {
                "chunk_id": str(item.chunk_id),
                "document_id": str(item.document_id),
                "document_version_id": str(item.document_version_id),
                "content_hash": item.content_hash,
                "content": item.content,
                "source_position": item.source_position,
                "security_level": item.security_level,
            }
            for item in items
        ],
    }


def _maximum_security_level(
    items: tuple[WorkflowKnowledgeItem, ...],
) -> SecurityLevel:
    return max(
        (item.security_level for item in items),
        default="PUBLIC",
        key=SECURITY_LEVEL_RANK.__getitem__,
    )


def _require_budget(
    state: _ExecutionState,
    budget: WorkflowExecutionBudget,
    started: float,
) -> None:
    elapsed_ms = int((monotonic() - started) * 1000)
    if (
        state.steps_executed > budget.max_steps
        or state.model_calls > budget.max_model_calls
        or state.retrieval_calls > budget.max_retrieval_calls
        or state.output_bytes > budget.max_total_output_bytes
        or elapsed_ms > budget.max_elapsed_ms
    ):
        raise WorkflowBudgetExceededError


def _require_next_node_budget(
    state: _ExecutionState,
    budget: WorkflowExecutionBudget,
    started: float,
    node_type: str,
) -> None:
    """在产生下一次节点或外部调用前检查容量，避免先超额调用再失败。"""

    _require_budget(state, budget, started)
    if state.steps_executed >= budget.max_steps:
        raise WorkflowBudgetExceededError
    if node_type == "model" and state.model_calls >= budget.max_model_calls:
        raise WorkflowBudgetExceededError
    if node_type == "knowledge_retrieval" and state.retrieval_calls >= budget.max_retrieval_calls:
        raise WorkflowBudgetExceededError


def _bounded_output(
    payload: dict[str, object],
    budget: WorkflowExecutionBudget,
) -> tuple[str, int]:
    try:
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError) as error:
        raise WorkflowNodeExecutionError from error
    if len(encoded) > budget.max_step_output_bytes:
        raise WorkflowBudgetExceededError
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def _payload_hash(payload: dict[str, object]) -> str:
    try:
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError) as error:
        raise WorkflowNodeExecutionError from error
    return hashlib.sha256(encoded).hexdigest()


def _skipped_step(
    run: WorkflowRun,
    node: WorkflowNode,
    sequence_no: int,
    now: datetime,
) -> WorkflowRunStep:
    return WorkflowRunStep(
        workflow_step_id=uuid4(),
        workflow_run_id=run.workflow_run_id,
        workspace_id=run.workspace_id,
        node_id=node.node_id,
        node_type=node.node_type,
        sequence_no=sequence_no,
        status="skipped",
        input_payload=None,
        output_payload=None,
        branch_key=None,
        policy_decision_id=None,
        policy_version=None,
        started_at=None,
        completed_at=now,
        error_code=None,
    )


def _stable_error_code(error: Exception) -> str:
    if isinstance(error, PlatformError):
        return error.error_code
    if isinstance(error, (TypeError, ValueError, KeyError)):
        return WorkflowNodeConfigError.error_code
    return WorkflowNodeExecutionError.error_code
