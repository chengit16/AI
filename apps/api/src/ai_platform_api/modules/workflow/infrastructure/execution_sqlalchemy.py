"""以 PostgreSQL 短事务保存工作流执行事实并执行授权知识检索。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from ai_platform_backend.indexing.tokenization import keyword_query
from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent
from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import CursorResult, and_, insert, select, update
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

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
from ai_platform_api.modules.retrieval.domain.models import (
    AuthorizedSearchScope,
    SearchIndex,
    Visibility,
)
from ai_platform_api.modules.workflow.domain.approval_runtime import ApprovalRuntimeState
from ai_platform_api.modules.workflow.domain.execution import (
    WorkflowExecutionBudget,
    WorkflowExecutionClaim,
    WorkflowExecutionStore,
    WorkflowKnowledgeItem,
    WorkflowKnowledgeResult,
    WorkflowKnowledgeRetriever,
    WorkflowNodeAttempt,
    WorkflowRunStep,
)
from ai_platform_api.modules.workflow.domain.models import WorkflowRun
from ai_platform_api.modules.workflow.infrastructure.approval_runtime_sqlalchemy import (
    SqlAlchemyApprovalRuntimeRepository,
    record_embedded_approval_created,
)
from ai_platform_api.modules.workflow.infrastructure.sqlalchemy import (
    workflow_run_from_row,
    workflow_version_from_row,
)
from ai_platform_api.persistence.tables import (
    document_index_publications,
    documents,
    index_versions,
    workflow_node_attempts,
    workflow_run_steps,
    workflow_runs,
    workflow_versions,
)

SessionFactory = Callable[[], Session]
SearchIndexFactory = Callable[[Session], SearchIndex]


class WorkflowExecutionStateError(RuntimeError):
    """数据库状态不符合单次认领和单向步骤转换约束。"""


class SqlAlchemyWorkflowExecutionStore(WorkflowExecutionStore):
    """每次状态转换使用独立短事务，模型或检索期间不持有数据库连接。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def claim(
        self,
        context: RequestContext,
        workflow_run_id: UUID,
        *,
        executor_version: str,
        budget: WorkflowExecutionBudget,
        now: datetime,
    ) -> WorkflowExecutionClaim | None:
        """仅将当前请求者所属的 queued Run 原子推进为 running。"""

        with self._session_factory() as session, session.begin():
            # 1. 锁定目标 Run 并同时复核空间、请求者和冻结版本，不能仅凭可猜测 ID 取得执行权。
            row = session.execute(
                select(workflow_runs)
                .where(
                    workflow_runs.c.workflow_run_id == workflow_run_id,
                    workflow_runs.c.workspace_id == context.workspace_id,
                )
                .with_for_update()
            ).one_or_none()
            if (
                row is None
                or row.status != "queued"
                or context.user_id is None
                or row.requested_by_account_id != context.user_id
            ):
                return None
            version_row = session.execute(
                select(workflow_versions).where(
                    workflow_versions.c.workflow_version_id == row.workflow_version_id,
                    workflow_versions.c.workflow_id == row.workflow_id,
                    workflow_versions.c.workspace_id == row.workspace_id,
                )
            ).one_or_none()
            if version_row is None:
                raise WorkflowExecutionStateError("工作流冻结版本不存在")
            resolved_budget = _execution_budget(row.execution_budget, budget)
            if row.executor_version is not None and row.executor_version != executor_version:
                raise WorkflowExecutionStateError("工作流执行器版本与冻结版本不一致")
            steps = tuple(
                _step_from_row(step)
                for step in session.execute(
                    select(workflow_run_steps)
                    .where(
                        workflow_run_steps.c.workflow_run_id == workflow_run_id,
                        workflow_run_steps.c.workspace_id == context.workspace_id,
                    )
                    .order_by(workflow_run_steps.c.sequence_no)
                )
            )
            # 2. 首次认领冻结预算；审批恢复只复用原预算和历史步骤，不重置累计用量。
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(workflow_runs)
                    .where(
                        workflow_runs.c.workflow_run_id == workflow_run_id,
                        workflow_runs.c.status == "queued",
                        workflow_runs.c.version == row.version,
                    )
                    .values(
                        status="running",
                        executor_version=executor_version,
                        execution_budget=resolved_budget.document(),
                        updated_at=now,
                        version=row.version + 1,
                    )
                ),
            )
            if result.rowcount != 1:
                return None
            # 3. 返回值携带提交前已读取的历史步骤，执行器据此重建活动边而不重放外部调用。
            run = replace(
                workflow_run_from_row(row),
                status="running",
                executor_version=executor_version,
                execution_budget=resolved_budget.document(),
                updated_at=now,
                version=row.version + 1,
            )
            return WorkflowExecutionClaim(
                run,
                workflow_version_from_row(version_row),
                resolved_budget,
                steps,
            )

    def start_step(
        self,
        step: WorkflowRunStep,
        attempt: WorkflowNodeAttempt,
    ) -> None:
        with self._session_factory() as session, session.begin():
            session.execute(insert(workflow_run_steps).values(**_step_values(step)))
            session.execute(insert(workflow_node_attempts).values(**_attempt_values(attempt)))

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
        with self._session_factory() as session, session.begin():
            step_result = cast(
                CursorResult[Any],
                session.execute(
                    update(workflow_run_steps)
                    .where(
                        workflow_run_steps.c.workflow_step_id == workflow_step_id,
                        workflow_run_steps.c.status == "running",
                    )
                    .values(
                        status="succeeded",
                        output_payload=output_payload,
                        branch_key=branch_key,
                        completed_at=now,
                    )
                ),
            )
            attempt_result = cast(
                CursorResult[Any],
                session.execute(
                    update(workflow_node_attempts)
                    .where(
                        workflow_node_attempts.c.workflow_attempt_id == workflow_attempt_id,
                        workflow_node_attempts.c.workflow_step_id == workflow_step_id,
                        workflow_node_attempts.c.status == "running",
                    )
                    .values(
                        status="succeeded",
                        output_hash=output_hash,
                        usage=usage,
                        completed_at=now,
                    )
                ),
            )
            if step_result.rowcount != 1 or attempt_result.rowcount != 1:
                raise WorkflowExecutionStateError("工作流步骤完成状态冲突")

    def skip_step(self, step: WorkflowRunStep) -> None:
        with self._session_factory() as session, session.begin():
            session.execute(insert(workflow_run_steps).values(**_step_values(step)))

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
    ) -> None:
        with self._session_factory() as session, session.begin():
            # 1. 先锁定仍在运行的审批节点，再原子关闭本次节点尝试，避免重复进入等待态。
            step = session.execute(
                select(workflow_run_steps)
                .where(workflow_run_steps.c.workflow_step_id == workflow_step_id)
                .with_for_update()
            ).one_or_none()
            if step is None or step.status != "running":
                raise WorkflowExecutionStateError("审批等待步骤不存在或状态冲突")
            session.execute(
                update(workflow_run_steps)
                .where(workflow_run_steps.c.workflow_step_id == workflow_step_id)
                .values(status="waiting_approval", output_payload=output_payload)
            )
            attempt_result = cast(
                CursorResult[Any],
                session.execute(
                    update(workflow_node_attempts)
                    .where(
                        workflow_node_attempts.c.workflow_attempt_id == workflow_attempt_id,
                        workflow_node_attempts.c.status == "running",
                    )
                    .values(
                        status="succeeded",
                        output_hash=output_hash,
                        completed_at=now,
                    )
                ),
            )
            if attempt_result.rowcount != 1:
                raise WorkflowExecutionStateError("审批等待尝试不存在或状态冲突")
            # 2. Run、审批实例与审批步骤在同一事务进入等待态，不能出现孤立等待事实。
            run = session.execute(
                select(workflow_runs)
                .where(workflow_runs.c.workflow_run_id == step.workflow_run_id)
                .with_for_update()
            ).one()
            if run.status != "running":
                raise WorkflowExecutionStateError("工作流运行不能进入审批等待态")
            instance = approval_state.instance
            if (
                instance.workflow_run_id != run.workflow_run_id
                or instance.workflow_step_id != step.workflow_step_id
                or instance.workspace_id != run.workspace_id
            ):
                raise WorkflowExecutionStateError("审批实例与工作流等待步骤不匹配")
            SqlAlchemyApprovalRuntimeRepository(session).add_state(approval_state)
            record_embedded_approval_created(session, context, approval_state)
            session.execute(
                update(workflow_runs)
                .where(workflow_runs.c.workflow_run_id == run.workflow_run_id)
                .values(
                    status="waiting_approval",
                    steps_executed=steps_executed,
                    model_calls=model_calls,
                    retrieval_calls=retrieval_calls,
                    output_bytes=output_bytes,
                    updated_at=now,
                    version=run.version + 1,
                )
            )
            _record_run_transition(
                session,
                context,
                run.workflow_run_id,
                run.version + 1,
                "workflow.run.waiting_approval",
                "waiting_approval",
                now,
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
        with self._session_factory() as session, session.begin():
            # 1. 终态只接受 running Run，并在同一更新中固化最终输出和全部预算用量。
            row = _locked_run(session, workflow_run_id)
            if row.status != "running":
                raise WorkflowExecutionStateError("只有运行中的工作流可以完成")
            next_version = row.version + 1
            session.execute(
                update(workflow_runs)
                .where(workflow_runs.c.workflow_run_id == workflow_run_id)
                .values(
                    status="succeeded",
                    output_payload=output_payload,
                    steps_executed=steps_executed,
                    model_calls=model_calls,
                    retrieval_calls=retrieval_calls,
                    output_bytes=output_bytes,
                    updated_at=now,
                    completed_at=now,
                    error_code=None,
                    version=next_version,
                )
            )
            # 2. 成功事实与审计、Outbox 共用事务和聚合版本，消费者不会观察到半完成状态。
            _record_run_transition(
                session,
                context,
                workflow_run_id,
                next_version,
                "workflow.run.succeeded",
                "succeeded",
                now,
            )
            return replace(
                workflow_run_from_row(row),
                status="succeeded",
                output_payload=output_payload,
                steps_executed=steps_executed,
                model_calls=model_calls,
                retrieval_calls=retrieval_calls,
                output_bytes=output_bytes,
                updated_at=now,
                completed_at=now,
                error_code=None,
                version=next_version,
            )

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
        """原子收敛当前 running Step/Attempt 和 Run，不保存异常正文。"""

        with self._session_factory() as session, session.begin():
            # 1. 优先使用执行器传入的活动标识，缺失时从数据库恢复唯一 running 尝试以完成兜底。
            row = _locked_run(session, workflow_run_id)
            if row.status in {"succeeded", "failed", "cancelled"}:
                return workflow_run_from_row(row)
            step_id, attempt_id = _resolve_running_execution(
                session,
                workflow_run_id,
                workflow_step_id,
                workflow_attempt_id,
            )
            if step_id is not None:
                session.execute(
                    update(workflow_run_steps)
                    .where(
                        workflow_run_steps.c.workflow_step_id == step_id,
                        workflow_run_steps.c.status == "running",
                    )
                    .values(status="failed", completed_at=now, error_code=error_code)
                )
            if attempt_id is not None:
                session.execute(
                    update(workflow_node_attempts)
                    .where(
                        workflow_node_attempts.c.workflow_attempt_id == attempt_id,
                        workflow_node_attempts.c.status == "running",
                    )
                    .values(status="failed", completed_at=now, error_code=error_code)
                )
            # 2. Step、Attempt、Run、审计和 Outbox 在同一事务进入失败终态，只持久化稳定错误码。
            next_version = row.version + 1
            session.execute(
                update(workflow_runs)
                .where(workflow_runs.c.workflow_run_id == workflow_run_id)
                .values(
                    status="failed",
                    updated_at=now,
                    completed_at=now,
                    error_code=error_code,
                    version=next_version,
                )
            )
            _record_run_transition(
                session,
                context,
                workflow_run_id,
                next_version,
                "workflow.run.failed",
                "failed",
                now,
                error_code=error_code,
            )
            return replace(
                workflow_run_from_row(row),
                status="failed",
                updated_at=now,
                completed_at=now,
                error_code=error_code,
                version=next_version,
            )


class SqlAlchemyWorkflowKnowledgeRetriever(WorkflowKnowledgeRetriever):
    """复用当前活动索引执行关键词只读检索，并完整消费 PDP 数据范围。"""

    def __init__(
        self,
        session_factory: SessionFactory,
        policy: PolicyDecisionPoint,
        search_indexes: SearchIndexFactory,
    ) -> None:
        self._session_factory = session_factory
        self._policy = policy
        self._search_indexes = search_indexes

    def retrieve(
        self,
        context: RequestContext,
        *,
        query: str,
        knowledge_base_ids: frozenset[UUID] | None,
        limit: int,
    ) -> WorkflowKnowledgeResult:
        # 1. 每次检索重新执行文档读取 PDP；正文被字段策略遮罩时直接拒绝，不进入搜索层。
        decision = self._policy.decide(
            PolicyRequest(
                context,
                "knowledge.document.read",
                ResourceReference("document", context.workspace_id, context.workspace_id),
            )
        )
        if not decision.allowed or "content" in decision.field_mask:
            raise PermissionError("知识正文权限不足")
        # 2. 只把当前活动且仍授权的索引版本交给关键词搜索，并按实际结果计算最高密级。
        with self._session_factory() as session, session.begin():
            rows = _authorized_index_rows(
                session,
                context,
                decision,
                knowledge_base_ids,
            )
            if not rows:
                return WorkflowKnowledgeResult(
                    (),
                    decision.decision_id,
                    decision.policy_version,
                    decision.maximum_security_level,
                )
            scope = _search_scope(context, decision, rows, knowledge_base_ids)
            candidates = self._search_indexes(session).keyword_search(
                scope,
                keyword_query(query),
                limit,
            )
            items = tuple(
                WorkflowKnowledgeItem(
                    chunk_id=item.chunk.chunk_id,
                    document_id=item.chunk.document_id,
                    document_version_id=item.chunk.document_version_id,
                    content_hash=item.chunk.content_hash,
                    content=item.chunk.content,
                    source_position=item.chunk.source_position,
                    security_level=item.chunk.security_level,
                )
                for item in candidates
            )
            maximum = cast(
                SecurityLevel,
                max(
                    (item.security_level for item in items),
                    default="PUBLIC",
                    key=SECURITY_LEVEL_RANK.__getitem__,
                ),
            )
            return WorkflowKnowledgeResult(
                items,
                decision.decision_id,
                decision.policy_version,
                maximum,
            )


def _step_values(step: WorkflowRunStep) -> dict[str, object]:
    return {
        "workflow_step_id": step.workflow_step_id,
        "workflow_run_id": step.workflow_run_id,
        "workspace_id": step.workspace_id,
        "node_id": step.node_id,
        "node_type": step.node_type,
        "sequence_no": step.sequence_no,
        "status": step.status,
        "input_payload": step.input_payload,
        "output_payload": step.output_payload,
        "branch_key": step.branch_key,
        "policy_decision_id": step.policy_decision_id,
        "policy_version": step.policy_version,
        "started_at": step.started_at,
        "completed_at": step.completed_at,
        "error_code": step.error_code,
    }


def _step_from_row(row: Row[Any]) -> WorkflowRunStep:
    return WorkflowRunStep(
        row.workflow_step_id,
        row.workflow_run_id,
        row.workspace_id,
        row.node_id,
        row.node_type,
        row.sequence_no,
        cast(Any, row.status),
        cast("dict[str, object] | None", row.input_payload),
        cast("dict[str, object] | None", row.output_payload),
        row.branch_key,
        row.policy_decision_id,
        row.policy_version,
        row.started_at,
        row.completed_at,
        row.error_code,
    )


def _execution_budget(
    document: object,
    default: WorkflowExecutionBudget,
) -> WorkflowExecutionBudget:
    if document is None:
        return default
    if not isinstance(document, dict) or set(document) != set(default.document()):
        raise WorkflowExecutionStateError("工作流冻结预算结构无效")
    try:
        values = {key: int(value) for key, value in document.items()}
        return WorkflowExecutionBudget(**values)
    except (TypeError, ValueError) as error:
        raise WorkflowExecutionStateError("工作流冻结预算数值无效") from error


def _attempt_values(attempt: WorkflowNodeAttempt) -> dict[str, object]:
    return {
        "workflow_attempt_id": attempt.workflow_attempt_id,
        "workflow_step_id": attempt.workflow_step_id,
        "workflow_run_id": attempt.workflow_run_id,
        "workspace_id": attempt.workspace_id,
        "attempt_no": attempt.attempt_no,
        "executor_version": attempt.executor_version,
        "status": attempt.status,
        "input_hash": attempt.input_hash,
        "output_hash": attempt.output_hash,
        "usage": attempt.usage,
        "started_at": attempt.started_at,
        "completed_at": attempt.completed_at,
        "error_code": attempt.error_code,
    }


def _locked_run(session: Session, workflow_run_id: UUID) -> Row[Any]:
    row = session.execute(
        select(workflow_runs)
        .where(workflow_runs.c.workflow_run_id == workflow_run_id)
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise WorkflowExecutionStateError("工作流运行不存在")
    return row


def _resolve_running_execution(
    session: Session,
    workflow_run_id: UUID,
    workflow_step_id: UUID | None,
    workflow_attempt_id: UUID | None,
) -> tuple[UUID | None, UUID | None]:
    if workflow_step_id is None:
        step = session.execute(
            select(workflow_run_steps.c.workflow_step_id)
            .where(
                workflow_run_steps.c.workflow_run_id == workflow_run_id,
                workflow_run_steps.c.status == "running",
            )
            .order_by(workflow_run_steps.c.sequence_no.desc())
            .limit(1)
        ).one_or_none()
        workflow_step_id = step.workflow_step_id if step is not None else None
    if workflow_attempt_id is None and workflow_step_id is not None:
        attempt = session.execute(
            select(workflow_node_attempts.c.workflow_attempt_id)
            .where(
                workflow_node_attempts.c.workflow_step_id == workflow_step_id,
                workflow_node_attempts.c.status == "running",
            )
            .order_by(workflow_node_attempts.c.attempt_no.desc())
            .limit(1)
        ).one_or_none()
        workflow_attempt_id = attempt.workflow_attempt_id if attempt is not None else None
    return workflow_step_id, workflow_attempt_id


def _record_run_transition(
    session: Session,
    context: RequestContext,
    workflow_run_id: UUID,
    aggregate_version: int,
    action: str,
    status: str,
    occurred_at: datetime,
    *,
    error_code: str | None = None,
) -> None:
    """终态事件只保存状态、错误码和计数，不复制节点输入、输出或模型正文。"""

    attributes: dict[str, object] = {"status": status}
    if error_code is not None:
        attributes["error_code"] = error_code
    SqlAlchemyAuditWriter(session).add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type="workflow_instance",
            resource_id=workflow_run_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            attributes=attributes,
        )
    )
    SqlAlchemyOutboxWriter(session).add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type=action,
            workspace_id=context.workspace_id,
            aggregate_id=workflow_run_id,
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


def _authorized_index_rows(
    session: Session,
    context: RequestContext,
    decision: PolicyDecision,
    knowledge_base_ids: frozenset[UUID] | None,
) -> list[Row[Any]]:
    # 1. 数据库查询先固定空间、活动发布、文档状态和密级上限，未授权记录不进入候选集合。
    allowed_levels = tuple(
        level
        for level, rank in SECURITY_LEVEL_RANK.items()
        if rank <= SECURITY_LEVEL_RANK[decision.maximum_security_level]
    )
    statement = (
        select(
            index_versions.c.index_version_id,
            index_versions.c.knowledge_base_id,
            index_versions.c.document_id,
            index_versions.c.visibility,
            index_versions.c.department_ids,
            index_versions.c.security_level,
            documents.c.created_by_account_id,
        )
        .join(
            document_index_publications,
            and_(
                document_index_publications.c.workspace_id == index_versions.c.workspace_id,
                document_index_publications.c.document_id == index_versions.c.document_id,
                document_index_publications.c.index_version_id == index_versions.c.index_version_id,
            ),
        )
        .join(
            documents,
            and_(
                documents.c.workspace_id == index_versions.c.workspace_id,
                documents.c.document_id == index_versions.c.document_id,
            ),
        )
        .where(
            index_versions.c.workspace_id == context.workspace_id,
            index_versions.c.status == "active",
            index_versions.c.security_level.in_(allowed_levels),
            documents.c.status == "active",
        )
    )
    if knowledge_base_ids is not None:
        statement = statement.where(index_versions.c.knowledge_base_id.in_(knowledge_base_ids))
    rows = list(session.execute(statement))
    # 2. 再消费 PDP 的工作空间、资源、本人和部门范围；私有文档始终要求创建者本人。
    scope = decision.resource_scope
    if scope.workspace:
        return [
            row
            for row in rows
            if row.visibility != "private" or row.created_by_account_id == context.user_id
        ]
    return [
        row
        for row in rows
        if row.document_id in scope.resource_ids
        or row.created_by_account_id in scope.account_ids
        or (
            row.visibility == "departments" and bool(set(row.department_ids) & scope.department_ids)
        )
    ]


def _search_scope(
    context: RequestContext,
    decision: PolicyDecision,
    rows: list[Row[Any]],
    knowledge_base_ids: frozenset[UUID] | None,
) -> AuthorizedSearchScope:
    visibilities = cast(
        frozenset[Visibility],
        frozenset(row.visibility for row in rows),
    )
    levels = cast(
        frozenset[SecurityLevel],
        frozenset(row.security_level for row in rows),
    )
    return AuthorizedSearchScope(
        workspace_id=context.workspace_id,
        index_version_ids=frozenset(row.index_version_id for row in rows),
        knowledge_base_ids=knowledge_base_ids,
        document_ids=None,
        department_ids=decision.resource_scope.department_ids,
        visibilities=visibilities,
        security_levels=levels,
        field_mask=decision.field_mask,
        private_document_ids=frozenset(
            row.document_id for row in rows if row.visibility == "private"
        ),
        allow_all_departments=decision.resource_scope.workspace,
    )
