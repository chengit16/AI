"""实现审批运行聚合、动作事实和工作流业务同步的 PostgreSQL Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from types import TracebackType
from typing import Any, cast
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord
from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import CursorResult, exists, insert, or_, select, update
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.integration.domain.events import IntegrationEvent
from ai_platform_api.modules.workflow.domain.approval_runtime import (
    ApprovalActionFact,
    ApprovalAssignment,
    ApprovalAssignmentStatus,
    ApprovalInstance,
    ApprovalInstanceStatus,
    ApprovalRuntimeAction,
    ApprovalRuntimeDirectory,
    ApprovalRuntimeLevel,
    ApprovalRuntimeLevelStatus,
    ApprovalRuntimeRepository,
    ApprovalRuntimeState,
    ApprovalRuntimeTransition,
    ApprovalRuntimeUnitOfWork,
    ApprovalRuntimeWriteConflictError,
    ApprovalSubjectEvent,
    ApprovalSubjectLifecycle,
    ApprovalWorkflowOutcome,
)
from ai_platform_api.modules.workflow.domain.approvals import ApprovalSubject
from ai_platform_api.persistence.tables import (
    approval_actions,
    approval_assignments,
    approval_instance_levels,
    approval_instances,
    workflow_run_steps,
    workflow_runs,
    workspace_memberships,
)

SessionFactory = Callable[[], Session]
ApprovalSubjectLifecycleFactory = Callable[[Session], ApprovalSubjectLifecycle]


class SqlAlchemyApprovalRuntimeRepository(ApprovalRuntimeRepository):
    """以实例行锁串行化聚合更新，并把审批动作作为只追加事实保存。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_request_idempotency(
        self,
        workspace_id: UUID,
        requester_account_id: UUID,
        idempotency_key: str,
    ) -> ApprovalRuntimeState | None:
        row = self._session.execute(
            select(approval_instances).where(
                approval_instances.c.workspace_id == workspace_id,
                approval_instances.c.requester_account_id == requester_account_id,
                approval_instances.c.idempotency_key == idempotency_key,
            )
        ).one_or_none()
        return self._state_from_instance(row) if row is not None else None

    def get_state(
        self,
        workspace_id: UUID,
        approval_instance_id: UUID,
        *,
        for_update: bool = False,
    ) -> ApprovalRuntimeState | None:
        statement = select(approval_instances).where(
            approval_instances.c.workspace_id == workspace_id,
            approval_instances.c.approval_instance_id == approval_instance_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return self._state_from_instance(row) if row is not None else None

    def list_visible(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        limit: int,
    ) -> tuple[ApprovalRuntimeState, ...]:
        visible_assignment = approval_assignments.alias("visible_approval_assignment")
        rows = tuple(
            self._session.execute(
                select(approval_instances)
                .where(
                    approval_instances.c.workspace_id == workspace_id,
                    or_(
                        approval_instances.c.requester_account_id == account_id,
                        exists(
                            select(1).where(
                                visible_assignment.c.workspace_id == workspace_id,
                                visible_assignment.c.approval_instance_id
                                == approval_instances.c.approval_instance_id,
                                visible_assignment.c.approver_account_id == account_id,
                            )
                        ),
                    ),
                )
                .order_by(
                    approval_instances.c.updated_at.desc(),
                    approval_instances.c.approval_instance_id,
                )
                .limit(limit)
            )
        )
        return tuple(self._state_from_instance(row) for row in rows)

    def get_action_by_idempotency(
        self,
        workspace_id: UUID,
        approval_instance_id: UUID,
        actor_account_id: UUID,
        idempotency_key: str,
    ) -> ApprovalActionFact | None:
        row = self._session.execute(
            select(approval_actions).where(
                approval_actions.c.workspace_id == workspace_id,
                approval_actions.c.approval_instance_id == approval_instance_id,
                approval_actions.c.actor_account_id == actor_account_id,
                approval_actions.c.idempotency_key == idempotency_key,
            )
        ).one_or_none()
        return _action(row) if row is not None else None

    def add_state(self, state: ApprovalRuntimeState) -> None:
        """一次写入实例、全部冻结层级和初始指派，提交前捕获唯一约束竞争。"""

        try:
            self._session.execute(
                insert(approval_instances).values(**_instance_values(state.instance))
            )
            self._session.execute(
                insert(approval_instance_levels),
                [_level_values(level) for level in state.levels],
            )
            self._session.execute(
                insert(approval_assignments),
                [_assignment_values(assignment) for assignment in state.assignments],
            )
            self._session.flush()
        except IntegrityError as error:
            raise ApprovalRuntimeWriteConflictError from error

    def save_transition(
        self,
        transition: ApprovalRuntimeTransition,
        *,
        expected_version: int,
    ) -> bool:
        """以实例乐观锁保护整个聚合，更新层级与指派后只追加动作事实。"""

        state = transition.state
        try:
            # 1. 实例版本是整个聚合的唯一并发令牌，更新失败时不得写入任何子事实。
            result = cast(
                CursorResult[Any],
                self._session.execute(
                    update(approval_instances)
                    .where(
                        approval_instances.c.approval_instance_id
                        == state.instance.approval_instance_id,
                        approval_instances.c.workspace_id == state.instance.workspace_id,
                        approval_instances.c.version == expected_version,
                    )
                    .values(**_instance_mutable_values(state.instance))
                ),
            )
            if result.rowcount != 1:
                return False
            # 2. 实例锁生效后覆盖子快照并只追加动作，新转交指派按稳定 ID 区分插入与更新。
            for level in state.levels:
                self._session.execute(
                    update(approval_instance_levels)
                    .where(approval_instance_levels.c.approval_level_id == level.approval_level_id)
                    .values(**_level_mutable_values(level))
                )
            existing_ids = frozenset(
                self._session.scalars(
                    select(approval_assignments.c.approval_assignment_id).where(
                        approval_assignments.c.approval_instance_id
                        == state.instance.approval_instance_id
                    )
                )
            )
            for assignment in state.assignments:
                if assignment.approval_assignment_id in existing_ids:
                    self._session.execute(
                        update(approval_assignments)
                        .where(
                            approval_assignments.c.approval_assignment_id
                            == assignment.approval_assignment_id
                        )
                        .values(**_assignment_mutable_values(assignment))
                    )
                else:
                    self._session.execute(
                        insert(approval_assignments).values(**_assignment_values(assignment))
                    )
            self._session.execute(
                insert(approval_actions).values(**_action_values(transition.action))
            )
            self._session.flush()
            return True
        except IntegrityError as error:
            raise ApprovalRuntimeWriteConflictError from error

    def due_instance_ids(
        self,
        workspace_id: UUID,
        *,
        now: Any,
        limit: int,
    ) -> tuple[UUID, ...]:
        return tuple(
            self._session.scalars(
                select(approval_instance_levels.c.approval_instance_id)
                .join(
                    approval_instances,
                    approval_instances.c.approval_instance_id
                    == approval_instance_levels.c.approval_instance_id,
                )
                .where(
                    approval_instance_levels.c.workspace_id == workspace_id,
                    approval_instances.c.status == "pending",
                    approval_instance_levels.c.status == "active",
                    or_(
                        approval_instance_levels.c.timeout_at <= now,
                        (
                            (approval_instance_levels.c.reminder_at <= now)
                            & approval_instance_levels.c.reminded_at.is_(None)
                        ),
                    ),
                )
                .order_by(
                    approval_instance_levels.c.timeout_at,
                    approval_instance_levels.c.reminder_at,
                    approval_instance_levels.c.approval_instance_id,
                )
                .limit(limit)
            )
        )

    def apply_workflow_outcome(
        self,
        state: ApprovalRuntimeState,
        outcome: ApprovalWorkflowOutcome,
        *,
        now: Any,
    ) -> int | None:
        """审批终态与等待 Step/Run 同事务推进，执行恢复仅在提交后发生。"""

        instance = state.instance
        if instance.workflow_run_id is None or instance.workflow_step_id is None:
            return None
        # 1. Step 与 Run 必须同时仍在等待审批，任一状态漂移都拒绝产生部分业务结果。
        step = self._session.execute(
            select(workflow_run_steps)
            .where(
                workflow_run_steps.c.workflow_step_id == instance.workflow_step_id,
                workflow_run_steps.c.workflow_run_id == instance.workflow_run_id,
                workflow_run_steps.c.workspace_id == instance.workspace_id,
            )
            .with_for_update()
        ).one_or_none()
        run = self._session.execute(
            select(workflow_runs)
            .where(
                workflow_runs.c.workflow_run_id == instance.workflow_run_id,
                workflow_runs.c.workspace_id == instance.workspace_id,
            )
            .with_for_update()
        ).one_or_none()
        if (
            step is None
            or run is None
            or step.status != "waiting_approval"
            or run.status != "waiting_approval"
        ):
            raise ApprovalRuntimeWriteConflictError
        output = dict(step.output_payload or {})
        output.update(
            {
                "approval_instance_id": str(instance.approval_instance_id),
                "approval_status": instance.status,
            }
        )
        next_version = cast(int, run.version) + 1
        # 2. 通过重新排队，驳回和撤回进入不同终态及稳定错误码，便于调用方准确恢复。
        if outcome == "resume":
            step_status, run_status, error_code, completed_at = "succeeded", "queued", None, None
        elif outcome == "reject":
            step_status, run_status, error_code, completed_at = (
                "failed",
                "failed",
                "APPROVAL_REJECTED",
                now,
            )
        else:
            step_status, run_status, error_code, completed_at = (
                "failed",
                "cancelled",
                "APPROVAL_WITHDRAWN",
                now,
            )
        # 3. Step 与 Run 在审批事务内连续更新；审计和 Outbox 随后使用同一聚合版本提交。
        self._session.execute(
            update(workflow_run_steps)
            .where(workflow_run_steps.c.workflow_step_id == instance.workflow_step_id)
            .values(
                status=step_status,
                output_payload=output,
                completed_at=now,
                error_code=error_code,
            )
        )
        self._session.execute(
            update(workflow_runs)
            .where(workflow_runs.c.workflow_run_id == instance.workflow_run_id)
            .values(
                status=run_status,
                updated_at=now,
                completed_at=completed_at,
                error_code=error_code,
                version=next_version,
            )
        )
        return next_version

    def _state_from_instance(self, row: Row[Any]) -> ApprovalRuntimeState:
        levels = tuple(
            _level(item)
            for item in self._session.execute(
                select(approval_instance_levels)
                .where(
                    approval_instance_levels.c.approval_instance_id == row.approval_instance_id,
                    approval_instance_levels.c.workspace_id == row.workspace_id,
                )
                .order_by(approval_instance_levels.c.sequence_no)
            )
        )
        assignments = tuple(
            _assignment(item)
            for item in self._session.execute(
                select(approval_assignments)
                .where(
                    approval_assignments.c.approval_instance_id == row.approval_instance_id,
                    approval_assignments.c.workspace_id == row.workspace_id,
                )
                .order_by(
                    approval_assignments.c.created_at,
                    approval_assignments.c.approval_assignment_id,
                )
            )
        )
        return ApprovalRuntimeState(_instance(row), levels, assignments)


class SqlAlchemyApprovalRuntimeDirectory(ApprovalRuntimeDirectory):
    """从成员事实过滤仍可接收转交或超时升级的活动账号。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def active_account_ids(
        self,
        workspace_id: UUID,
        account_ids: tuple[UUID, ...],
    ) -> frozenset[UUID]:
        if not account_ids:
            return frozenset()
        return frozenset(
            self._session.scalars(
                select(workspace_memberships.c.account_id).where(
                    workspace_memberships.c.workspace_id == workspace_id,
                    workspace_memberships.c.account_id.in_(account_ids),
                    workspace_memberships.c.status == "active",
                )
            )
        )


class NoopApprovalSubjectLifecycle(ApprovalSubjectLifecycle):
    """让未注册业务扩展的审批主题保持原有独立运行行为。"""

    def bind(
        self,
        state: ApprovalRuntimeState,
        subject: ApprovalSubject,
    ) -> ApprovalSubjectEvent | None:
        del state, subject
        return None

    def apply_transition(
        self,
        previous: ApprovalRuntimeState,
        transition: ApprovalRuntimeTransition,
    ) -> ApprovalSubjectEvent | None:
        del previous, transition
        return None


class RoutedApprovalSubjectLifecycle(ApprovalSubjectLifecycle):
    """按冻结资源类型把审批主题分发给唯一业务生命周期。

    路由在组合根中显式注册，避免多个生命周期同时写入同一审批事务，也避免
    后加入的工具审批替换既有 Agent 发布审批。
    """

    def __init__(self, routes: dict[str, ApprovalSubjectLifecycle]) -> None:
        if not routes or any(not key for key in routes):
            raise ValueError("审批主题生命周期路由不能为空")
        self._routes = dict(routes)

    def bind(
        self,
        state: ApprovalRuntimeState,
        subject: ApprovalSubject,
    ) -> ApprovalSubjectEvent | None:
        lifecycle = self._routes.get(subject.resource_type)
        return lifecycle.bind(state, subject) if lifecycle is not None else None

    def apply_transition(
        self,
        previous: ApprovalRuntimeState,
        transition: ApprovalRuntimeTransition,
    ) -> ApprovalSubjectEvent | None:
        lifecycle = self._routes.get(previous.instance.resource_type)
        return lifecycle.apply_transition(previous, transition) if lifecycle is not None else None


class SqlAlchemyApprovalRuntimeUnitOfWork(ApprovalRuntimeUnitOfWork):
    """为审批聚合、工作流业务、审计和 Outbox 提供不可嵌套事务。"""

    def __init__(
        self,
        session_factory: SessionFactory,
        subject_lifecycle_factory: ApprovalSubjectLifecycleFactory | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._subject_lifecycle_factory = subject_lifecycle_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyApprovalRuntimeRepository,
                SqlAlchemyApprovalRuntimeDirectory,
                ApprovalSubjectLifecycle,
                SqlAlchemyAuditWriter,
                SqlAlchemyOutboxWriter,
            ]
            | None
        ] = ContextVar("approval_runtime_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyApprovalRuntimeUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Approval Runtime Unit of Work 不允许重复进入")
        session = self._session_factory()
        subjects = (
            self._subject_lifecycle_factory(session)
            if self._subject_lifecycle_factory is not None
            else NoopApprovalSubjectLifecycle()
        )
        self._state.set(
            (
                session,
                SqlAlchemyApprovalRuntimeRepository(session),
                SqlAlchemyApprovalRuntimeDirectory(session),
                subjects,
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
    def runtimes(self) -> SqlAlchemyApprovalRuntimeRepository:
        return self._require_state()[1]

    @property
    def directory(self) -> SqlAlchemyApprovalRuntimeDirectory:
        return self._require_state()[2]

    @property
    def subjects(self) -> ApprovalSubjectLifecycle:
        return self._require_state()[3]

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        return self._require_state()[4]

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        return self._require_state()[5]

    def commit(self) -> None:
        try:
            self._require_state()[0].commit()
        except IntegrityError as error:
            self._require_state()[0].rollback()
            raise ApprovalRuntimeWriteConflictError from error

    def _require_state(
        self,
    ) -> tuple[
        Session,
        SqlAlchemyApprovalRuntimeRepository,
        SqlAlchemyApprovalRuntimeDirectory,
        ApprovalSubjectLifecycle,
        SqlAlchemyAuditWriter,
        SqlAlchemyOutboxWriter,
    ]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Approval Runtime Unit of Work 尚未进入事务范围")
        return state


def record_embedded_approval_created(
    session: Session,
    context: RequestContext,
    state: ApprovalRuntimeState,
) -> None:
    """在工作流等待事务内记录审批实例创建，避免业务先等待但审批事实尚不可见。"""

    instance = state.instance
    attributes = {"chain_digest": instance.chain_digest, "level_count": len(state.levels)}
    SqlAlchemyAuditWriter(session).add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=instance.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action="approval.instance.created",
            resource_type="approval_instance",
            resource_id=instance.approval_instance_id,
            outcome="succeeded",
            occurred_at=instance.created_at,
            request_id=context.request_id,
            trace_id=instance.trace_id,
            traceparent=instance.traceparent,
            authorization=context.audit_authorization,
            attributes=attributes,
        )
    )
    SqlAlchemyOutboxWriter(session).add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type="approval.instance.created",
            workspace_id=instance.workspace_id,
            aggregate_id=instance.approval_instance_id,
            aggregate_version=instance.version,
            occurred_at=instance.created_at,
            trace_id=instance.trace_id,
            traceparent=instance.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=attributes,
        )
    )


def _instance(row: Row[Any]) -> ApprovalInstance:
    return ApprovalInstance(
        row.approval_instance_id,
        row.workspace_id,
        row.approval_policy_id,
        row.approval_policy_version_id,
        row.requester_account_id,
        row.resource_type,
        row.operation,
        row.resource_id,
        row.subject_digest,
        row.chain_digest,
        row.personal_owner_confirmation,
        cast(ApprovalInstanceStatus, row.status),
        row.current_sequence_no,
        row.idempotency_key,
        row.request_hash,
        row.workflow_run_id,
        row.workflow_step_id,
        row.trace_id,
        row.traceparent,
        row.created_at,
        row.updated_at,
        row.completed_at,
        row.version,
    )


def _level(row: Row[Any]) -> ApprovalRuntimeLevel:
    return ApprovalRuntimeLevel(
        row.approval_level_id,
        row.approval_instance_id,
        row.workspace_id,
        row.sequence_no,
        cast(Any, row.mode),
        cast(ApprovalRuntimeLevelStatus, row.status),
        row.reminder_after_minutes,
        row.timeout_after_minutes,
        cast(Any, row.timeout_action),
        tuple(row.fallback_approver_account_ids),
        row.fallback_activated,
        row.reminder_at,
        row.reminded_at,
        row.timeout_at,
        row.activated_at,
        row.completed_at,
        row.version,
    )


def _assignment(row: Row[Any]) -> ApprovalAssignment:
    return ApprovalAssignment(
        row.approval_assignment_id,
        row.approval_instance_id,
        row.approval_level_id,
        row.workspace_id,
        row.approver_account_id,
        cast(ApprovalAssignmentStatus, row.status),
        row.transferred_to_account_id,
        row.created_at,
        row.decided_at,
        row.version,
    )


def _action(row: Row[Any]) -> ApprovalActionFact:
    return ApprovalActionFact(
        row.approval_action_id,
        row.approval_instance_id,
        row.approval_level_id,
        row.workspace_id,
        row.actor_account_id,
        cast(ApprovalRuntimeAction, row.action),
        row.idempotency_key,
        row.target_account_id,
        row.reason_code,
        row.occurred_at,
    )


def _instance_values(instance: ApprovalInstance) -> dict[str, object]:
    return {
        "approval_instance_id": instance.approval_instance_id,
        "workspace_id": instance.workspace_id,
        "approval_policy_id": instance.approval_policy_id,
        "approval_policy_version_id": instance.approval_policy_version_id,
        "requester_account_id": instance.requester_account_id,
        "resource_type": instance.resource_type,
        "operation": instance.operation,
        "resource_id": instance.resource_id,
        "subject_digest": instance.subject_digest,
        "chain_digest": instance.chain_digest,
        "personal_owner_confirmation": instance.personal_owner_confirmation,
        "status": instance.status,
        "current_sequence_no": instance.current_sequence_no,
        "idempotency_key": instance.idempotency_key,
        "request_hash": instance.request_hash,
        "workflow_run_id": instance.workflow_run_id,
        "workflow_step_id": instance.workflow_step_id,
        "trace_id": instance.trace_id,
        "traceparent": instance.traceparent,
        "created_at": instance.created_at,
        "updated_at": instance.updated_at,
        "completed_at": instance.completed_at,
        "version": instance.version,
    }


def _instance_mutable_values(instance: ApprovalInstance) -> dict[str, object]:
    return {
        "status": instance.status,
        "current_sequence_no": instance.current_sequence_no,
        "updated_at": instance.updated_at,
        "completed_at": instance.completed_at,
        "version": instance.version,
    }


def _level_values(level: ApprovalRuntimeLevel) -> dict[str, object]:
    return {
        "approval_level_id": level.approval_level_id,
        "approval_instance_id": level.approval_instance_id,
        "workspace_id": level.workspace_id,
        "sequence_no": level.sequence_no,
        "mode": level.mode,
        "status": level.status,
        "reminder_after_minutes": level.reminder_after_minutes,
        "timeout_after_minutes": level.timeout_after_minutes,
        "timeout_action": level.timeout_action,
        "fallback_approver_account_ids": list(level.fallback_approver_account_ids),
        "fallback_activated": level.fallback_activated,
        "reminder_at": level.reminder_at,
        "reminded_at": level.reminded_at,
        "timeout_at": level.timeout_at,
        "activated_at": level.activated_at,
        "completed_at": level.completed_at,
        "version": level.version,
    }


def _level_mutable_values(level: ApprovalRuntimeLevel) -> dict[str, object]:
    values = _level_values(level)
    for key in ("approval_level_id", "approval_instance_id", "workspace_id", "sequence_no", "mode"):
        values.pop(key)
    return values


def _assignment_values(assignment: ApprovalAssignment) -> dict[str, object]:
    return {
        "approval_assignment_id": assignment.approval_assignment_id,
        "approval_instance_id": assignment.approval_instance_id,
        "approval_level_id": assignment.approval_level_id,
        "workspace_id": assignment.workspace_id,
        "approver_account_id": assignment.approver_account_id,
        "status": assignment.status,
        "transferred_to_account_id": assignment.transferred_to_account_id,
        "created_at": assignment.created_at,
        "decided_at": assignment.decided_at,
        "version": assignment.version,
    }


def _assignment_mutable_values(assignment: ApprovalAssignment) -> dict[str, object]:
    return {
        "status": assignment.status,
        "transferred_to_account_id": assignment.transferred_to_account_id,
        "decided_at": assignment.decided_at,
        "version": assignment.version,
    }


def _action_values(action: ApprovalActionFact) -> dict[str, object]:
    return {
        "approval_action_id": action.approval_action_id,
        "approval_instance_id": action.approval_instance_id,
        "approval_level_id": action.approval_level_id,
        "workspace_id": action.workspace_id,
        "actor_account_id": action.actor_account_id,
        "action": action.action,
        "idempotency_key": action.idempotency_key,
        "target_account_id": action.target_account_id,
        "reason_code": action.reason_code,
        "occurred_at": action.occurred_at,
    }
