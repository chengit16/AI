"""编排工作流定义、草稿校验、不可变发布和冻结版本运行事实。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.integration.domain.events import IntegrationEvent
from ai_platform_api.modules.workflow.domain.models import (
    WorkflowDefinition,
    WorkflowDraft,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowGraphViolation,
    WorkflowNode,
    WorkflowPublication,
    WorkflowRun,
    WorkflowUnitOfWork,
    WorkflowVersion,
    WorkflowWriteConflictError,
    validate_workflow_graph,
    workflow_graph_digest,
)

IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
MAX_GRAPH_DOCUMENT_BYTES = 256 * 1024
MAX_RUN_INPUT_BYTES = 64 * 1024

__all__ = [
    "WorkflowConflictError",
    "WorkflowDefinition",
    "WorkflowDefinitionService",
    "WorkflowDeniedError",
    "WorkflowDraft",
    "WorkflowEdge",
    "WorkflowGraph",
    "WorkflowGraphInvalidError",
    "WorkflowGraphViolation",
    "WorkflowIdempotencyConflictError",
    "WorkflowNode",
    "WorkflowNotFoundError",
    "WorkflowPublication",
    "WorkflowRun",
    "WorkflowValidationError",
    "WorkflowVersion",
]


class WorkflowDeniedError(PlatformError):
    """表示当前可信上下文没有工作流资源访问范围。"""

    error_code = "POLICY_DENIED"


class WorkflowNotFoundError(PlatformError):
    """表示工作流、版本或运行在当前工作空间内不可见。"""

    error_code = "RESOURCE_NOT_FOUND"


class WorkflowValidationError(PlatformError):
    """表示名称、修订号、幂等键或 JSON 输入不符合稳定接口约束。"""

    error_code = "VALIDATION_ERROR"


class WorkflowGraphInvalidError(PlatformError):
    """表示工作流图未通过发布所需的确定性结构校验。"""

    error_code = "WORKFLOW_GRAPH_INVALID"

    def __init__(self, violations: tuple[WorkflowGraphViolation, ...]) -> None:
        self.violations = violations
        super().__init__(self.error_code)


class WorkflowConflictError(PlatformError):
    """表示工作流修订、发布指针或当前版本不允许本次操作。"""

    error_code = "WORKFLOW_CONFLICT"


class WorkflowIdempotencyConflictError(PlatformError):
    """表示同一运行幂等键已经绑定到不同请求。"""

    error_code = "IDEMPOTENCY_CONFLICT"


class WorkflowDefinitionService:
    """管理工作流定义生命周期，并保证发布版本与运行引用均可追溯。"""

    def __init__(self, unit_of_work: WorkflowUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def create(
        self,
        context: RequestContext,
        *,
        name: str,
        description: str | None,
        graph: WorkflowGraph,
    ) -> tuple[WorkflowDefinition, WorkflowDraft]:
        """创建定义和首个可无效草稿；非法图只能保存，不能发布或运行。"""

        # 1. 先规范化可公开字段并计算图摘要；结构错误保留在草稿中供编辑器定位。
        account_id = _browser_account(context)
        normalized_name = _normalize_name(name)
        normalized_description = _normalize_description(description)
        graph_digest = _validated_graph_digest(graph)
        now = datetime.now(UTC)
        workflow = WorkflowDefinition(
            workflow_id=uuid4(),
            workspace_id=context.workspace_id,
            name=normalized_name,
            description=normalized_description,
            status="active",
            current_version_id=None,
            created_by_account_id=account_id,
            created_at=now,
            updated_at=now,
            version=1,
        )
        draft = WorkflowDraft(
            workflow_id=workflow.workflow_id,
            workspace_id=workflow.workspace_id,
            revision=1,
            graph=graph,
            graph_digest=graph_digest,
            validation_errors=validate_workflow_graph(graph),
            updated_by_account_id=account_id,
            updated_at=now,
        )
        # 2. 定义、草稿、审计与 Outbox 必须一次提交，禁止留下无草稿的半成品定义。
        try:
            with self._unit_of_work as unit_of_work:
                unit_of_work.workflows.add_workflow(workflow, draft)
                _record_change(unit_of_work, context, workflow, "created", now)
                unit_of_work.commit()
                return workflow, draft
        except WorkflowWriteConflictError as error:
            raise WorkflowConflictError from error

    def list(
        self,
        context: RequestContext,
        *,
        limit: int,
    ) -> tuple[WorkflowDefinition, ...]:
        """按 PDP 返回的数据范围列出工作流，资源级授权不能扩大为整个空间。"""

        _browser_account(context)
        if not 1 <= limit <= 200:
            raise WorkflowValidationError
        with self._unit_of_work as unit_of_work:
            values = unit_of_work.workflows.list_workflows(context.workspace_id, limit=limit)
        if context.authorized_workspace:
            return values
        return tuple(
            value for value in values if value.workflow_id in context.authorized_resource_ids
        )

    def get(
        self,
        context: RequestContext,
        *,
        workflow_id: UUID,
    ) -> tuple[WorkflowDefinition, WorkflowDraft, WorkflowPublication | None]:
        """读取定义、当前草稿和发布指针，不把历史版本内容混入可编辑草稿。"""

        _browser_account(context)
        _require_resource_scope(context, workflow_id)
        with self._unit_of_work as unit_of_work:
            workflow = _require_workflow(unit_of_work, context.workspace_id, workflow_id)
            draft = _require_draft(unit_of_work, context.workspace_id, workflow_id)
            publication = unit_of_work.workflows.get_publication(
                context.workspace_id,
                workflow_id,
            )
            return workflow, draft, publication

    def update_draft(
        self,
        context: RequestContext,
        *,
        workflow_id: UUID,
        expected_revision: int,
        graph: WorkflowGraph,
    ) -> WorkflowDraft:
        """使用修订号替换草稿图，旧页面不能覆盖其他操作者的新修改。"""

        # 1. 请求进入事务前先完成稳定参数和载荷上限检查，避免持锁后处理超大 JSON。
        account_id = _browser_account(context)
        _require_resource_scope(context, workflow_id)
        if expected_revision < 1:
            raise WorkflowValidationError
        graph_digest = _validated_graph_digest(graph)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                # 2. 锁内复核定义状态和草稿修订，再原子写入新图、审计与集成事件。
                workflow = _require_workflow(
                    unit_of_work,
                    context.workspace_id,
                    workflow_id,
                    for_update=True,
                )
                if workflow.status != "active":
                    raise WorkflowConflictError
                current = _require_draft(
                    unit_of_work,
                    context.workspace_id,
                    workflow_id,
                    for_update=True,
                )
                if current.revision != expected_revision:
                    raise WorkflowConflictError
                updated = replace(
                    current,
                    revision=current.revision + 1,
                    graph=graph,
                    graph_digest=graph_digest,
                    validation_errors=validate_workflow_graph(graph),
                    updated_by_account_id=account_id,
                    updated_at=now,
                )
                if not unit_of_work.workflows.save_draft(
                    updated,
                    expected_revision=current.revision,
                ):
                    raise WorkflowConflictError
                _record_draft_change(unit_of_work, context, updated, now)
                unit_of_work.commit()
                return updated
        except WorkflowWriteConflictError as error:
            raise WorkflowConflictError from error

    def validate_draft(
        self,
        context: RequestContext,
        *,
        workflow_id: UUID,
    ) -> WorkflowDraft:
        """按当前规则重新校验草稿并返回结果，发布仍会在锁内再次校验。"""

        _browser_account(context)
        _require_resource_scope(context, workflow_id)
        with self._unit_of_work as unit_of_work:
            _require_workflow(unit_of_work, context.workspace_id, workflow_id)
            draft = _require_draft(unit_of_work, context.workspace_id, workflow_id)
            return replace(draft, validation_errors=validate_workflow_graph(draft.graph))

    def publish(
        self,
        context: RequestContext,
        *,
        workflow_id: UUID,
        expected_revision: int,
    ) -> tuple[WorkflowDefinition, WorkflowVersion, WorkflowPublication]:
        """在同一事务内冻结有效草稿、切换当前指针并记录发布事实。"""

        account_id = _browser_account(context)
        _require_resource_scope(context, workflow_id)
        if expected_revision < 1:
            raise WorkflowValidationError
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                # 1. 锁定定义、草稿和当前指针，防止重复发布同一修订或并发覆盖。
                workflow = _require_workflow(
                    unit_of_work,
                    context.workspace_id,
                    workflow_id,
                    for_update=True,
                )
                draft = _require_draft(
                    unit_of_work,
                    context.workspace_id,
                    workflow_id,
                    for_update=True,
                )
                if workflow.status != "active" or draft.revision != expected_revision:
                    raise WorkflowConflictError
                violations = validate_workflow_graph(draft.graph)
                if violations:
                    raise WorkflowGraphInvalidError(violations)
                if workflow_graph_digest(draft.graph) != draft.graph_digest:
                    raise WorkflowConflictError
                current = unit_of_work.workflows.get_publication(
                    context.workspace_id,
                    workflow_id,
                    for_update=True,
                )
                # 2. 新版本只插入一次，历史版本不修改；当前指针独立记录发布代次。
                version = WorkflowVersion(
                    workflow_version_id=uuid4(),
                    workflow_id=workflow_id,
                    workspace_id=context.workspace_id,
                    version_number=unit_of_work.workflows.next_version_number(
                        context.workspace_id,
                        workflow_id,
                    ),
                    source_draft_revision=draft.revision,
                    graph=draft.graph,
                    graph_digest=draft.graph_digest,
                    published_by_account_id=account_id,
                    published_at=now,
                )
                publication = WorkflowPublication(
                    workflow_id=workflow_id,
                    workspace_id=context.workspace_id,
                    workflow_version_id=version.workflow_version_id,
                    generation=1 if current is None else current.generation + 1,
                    published_by_account_id=account_id,
                    published_at=now,
                )
                updated_workflow = replace(
                    workflow,
                    current_version_id=version.workflow_version_id,
                    updated_at=now,
                    version=workflow.version + 1,
                )
                unit_of_work.workflows.add_version(version)
                unit_of_work.workflows.set_publication(publication)
                if not unit_of_work.workflows.save_workflow(
                    updated_workflow,
                    expected_version=workflow.version,
                ):
                    raise WorkflowConflictError
                # 3. 版本、指针、定义、审计和 Outbox 同事务提交，观察者不会看到半发布状态。
                _record_publication(unit_of_work, context, version, publication, now)
                unit_of_work.commit()
                return updated_workflow, version, publication
        except WorkflowWriteConflictError as error:
            raise WorkflowConflictError from error

    def create_run(
        self,
        context: RequestContext,
        *,
        workflow_id: UUID,
        workflow_version_id: UUID,
        idempotency_key: str,
        input_payload: dict[str, object],
    ) -> WorkflowRun:
        """只为当前发布版本创建排队运行事实，不在 P1F-01 执行任何节点。"""

        # 1. 在进入事务前固定请求摘要，使同一幂等键只能代表完全相同的运行请求。
        account_id = _browser_account(context)
        _require_resource_scope(context, workflow_id)
        _require_idempotency_key(idempotency_key)
        request_hash = _run_request_hash(workflow_id, workflow_version_id, input_payload)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                # 2. 已存在请求优先返回原事实；键相同但摘要不同必须稳定拒绝。
                existing = unit_of_work.workflows.get_run_by_idempotency(
                    context.workspace_id,
                    account_id,
                    idempotency_key,
                )
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise WorkflowIdempotencyConflictError
                    return existing
                # 3. 新运行只能冻结当前发布版本，旧版本和被篡改版本都不能重新排队。
                workflow = _require_workflow(
                    unit_of_work,
                    context.workspace_id,
                    workflow_id,
                )
                publication = unit_of_work.workflows.get_publication(
                    context.workspace_id,
                    workflow_id,
                )
                if (
                    workflow.status != "active"
                    or publication is None
                    or publication.workflow_version_id != workflow_version_id
                    or workflow.current_version_id != workflow_version_id
                ):
                    raise WorkflowConflictError
                version = unit_of_work.workflows.get_version(
                    context.workspace_id,
                    workflow_id,
                    workflow_version_id,
                )
                if version is None or workflow_graph_digest(version.graph) != version.graph_digest:
                    raise WorkflowConflictError
                # 4. 运行、审计和 Outbox 同事务提交；节点执行器只消费已提交的排队事实。
                run = WorkflowRun(
                    workflow_run_id=uuid4(),
                    workflow_id=workflow_id,
                    workspace_id=context.workspace_id,
                    workflow_version_id=workflow_version_id,
                    requested_by_account_id=account_id,
                    status="queued",
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    input_payload=input_payload,
                    trace_id=context.trace.trace_id,
                    traceparent=context.trace.traceparent,
                    created_at=now,
                    updated_at=now,
                    completed_at=None,
                    error_code=None,
                    version=1,
                )
                unit_of_work.workflows.add_run(run)
                _record_run_queued(unit_of_work, context, run, now)
                unit_of_work.commit()
                return run
        except WorkflowWriteConflictError as error:
            if error.reason == "idempotency":
                raise WorkflowIdempotencyConflictError from error
            raise WorkflowConflictError from error

    def get_run(
        self,
        context: RequestContext,
        *,
        workflow_id: UUID,
        workflow_run_id: UUID,
    ) -> WorkflowRun:
        """读取工作空间内的运行事实；当前节点不会推进排队状态。"""

        _browser_account(context)
        # 运行读取权限绑定运行实例本身，不能用父工作流 ID 放宽资源级授权。
        _require_resource_scope(context, workflow_run_id)
        with self._unit_of_work as unit_of_work:
            run = unit_of_work.workflows.get_run(
                context.workspace_id,
                workflow_id,
                workflow_run_id,
            )
            if run is None:
                raise WorkflowNotFoundError
            return run


def _browser_account(context: RequestContext) -> UUID:
    if (
        context.user_id is None
        or context.user_id != context.actor_id
        or context.authentication_method != "browser_session"
    ):
        raise WorkflowDeniedError
    return context.user_id


def _require_resource_scope(context: RequestContext, workflow_id: UUID) -> None:
    if not context.authorized_workspace and workflow_id not in context.authorized_resource_ids:
        raise WorkflowDeniedError


def _require_workflow(
    unit_of_work: WorkflowUnitOfWork,
    workspace_id: UUID,
    workflow_id: UUID,
    *,
    for_update: bool = False,
) -> WorkflowDefinition:
    workflow = unit_of_work.workflows.get_workflow(
        workspace_id,
        workflow_id,
        for_update=for_update,
    )
    if workflow is None:
        raise WorkflowNotFoundError
    return workflow


def _require_draft(
    unit_of_work: WorkflowUnitOfWork,
    workspace_id: UUID,
    workflow_id: UUID,
    *,
    for_update: bool = False,
) -> WorkflowDraft:
    draft = unit_of_work.workflows.get_draft(
        workspace_id,
        workflow_id,
        for_update=for_update,
    )
    if draft is None:
        raise WorkflowNotFoundError
    return draft


def _normalize_name(name: str) -> str:
    normalized = name.strip()
    if not 1 <= len(normalized) <= 120:
        raise WorkflowValidationError
    return normalized


def _normalize_description(description: str | None) -> str | None:
    if description is None:
        return None
    normalized = description.strip()
    if not normalized:
        return None
    if len(normalized) > 1000:
        raise WorkflowValidationError
    return normalized


def _validated_graph_digest(graph: WorkflowGraph) -> str:
    try:
        payload = json.dumps(
            {
                "entry_node_id": graph.entry_node_id,
                "schema_version": graph.schema_version,
                "nodes": [
                    {
                        "config": node.config,
                        "name": node.name,
                        "node_id": node.node_id,
                        "node_type": node.node_type,
                    }
                    for node in graph.nodes
                ],
                "edges": [
                    {
                        "condition_key": edge.condition_key,
                        "edge_id": edge.edge_id,
                        "source_node_id": edge.source_node_id,
                        "target_node_id": edge.target_node_id,
                    }
                    for edge in graph.edges
                ],
            },
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError) as error:
        raise WorkflowValidationError from error
    if len(payload) > MAX_GRAPH_DOCUMENT_BYTES:
        raise WorkflowValidationError
    return hashlib.sha256(payload).hexdigest()


def _require_idempotency_key(value: str) -> None:
    if IDEMPOTENCY_PATTERN.fullmatch(value) is None:
        raise WorkflowValidationError


def _run_request_hash(
    workflow_id: UUID,
    workflow_version_id: UUID,
    input_payload: dict[str, object],
) -> str:
    try:
        payload = json.dumps(
            {
                "workflow_id": str(workflow_id),
                "workflow_version_id": str(workflow_version_id),
                "input": input_payload,
            },
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError) as error:
        raise WorkflowValidationError from error
    if len(payload) > MAX_RUN_INPUT_BYTES:
        raise WorkflowValidationError
    return hashlib.sha256(payload).hexdigest()


def _record_change(
    unit_of_work: WorkflowUnitOfWork,
    context: RequestContext,
    workflow: WorkflowDefinition,
    transition: str,
    occurred_at: datetime,
) -> None:
    action = f"workflow.definition.{transition}"
    attributes: dict[str, object] = {"status": workflow.status, "version": workflow.version}
    _record_audit_event(
        unit_of_work,
        context,
        action=action,
        resource_type="workflow_definition",
        resource_id=workflow.workflow_id,
        aggregate_version=workflow.version,
        occurred_at=occurred_at,
        attributes=attributes,
    )


def _record_draft_change(
    unit_of_work: WorkflowUnitOfWork,
    context: RequestContext,
    draft: WorkflowDraft,
    occurred_at: datetime,
) -> None:
    _record_audit_event(
        unit_of_work,
        context,
        action="workflow.draft.updated",
        resource_type="workflow_definition",
        resource_id=draft.workflow_id,
        aggregate_version=draft.revision,
        occurred_at=occurred_at,
        attributes={
            "revision": draft.revision,
            "graph_digest": draft.graph_digest,
            "validation_error_codes": [item.code for item in draft.validation_errors],
        },
    )


def _record_publication(
    unit_of_work: WorkflowUnitOfWork,
    context: RequestContext,
    version: WorkflowVersion,
    publication: WorkflowPublication,
    occurred_at: datetime,
) -> None:
    _record_audit_event(
        unit_of_work,
        context,
        action="workflow.version.published",
        resource_type="workflow_definition",
        resource_id=version.workflow_id,
        aggregate_version=publication.generation,
        occurred_at=occurred_at,
        attributes={
            "workflow_version_id": str(version.workflow_version_id),
            "version_number": version.version_number,
            "source_draft_revision": version.source_draft_revision,
            "graph_digest": version.graph_digest,
        },
    )


def _record_run_queued(
    unit_of_work: WorkflowUnitOfWork,
    context: RequestContext,
    run: WorkflowRun,
    occurred_at: datetime,
) -> None:
    _record_audit_event(
        unit_of_work,
        context,
        action="workflow.run.queued",
        resource_type="workflow_instance",
        resource_id=run.workflow_run_id,
        aggregate_version=run.version,
        occurred_at=occurred_at,
        attributes={
            "workflow_id": str(run.workflow_id),
            "workflow_version_id": str(run.workflow_version_id),
            "status": run.status,
        },
    )


def _record_audit_event(
    unit_of_work: WorkflowUnitOfWork,
    context: RequestContext,
    *,
    action: str,
    resource_type: str,
    resource_id: UUID,
    aggregate_version: int,
    occurred_at: datetime,
    attributes: dict[str, object],
) -> None:
    """工作流事件只记录 ID、版本和摘要，草稿配置与运行输入不得进入审计或 Outbox。"""

    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            attributes=attributes,
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type=action,
            workspace_id=context.workspace_id,
            aggregate_id=resource_id,
            aggregate_version=aggregate_version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=attributes,
        )
    )
