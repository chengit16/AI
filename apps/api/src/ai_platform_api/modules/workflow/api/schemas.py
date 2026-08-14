"""定义工作流定义、图校验、发布版本和运行事实的 HTTP Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ai_platform_api.modules.workflow.application.service import (
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
)


class WorkflowNodeDocument(BaseModel):
    """表示工作流图中的单个节点文档。"""

    node_id: str = Field(min_length=1, max_length=64)
    node_type: Literal[
        "trigger",
        "condition",
        "knowledge_retrieval",
        "model",
        "approval",
        "result",
    ]
    name: str = Field(min_length=1, max_length=120)
    config: dict[str, object] = Field(default_factory=dict)


class WorkflowEdgeDocument(BaseModel):
    """表示工作流图中的有向边文档。"""

    edge_id: str = Field(min_length=1, max_length=64)
    source_node_id: str = Field(min_length=1, max_length=64)
    target_node_id: str = Field(min_length=1, max_length=64)
    condition_key: str | None = Field(default=None, max_length=64)


class WorkflowGraphDocument(BaseModel):
    """表示首期可序列化工作流图，允许先保存无效草稿再显式校验。"""

    schema_version: int = Field(default=1, ge=1, le=1)
    entry_node_id: str = Field(min_length=1, max_length=64)
    nodes: list[WorkflowNodeDocument] = Field(max_length=100)
    edges: list[WorkflowEdgeDocument] = Field(max_length=200)

    def to_domain(self) -> WorkflowGraph:
        """转换为领域图，由确定性图校验器执行跨节点规则。"""

        return WorkflowGraph(
            schema_version=self.schema_version,
            entry_node_id=self.entry_node_id,
            nodes=tuple(
                WorkflowNode(item.node_id, item.node_type, item.name, item.config)
                for item in self.nodes
            ),
            edges=tuple(
                WorkflowEdge(
                    item.edge_id,
                    item.source_node_id,
                    item.target_node_id,
                    item.condition_key,
                )
                for item in self.edges
            ),
        )


class WorkflowGraphViolationResponse(BaseModel):
    """返回稳定图校验码及可选节点或边定位。"""

    code: str
    node_id: str | None
    edge_id: str | None


class CreateWorkflowRequest(BaseModel):
    """创建工作流定义及首个草稿。"""

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    graph: WorkflowGraphDocument


class UpdateWorkflowDraftRequest(BaseModel):
    """按乐观锁修订号整体替换工作流草稿。"""

    expected_revision: int = Field(ge=1)
    graph: WorkflowGraphDocument


class PublishWorkflowRequest(BaseModel):
    """发布指定修订的已校验草稿。"""

    expected_revision: int = Field(ge=1)


class CreateWorkflowRunRequest(BaseModel):
    """为当前发布版本创建排队运行事实。"""

    workflow_version_id: UUID
    input_payload: dict[str, object] = Field(default_factory=dict)


class WorkflowDefinitionResponse(BaseModel):
    """返回工作流定义和当前发布版本指针。"""

    workflow_id: UUID
    workspace_id: UUID
    name: str
    description: str | None
    status: Literal["active", "archived"]
    current_version_id: UUID | None
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    version: int


class WorkflowDraftResponse(BaseModel):
    """返回当前可编辑草稿、摘要和确定性校验结果。"""

    workflow_id: UUID
    workspace_id: UUID
    revision: int
    graph: WorkflowGraphDocument
    graph_digest: str
    validation_errors: list[WorkflowGraphViolationResponse]
    updated_by_account_id: UUID
    updated_at: datetime


class WorkflowVersionResponse(BaseModel):
    """返回不可变发布版本及其来源草稿修订。"""

    workflow_version_id: UUID
    workflow_id: UUID
    workspace_id: UUID
    version_number: int
    source_draft_revision: int
    graph: WorkflowGraphDocument
    graph_digest: str
    published_by_account_id: UUID
    published_at: datetime


class WorkflowPublicationResponse(BaseModel):
    """返回当前发布指针和单调发布代次。"""

    workflow_id: UUID
    workspace_id: UUID
    workflow_version_id: UUID
    generation: int
    published_by_account_id: UUID
    published_at: datetime


class WorkflowDetailResponse(BaseModel):
    """聚合定义、当前草稿和可选发布指针。"""

    workflow: WorkflowDefinitionResponse
    draft: WorkflowDraftResponse
    publication: WorkflowPublicationResponse | None


class WorkflowListResponse(BaseModel):
    """返回当前授权范围内的工作流定义。"""

    items: list[WorkflowDefinitionResponse]


class WorkflowPublishResponse(BaseModel):
    """聚合发布后的定义、不可变版本和当前指针。"""

    workflow: WorkflowDefinitionResponse
    version: WorkflowVersionResponse
    publication: WorkflowPublicationResponse


class WorkflowRunResponse(BaseModel):
    """返回冻结版本的工作流运行事实。"""

    workflow_run_id: UUID
    workflow_id: UUID
    workspace_id: UUID
    workflow_version_id: UUID
    requested_by_account_id: UUID
    status: Literal[
        "queued",
        "running",
        "waiting_approval",
        "succeeded",
        "failed",
        "cancelled",
    ]
    # 字段级 ABAC 无权读取输入时返回 null，避免原始值先进入响应再由前端隐藏。
    input_payload: dict[str, object] | None
    # 输出和输入使用同一资源的独立字段策略，任一字段无权读取时只返回 null。
    output_payload: dict[str, object] | None = None
    executor_version: str | None = None
    steps_executed: int = 0
    model_calls: int = 0
    retrieval_calls: int = 0
    output_bytes: int = 0
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    error_code: str | None
    version: int
