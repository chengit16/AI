"""把通用审批事务绑定到工具确认事实，不负责执行任何工具 Adapter。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid5

from sqlalchemy import CursorResult, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.tool_execution.domain.confirmations import (
    ToolCallBinding,
    ToolConfirmationMode,
    confirmation_binding_hash,
)
from ai_platform_api.modules.tool_execution.domain.planning import ToolPolicyDecisionRecord
from ai_platform_api.modules.tool_execution.infrastructure.planning_sqlalchemy import (
    insert_tool_policy_decision,
)
from ai_platform_api.modules.workflow.domain.approval_runtime import (
    ApprovalRuntimeState,
    ApprovalRuntimeStateError,
    ApprovalRuntimeTransition,
    ApprovalRuntimeValidationError,
    ApprovalSubjectEvent,
    ApprovalSubjectLifecycle,
    approval_subject_digest,
)
from ai_platform_api.modules.workflow.domain.approvals import ApprovalSubject
from ai_platform_api.persistence.tables import (
    agent_tool_definitions,
    tool_confirmations,
    tool_runs,
    tool_steps,
)

_CONFIRMATION_NAMESPACE = UUID("a7000000-0000-4000-8000-000000000406")
_SUBJECT_FIELDS = frozenset(
    {
        "canonical_arguments_hash",
        "confirmation_hash",
        "expires_at",
        "field_mask_hash",
        "permission_code",
        "policy_decision_id",
        "policy_evaluated_at",
        "policy_version",
        "resource_scope_hash",
        "run_id",
        "step_id",
        "tool_id",
        "tool_version",
    }
)


class SqlAlchemyToolConfirmationSubjectLifecycle(ApprovalSubjectLifecycle):
    """在审批 Unit of Work 内绑定确认，并同步不可变的审批原始终态。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def bind(
        self,
        state: ApprovalRuntimeState,
        subject: ApprovalSubject,
    ) -> ApprovalSubjectEvent | None:
        """同事务冻结 PDP、确认绑定和 Run/Step 等待状态。"""

        if subject.resource_type != "tool.call":
            return None

        # 1. 先验证封闭主题和审批摘要，未知字段不能进入确认事实或改变幂等语义。
        parsed = _parse_subject(subject)
        instance = state.instance
        if (
            subject.operation != "execute"
            or subject.resource_id != parsed.binding.step_id
            or instance.subject_digest != approval_subject_digest(subject)
        ):
            raise ApprovalRuntimeValidationError
        mode: ToolConfirmationMode = (
            "personal_owner" if instance.personal_owner_confirmation else "enterprise_approval"
        )

        # 2. 锁定可信来源并复核申请人、工具定义和有效期，伪造主题不能补发执行权。
        source = _load_bind_source(self._session, subject.workspace_id, parsed.binding)
        if not _valid_bind_source(source, subject, parsed, instance.created_at):
            raise ApprovalRuntimeStateError

        # 3. PDP、确认和父子等待状态全部加入审批事务，任一步失败都会整体回滚。
        confirmation_id = uuid5(
            _CONFIRMATION_NAMESPACE,
            f"{parsed.binding.step_id}:{instance.approval_instance_id}",
        )
        try:
            insert_tool_policy_decision(self._session, parsed.policy)
            _insert_confirmation(self._session, state, subject, parsed, mode, confirmation_id)
            _move_to_waiting(
                self._session,
                source,
                parsed.binding,
                mode,
                instance.created_at,
            )
        except IntegrityError as error:
            raise ApprovalRuntimeStateError from error
        return _subject_event(
            "tool.call.confirmation_requested",
            confirmation_id,
            parsed.binding,
            1,
            mode,
            "pending",
            parsed.confirmation_hash,
        )

    def apply_transition(
        self,
        previous: ApprovalRuntimeState,
        transition: ApprovalRuntimeTransition,
    ) -> ApprovalSubjectEvent | None:
        """同步审批终态；批准后仍必须经独立当前 PDP 恢复入口才能 ready。"""

        instance = transition.state.instance
        if previous.instance.resource_type != "tool.call" or instance.status == "pending":
            return None

        # 1. 锁定同空间确认并复核审批链摘要；已经超时关闭的确认不得被后续审批复活。
        row = (
            self._session.execute(
                select(tool_confirmations)
                .where(
                    tool_confirmations.c.workspace_id == instance.workspace_id,
                    tool_confirmations.c.approval_instance_id == instance.approval_instance_id,
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ApprovalRuntimeStateError
        if row["state"] != "pending":
            return None
        if (
            row["subject_digest"] != previous.instance.subject_digest
            or row["chain_digest"] != previous.instance.chain_digest
        ):
            raise ApprovalRuntimeStateError

        # 2. 超过冻结期限统一记为 expired，否则只接受审批引擎定义的稳定终态。
        occurred_at = transition.action.occurred_at
        next_state = (
            "expired"
            if occurred_at >= cast(datetime, row["expires_at"])
            else cast(str, instance.status)
        )
        if next_state not in {"approved", "rejected", "withdrawn", "expired"}:
            raise ApprovalRuntimeStateError
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(tool_confirmations)
                .where(
                    tool_confirmations.c.confirmation_id == row["confirmation_id"],
                    tool_confirmations.c.version == row["version"],
                )
                .values(
                    state=next_state,
                    confirmed_by_actor_id=transition.action.actor_account_id,
                    resolved_at=occurred_at,
                    updated_at=occurred_at,
                    version=cast(int, row["version"]) + 1,
                )
            ),
        )
        if result.rowcount != 1:
            raise ApprovalRuntimeStateError
        binding = _binding(row)
        return _subject_event(
            "tool.call.confirmation_resolved",
            cast(UUID, row["confirmation_id"]),
            binding,
            cast(int, row["version"]) + 1,
            cast(ToolConfirmationMode, row["mode"]),
            next_state,
            cast(str, row["confirmation_hash"]),
        )


@dataclass(frozen=True)
class _ParsedSubject:
    """保存从封闭审批主题解析出的确认与 PDP 最小事实。"""

    binding: ToolCallBinding
    policy: ToolPolicyDecisionRecord
    confirmation_hash: str
    expires_at: datetime


@dataclass(frozen=True)
class _BindSource:
    """保存同一审批事务中锁定的 Step、Run 和工具定义。"""

    step: RowMapping
    run: RowMapping
    definition: RowMapping


def _load_bind_source(
    session: Session,
    workspace_id: UUID,
    binding: ToolCallBinding,
) -> _BindSource:
    """按固定顺序锁定任务来源，并读取不可变工具定义。"""

    # 1. Step、Run 按固定顺序加行锁，使申请确认与取消、计划更新不能交叉覆盖。
    step = (
        session.execute(
            select(tool_steps)
            .where(
                tool_steps.c.workspace_id == workspace_id,
                tool_steps.c.step_id == binding.step_id,
            )
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    run = (
        session.execute(
            select(tool_runs)
            .where(
                tool_runs.c.workspace_id == workspace_id,
                tool_runs.c.run_id == binding.run_id,
            )
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )

    # 2. 定义本身不可变，无需加锁；缺失任一来源统一视为非法审批状态。
    definition = (
        session.execute(
            select(agent_tool_definitions).where(
                agent_tool_definitions.c.tool_id == binding.tool_id,
                agent_tool_definitions.c.tool_version == binding.tool_version,
            )
        )
        .mappings()
        .one_or_none()
    )
    if step is None or run is None or definition is None:
        raise ApprovalRuntimeStateError
    return _BindSource(step, run, definition)


def _insert_confirmation(
    session: Session,
    state: ApprovalRuntimeState,
    subject: ApprovalSubject,
    parsed: _ParsedSubject,
    mode: ToolConfirmationMode,
    confirmation_id: UUID,
) -> None:
    """写入不含参数、策略正文和凭证的确认绑定。"""

    instance = state.instance
    session.execute(
        insert(tool_confirmations).values(
            confirmation_id=confirmation_id,
            approval_instance_id=instance.approval_instance_id,
            workspace_id=parsed.binding.workspace_id,
            run_id=parsed.binding.run_id,
            step_id=parsed.binding.step_id,
            tool_id=parsed.binding.tool_id,
            tool_version=parsed.binding.tool_version,
            canonical_arguments_hash=parsed.binding.canonical_arguments_hash,
            mode=mode,
            policy_decision_id=parsed.policy.decision_id,
            permission_code=parsed.policy.permission_code,
            policy_version=parsed.policy.policy_version,
            resource_scope_hash=parsed.policy.resource_scope_hash,
            field_mask_hash=parsed.policy.field_mask_hash,
            policy_evaluated_at=parsed.policy.evaluated_at,
            risk_level=subject.risk_level,
            confirmation_hash=parsed.confirmation_hash,
            subject_digest=instance.subject_digest,
            chain_digest=instance.chain_digest,
            state="pending",
            confirmed_by_actor_id=None,
            expires_at=parsed.expires_at,
            resolved_at=None,
            created_at=instance.created_at,
            updated_at=instance.created_at,
            version=1,
        )
    )


def _move_to_waiting(
    session: Session,
    source: _BindSource,
    binding: ToolCallBinding,
    mode: ToolConfirmationMode,
    occurred_at: datetime,
) -> None:
    """以乐观版本同时推进 Step 与 Run，拒绝任何竞争状态覆盖。"""

    waiting_state: Literal["waiting_confirmation", "waiting_approval"] = (
        "waiting_confirmation" if mode == "personal_owner" else "waiting_approval"
    )
    updated_step = session.execute(
        update(tool_steps)
        .where(
            tool_steps.c.step_id == binding.step_id,
            tool_steps.c.version == source.step["version"],
        )
        .values(
            state=waiting_state,
            updated_at=occurred_at,
            version=cast(int, source.step["version"]) + 1,
        )
        .returning(tool_steps.c.version)
    ).one_or_none()
    updated_run = session.execute(
        update(tool_runs)
        .where(
            tool_runs.c.run_id == binding.run_id,
            tool_runs.c.version == source.run["version"],
        )
        .values(
            state=waiting_state,
            updated_at=occurred_at,
            version=cast(int, source.run["version"]) + 1,
        )
        .returning(tool_runs.c.version)
    ).one_or_none()
    if updated_step is None or updated_run is None:
        raise ApprovalRuntimeStateError


def _parse_subject(subject: ApprovalSubject) -> _ParsedSubject:
    """严格解析审批主题，拒绝未知字段、无时区时间和失配摘要。"""

    # 1. 字段集合必须与冻结契约完全一致，防止静默接受未纳入摘要的新执行语义。
    if subject.resource_id is None or set(subject.fields) != _SUBJECT_FIELDS:
        raise ApprovalRuntimeValidationError
    fields = subject.fields
    try:
        binding = ToolCallBinding(
            workspace_id=subject.workspace_id,
            run_id=UUID(str(fields["run_id"])),
            step_id=UUID(str(fields["step_id"])),
            tool_id=UUID(str(fields["tool_id"])),
            tool_version=_strict_positive_int(fields["tool_version"]),
            canonical_arguments_hash=_hash(fields["canonical_arguments_hash"]),
        )
        evaluated_at = datetime.fromisoformat(str(fields["policy_evaluated_at"]))
        expires_at = datetime.fromisoformat(str(fields["expires_at"]))
        policy = ToolPolicyDecisionRecord(
            decision_id=UUID(str(fields["policy_decision_id"])),
            workspace_id=subject.workspace_id,
            run_id=binding.run_id,
            step_id=binding.step_id,
            tool_id=binding.tool_id,
            tool_version=binding.tool_version,
            canonical_arguments_hash=binding.canonical_arguments_hash,
            permission_code=str(fields["permission_code"]),
            policy_version=_strict_positive_int(fields["policy_version"]),
            resource_scope_hash=_hash(fields["resource_scope_hash"]),
            field_mask_hash=_hash(fields["field_mask_hash"]),
            evaluated_at=evaluated_at,
        )
        confirmation_hash = _hash(fields["confirmation_hash"])
    except (TypeError, ValueError) as error:
        raise ApprovalRuntimeValidationError from error

    # 2. 主题身份、时区、风险和确认摘要必须同时匹配，任一异常均失败关闭。
    if (
        binding.step_id != subject.resource_id
        or evaluated_at.tzinfo is None
        or expires_at.tzinfo is None
        or subject.risk_level not in {"high", "critical"}
        or confirmation_hash != confirmation_binding_hash(binding, policy.policy_version)
    ):
        raise ApprovalRuntimeValidationError
    return _ParsedSubject(binding, policy, confirmation_hash, expires_at)


def _valid_bind_source(
    source: _BindSource,
    subject: ApprovalSubject,
    parsed: _ParsedSubject,
    created_at: datetime,
) -> bool:
    binding = parsed.binding
    return bool(
        source.step["run_id"] == binding.run_id
        and source.step["workspace_id"] == binding.workspace_id
        and source.step["tool_id"] == binding.tool_id
        and source.step["tool_version"] == binding.tool_version
        and source.step["canonical_arguments_hash"] == binding.canonical_arguments_hash
        and source.step["state"] == "policy_checking"
        and source.run["state"] == "running"
        and source.run["requested_by_account_id"] == subject.requester_account_id
        and created_at < source.run["deadline_at"]
        and parsed.expires_at == source.run["deadline_at"]
        and source.definition["status"] == "active"
        and source.definition["access_mode"] == "write"
        and source.definition["adapter_kind"] == "synthetic_internal_write"
        and source.definition["synthetic"] is True
        and source.definition["risk_level"] == subject.risk_level
        and source.definition["permission_code"] == parsed.policy.permission_code
    )


def _strict_positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError
    return value


def _hash(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError
    return value


def _binding(row: RowMapping) -> ToolCallBinding:
    return ToolCallBinding(
        workspace_id=cast(UUID, row["workspace_id"]),
        run_id=cast(UUID, row["run_id"]),
        step_id=cast(UUID, row["step_id"]),
        tool_id=cast(UUID, row["tool_id"]),
        tool_version=cast(int, row["tool_version"]),
        canonical_arguments_hash=cast(str, row["canonical_arguments_hash"]),
    )


def _subject_event(
    event_type: str,
    confirmation_id: UUID,
    binding: ToolCallBinding,
    aggregate_version: int,
    mode: ToolConfirmationMode,
    state: str,
    confirmation_hash: str,
) -> ApprovalSubjectEvent:
    return ApprovalSubjectEvent(
        event_type=event_type,
        resource_type="tool_confirmation",
        resource_id=confirmation_id,
        aggregate_id=binding.run_id,
        aggregate_version=aggregate_version,
        attributes={
            "confirmation_hash": confirmation_hash,
            "mode": mode,
            "state": state,
            "step_id": str(binding.step_id),
            "tool_id": str(binding.tool_id),
            "tool_version": binding.tool_version,
        },
    )


__all__ = ["SqlAlchemyToolConfirmationSubjectLifecycle"]
