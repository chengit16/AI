"""验证 P1F-01 工作流 DAG、摘要和不可变版本的确定性边界。"""

from dataclasses import FrozenInstanceError

import pytest
from ai_platform_api.modules.workflow.domain.models import (
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    validate_workflow_graph,
    workflow_graph_digest,
)


def _valid_graph() -> WorkflowGraph:
    return WorkflowGraph(
        schema_version=1,
        entry_node_id="start",
        nodes=(
            WorkflowNode("start", "trigger", "开始", {}),
            WorkflowNode("check", "condition", "条件", {"expression": "risk == 'high'"}),
            WorkflowNode("done", "result", "完成", {}),
        ),
        edges=(
            WorkflowEdge("start_to_check", "start", "check"),
            WorkflowEdge("check_to_done", "check", "done", "default"),
        ),
    )


def test_valid_graph_has_stable_digest_and_no_violations() -> None:
    graph = _valid_graph()

    assert validate_workflow_graph(graph) == ()
    assert workflow_graph_digest(graph) == workflow_graph_digest(graph)
    assert len(workflow_graph_digest(graph)) == 64


def test_cycle_and_result_outgoing_are_rejected() -> None:
    graph = _valid_graph()
    invalid = WorkflowGraph(
        graph.schema_version,
        graph.entry_node_id,
        graph.nodes,
        (*graph.edges, WorkflowEdge("done_to_check", "done", "check")),
    )

    codes = {item.code for item in validate_workflow_graph(invalid)}

    assert "GRAPH_CYCLE" in codes
    assert "GRAPH_RESULT_HAS_OUTGOING" in codes


def test_dangling_edge_stops_full_graph_traversal() -> None:
    graph = _valid_graph()
    invalid = WorkflowGraph(
        graph.schema_version,
        graph.entry_node_id,
        graph.nodes,
        (*graph.edges, WorkflowEdge("missing_target", "check", "missing")),
    )

    violations = validate_workflow_graph(invalid)

    assert any(item.code == "GRAPH_EDGE_DANGLING" for item in violations)


def test_unreachable_node_and_dead_end_are_rejected() -> None:
    graph = _valid_graph()
    invalid = WorkflowGraph(
        graph.schema_version,
        graph.entry_node_id,
        (
            *graph.nodes,
            WorkflowNode("orphan", "model", "孤立模型", {}),
            WorkflowNode("dead_end", "approval", "无结果路径", {}),
        ),
        (*graph.edges, WorkflowEdge("check_to_dead", "check", "dead_end")),
    )

    violations = validate_workflow_graph(invalid)

    assert any(
        item.code == "GRAPH_NODE_UNREACHABLE" and item.node_id == "orphan" for item in violations
    )
    assert any(
        item.code == "GRAPH_NODE_WITHOUT_RESULT_PATH" and item.node_id == "dead_end"
        for item in violations
    )


def test_graph_entities_are_frozen() -> None:
    graph = _valid_graph()

    with pytest.raises(FrozenInstanceError):
        graph.entry_node_id = "changed"  # type: ignore[misc]
