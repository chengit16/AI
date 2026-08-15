"""验证 P1F-01 工作流应用服务的草稿、发布和资源范围规则。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import TracebackType
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.integration.domain.events import IntegrationEvent
from ai_platform_api.modules.workflow.application.service import (
    WorkflowConflictError,
    WorkflowDefinitionService,
    WorkflowDeniedError,
    WorkflowGraphInvalidError,
    WorkflowValidationError,
)
from ai_platform_api.modules.workflow.domain.models import (
    WorkflowDefinition,
    WorkflowDraft,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowPublication,
    WorkflowRun,
    WorkflowVersion,
)
from ai_platform_backend.integration.domain import AuditRecord

ACCOUNT_ID = UUID("f1000000-0000-4000-8000-000000000001")
WORKSPACE_ID = UUID("f1000000-0000-4000-8000-000000000002")
TRACE = TraceContext("e" * 32, "f" * 16)


class MemoryWorkflowRepository:
    """为应用服务测试保留最小内存事实，不模拟 SQLAlchemy 行为。"""

    def __init__(self) -> None:
        self.workflow: WorkflowDefinition | None = None
        self.draft: WorkflowDraft | None = None
        self.publication: WorkflowPublication | None = None
        self.versions: dict[UUID, WorkflowVersion] = {}
        self.runs: dict[UUID, WorkflowRun] = {}

    def add_workflow(self, workflow: WorkflowDefinition, draft: WorkflowDraft) -> None:
        self.workflow = workflow
        self.draft = draft

    def list_workflows(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[WorkflowDefinition, ...]:
        if self.workflow is None or self.workflow.workspace_id != workspace_id:
            return ()
        return (self.workflow,)[:limit]

    def get_workflow(
        self,
        workspace_id: UUID,
        workflow_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkflowDefinition | None:
        del for_update
        if (
            self.workflow is None
            or self.workflow.workspace_id != workspace_id
            or self.workflow.workflow_id != workflow_id
        ):
            return None
        return self.workflow

    def save_workflow(self, workflow: WorkflowDefinition, *, expected_version: int) -> bool:
        if self.workflow is None or self.workflow.version != expected_version:
            return False
        self.workflow = workflow
        return True

    def get_draft(
        self,
        workspace_id: UUID,
        workflow_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkflowDraft | None:
        del for_update
        if (
            self.draft is None
            or self.draft.workspace_id != workspace_id
            or self.draft.workflow_id != workflow_id
        ):
            return None
        return self.draft

    def save_draft(self, draft: WorkflowDraft, *, expected_revision: int) -> bool:
        if self.draft is None or self.draft.revision != expected_revision:
            return False
        self.draft = draft
        return True

    def next_version_number(self, workspace_id: UUID, workflow_id: UUID) -> int:
        matching = (
            version.version_number
            for version in self.versions.values()
            if version.workspace_id == workspace_id and version.workflow_id == workflow_id
        )
        return max(matching, default=0) + 1

    def add_version(self, version: WorkflowVersion) -> None:
        self.versions[version.workflow_version_id] = version

    def get_version(
        self,
        workspace_id: UUID,
        workflow_id: UUID,
        workflow_version_id: UUID,
    ) -> WorkflowVersion | None:
        version = self.versions.get(workflow_version_id)
        if (
            version is None
            or version.workspace_id != workspace_id
            or version.workflow_id != workflow_id
        ):
            return None
        return version

    def set_publication(self, publication: WorkflowPublication) -> None:
        self.publication = publication

    def get_publication(
        self,
        workspace_id: UUID,
        workflow_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkflowPublication | None:
        del for_update
        if (
            self.publication is None
            or self.publication.workspace_id != workspace_id
            or self.publication.workflow_id != workflow_id
        ):
            return None
        return self.publication

    def get_run_by_idempotency(
        self,
        workspace_id: UUID,
        account_id: UUID,
        idempotency_key: str,
    ) -> WorkflowRun | None:
        return next(
            (
                run
                for run in self.runs.values()
                if run.workspace_id == workspace_id
                and run.requested_by_account_id == account_id
                and run.idempotency_key == idempotency_key
            ),
            None,
        )

    def add_run(self, run: WorkflowRun) -> None:
        self.runs[run.workflow_run_id] = run

    def list_runs(
        self,
        workspace_id: UUID,
        workflow_id: UUID,
        *,
        limit: int,
    ) -> tuple[WorkflowRun, ...]:
        values = (
            run
            for run in reversed(tuple(self.runs.values()))
            if run.workspace_id == workspace_id and run.workflow_id == workflow_id
        )
        return tuple(values)[:limit]

    def get_run(
        self,
        workspace_id: UUID,
        workflow_id: UUID,
        workflow_run_id: UUID,
    ) -> WorkflowRun | None:
        run = self.runs.get(workflow_run_id)
        if run is None or run.workspace_id != workspace_id or run.workflow_id != workflow_id:
            return None
        return run


@dataclass
class MemoryAuditWriter:
    records: list[AuditRecord]

    def add(self, record: AuditRecord) -> None:
        self.records.append(record)


@dataclass
class MemoryOutboxWriter:
    events: list[IntegrationEvent]

    def add(self, event: IntegrationEvent) -> None:
        self.events.append(event)


class MemoryWorkflowUnitOfWork:
    """复用同一内存 Repository，并记录应用服务是否提交事务。"""

    def __init__(self) -> None:
        self.workflows = MemoryWorkflowRepository()
        self.audit = MemoryAuditWriter([])
        self.outbox = MemoryOutboxWriter([])
        self.commit_count = 0

    def __enter__(self) -> MemoryWorkflowUnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    def commit(self) -> None:
        self.commit_count += 1


def context() -> RequestContext:
    return replace(
        RequestContext.trusted(
            actor_id=ACCOUNT_ID,
            user_id=ACCOUNT_ID,
            workspace_id=WORKSPACE_ID,
            trace=TRACE,
            authentication_method="browser_session",
        ),
        authorized_workspace=True,
    )


def valid_graph() -> WorkflowGraph:
    return WorkflowGraph(
        1,
        "trigger",
        (
            WorkflowNode("trigger", "trigger", "合成触发器", {}),
            WorkflowNode("result", "result", "合成结果", {}),
        ),
        (WorkflowEdge("trigger_to_result", "trigger", "result"),),
    )


def test_invalid_draft_can_be_saved_but_cannot_be_published() -> None:
    unit_of_work = MemoryWorkflowUnitOfWork()
    service = WorkflowDefinitionService(unit_of_work)
    invalid = WorkflowGraph(
        1,
        "trigger",
        (
            WorkflowNode("trigger", "trigger", "合成触发器", {}),
            WorkflowNode("result", "result", "合成结果", {}),
        ),
        (
            WorkflowEdge("forward", "trigger", "result"),
            WorkflowEdge("cycle", "result", "trigger"),
        ),
    )

    workflow, draft = service.create(
        context(),
        name="合成无效草稿",
        description=None,
        graph=invalid,
    )
    assert draft.validation_errors
    assert unit_of_work.commit_count == 1
    with pytest.raises(WorkflowGraphInvalidError):
        service.publish(
            context(),
            workflow_id=workflow.workflow_id,
            expected_revision=draft.revision,
        )
    assert unit_of_work.commit_count == 1


def test_revision_conflict_and_run_scope_use_distinct_resource_ids() -> None:
    unit_of_work = MemoryWorkflowUnitOfWork()
    service = WorkflowDefinitionService(unit_of_work)
    workflow, draft = service.create(
        context(),
        name="合成版本工作流",
        description="验证资源级范围",
        graph=valid_graph(),
    )
    with pytest.raises(WorkflowConflictError):
        service.update_draft(
            context(),
            workflow_id=workflow.workflow_id,
            expected_revision=draft.revision + 1,
            graph=valid_graph(),
        )
    _, version, _ = service.publish(
        context(),
        workflow_id=workflow.workflow_id,
        expected_revision=draft.revision,
    )
    run = service.create_run(
        context(),
        workflow_id=workflow.workflow_id,
        workflow_version_id=version.workflow_version_id,
        idempotency_key="synthetic-unit-run-0001",
        input_payload={},
    )

    run_context = replace(
        context(),
        authorized_workspace=False,
        authorized_resource_ids=frozenset({run.workflow_run_id}),
    )
    assert (
        service.get_run(
            run_context,
            workflow_id=workflow.workflow_id,
            workflow_run_id=run.workflow_run_id,
        )
        == run
    )
    assert service.list_runs(run_context, workflow_id=workflow.workflow_id, limit=50) == (run,)
    assert (
        service.list_runs(
            replace(run_context, authorized_resource_ids=frozenset()),
            workflow_id=workflow.workflow_id,
            limit=50,
        )
        == ()
    )
    with pytest.raises(WorkflowDeniedError):
        service.get_run(
            replace(
                run_context,
                authorized_resource_ids=frozenset({workflow.workflow_id}),
            ),
            workflow_id=workflow.workflow_id,
            workflow_run_id=run.workflow_run_id,
        )


def test_graph_and_run_payload_size_limits_fail_before_writes() -> None:
    unit_of_work = MemoryWorkflowUnitOfWork()
    service = WorkflowDefinitionService(unit_of_work)
    oversized_graph = WorkflowGraph(
        1,
        "trigger",
        (
            WorkflowNode("trigger", "trigger", "合成触发器", {"value": "x" * 300_000}),
            WorkflowNode("result", "result", "合成结果", {}),
        ),
        (WorkflowEdge("trigger_to_result", "trigger", "result"),),
    )
    with pytest.raises(WorkflowValidationError):
        service.create(
            context(),
            name="超限合成工作流",
            description=None,
            graph=oversized_graph,
        )
    assert unit_of_work.workflows.workflow is None
    assert unit_of_work.audit.records == []

    workflow, draft = service.create(
        context(),
        name="合成输入限制工作流",
        description=None,
        graph=valid_graph(),
    )
    _, version, _ = service.publish(
        context(),
        workflow_id=workflow.workflow_id,
        expected_revision=draft.revision,
    )
    with pytest.raises(WorkflowValidationError):
        service.create_run(
            context(),
            workflow_id=workflow.workflow_id,
            workflow_version_id=version.workflow_version_id,
            idempotency_key="synthetic-unit-run-oversized",
            input_payload={"value": "x" * 70_000},
        )
    assert unit_of_work.workflows.runs == {}
