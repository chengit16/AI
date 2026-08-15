"""编排审批实例创建、幂等动作、超时处理和工作流业务状态同步。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord
from ai_platform_backend.observability import observed_operation

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.integration.domain.events import IntegrationEvent
from ai_platform_api.modules.workflow.application.approvals import ApprovalPolicyService
from ai_platform_api.modules.workflow.domain.approval_runtime import (
    ApprovalActionFact,
    ApprovalRuntimeCommand,
    ApprovalRuntimeDeniedError,
    ApprovalRuntimeState,
    ApprovalRuntimeStateError,
    ApprovalRuntimeTransition,
    ApprovalRuntimeUnitOfWork,
    ApprovalRuntimeValidationError,
    ApprovalRuntimeWriteConflictError,
    ApprovalSubjectEvent,
    ApprovalWorkflowResume,
    apply_approval_command,
    apply_due_approval_event,
    approval_subject_digest,
    create_approval_runtime,
)
from ai_platform_api.modules.workflow.domain.approvals import ApprovalRiskLevel, ApprovalSubject

__all__ = [
    "ApprovalActionFact",
    "ApprovalCommandResult",
    "ApprovalInstanceConflict",
    "ApprovalInstanceDenied",
    "ApprovalInstanceNotFound",
    "ApprovalInstanceService",
    "ApprovalInstanceStateConflict",
    "ApprovalInstanceValidation",
    "ApprovalRiskLevel",
    "ApprovalRuntimeCommand",
    "ApprovalRuntimeState",
    "ApprovalSubject",
    "SecurityLevel",
]


class ApprovalInstanceDenied(PlatformError):
    """当前账号无权读取实例或执行指定审批动作。"""

    error_code = "APPROVAL_ACTION_DENIED"


class ApprovalInstanceNotFound(PlatformError):
    """审批实例在当前工作空间内不存在。"""

    error_code = "RESOURCE_NOT_FOUND"


class ApprovalInstanceValidation(PlatformError):
    """幂等键、原因码、转交目标或审批主题无效。"""

    error_code = "APPROVAL_INSTANCE_INVALID"


class ApprovalInstanceConflict(PlatformError):
    """创建幂等键或并发命令与已有审批事实冲突。"""

    error_code = "APPROVAL_INSTANCE_CONFLICT"


class ApprovalInstanceStateConflict(PlatformError):
    """审批实例或关联业务已经离开命令要求的前置状态。"""

    error_code = "APPROVAL_STATE_CONFLICT"


@dataclass(frozen=True)
class ApprovalCommandResult:
    """返回最新审批聚合、是否为幂等重放及提交后工作流恢复信息。"""

    state: ApprovalRuntimeState
    replayed: bool
    resume: ApprovalWorkflowResume | None = None


class ApprovalInstanceService:
    """以实例版本和动作幂等键串行化审批命令，并同步关联业务状态。"""

    def __init__(
        self,
        unit_of_work: ApprovalRuntimeUnitOfWork,
        policies: ApprovalPolicyService,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policies = policies

    @observed_operation(component="approval", operation="start")
    def start(
        self,
        context: RequestContext,
        *,
        subject: ApprovalSubject,
        idempotency_key: str,
    ) -> ApprovalCommandResult:
        """按当前策略解析链并创建独立审批实例，相同请求只产生一个聚合。"""

        account_id = _browser_account(context)
        if (
            subject.workspace_id != context.workspace_id
            or subject.requester_account_id != account_id
        ):
            raise ApprovalInstanceDenied
        try:
            # 1. 先按主题摘要回放原请求；策略修订不能改变已经成功创建的幂等结果。
            subject_digest = approval_subject_digest(subject)
            with self._unit_of_work as unit_of_work:
                existing = unit_of_work.runtimes.get_by_request_idempotency(
                    context.workspace_id,
                    account_id,
                    idempotency_key,
                )
            if existing is not None:
                if existing.instance.subject_digest != subject_digest:
                    raise ApprovalInstanceConflict
                return ApprovalCommandResult(existing, True)
            # 2. 策略与组织目录在只读快照中确定审批链，运行事实随后冻结版本和摘要。
            chain = self._policies.preview_chain(context, subject=subject)
            state = create_approval_runtime(
                chain,
                subject,
                idempotency_key=idempotency_key,
                trace_id=context.trace.trace_id,
                traceparent=context.trace.traceparent,
                now=datetime.now(UTC),
            )
            # 3. 写入前再次检查并发请求；同键不同主题始终拒绝覆盖原审批事实。
            with self._unit_of_work as unit_of_work:
                existing = unit_of_work.runtimes.get_by_request_idempotency(
                    context.workspace_id,
                    account_id,
                    idempotency_key,
                )
                if existing is not None:
                    if existing.instance.subject_digest != state.instance.subject_digest:
                        raise ApprovalInstanceConflict
                    return ApprovalCommandResult(existing, True)
                unit_of_work.runtimes.add_state(state)
                subject_event = unit_of_work.subjects.bind(state, subject)
                _record_created(unit_of_work, context, state)
                if subject_event is not None:
                    _record_subject_event(
                        unit_of_work,
                        context,
                        state,
                        subject_event,
                        occurred_at=state.instance.created_at,
                    )
                unit_of_work.commit()
                return ApprovalCommandResult(state, False)
        except ApprovalRuntimeValidationError as error:
            raise ApprovalInstanceValidation from error
        except ApprovalRuntimeDeniedError as error:
            raise ApprovalInstanceDenied from error
        except ApprovalRuntimeStateError as error:
            raise ApprovalInstanceStateConflict from error
        except ApprovalRuntimeWriteConflictError as error:
            # 唯一约束竞争可能来自同一幂等请求；回滚后重新读取才能区分回放与真实冲突。
            with self._unit_of_work as unit_of_work:
                existing = unit_of_work.runtimes.get_by_request_idempotency(
                    context.workspace_id,
                    account_id,
                    idempotency_key,
                )
            if existing is not None and existing.instance.subject_digest == approval_subject_digest(
                subject
            ):
                return ApprovalCommandResult(existing, True)
            raise ApprovalInstanceConflict from error

    def list(
        self,
        context: RequestContext,
        *,
        limit: int,
    ) -> tuple[ApprovalRuntimeState, ...]:
        """只列出申请人或曾被指派审批人可见的实例，不因菜单权限扩大数据范围。"""

        account_id = _browser_account(context)
        if not 1 <= limit <= 200:
            raise ApprovalInstanceValidation
        with self._unit_of_work as unit_of_work:
            return unit_of_work.runtimes.list_visible(
                context.workspace_id,
                account_id,
                limit=limit,
            )

    def get(
        self,
        context: RequestContext,
        *,
        approval_instance_id: UUID,
    ) -> ApprovalRuntimeState:
        """读取参与者可见的审批实例、层级和指派，不返回审批条件字段原文。"""

        account_id = _browser_account(context)
        _require_scope(context, approval_instance_id)
        with self._unit_of_work as unit_of_work:
            state = unit_of_work.runtimes.get_state(context.workspace_id, approval_instance_id)
        if state is None:
            raise ApprovalInstanceNotFound
        if not _visible_to(state, account_id):
            raise ApprovalInstanceDenied
        return state

    @observed_operation(component="approval", operation="act")
    def act(
        self,
        context: RequestContext,
        *,
        approval_instance_id: UUID,
        command: ApprovalRuntimeCommand,
    ) -> ApprovalCommandResult:
        """应用人工动作；审批、业务状态、审计与 Outbox 在一次事务中提交。"""

        account_id = _browser_account(context)
        _require_scope(context, approval_instance_id)
        if command.actor_account_id != account_id:
            raise ApprovalInstanceDenied
        try:
            with self._unit_of_work as unit_of_work:
                # 1. 先锁定实例再读取动作事实，保证并发重复请求能看到前一个事务的提交。
                state = _require_state(
                    unit_of_work,
                    context.workspace_id,
                    approval_instance_id,
                    for_update=True,
                )
                existing_action = unit_of_work.runtimes.get_action_by_idempotency(
                    context.workspace_id,
                    approval_instance_id,
                    account_id,
                    command.idempotency_key,
                )
                if existing_action is not None:
                    _require_same_command(existing_action, command)
                    return ApprovalCommandResult(state, True)
                target_ids = (command.target_account_id,) if command.target_account_id else ()
                active_targets = unit_of_work.directory.active_account_ids(
                    context.workspace_id,
                    target_ids,
                )
                transition = apply_approval_command(
                    state,
                    command,
                    active_target_accounts=active_targets,
                    now=datetime.now(UTC),
                )
                # 2. 审批终态和关联工作流必须在同一事务推进，Outbox 不得先于业务状态可见。
                workflow_version, subject_event = _persist_transition(
                    unit_of_work,
                    state,
                    transition,
                )
                _record_transition(unit_of_work, context, transition)
                if subject_event is not None:
                    _record_subject_event(
                        unit_of_work,
                        context,
                        transition.state,
                        subject_event,
                        occurred_at=transition.action.occurred_at,
                    )
                if workflow_version is not None:
                    _record_workflow_outcome(
                        unit_of_work,
                        context,
                        transition,
                        workflow_version,
                    )
                unit_of_work.commit()
                return ApprovalCommandResult(
                    transition.state,
                    False,
                    _resume(transition) if workflow_version is not None else None,
                )
        except ApprovalRuntimeValidationError as error:
            raise ApprovalInstanceValidation from error
        except ApprovalRuntimeDeniedError as error:
            raise ApprovalInstanceDenied from error
        except ApprovalRuntimeStateError as error:
            raise ApprovalInstanceStateConflict from error
        except ApprovalRuntimeWriteConflictError as error:
            raise ApprovalInstanceConflict from error

    @observed_operation(component="approval", operation="process_due")
    def process_due(
        self,
        context: RequestContext,
        *,
        limit: int,
        now: datetime | None = None,
    ) -> tuple[ApprovalCommandResult, ...]:
        """处理到期提醒和超时动作；每个实例使用独立短事务降低批量锁范围。"""

        actor_account_id = _browser_account(context)
        if not 1 <= limit <= 100:
            raise ApprovalInstanceValidation
        occurred_at = now or datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            instance_ids = unit_of_work.runtimes.due_instance_ids(
                context.workspace_id,
                now=occurred_at,
                limit=limit,
            )
        results: list[ApprovalCommandResult] = []
        # 每个到期实例单独提交，单个冲突不会扩大为整批回滚或长时间持锁。
        for instance_id in instance_ids:
            result = self._process_one_due(
                context,
                instance_id,
                actor_account_id=actor_account_id,
                now=occurred_at,
            )
            if result is not None:
                results.append(result)
        return tuple(results)

    def _process_one_due(
        self,
        context: RequestContext,
        approval_instance_id: UUID,
        *,
        actor_account_id: UUID,
        now: datetime,
    ) -> ApprovalCommandResult | None:
        with self._unit_of_work as unit_of_work:
            # 1. 锁定单个实例并从冻结层级生成稳定到期幂等键，重复扫描不会制造重复动作。
            state = _require_state(
                unit_of_work,
                context.workspace_id,
                approval_instance_id,
                for_update=True,
            )
            active_level = next(
                (level for level in state.levels if level.status == "active"),
                None,
            )
            if active_level is None:
                return None
            due_marker = active_level.timeout_at or active_level.reminder_at or now
            idempotency_key = (
                f"due:{active_level.approval_level_id}:{int(due_marker.timestamp())}:"
                f"{state.instance.version}"
            )
            active_fallback = unit_of_work.directory.active_account_ids(
                context.workspace_id,
                active_level.fallback_approver_account_ids,
            )
            transition = apply_due_approval_event(
                state,
                actor_account_id=actor_account_id,
                active_fallback_accounts=active_fallback,
                idempotency_key=idempotency_key,
                now=now,
            )
            if transition is None:
                return None
            # 2. 聚合、关联工作流、审计与 Outbox 共用当前短事务，提交后才允许恢复执行。
            workflow_version, subject_event = _persist_transition(
                unit_of_work,
                state,
                transition,
            )
            _record_transition(unit_of_work, context, transition)
            if subject_event is not None:
                _record_subject_event(
                    unit_of_work,
                    context,
                    transition.state,
                    subject_event,
                    occurred_at=transition.action.occurred_at,
                )
            if workflow_version is not None:
                _record_workflow_outcome(unit_of_work, context, transition, workflow_version)
            unit_of_work.commit()
            return ApprovalCommandResult(
                transition.state,
                False,
                _resume(transition) if workflow_version is not None else None,
            )


def _persist_transition(
    unit_of_work: ApprovalRuntimeUnitOfWork,
    previous: ApprovalRuntimeState,
    transition: ApprovalRuntimeTransition,
) -> tuple[int | None, ApprovalSubjectEvent | None]:
    if not unit_of_work.runtimes.save_transition(
        transition,
        expected_version=previous.instance.version,
    ):
        raise ApprovalRuntimeWriteConflictError
    subject_event = unit_of_work.subjects.apply_transition(previous, transition)
    if transition.workflow_outcome is None:
        return None, subject_event
    workflow_version = unit_of_work.runtimes.apply_workflow_outcome(
        transition.state,
        transition.workflow_outcome,
        now=transition.action.occurred_at,
    )
    return workflow_version, subject_event


def _require_state(
    unit_of_work: ApprovalRuntimeUnitOfWork,
    workspace_id: UUID,
    approval_instance_id: UUID,
    *,
    for_update: bool,
) -> ApprovalRuntimeState:
    state = unit_of_work.runtimes.get_state(
        workspace_id,
        approval_instance_id,
        for_update=for_update,
    )
    if state is None:
        raise ApprovalInstanceNotFound
    return state


def _browser_account(context: RequestContext) -> UUID:
    if (
        context.authentication_method != "browser_session"
        or context.user_id is None
        or context.user_id != context.actor_id
    ):
        raise ApprovalInstanceDenied
    return context.user_id


def _require_scope(context: RequestContext, approval_instance_id: UUID) -> None:
    if (
        not context.authorized_workspace
        and approval_instance_id not in context.authorized_resource_ids
    ):
        raise ApprovalInstanceDenied


def _visible_to(state: ApprovalRuntimeState, account_id: UUID) -> bool:
    return state.instance.requester_account_id == account_id or any(
        assignment.approver_account_id == account_id for assignment in state.assignments
    )


def _require_same_command(action: ApprovalActionFact, command: ApprovalRuntimeCommand) -> None:
    if (
        action.action != command.action
        or action.target_account_id != command.target_account_id
        or action.reason_code != command.reason_code
    ):
        raise ApprovalInstanceConflict


def _resume(transition: ApprovalRuntimeTransition) -> ApprovalWorkflowResume | None:
    instance = transition.state.instance
    if transition.workflow_outcome != "resume" or instance.workflow_run_id is None:
        return None
    return ApprovalWorkflowResume(
        instance.workflow_run_id,
        instance.requester_account_id,
        instance.workspace_id,
        instance.trace_id,
        instance.traceparent,
    )


def _record_created(
    unit_of_work: ApprovalRuntimeUnitOfWork,
    context: RequestContext,
    state: ApprovalRuntimeState,
) -> None:
    _record(
        unit_of_work,
        context,
        state,
        "approval.instance.created",
        state.instance.created_at,
        {"chain_digest": state.instance.chain_digest, "level_count": len(state.levels)},
    )


def _record_transition(
    unit_of_work: ApprovalRuntimeUnitOfWork,
    context: RequestContext,
    transition: ApprovalRuntimeTransition,
) -> None:
    action = transition.action
    attributes: dict[str, object] = {
        "approval_action_id": str(action.approval_action_id),
        "approval_status": transition.state.instance.status,
        "current_sequence_no": transition.state.instance.current_sequence_no,
        "action": action.action,
    }
    if action.approval_level_id is not None:
        attributes["approval_level_id"] = str(action.approval_level_id)
    _record(
        unit_of_work,
        context,
        transition.state,
        f"approval.instance.{action.action}",
        action.occurred_at,
        attributes,
    )


def _record_subject_event(
    unit_of_work: ApprovalRuntimeUnitOfWork,
    context: RequestContext,
    state: ApprovalRuntimeState,
    event: ApprovalSubjectEvent,
    *,
    occurred_at: datetime,
) -> None:
    """把业务主题状态变化与审批事务共同写入审计和 Outbox。"""

    attributes = {
        **event.attributes,
        "approval_instance_id": str(state.instance.approval_instance_id),
        "approval_status": state.instance.status,
    }
    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=state.instance.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=event.event_type,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes=attributes,
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type=event.event_type,
            workspace_id=state.instance.workspace_id,
            aggregate_id=event.aggregate_id,
            aggregate_version=event.aggregate_version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=attributes,
        )
    )


def _record(
    unit_of_work: ApprovalRuntimeUnitOfWork,
    context: RequestContext,
    state: ApprovalRuntimeState,
    action: str,
    occurred_at: datetime,
    attributes: dict[str, object],
) -> None:
    instance = state.instance
    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=instance.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type="approval_instance",
            resource_id=instance.approval_instance_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes=attributes,
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type=action,
            workspace_id=instance.workspace_id,
            aggregate_id=instance.approval_instance_id,
            aggregate_version=instance.version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=attributes,
        )
    )


def _record_workflow_outcome(
    unit_of_work: ApprovalRuntimeUnitOfWork,
    context: RequestContext,
    transition: ApprovalRuntimeTransition,
    workflow_version: int,
) -> None:
    instance = transition.state.instance
    if instance.workflow_run_id is None or transition.workflow_outcome is None:
        return
    event_type = {
        "resume": "workflow.run.queued_after_approval",
        "reject": "workflow.run.rejected_by_approval",
        "withdraw": "workflow.run.cancelled_by_approval",
    }[transition.workflow_outcome]
    attributes: dict[str, object] = {
        "approval_instance_id": str(instance.approval_instance_id),
        "approval_status": instance.status,
    }
    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=instance.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=event_type,
            resource_type="workflow_run",
            resource_id=instance.workflow_run_id,
            outcome="succeeded",
            occurred_at=transition.action.occurred_at,
            request_id=context.request_id,
            trace_id=instance.trace_id,
            traceparent=instance.traceparent,
            authorization=context.audit_authorization,
            attributes=attributes,
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type=event_type,
            workspace_id=instance.workspace_id,
            aggregate_id=instance.workflow_run_id,
            aggregate_version=workflow_version,
            occurred_at=transition.action.occurred_at,
            trace_id=instance.trace_id,
            traceparent=instance.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=attributes,
        )
    )
