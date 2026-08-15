"""验证 P1F-04 审批运行状态机、异常动作和冻结摘要。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import pytest
from ai_platform_api.modules.workflow.domain.approval_runtime import (
    ApprovalRuntimeCommand,
    ApprovalRuntimeDeniedError,
    ApprovalRuntimeState,
    ApprovalRuntimeValidationError,
    apply_approval_command,
    apply_due_approval_event,
    create_approval_runtime,
)
from ai_platform_api.modules.workflow.domain.approvals import (
    ApprovalChain,
    ApprovalSubject,
    ResolvedApprovalLevel,
)

WORKSPACE_ID = UUID("f4000000-0000-4000-8000-000000000001")
REQUESTER_ID = UUID("f4000000-0000-4000-8000-000000000002")
APPROVER_A_ID = UUID("f4000000-0000-4000-8000-000000000003")
APPROVER_B_ID = UUID("f4000000-0000-4000-8000-000000000004")
APPROVER_C_ID = UUID("f4000000-0000-4000-8000-000000000005")
APPROVER_D_ID = UUID("f4000000-0000-4000-8000-000000000006")
FALLBACK_ID = UUID("f4000000-0000-4000-8000-000000000007")
SYSTEM_ID = UUID("f4000000-0000-4000-8000-000000000008")
NOW = datetime(2026, 8, 15, 9, tzinfo=UTC)


def subject() -> ApprovalSubject:
    """构造仅含合成字段的固定审批主题。"""

    return ApprovalSubject(
        WORKSPACE_ID,
        REQUESTER_ID,
        "workflow.approval",
        "submit",
        None,
        (),
        "INTERNAL",
        "high",
        {"amount": 1_500},
    )


def level(
    sequence_no: int,
    approvers: tuple[UUID, ...],
    *,
    mode: Literal["any", "all"] = "any",
    timeout_action: Literal["escalate", "transfer", "reject", "wait"] = "wait",
    fallback: tuple[UUID, ...] = (),
) -> ResolvedApprovalLevel:
    """构造具有短提醒和超时游标的冻结层级。"""

    return ResolvedApprovalLevel(
        sequence_no,
        mode,
        approvers,
        10,
        30,
        timeout_action,
        fallback,
    )


def chain(*levels: ResolvedApprovalLevel) -> ApprovalChain:
    """构造摘要格式有效的企业审批链。"""

    return ApprovalChain(WORKSPACE_ID, None, None, False, tuple(levels), "a" * 64)


def runtime(*levels: ResolvedApprovalLevel) -> ApprovalRuntimeState:
    """把合成审批链冻结为默认运行聚合。"""

    return create_approval_runtime(
        chain(*levels),
        subject(),
        idempotency_key="synthetic-runtime",
        trace_id="b" * 32,
        traceparent=f"00-{'b' * 32}-{'c' * 16}-01",
        now=NOW,
    )


def command(
    action: Literal["approve", "reject", "transfer", "withdraw"],
    actor: UUID,
    identity: str,
    *,
    target: UUID | None = None,
) -> ApprovalRuntimeCommand:
    """构造稳定幂等键的人工审批命令。"""

    return ApprovalRuntimeCommand(action, actor, identity, target, "synthetic_reason")


def test_any_then_all_levels_advance_serially_and_resume_once() -> None:
    state = runtime(
        level(1, (APPROVER_A_ID, APPROVER_B_ID)),
        level(2, (APPROVER_C_ID, APPROVER_D_ID), mode="all"),
    )

    first = apply_approval_command(
        state,
        command("approve", APPROVER_A_ID, "approve-a"),
        active_target_accounts=frozenset(),
        now=NOW + timedelta(minutes=1),
    )
    second = apply_approval_command(
        first.state,
        command("approve", APPROVER_C_ID, "approve-c"),
        active_target_accounts=frozenset(),
        now=NOW + timedelta(minutes=2),
    )
    final = apply_approval_command(
        second.state,
        command("approve", APPROVER_D_ID, "approve-d"),
        active_target_accounts=frozenset(),
        now=NOW + timedelta(minutes=3),
    )

    assert first.state.instance.current_sequence_no == 2
    assert first.state.levels[0].status == "approved"
    assert second.state.instance.status == "pending"
    assert final.state.instance.status == "approved"
    assert final.workflow_outcome == "resume"


def test_reject_closes_current_and_future_open_responsibilities() -> None:
    state = runtime(
        level(1, (APPROVER_A_ID,)),
        level(2, (APPROVER_B_ID,)),
    )

    transition = apply_approval_command(
        state,
        command("reject", APPROVER_A_ID, "reject-a"),
        active_target_accounts=frozenset(),
        now=NOW + timedelta(minutes=1),
    )

    assert transition.state.instance.status == "rejected"
    assert {item.status for item in transition.state.levels} == {"rejected"}
    assert {item.status for item in transition.state.assignments} == {"cancelled", "rejected"}
    assert transition.workflow_outcome == "reject"


def test_transfer_accepts_only_new_active_member_and_blocks_requester() -> None:
    state = runtime(level(1, (APPROVER_A_ID, APPROVER_B_ID)))

    transferred = apply_approval_command(
        state,
        command("transfer", APPROVER_A_ID, "transfer-a", target=APPROVER_C_ID),
        active_target_accounts=frozenset({APPROVER_C_ID}),
        now=NOW + timedelta(minutes=1),
    )

    assert any(
        item.approver_account_id == APPROVER_C_ID and item.status == "pending"
        for item in transferred.state.assignments
    )
    with pytest.raises(ApprovalRuntimeValidationError):
        apply_approval_command(
            state,
            command("transfer", APPROVER_A_ID, "transfer-existing", target=APPROVER_B_ID),
            active_target_accounts=frozenset({APPROVER_B_ID}),
            now=NOW + timedelta(minutes=1),
        )
    with pytest.raises(ApprovalRuntimeValidationError):
        apply_approval_command(
            state,
            command("transfer", APPROVER_A_ID, "transfer-requester", target=REQUESTER_ID),
            active_target_accounts=frozenset({REQUESTER_ID}),
            now=NOW + timedelta(minutes=1),
        )


def test_only_requester_can_withdraw_pending_instance() -> None:
    state = runtime(level(1, (APPROVER_A_ID,)))

    transition = apply_approval_command(
        state,
        command("withdraw", REQUESTER_ID, "withdraw-requester"),
        active_target_accounts=frozenset(),
        now=NOW + timedelta(minutes=1),
    )

    assert transition.state.instance.status == "withdrawn"
    assert transition.workflow_outcome == "withdraw"
    with pytest.raises(ApprovalRuntimeDeniedError):
        apply_approval_command(
            state,
            command("withdraw", APPROVER_A_ID, "withdraw-approver"),
            active_target_accounts=frozenset(),
            now=NOW + timedelta(minutes=1),
        )


def test_reminder_is_emitted_once_before_timeout() -> None:
    state = runtime(level(1, (APPROVER_A_ID,)))

    reminded = apply_due_approval_event(
        state,
        actor_account_id=SYSTEM_ID,
        active_fallback_accounts=frozenset(),
        idempotency_key="due-reminder",
        now=NOW + timedelta(minutes=10),
    )

    assert reminded is not None
    assert reminded.action.action == "remind"
    assert (
        apply_due_approval_event(
            reminded.state,
            actor_account_id=SYSTEM_ID,
            active_fallback_accounts=frozenset(),
            idempotency_key="due-reminder-repeat",
            now=NOW + timedelta(minutes=11),
        )
        is None
    )


@pytest.mark.parametrize(
    ("timeout_action", "expected_action"),
    [("transfer", "timeout_transfer"), ("escalate", "escalate")],
)
def test_timeout_activates_new_fallback_approvers(
    timeout_action: Literal["transfer", "escalate"],
    expected_action: Literal["timeout_transfer", "escalate"],
) -> None:
    state = runtime(
        level(
            1,
            (APPROVER_A_ID,),
            timeout_action=timeout_action,
            fallback=(FALLBACK_ID,),
        )
    )

    transition = apply_due_approval_event(
        state,
        actor_account_id=SYSTEM_ID,
        active_fallback_accounts=frozenset({FALLBACK_ID}),
        idempotency_key=f"due-{timeout_action}",
        now=NOW + timedelta(minutes=30),
    )

    assert transition is not None
    assert transition.action.action == expected_action
    assert transition.state.levels[0].fallback_activated is True
    assert transition.state.assignments[-1].approver_account_id == FALLBACK_ID
    assert transition.state.assignments[-1].status == "pending"


def test_timeout_rejects_or_extends_wait_without_resuming_workflow() -> None:
    rejected = apply_due_approval_event(
        runtime(level(1, (APPROVER_A_ID,), timeout_action="reject")),
        actor_account_id=SYSTEM_ID,
        active_fallback_accounts=frozenset(),
        idempotency_key="due-reject",
        now=NOW + timedelta(minutes=30),
    )
    waited = apply_due_approval_event(
        runtime(level(1, (APPROVER_A_ID,), timeout_action="wait")),
        actor_account_id=SYSTEM_ID,
        active_fallback_accounts=frozenset(),
        idempotency_key="due-wait",
        now=NOW + timedelta(minutes=30),
    )

    assert rejected is not None
    assert rejected.action.action == "timeout_reject"
    assert rejected.workflow_outcome == "reject"
    assert waited is not None
    assert waited.action.action == "timeout_wait"
    assert waited.state.instance.status == "pending"
    assert waited.state.levels[0].timeout_at == NOW + timedelta(minutes=60)


def test_runtime_freezes_subject_digest_without_subject_fields() -> None:
    state = runtime(level(1, (APPROVER_A_ID,)))

    assert state.instance.subject_digest != "a" * 64
    assert not hasattr(state.instance, "fields")
    assert replace(subject(), fields={"amount": 2_000}) != subject()
