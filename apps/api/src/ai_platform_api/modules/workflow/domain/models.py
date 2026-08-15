"""定义工作流草稿、不可变版本、发布指针、运行事实和 DAG 校验规则。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter

from ai_platform_api.modules.integration.domain.events import OutboxWriter

WorkflowNodeType = Literal[
    "trigger",
    "condition",
    "knowledge_retrieval",
    "model",
    "approval",
    "result",
]
WorkflowStatus = Literal["active", "archived"]
WorkflowRunStatus = Literal[
    "queued",
    "running",
    "waiting_approval",
    "succeeded",
    "failed",
    "cancelled",
]

WORKFLOW_NODE_TYPES = frozenset(
    {
        "trigger",
        "condition",
        "knowledge_retrieval",
        "model",
        "approval",
        "result",
    }
)
NODE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
MAX_WORKFLOW_NODES = 100
MAX_WORKFLOW_EDGES = 200


@dataclass(frozen=True)
class WorkflowNode:
    """描述工作流图中的稳定节点身份；节点执行语义由后续执行器版本解释。"""

    node_id: str
    node_type: WorkflowNodeType
    name: str
    config: dict[str, object]


@dataclass(frozen=True)
class WorkflowEdge:
    """描述两个节点之间的有向连接和可选条件分支标识。"""

    edge_id: str
    source_node_id: str
    target_node_id: str
    condition_key: str | None = None


@dataclass(frozen=True)
class WorkflowGraph:
    """冻结可序列化 DAG；首期图版本独立于节点执行器版本演进。"""

    schema_version: int
    entry_node_id: str
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...]


@dataclass(frozen=True)
class WorkflowGraphViolation:
    """提供稳定校验原因码和定位信息，避免向调用方暴露内部异常。"""

    code: str
    node_id: str | None = None
    edge_id: str | None = None


@dataclass(frozen=True)
class WorkflowDefinition:
    """表示工作空间内可命名、归档并指向当前发布版本的工作流。"""

    workflow_id: UUID
    workspace_id: UUID
    name: str
    description: str | None
    status: WorkflowStatus
    current_version_id: UUID | None
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class WorkflowDraft:
    """保存可修改图和乐观锁修订号；发布时会复制为不可变版本。"""

    workflow_id: UUID
    workspace_id: UUID
    revision: int
    graph: WorkflowGraph
    graph_digest: str
    validation_errors: tuple[WorkflowGraphViolation, ...]
    updated_by_account_id: UUID
    updated_at: datetime


@dataclass(frozen=True)
class WorkflowVersion:
    """冻结一次已校验发布图，历史版本不得更新或删除。"""

    workflow_version_id: UUID
    workflow_id: UUID
    workspace_id: UUID
    version_number: int
    source_draft_revision: int
    graph: WorkflowGraph
    graph_digest: str
    published_by_account_id: UUID
    published_at: datetime


@dataclass(frozen=True)
class WorkflowPublication:
    """记录工作流当前版本指针和单调递增发布代次。"""

    workflow_id: UUID
    workspace_id: UUID
    workflow_version_id: UUID
    generation: int
    published_by_account_id: UUID
    published_at: datetime


@dataclass(frozen=True)
class WorkflowRun:
    """记录一次运行冻结的工作流版本、输入、执行预算和最终输出。"""

    workflow_run_id: UUID
    workflow_id: UUID
    workspace_id: UUID
    workflow_version_id: UUID
    requested_by_account_id: UUID
    status: WorkflowRunStatus
    idempotency_key: str
    request_hash: str
    input_payload: dict[str, object]
    trace_id: str
    traceparent: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    error_code: str | None
    version: int
    output_payload: dict[str, object] | None = None
    executor_version: str | None = None
    execution_budget: dict[str, int] | None = None
    steps_executed: int = 0
    model_calls: int = 0
    retrieval_calls: int = 0
    output_bytes: int = 0


class WorkflowWriteConflictError(Exception):
    """数据库唯一约束、乐观锁或发布指针竞争拒绝写入。"""

    def __init__(self, reason: Literal["revision", "idempotency", "write"]) -> None:
        self.reason = reason
        super().__init__(reason)


class WorkflowRepository(Protocol):
    """在工作空间边界内读写工作流定义、草稿、版本、发布和运行事实。"""

    def add_workflow(self, workflow: WorkflowDefinition, draft: WorkflowDraft) -> None: ...

    def list_workflows(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[WorkflowDefinition, ...]: ...

    def get_workflow(
        self,
        workspace_id: UUID,
        workflow_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkflowDefinition | None: ...

    def save_workflow(self, workflow: WorkflowDefinition, *, expected_version: int) -> bool: ...

    def get_draft(
        self,
        workspace_id: UUID,
        workflow_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkflowDraft | None: ...

    def save_draft(self, draft: WorkflowDraft, *, expected_revision: int) -> bool: ...

    def next_version_number(self, workspace_id: UUID, workflow_id: UUID) -> int: ...

    def add_version(self, version: WorkflowVersion) -> None: ...

    def get_version(
        self,
        workspace_id: UUID,
        workflow_id: UUID,
        workflow_version_id: UUID,
    ) -> WorkflowVersion | None: ...

    def set_publication(self, publication: WorkflowPublication) -> None: ...

    def get_publication(
        self,
        workspace_id: UUID,
        workflow_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkflowPublication | None: ...

    def get_run_by_idempotency(
        self,
        workspace_id: UUID,
        account_id: UUID,
        idempotency_key: str,
    ) -> WorkflowRun | None: ...

    def add_run(self, run: WorkflowRun) -> None: ...

    def list_runs(
        self,
        workspace_id: UUID,
        workflow_id: UUID,
        *,
        limit: int,
    ) -> tuple[WorkflowRun, ...]: ...

    def get_run(
        self,
        workspace_id: UUID,
        workflow_id: UUID,
        workflow_run_id: UUID,
    ) -> WorkflowRun | None: ...


class WorkflowUnitOfWork(Protocol):
    """保证工作流事实、审计和 Outbox 在同一事务内提交。"""

    @property
    def workflows(self) -> WorkflowRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> WorkflowUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


def workflow_graph_document(graph: WorkflowGraph) -> dict[str, object]:
    """按稳定字段顺序生成可持久化图文档，供摘要、数据库和 API 共用。"""

    return {
        "schema_version": graph.schema_version,
        "entry_node_id": graph.entry_node_id,
        "nodes": [
            {
                "node_id": node.node_id,
                "node_type": node.node_type,
                "name": node.name,
                "config": node.config,
            }
            for node in graph.nodes
        ],
        "edges": [
            {
                "edge_id": edge.edge_id,
                "source_node_id": edge.source_node_id,
                "target_node_id": edge.target_node_id,
                "condition_key": edge.condition_key,
            }
            for edge in graph.edges
        ],
    }


def workflow_graph_digest(graph: WorkflowGraph) -> str:
    """计算规范 JSON 的 SHA-256，发布和运行以该摘要复核版本未被篡改。"""

    payload = json.dumps(
        workflow_graph_document(graph),
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def validate_workflow_graph(graph: WorkflowGraph) -> tuple[WorkflowGraphViolation, ...]:
    """确定性校验首期 DAG 结构；节点配置语义由对应执行器在 P1F-02 继续收紧。"""

    violations: list[WorkflowGraphViolation] = []
    if graph.schema_version != 1:
        violations.append(WorkflowGraphViolation("GRAPH_SCHEMA_VERSION_UNSUPPORTED"))
    if not 2 <= len(graph.nodes) <= MAX_WORKFLOW_NODES:
        violations.append(WorkflowGraphViolation("GRAPH_NODE_COUNT_INVALID"))
    if len(graph.edges) > MAX_WORKFLOW_EDGES:
        violations.append(WorkflowGraphViolation("GRAPH_EDGE_COUNT_EXCEEDED"))

    # 1. 先校验节点和边的局部约束，只有引用完整时才进入全图遍历。
    node_by_id: dict[str, WorkflowNode] = {}
    for node in graph.nodes:
        if not NODE_KEY_PATTERN.fullmatch(node.node_id):
            violations.append(WorkflowGraphViolation("GRAPH_NODE_ID_INVALID", node.node_id))
        if node.node_id in node_by_id:
            violations.append(WorkflowGraphViolation("GRAPH_NODE_ID_DUPLICATE", node.node_id))
        else:
            node_by_id[node.node_id] = node
        if node.node_type not in WORKFLOW_NODE_TYPES:
            violations.append(WorkflowGraphViolation("GRAPH_NODE_TYPE_INVALID", node.node_id))
        if not 1 <= len(node.name.strip()) <= 120:
            violations.append(WorkflowGraphViolation("GRAPH_NODE_NAME_INVALID", node.node_id))

    if graph.entry_node_id not in node_by_id:
        violations.append(WorkflowGraphViolation("GRAPH_ENTRY_NODE_MISSING", graph.entry_node_id))
    trigger_ids = [node.node_id for node in graph.nodes if node.node_type == "trigger"]
    if len(trigger_ids) != 1:
        violations.append(WorkflowGraphViolation("GRAPH_TRIGGER_COUNT_INVALID"))
    elif graph.entry_node_id != trigger_ids[0]:
        violations.append(WorkflowGraphViolation("GRAPH_ENTRY_NOT_TRIGGER", graph.entry_node_id))
    if not any(node.node_type == "result" for node in graph.nodes):
        violations.append(WorkflowGraphViolation("GRAPH_RESULT_MISSING"))

    outgoing = {node_id: set[str]() for node_id in node_by_id}
    incoming = {node_id: set[str]() for node_id in node_by_id}
    edge_ids: set[str] = set()
    edge_pairs: set[tuple[str, str, str | None]] = set()
    has_dangling_edge = False
    for edge in graph.edges:
        if not NODE_KEY_PATTERN.fullmatch(edge.edge_id):
            violations.append(WorkflowGraphViolation("GRAPH_EDGE_ID_INVALID", edge_id=edge.edge_id))
        if edge.edge_id in edge_ids:
            violations.append(
                WorkflowGraphViolation("GRAPH_EDGE_ID_DUPLICATE", edge_id=edge.edge_id)
            )
        edge_ids.add(edge.edge_id)
        if edge.source_node_id not in node_by_id or edge.target_node_id not in node_by_id:
            has_dangling_edge = True
            violations.append(WorkflowGraphViolation("GRAPH_EDGE_DANGLING", edge_id=edge.edge_id))
            continue
        if edge.source_node_id == edge.target_node_id:
            violations.append(
                WorkflowGraphViolation("GRAPH_EDGE_SELF_REFERENCE", edge_id=edge.edge_id)
            )
        pair = (edge.source_node_id, edge.target_node_id, edge.condition_key)
        if pair in edge_pairs:
            violations.append(WorkflowGraphViolation("GRAPH_EDGE_DUPLICATE", edge_id=edge.edge_id))
        edge_pairs.add(pair)
        outgoing[edge.source_node_id].add(edge.target_node_id)
        incoming[edge.target_node_id].add(edge.source_node_id)

    # 2. 入口和结果节点的方向约束防止执行器遇到隐式起点或无定义出口。
    for trigger_id in trigger_ids:
        if incoming.get(trigger_id):
            violations.append(WorkflowGraphViolation("GRAPH_TRIGGER_HAS_INCOMING", trigger_id))
    for node in graph.nodes:
        if node.node_type == "result" and outgoing.get(node.node_id):
            violations.append(WorkflowGraphViolation("GRAPH_RESULT_HAS_OUTGOING", node.node_id))

    if has_dangling_edge or graph.entry_node_id not in node_by_id:
        return _deduplicate_violations(violations)

    # 3. Kahn 拓扑排序拒绝任意循环，再检查入口可达和所有路径最终可到达结果。
    indegree = {node_id: len(sources) for node_id, sources in incoming.items()}
    ready = deque(sorted(node_id for node_id, count in indegree.items() if count == 0))
    visited_count = 0
    while ready:
        node_id = ready.popleft()
        visited_count += 1
        for target_id in sorted(outgoing[node_id]):
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                ready.append(target_id)
    if visited_count != len(node_by_id):
        violations.append(WorkflowGraphViolation("GRAPH_CYCLE"))

    reachable = _reachable_nodes(graph.entry_node_id, outgoing)
    for node_id in sorted(node_by_id.keys() - reachable):
        violations.append(WorkflowGraphViolation("GRAPH_NODE_UNREACHABLE", node_id))
    result_ids = {
        node.node_id
        for node in graph.nodes
        if node.node_type == "result" and node.node_id in node_by_id
    }
    can_reach_result = _reverse_reachable_nodes(result_ids, incoming)
    for node_id in sorted(reachable - can_reach_result):
        violations.append(WorkflowGraphViolation("GRAPH_NODE_WITHOUT_RESULT_PATH", node_id))
    return _deduplicate_violations(violations)


def _reachable_nodes(entry_node_id: str, outgoing: dict[str, set[str]]) -> set[str]:
    visited: set[str] = set()
    pending = [entry_node_id]
    while pending:
        node_id = pending.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        pending.extend(sorted(outgoing[node_id], reverse=True))
    return visited


def _reverse_reachable_nodes(
    result_ids: set[str],
    incoming: dict[str, set[str]],
) -> set[str]:
    visited: set[str] = set()
    pending = sorted(result_ids, reverse=True)
    while pending:
        node_id = pending.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        pending.extend(sorted(incoming[node_id], reverse=True))
    return visited


def _deduplicate_violations(
    violations: list[WorkflowGraphViolation],
) -> tuple[WorkflowGraphViolation, ...]:
    seen: set[tuple[str, str | None, str | None]] = set()
    result: list[WorkflowGraphViolation] = []
    for violation in violations:
        key = (violation.code, violation.node_id, violation.edge_id)
        if key not in seen:
            seen.add(key)
            result.append(violation)
    return tuple(result)
