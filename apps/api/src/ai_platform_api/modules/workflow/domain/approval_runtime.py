"""定义审批实例、层级、指派、动作事实和确定性状态转换。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditWriter

from ai_platform_api.modules.integration.domain.events import OutboxWriter
from ai_platform_api.modules.workflow.domain.approvals import ApprovalChain, ApprovalSubject

ApprovalInstanceStatus = Literal["pending", "approved", "rejected", "withdrawn"]
ApprovalRuntimeLevelStatus = Literal[
    "waiting",
    "active",
    "approved",
    "rejected",
    "withdrawn",
]
ApprovalAssignmentStatus = Literal[
    "waiting",
    "pending",
    "approved",
    "rejected",
    "transferred",
    "cancelled",
]
ApprovalRuntimeAction = Literal[
    "approve",
    "reject",
    "transfer",
    "withdraw",
    "remind",
    "escalate",
    "timeout_transfer",
    "timeout_reject",
    "timeout_wait",
]
ApprovalWorkflowOutcome = Literal["resume", "reject", "withdraw"]

_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REASON_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class ApprovalInstance:
    """冻结审批主题、策略版本和当前串行层级，不保存条件字段原文。"""

    approval_instance_id: UUID
    workspace_id: UUID
    approval_policy_id: UUID | None
    approval_policy_version_id: UUID | None
    requester_account_id: UUID
    resource_type: str
    operation: str
    resource_id: UUID | None
    subject_digest: str
    chain_digest: str
    personal_owner_confirmation: bool
    status: ApprovalInstanceStatus
    current_sequence_no: int
    idempotency_key: str
    request_hash: str
    workflow_run_id: UUID | None
    workflow_step_id: UUID | None
    trace_id: str
    traceparent: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    version: int


@dataclass(frozen=True)
class ApprovalRuntimeLevel:
    """保存一个冻结层级的模式、候补人和提醒超时游标。"""

    approval_level_id: UUID
    approval_instance_id: UUID
    workspace_id: UUID
    sequence_no: int
    mode: Literal["any", "all"]
    status: ApprovalRuntimeLevelStatus
    reminder_after_minutes: int
    timeout_after_minutes: int
    timeout_action: Literal["escalate", "transfer", "reject", "wait"]
    fallback_approver_account_ids: tuple[UUID, ...]
    fallback_activated: bool
    reminder_at: datetime | None
    reminded_at: datetime | None
    timeout_at: datetime | None
    activated_at: datetime | None
    completed_at: datetime | None
    version: int


@dataclass(frozen=True)
class ApprovalAssignment:
    """记录审批人对指定层级的当前责任及转交结果。"""

    approval_assignment_id: UUID
    approval_instance_id: UUID
    approval_level_id: UUID
    workspace_id: UUID
    approver_account_id: UUID
    status: ApprovalAssignmentStatus
    transferred_to_account_id: UUID | None
    created_at: datetime
    decided_at: datetime | None
    version: int


@dataclass(frozen=True)
class ApprovalActionFact:
    """只追加一次幂等审批命令事实，原因码不承载自由文本或敏感正文。"""

    approval_action_id: UUID
    approval_instance_id: UUID
    approval_level_id: UUID | None
    workspace_id: UUID
    actor_account_id: UUID
    action: ApprovalRuntimeAction
    idempotency_key: str
    target_account_id: UUID | None
    reason_code: str | None
    occurred_at: datetime


@dataclass(frozen=True)
class ApprovalRuntimeState:
    """聚合实例、冻结层级和指派，所有状态转换以实例版本为并发边界。"""

    instance: ApprovalInstance
    levels: tuple[ApprovalRuntimeLevel, ...]
    assignments: tuple[ApprovalAssignment, ...]


@dataclass(frozen=True)
class ApprovalRuntimeCommand:
    """表示一个已认证账号发起的幂等审批动作。"""

    action: Literal["approve", "reject", "transfer", "withdraw"]
    actor_account_id: UUID
    idempotency_key: str
    target_account_id: UUID | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class ApprovalRuntimeTransition:
    """返回状态快照、新动作事实及需要原子同步的工作流结果。"""

    state: ApprovalRuntimeState
    action: ApprovalActionFact
    workflow_outcome: ApprovalWorkflowOutcome | None


@dataclass(frozen=True)
class ApprovalSubjectEvent:
    """描述审批事务内由业务主题产生的最小审计和集成事件。"""

    event_type: str
    resource_type: str
    resource_id: UUID
    aggregate_id: UUID
    aggregate_version: int
    attributes: dict[str, object]


@dataclass(frozen=True)
class ApprovalWorkflowResume:
    """在审批事务提交后携带恢复工作流所需的最小可信上下文。"""

    workflow_run_id: UUID
    requester_account_id: UUID
    workspace_id: UUID
    trace_id: str
    traceparent: str


class ApprovalRuntimeValidationError(Exception):
    """审批实例参数、幂等键、目标账号或冻结链结构无效。"""


class ApprovalRuntimeDeniedError(Exception):
    """当前账号不是申请人、活动审批人或允许的转交目标。"""


class ApprovalRuntimeStateError(Exception):
    """审批实例、层级或工作流已经离开当前命令要求的状态。"""


class ApprovalRuntimeWriteConflictError(Exception):
    """审批幂等键、唯一指派或实例乐观锁发生并发冲突。"""


class ApprovalRuntimeRepository(Protocol):
    """在工作空间边界内原子维护审批运行聚合和只追加动作事实。"""

    def get_by_request_idempotency(
        self,
        workspace_id: UUID,
        requester_account_id: UUID,
        idempotency_key: str,
    ) -> ApprovalRuntimeState | None: ...

    def get_state(
        self,
        workspace_id: UUID,
        approval_instance_id: UUID,
        *,
        for_update: bool = False,
    ) -> ApprovalRuntimeState | None: ...

    def list_visible(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        limit: int,
    ) -> tuple[ApprovalRuntimeState, ...]: ...

    def get_action_by_idempotency(
        self,
        workspace_id: UUID,
        approval_instance_id: UUID,
        actor_account_id: UUID,
        idempotency_key: str,
    ) -> ApprovalActionFact | None: ...

    def add_state(self, state: ApprovalRuntimeState) -> None: ...

    def save_transition(
        self,
        transition: ApprovalRuntimeTransition,
        *,
        expected_version: int,
    ) -> bool: ...

    def due_instance_ids(
        self,
        workspace_id: UUID,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[UUID, ...]: ...

    def apply_workflow_outcome(
        self,
        state: ApprovalRuntimeState,
        outcome: ApprovalWorkflowOutcome,
        *,
        now: datetime,
    ) -> int | None: ...


class ApprovalRuntimeDirectory(Protocol):
    """只回答目标账号是否仍为当前工作空间活动成员。"""

    def active_account_ids(
        self,
        workspace_id: UUID,
        account_ids: tuple[UUID, ...],
    ) -> frozenset[UUID]: ...


class ApprovalSubjectLifecycle(Protocol):
    """在审批事务内绑定业务主题，并把终态同步回业务聚合。"""

    def bind(
        self,
        state: ApprovalRuntimeState,
        subject: ApprovalSubject,
    ) -> ApprovalSubjectEvent | None: ...

    def apply_transition(
        self,
        previous: ApprovalRuntimeState,
        transition: ApprovalRuntimeTransition,
    ) -> ApprovalSubjectEvent | None: ...


class ApprovalRuntimeUnitOfWork(Protocol):
    """保证审批聚合、业务状态、审计和 Outbox 使用同一数据库事务。"""

    @property
    def runtimes(self) -> ApprovalRuntimeRepository: ...

    @property
    def directory(self) -> ApprovalRuntimeDirectory: ...

    @property
    def subjects(self) -> ApprovalSubjectLifecycle: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> ApprovalRuntimeUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


def create_approval_runtime(
    chain: ApprovalChain,
    subject: ApprovalSubject,
    *,
    idempotency_key: str,
    trace_id: str,
    traceparent: str,
    now: datetime,
    workflow_run_id: UUID | None = None,
    workflow_step_id: UUID | None = None,
) -> ApprovalRuntimeState:
    """把确定性审批链冻结为运行事实，只有首层在创建时进入活动状态。"""

    # 1. 链路、主题、幂等键及工作流绑定必须成对有效，避免写入无法恢复的半绑定实例。
    if (
        chain.workspace_id != subject.workspace_id
        or not chain.levels
        or not _IDEMPOTENCY_PATTERN.fullmatch(idempotency_key)
        or (workflow_run_id is None) != (workflow_step_id is None)
    ):
        raise ApprovalRuntimeValidationError
    instance_id = uuid4()
    subject_digest = _subject_digest(subject)
    request_hash = _request_hash(
        subject_digest, chain.chain_digest, workflow_run_id, workflow_step_id
    )
    instance = ApprovalInstance(
        instance_id,
        subject.workspace_id,
        chain.approval_policy_id,
        chain.approval_policy_version_id,
        subject.requester_account_id,
        subject.resource_type,
        subject.operation,
        subject.resource_id,
        subject_digest,
        chain.chain_digest,
        chain.personal_owner_confirmation,
        "pending",
        1,
        idempotency_key,
        request_hash,
        workflow_run_id,
        workflow_step_id,
        trace_id,
        traceparent,
        now,
        now,
        None,
        1,
    )
    levels: list[ApprovalRuntimeLevel] = []
    assignments: list[ApprovalAssignment] = []
    # 2. 串行层级全部预先冻结，但只有首层生成提醒与超时游标并激活审批人。
    for resolved in chain.levels:
        active = resolved.sequence_no == 1
        level_id = uuid4()
        levels.append(
            ApprovalRuntimeLevel(
                level_id,
                instance_id,
                subject.workspace_id,
                resolved.sequence_no,
                resolved.mode,
                "active" if active else "waiting",
                resolved.reminder_after_minutes,
                resolved.timeout_after_minutes,
                resolved.timeout_action,
                resolved.fallback_approver_account_ids,
                False,
                now + timedelta(minutes=resolved.reminder_after_minutes) if active else None,
                None,
                now + timedelta(minutes=resolved.timeout_after_minutes) if active else None,
                now if active else None,
                None,
                1,
            )
        )
        assignments.extend(
            ApprovalAssignment(
                uuid4(),
                instance_id,
                level_id,
                subject.workspace_id,
                approver_id,
                "pending" if active else "waiting",
                None,
                now,
                None,
                1,
            )
            for approver_id in resolved.approver_account_ids
        )
    # 3. 聚合只保存摘要与已解析审批人，策略条件字段原文不会进入审批运行表。
    return ApprovalRuntimeState(instance, tuple(levels), tuple(assignments))


def approval_subject_digest(subject: ApprovalSubject) -> str:
    """生成不含字段原文的稳定主题摘要，供创建幂等检查在策略解析前使用。"""

    return _subject_digest(subject)


def apply_approval_command(
    state: ApprovalRuntimeState,
    command: ApprovalRuntimeCommand,
    *,
    active_target_accounts: frozenset[UUID],
    now: datetime,
) -> ApprovalRuntimeTransition:
    """执行人工动作并生成唯一后继状态，调用方负责先处理幂等重放。"""

    _validate_command(command)
    if state.instance.status != "pending":
        raise ApprovalRuntimeStateError
    if command.action == "withdraw":
        return _withdraw(state, command, now)
    level = _active_level(state)
    assignment = _pending_assignment(state, level, command.actor_account_id)
    if command.action == "transfer":
        return _transfer(state, level, assignment, command, active_target_accounts, now)
    if command.action == "reject":
        return _reject(state, level, assignment, command, now, "reject")
    return _approve(state, level, assignment, command, now)


def apply_due_approval_event(
    state: ApprovalRuntimeState,
    *,
    actor_account_id: UUID,
    active_fallback_accounts: frozenset[UUID],
    idempotency_key: str,
    now: datetime,
) -> ApprovalRuntimeTransition | None:
    """优先处理超时，再处理一次性提醒；未到期时不产生动作事实。"""

    if state.instance.status != "pending" or not _IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
        return None
    level = _active_level(state)
    if level.timeout_at is not None and level.timeout_at <= now:
        return _timeout(
            state,
            level,
            actor_account_id,
            active_fallback_accounts,
            idempotency_key,
            now,
        )
    if level.reminder_at is not None and level.reminder_at <= now and level.reminded_at is None:
        updated_level = replace(level, reminded_at=now, version=level.version + 1)
        next_state = _replace_runtime(
            state,
            levels=_replace_level(state.levels, updated_level),
            now=now,
        )
        action = _action(
            next_state, updated_level, actor_account_id, "remind", idempotency_key, now
        )
        return ApprovalRuntimeTransition(next_state, action, None)
    return None


def _approve(
    state: ApprovalRuntimeState,
    level: ApprovalRuntimeLevel,
    assignment: ApprovalAssignment,
    command: ApprovalRuntimeCommand,
    now: datetime,
) -> ApprovalRuntimeTransition:
    # 1. 当前审批人的决定先进入指派事实，再依据 any/all 判断本层是否完成。
    assignments = _replace_assignment(
        state.assignments,
        replace(assignment, status="approved", decided_at=now, version=assignment.version + 1),
    )
    pending = tuple(
        item
        for item in assignments
        if item.approval_level_id == level.approval_level_id and item.status == "pending"
    )
    level_completed = level.mode == "any" or not pending
    levels = state.levels
    workflow_outcome: ApprovalWorkflowOutcome | None = None
    # 2. 层级完成后串行激活下一层；最终层通过才产生恢复工作流的业务结果。
    if level_completed:
        assignments = _cancel_pending(assignments, level.approval_level_id, now)
        levels = _replace_level(
            levels,
            replace(level, status="approved", completed_at=now, version=level.version + 1),
        )
        next_level = _level_by_sequence(levels, level.sequence_no + 1)
        if next_level is None:
            instance = replace(
                state.instance,
                status="approved",
                updated_at=now,
                completed_at=now,
                version=state.instance.version + 1,
            )
            workflow_outcome = "resume"
        else:
            levels, assignments = _activate_level(levels, assignments, next_level, now)
            instance = replace(
                state.instance,
                current_sequence_no=next_level.sequence_no,
                updated_at=now,
                version=state.instance.version + 1,
            )
    else:
        instance = replace(
            state.instance,
            updated_at=now,
            version=state.instance.version + 1,
        )
    next_state = ApprovalRuntimeState(instance, levels, assignments)
    return ApprovalRuntimeTransition(
        next_state,
        _action(
            next_state, level, command.actor_account_id, "approve", command.idempotency_key, now
        ),
        workflow_outcome,
    )


def _reject(
    state: ApprovalRuntimeState,
    level: ApprovalRuntimeLevel,
    assignment: ApprovalAssignment,
    command: ApprovalRuntimeCommand,
    now: datetime,
    action_type: Literal["reject", "timeout_reject"],
) -> ApprovalRuntimeTransition:
    assignments = _replace_assignment(
        state.assignments,
        replace(assignment, status="rejected", decided_at=now, version=assignment.version + 1),
    )
    assignments = tuple(
        replace(item, status="cancelled", decided_at=now, version=item.version + 1)
        if item.status in {"waiting", "pending"}
        else item
        for item in assignments
    )
    levels = tuple(
        replace(item, status="rejected", completed_at=now, version=item.version + 1)
        if item.status in {"waiting", "active"}
        else item
        for item in state.levels
    )
    next_state = ApprovalRuntimeState(
        replace(
            state.instance,
            status="rejected",
            updated_at=now,
            completed_at=now,
            version=state.instance.version + 1,
        ),
        levels,
        assignments,
    )
    return ApprovalRuntimeTransition(
        next_state,
        _action(
            next_state,
            level,
            command.actor_account_id,
            action_type,
            command.idempotency_key,
            now,
            reason_code=command.reason_code,
        ),
        "reject",
    )


def _transfer(
    state: ApprovalRuntimeState,
    level: ApprovalRuntimeLevel,
    assignment: ApprovalAssignment,
    command: ApprovalRuntimeCommand,
    active_target_accounts: frozenset[UUID],
    now: datetime,
) -> ApprovalRuntimeTransition:
    target = command.target_account_id
    if (
        target is None
        or target not in active_target_accounts
        or target == command.actor_account_id
        or (
            target == state.instance.requester_account_id
            and not state.instance.personal_owner_confirmation
        )
        or any(
            item.approval_level_id == level.approval_level_id and item.approver_account_id == target
            for item in state.assignments
        )
    ):
        raise ApprovalRuntimeValidationError
    transferred = replace(
        assignment,
        status="transferred",
        transferred_to_account_id=target,
        decided_at=now,
        version=assignment.version + 1,
    )
    assignments = (
        *_replace_assignment(state.assignments, transferred),
        ApprovalAssignment(
            uuid4(),
            state.instance.approval_instance_id,
            level.approval_level_id,
            state.instance.workspace_id,
            target,
            "pending",
            None,
            now,
            None,
            1,
        ),
    )
    next_state = _replace_runtime(state, assignments=assignments, now=now)
    return ApprovalRuntimeTransition(
        next_state,
        _action(
            next_state,
            level,
            command.actor_account_id,
            "transfer",
            command.idempotency_key,
            now,
            target_account_id=target,
            reason_code=command.reason_code,
        ),
        None,
    )


def _withdraw(
    state: ApprovalRuntimeState,
    command: ApprovalRuntimeCommand,
    now: datetime,
) -> ApprovalRuntimeTransition:
    if command.actor_account_id != state.instance.requester_account_id:
        raise ApprovalRuntimeDeniedError
    levels = tuple(
        replace(level, status="withdrawn", completed_at=now, version=level.version + 1)
        if level.status in {"waiting", "active"}
        else level
        for level in state.levels
    )
    assignments = tuple(
        replace(item, status="cancelled", decided_at=now, version=item.version + 1)
        if item.status in {"waiting", "pending"}
        else item
        for item in state.assignments
    )
    next_state = ApprovalRuntimeState(
        replace(
            state.instance,
            status="withdrawn",
            updated_at=now,
            completed_at=now,
            version=state.instance.version + 1,
        ),
        levels,
        assignments,
    )
    return ApprovalRuntimeTransition(
        next_state,
        _action(
            next_state,
            None,
            command.actor_account_id,
            "withdraw",
            command.idempotency_key,
            now,
            reason_code=command.reason_code,
        ),
        "withdraw",
    )


def _timeout(
    state: ApprovalRuntimeState,
    level: ApprovalRuntimeLevel,
    actor_account_id: UUID,
    active_fallback_accounts: frozenset[UUID],
    idempotency_key: str,
    now: datetime,
) -> ApprovalRuntimeTransition:
    # 1. reject 是唯一直接终止实例的超时策略，并复用人工驳回的关闭全部层级规则。
    if level.timeout_action == "reject":
        pending = next(
            (
                item
                for item in state.assignments
                if item.approval_level_id == level.approval_level_id and item.status == "pending"
            ),
            None,
        )
        if pending is None:
            raise ApprovalRuntimeStateError
        return _reject(
            state,
            level,
            pending,
            ApprovalRuntimeCommand(
                "reject", actor_account_id, idempotency_key, reason_code="timeout"
            ),
            now,
            "timeout_reject",
        )
    # 2. transfer/escalate 只激活仍为成员且未参与本层的候补，禁止借超时引入申请人自审。
    if level.timeout_action in {"transfer", "escalate"} and not level.fallback_activated:
        assigned_accounts = {
            item.approver_account_id
            for item in state.assignments
            if item.approval_level_id == level.approval_level_id
        }
        fallback = tuple(
            account_id
            for account_id in level.fallback_approver_account_ids
            if account_id in active_fallback_accounts
            and account_id not in assigned_accounts
            and (
                account_id != state.instance.requester_account_id
                or state.instance.personal_owner_confirmation
            )
        )
        if not fallback:
            raise ApprovalRuntimeDeniedError
        assignments = _cancel_pending(state.assignments, level.approval_level_id, now)
        assignments += tuple(
            ApprovalAssignment(
                uuid4(),
                state.instance.approval_instance_id,
                level.approval_level_id,
                state.instance.workspace_id,
                account_id,
                "pending",
                None,
                now,
                None,
                1,
            )
            for account_id in fallback
        )
        updated_level = replace(
            level,
            fallback_activated=True,
            reminder_at=now + timedelta(minutes=level.reminder_after_minutes),
            reminded_at=None,
            timeout_at=now + timedelta(minutes=level.timeout_after_minutes),
            version=level.version + 1,
        )
        next_state = _replace_runtime(
            state,
            levels=_replace_level(state.levels, updated_level),
            assignments=assignments,
            now=now,
        )
        action_type: ApprovalRuntimeAction = (
            "escalate" if level.timeout_action == "escalate" else "timeout_transfer"
        )
        return ApprovalRuntimeTransition(
            next_state,
            _action(next_state, updated_level, actor_account_id, action_type, idempotency_key, now),
            None,
        )
    # 3. wait 或已激活过候补的层级只续期游标，避免同一候补集合被反复追加。
    updated_level = replace(
        level,
        reminder_at=now + timedelta(minutes=level.reminder_after_minutes),
        reminded_at=None,
        timeout_at=now + timedelta(minutes=level.timeout_after_minutes),
        version=level.version + 1,
    )
    next_state = _replace_runtime(
        state,
        levels=_replace_level(state.levels, updated_level),
        now=now,
    )
    return ApprovalRuntimeTransition(
        next_state,
        _action(next_state, updated_level, actor_account_id, "timeout_wait", idempotency_key, now),
        None,
    )


def _activate_level(
    levels: tuple[ApprovalRuntimeLevel, ...],
    assignments: tuple[ApprovalAssignment, ...],
    level: ApprovalRuntimeLevel,
    now: datetime,
) -> tuple[tuple[ApprovalRuntimeLevel, ...], tuple[ApprovalAssignment, ...]]:
    updated_level = replace(
        level,
        status="active",
        reminder_at=now + timedelta(minutes=level.reminder_after_minutes),
        timeout_at=now + timedelta(minutes=level.timeout_after_minutes),
        activated_at=now,
        version=level.version + 1,
    )
    updated_assignments = tuple(
        replace(item, status="pending", version=item.version + 1)
        if item.approval_level_id == level.approval_level_id and item.status == "waiting"
        else item
        for item in assignments
    )
    return _replace_level(levels, updated_level), updated_assignments


def _validate_command(command: ApprovalRuntimeCommand) -> None:
    if (
        not _IDEMPOTENCY_PATTERN.fullmatch(command.idempotency_key)
        or (command.reason_code is not None and not _REASON_PATTERN.fullmatch(command.reason_code))
        or (command.action == "transfer") != (command.target_account_id is not None)
    ):
        raise ApprovalRuntimeValidationError


def _active_level(state: ApprovalRuntimeState) -> ApprovalRuntimeLevel:
    levels = tuple(level for level in state.levels if level.status == "active")
    if len(levels) != 1 or levels[0].sequence_no != state.instance.current_sequence_no:
        raise ApprovalRuntimeStateError
    return levels[0]


def _pending_assignment(
    state: ApprovalRuntimeState,
    level: ApprovalRuntimeLevel,
    actor_account_id: UUID,
) -> ApprovalAssignment:
    matches = tuple(
        assignment
        for assignment in state.assignments
        if assignment.approval_level_id == level.approval_level_id
        and assignment.approver_account_id == actor_account_id
        and assignment.status == "pending"
    )
    if len(matches) != 1:
        raise ApprovalRuntimeDeniedError
    return matches[0]


def _cancel_pending(
    assignments: tuple[ApprovalAssignment, ...],
    approval_level_id: UUID,
    now: datetime,
) -> tuple[ApprovalAssignment, ...]:
    return tuple(
        replace(item, status="cancelled", decided_at=now, version=item.version + 1)
        if item.approval_level_id == approval_level_id and item.status == "pending"
        else item
        for item in assignments
    )


def _replace_runtime(
    state: ApprovalRuntimeState,
    *,
    levels: tuple[ApprovalRuntimeLevel, ...] | None = None,
    assignments: tuple[ApprovalAssignment, ...] | None = None,
    now: datetime,
) -> ApprovalRuntimeState:
    return ApprovalRuntimeState(
        replace(
            state.instance,
            updated_at=now,
            version=state.instance.version + 1,
        ),
        levels or state.levels,
        assignments or state.assignments,
    )


def _replace_level(
    levels: tuple[ApprovalRuntimeLevel, ...],
    updated: ApprovalRuntimeLevel,
) -> tuple[ApprovalRuntimeLevel, ...]:
    return tuple(
        updated if item.approval_level_id == updated.approval_level_id else item for item in levels
    )


def _replace_assignment(
    assignments: tuple[ApprovalAssignment, ...],
    updated: ApprovalAssignment,
) -> tuple[ApprovalAssignment, ...]:
    return tuple(
        updated if item.approval_assignment_id == updated.approval_assignment_id else item
        for item in assignments
    )


def _level_by_sequence(
    levels: tuple[ApprovalRuntimeLevel, ...],
    sequence_no: int,
) -> ApprovalRuntimeLevel | None:
    return next((level for level in levels if level.sequence_no == sequence_no), None)


def _action(
    state: ApprovalRuntimeState,
    level: ApprovalRuntimeLevel | None,
    actor_account_id: UUID,
    action_type: ApprovalRuntimeAction,
    idempotency_key: str,
    now: datetime,
    *,
    target_account_id: UUID | None = None,
    reason_code: str | None = None,
) -> ApprovalActionFact:
    return ApprovalActionFact(
        uuid4(),
        state.instance.approval_instance_id,
        level.approval_level_id if level is not None else None,
        state.instance.workspace_id,
        actor_account_id,
        action_type,
        idempotency_key,
        target_account_id,
        reason_code,
        now,
    )


def _subject_digest(subject: ApprovalSubject) -> str:
    document = {
        "workspace_id": str(subject.workspace_id),
        "requester_account_id": str(subject.requester_account_id),
        "resource_type": subject.resource_type,
        "operation": subject.operation,
        "resource_id": str(subject.resource_id) if subject.resource_id is not None else None,
        "department_ids": [str(item) for item in sorted(subject.department_ids, key=str)],
        "security_level": subject.security_level,
        "risk_level": subject.risk_level,
        "fields": subject.fields,
    }
    return hashlib.sha256(_canonical_json(document)).hexdigest()


def _request_hash(
    subject_digest: str,
    chain_digest: str,
    workflow_run_id: UUID | None,
    workflow_step_id: UUID | None,
) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "subject_digest": subject_digest,
                "chain_digest": chain_digest,
                "workflow_run_id": str(workflow_run_id) if workflow_run_id else None,
                "workflow_step_id": str(workflow_step_id) if workflow_step_id else None,
            }
        )
    ).hexdigest()


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError) as error:
        raise ApprovalRuntimeValidationError from error
