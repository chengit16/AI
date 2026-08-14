"""实现工作流定义、草稿、不可变版本、发布和运行事实的 PostgreSQL Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from types import TracebackType
from typing import Any, cast
from uuid import UUID

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import CursorResult, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.workflow.domain.models import (
    WorkflowDefinition,
    WorkflowDraft,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowGraphViolation,
    WorkflowNode,
    WorkflowNodeType,
    WorkflowPublication,
    WorkflowRepository,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowStatus,
    WorkflowUnitOfWork,
    WorkflowVersion,
    WorkflowWriteConflictError,
    workflow_graph_document,
)
from ai_platform_api.persistence.tables import (
    workflow_drafts,
    workflow_publications,
    workflow_runs,
    workflow_versions,
    workflows,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyWorkflowRepository(WorkflowRepository):
    """在单个事务中维护工作流当前指针，并保持历史版本只插入不更新。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_workflow(self, workflow: WorkflowDefinition, draft: WorkflowDraft) -> None:
        try:
            self._session.execute(
                insert(workflows).values(
                    workflow_id=workflow.workflow_id,
                    workspace_id=workflow.workspace_id,
                    name=workflow.name,
                    description=workflow.description,
                    status=workflow.status,
                    created_by_account_id=workflow.created_by_account_id,
                    created_at=workflow.created_at,
                    updated_at=workflow.updated_at,
                    version=workflow.version,
                )
            )
            self._session.execute(
                insert(workflow_drafts).values(
                    workflow_id=draft.workflow_id,
                    workspace_id=draft.workspace_id,
                    revision=draft.revision,
                    graph=workflow_graph_document(draft.graph),
                    graph_digest=draft.graph_digest,
                    validation_errors=_violations_document(draft.validation_errors),
                    updated_by_account_id=draft.updated_by_account_id,
                    updated_at=draft.updated_at,
                )
            )
        except IntegrityError as error:
            raise WorkflowWriteConflictError("write") from error

    def list_workflows(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[WorkflowDefinition, ...]:
        rows = self._session.execute(
            select(
                workflows,
                workflow_publications.c.workflow_version_id.label("current_version_id"),
            )
            .outerjoin(
                workflow_publications,
                (workflow_publications.c.workflow_id == workflows.c.workflow_id)
                & (workflow_publications.c.workspace_id == workflows.c.workspace_id),
            )
            .where(workflows.c.workspace_id == workspace_id)
            .order_by(workflows.c.updated_at.desc(), workflows.c.workflow_id)
            .limit(limit)
        )
        return tuple(_workflow(row) for row in rows)

    def get_workflow(
        self,
        workspace_id: UUID,
        workflow_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkflowDefinition | None:
        statement = (
            select(
                workflows,
                workflow_publications.c.workflow_version_id.label("current_version_id"),
            )
            .outerjoin(
                workflow_publications,
                (workflow_publications.c.workflow_id == workflows.c.workflow_id)
                & (workflow_publications.c.workspace_id == workflows.c.workspace_id),
            )
            .where(
                workflows.c.workspace_id == workspace_id,
                workflows.c.workflow_id == workflow_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=workflows)
        row = self._session.execute(statement).one_or_none()
        return _workflow(row) if row is not None else None

    def save_workflow(self, workflow: WorkflowDefinition, *, expected_version: int) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(workflows)
                .where(
                    workflows.c.workspace_id == workflow.workspace_id,
                    workflows.c.workflow_id == workflow.workflow_id,
                    workflows.c.version == expected_version,
                )
                .values(
                    name=workflow.name,
                    description=workflow.description,
                    status=workflow.status,
                    updated_at=workflow.updated_at,
                    version=workflow.version,
                )
            ),
        )
        return result.rowcount == 1

    def get_draft(
        self,
        workspace_id: UUID,
        workflow_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkflowDraft | None:
        statement = select(workflow_drafts).where(
            workflow_drafts.c.workspace_id == workspace_id,
            workflow_drafts.c.workflow_id == workflow_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _draft(row) if row is not None else None

    def save_draft(self, draft: WorkflowDraft, *, expected_revision: int) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(workflow_drafts)
                .where(
                    workflow_drafts.c.workspace_id == draft.workspace_id,
                    workflow_drafts.c.workflow_id == draft.workflow_id,
                    workflow_drafts.c.revision == expected_revision,
                )
                .values(
                    revision=draft.revision,
                    graph=workflow_graph_document(draft.graph),
                    graph_digest=draft.graph_digest,
                    validation_errors=_violations_document(draft.validation_errors),
                    updated_by_account_id=draft.updated_by_account_id,
                    updated_at=draft.updated_at,
                )
            ),
        )
        return result.rowcount == 1

    def next_version_number(self, workspace_id: UUID, workflow_id: UUID) -> int:
        return (
            int(
                self._session.scalar(
                    select(func.max(workflow_versions.c.version_number)).where(
                        workflow_versions.c.workspace_id == workspace_id,
                        workflow_versions.c.workflow_id == workflow_id,
                    )
                )
                or 0
            )
            + 1
        )

    def add_version(self, version: WorkflowVersion) -> None:
        try:
            self._session.execute(
                insert(workflow_versions).values(
                    workflow_version_id=version.workflow_version_id,
                    workflow_id=version.workflow_id,
                    workspace_id=version.workspace_id,
                    version_number=version.version_number,
                    source_draft_revision=version.source_draft_revision,
                    graph=workflow_graph_document(version.graph),
                    graph_digest=version.graph_digest,
                    published_by_account_id=version.published_by_account_id,
                    published_at=version.published_at,
                )
            )
        except IntegrityError as error:
            if _constraint_name(error) == "uq_workflow_versions_draft_revision":
                raise WorkflowWriteConflictError("revision") from error
            raise WorkflowWriteConflictError("write") from error

    def get_version(
        self,
        workspace_id: UUID,
        workflow_id: UUID,
        workflow_version_id: UUID,
    ) -> WorkflowVersion | None:
        row = self._session.execute(
            select(workflow_versions).where(
                workflow_versions.c.workspace_id == workspace_id,
                workflow_versions.c.workflow_id == workflow_id,
                workflow_versions.c.workflow_version_id == workflow_version_id,
            )
        ).one_or_none()
        return _version(row) if row is not None else None

    def set_publication(self, publication: WorkflowPublication) -> None:
        statement = postgresql_insert(workflow_publications).values(
            workflow_id=publication.workflow_id,
            workspace_id=publication.workspace_id,
            workflow_version_id=publication.workflow_version_id,
            generation=publication.generation,
            published_by_account_id=publication.published_by_account_id,
            published_at=publication.published_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[workflow_publications.c.workflow_id],
            set_={
                "workflow_version_id": publication.workflow_version_id,
                "generation": publication.generation,
                "published_by_account_id": publication.published_by_account_id,
                "published_at": publication.published_at,
            },
        )
        self._session.execute(statement)

    def get_publication(
        self,
        workspace_id: UUID,
        workflow_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkflowPublication | None:
        statement = select(workflow_publications).where(
            workflow_publications.c.workspace_id == workspace_id,
            workflow_publications.c.workflow_id == workflow_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _publication(row) if row is not None else None

    def get_run_by_idempotency(
        self,
        workspace_id: UUID,
        account_id: UUID,
        idempotency_key: str,
    ) -> WorkflowRun | None:
        row = self._session.execute(
            select(workflow_runs).where(
                workflow_runs.c.workspace_id == workspace_id,
                workflow_runs.c.requested_by_account_id == account_id,
                workflow_runs.c.idempotency_key == idempotency_key,
            )
        ).one_or_none()
        return _run(row) if row is not None else None

    def add_run(self, run: WorkflowRun) -> None:
        try:
            self._session.execute(
                insert(workflow_runs).values(
                    workflow_run_id=run.workflow_run_id,
                    workflow_id=run.workflow_id,
                    workspace_id=run.workspace_id,
                    workflow_version_id=run.workflow_version_id,
                    requested_by_account_id=run.requested_by_account_id,
                    status=run.status,
                    idempotency_key=run.idempotency_key,
                    request_hash=run.request_hash,
                    input_payload=run.input_payload,
                    trace_id=run.trace_id,
                    traceparent=run.traceparent,
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                    completed_at=run.completed_at,
                    error_code=run.error_code,
                    version=run.version,
                )
            )
        except IntegrityError as error:
            if _constraint_name(error) == "uq_workflow_runs_idempotency":
                raise WorkflowWriteConflictError("idempotency") from error
            raise WorkflowWriteConflictError("write") from error

    def get_run(
        self,
        workspace_id: UUID,
        workflow_id: UUID,
        workflow_run_id: UUID,
    ) -> WorkflowRun | None:
        row = self._session.execute(
            select(workflow_runs).where(
                workflow_runs.c.workspace_id == workspace_id,
                workflow_runs.c.workflow_id == workflow_id,
                workflow_runs.c.workflow_run_id == workflow_run_id,
            )
        ).one_or_none()
        return _run(row) if row is not None else None


class SqlAlchemyWorkflowUnitOfWork(WorkflowUnitOfWork):
    """为工作流事实提供不可嵌套的显式 SQLAlchemy 事务边界。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyWorkflowRepository,
                SqlAlchemyAuditWriter,
                SqlAlchemyOutboxWriter,
            ]
            | None
        ] = ContextVar("workflow_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyWorkflowUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Workflow Unit of Work 不允许重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyWorkflowRepository(session),
                SqlAlchemyAuditWriter(session),
                SqlAlchemyOutboxWriter(session),
            )
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        state = self._state.get()
        if state is not None:
            if exc_type is not None:
                state[0].rollback()
            state[0].close()
            self._state.set(None)

    @property
    def workflows(self) -> SqlAlchemyWorkflowRepository:
        return self._require_state()[1]

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        return self._require_state()[2]

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        return self._require_state()[3]

    def commit(self) -> None:
        self._require_state()[0].commit()

    def _require_state(
        self,
    ) -> tuple[
        Session,
        SqlAlchemyWorkflowRepository,
        SqlAlchemyAuditWriter,
        SqlAlchemyOutboxWriter,
    ]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Workflow Unit of Work 尚未进入事务范围")
        return state


def _workflow(row: Row[Any]) -> WorkflowDefinition:
    return WorkflowDefinition(
        row.workflow_id,
        row.workspace_id,
        row.name,
        row.description,
        cast("WorkflowStatus", row.status),
        row.current_version_id,
        row.created_by_account_id,
        row.created_at,
        row.updated_at,
        row.version,
    )


def _draft(row: Row[Any]) -> WorkflowDraft:
    return WorkflowDraft(
        row.workflow_id,
        row.workspace_id,
        row.revision,
        _graph(row.graph),
        row.graph_digest,
        _violations(row.validation_errors),
        row.updated_by_account_id,
        row.updated_at,
    )


def _version(row: Row[Any]) -> WorkflowVersion:
    return WorkflowVersion(
        row.workflow_version_id,
        row.workflow_id,
        row.workspace_id,
        row.version_number,
        row.source_draft_revision,
        _graph(row.graph),
        row.graph_digest,
        row.published_by_account_id,
        row.published_at,
    )


def _publication(row: Row[Any]) -> WorkflowPublication:
    return WorkflowPublication(
        row.workflow_id,
        row.workspace_id,
        row.workflow_version_id,
        row.generation,
        row.published_by_account_id,
        row.published_at,
    )


def _run(row: Row[Any]) -> WorkflowRun:
    return WorkflowRun(
        row.workflow_run_id,
        row.workflow_id,
        row.workspace_id,
        row.workflow_version_id,
        row.requested_by_account_id,
        cast("WorkflowRunStatus", row.status),
        row.idempotency_key,
        row.request_hash,
        cast("dict[str, object]", row.input_payload),
        row.trace_id,
        row.traceparent,
        row.created_at,
        row.updated_at,
        row.completed_at,
        row.error_code,
        row.version,
    )


def _graph(value: object) -> WorkflowGraph:
    document = cast("dict[str, Any]", value)
    return WorkflowGraph(
        schema_version=int(document["schema_version"]),
        entry_node_id=str(document["entry_node_id"]),
        nodes=tuple(
            WorkflowNode(
                node_id=str(node["node_id"]),
                node_type=cast("WorkflowNodeType", node["node_type"]),
                name=str(node["name"]),
                config=cast("dict[str, object]", node["config"]),
            )
            for node in cast("list[dict[str, Any]]", document["nodes"])
        ),
        edges=tuple(
            WorkflowEdge(
                edge_id=str(edge["edge_id"]),
                source_node_id=str(edge["source_node_id"]),
                target_node_id=str(edge["target_node_id"]),
                condition_key=(
                    str(edge["condition_key"]) if edge.get("condition_key") is not None else None
                ),
            )
            for edge in cast("list[dict[str, Any]]", document["edges"])
        ),
    )


def _violations(value: object) -> tuple[WorkflowGraphViolation, ...]:
    return tuple(
        WorkflowGraphViolation(
            code=str(item["code"]),
            node_id=str(item["node_id"]) if item.get("node_id") is not None else None,
            edge_id=str(item["edge_id"]) if item.get("edge_id") is not None else None,
        )
        for item in cast("list[dict[str, object]]", value)
    )


def _violations_document(
    values: tuple[WorkflowGraphViolation, ...],
) -> list[dict[str, str | None]]:
    return [
        {"code": value.code, "node_id": value.node_id, "edge_id": value.edge_id} for value in values
    ]


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    value = getattr(diagnostic, "constraint_name", None)
    return value if isinstance(value, str) else None
