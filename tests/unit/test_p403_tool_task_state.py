"""验证 P4-03 应用边界冻结身份、预算、摘要和内部 Worker 参数。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.tool_execution.application.errors import (
    ToolExecutionDeniedError,
    ToolRunBudgetExceededError,
    ToolRunConflictError,
)
from ai_platform_api.modules.tool_execution.application.tasks import ToolTaskService
from ai_platform_api.modules.tool_execution.domain.tasks import (
    ClaimedToolAttempt,
    ToolRun,
    ToolRunBudget,
)

NOW = datetime(2026, 8, 16, 6, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("a8000000-0000-4000-8000-000000000001")
ACCOUNT_ID = UUID("a8000000-0000-4000-8000-000000000002")
ACTOR_ID = UUID("a8000000-0000-4000-8000-000000000003")
SERVICE_ID = UUID("a8000000-0000-4000-8000-000000000004")
RELEASE_ID = UUID("a8000000-0000-4000-8000-000000000005")
TRACE = TraceContext("4" * 32, "5" * 16)
BUDGET = ToolRunBudget(5, 2, 120, 50_000)


class StubToolTaskStore:
    """只记录应用输入，状态机和租约原子性由 PostgreSQL 专项测试覆盖。"""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.run: ToolRun | None = None

    def create_run(self, **values: Any) -> ToolRun:
        self.created.append(values)
        self.run = _run(values)
        return self.run

    def get_run(self, workspace_id: UUID, run_id: UUID) -> ToolRun | None:
        if self.run is None or self.run.workspace_id != workspace_id or self.run.run_id != run_id:
            return None
        return self.run

    def append_step(self, **values: Any) -> Any:
        return values

    def claim_next(self, **values: Any) -> None:
        return None


def _context(
    *,
    workspace_id: UUID = WORKSPACE_ID,
    authentication_method: str = "open_api_key",
) -> RequestContext:
    return RequestContext.trusted(
        actor_id=ACTOR_ID,
        user_id=ACCOUNT_ID,
        workspace_id=workspace_id,
        trace=TRACE,
        authentication_method=authentication_method,
    )


def _run(values: dict[str, Any]) -> ToolRun:
    budget = values["budget"]
    return ToolRun(
        run_id=values["run_id"],
        workspace_id=values["workspace_id"],
        requested_by_actor_id=values["actor_id"],
        requested_by_account_id=values["account_id"],
        service_id=values["service_id"],
        agent_release_id=values["agent_release_id"],
        state="pending",
        budget=budget,
        cancel_requested_at=None,
        deadline_at=values["created_at"] + timedelta(seconds=budget.max_execution_seconds),
        created_at=values["created_at"],
        updated_at=values["created_at"],
        completed_at=None,
        recovery_generation=0,
        recovery_reason_code=None,
        recovery_required_at=None,
        last_recovered_by_actor_id=None,
        last_recovered_at=None,
        version=1,
    )


def test_create_run_freezes_trusted_identity_and_reproducible_request_hash() -> None:
    store = StubToolTaskStore()
    service = ToolTaskService(store)  # type: ignore[arg-type]

    first = service.create_run(
        _context(),
        service_id=SERVICE_ID,
        agent_release_id=RELEASE_ID,
        idempotency_key="p403-request-0001",
        budget=BUDGET,
        created_at=NOW,
    )
    service.create_run(
        _context(),
        service_id=SERVICE_ID,
        agent_release_id=RELEASE_ID,
        idempotency_key="p403-request-0001",
        budget=BUDGET,
        created_at=NOW,
    )

    assert first.workspace_id == WORKSPACE_ID
    assert first.requested_by_actor_id == ACTOR_ID
    assert first.requested_by_account_id == ACCOUNT_ID
    assert store.created[0]["request_hash"] == store.created[1]["request_hash"]
    assert len(store.created[0]["request_hash"]) == 64
    assert store.created[0]["trace_id"] == TRACE.trace_id


@pytest.mark.parametrize(
    "budget",
    [
        replace(BUDGET, max_steps=0),
        replace(BUDGET, max_attempts_per_step=6),
        replace(BUDGET, max_execution_seconds=1801),
        replace(BUDGET, max_cost_microunits=-1),
    ],
)
def test_create_run_rejects_out_of_contract_budget(budget: ToolRunBudget) -> None:
    service = ToolTaskService(StubToolTaskStore())  # type: ignore[arg-type]

    with pytest.raises(ToolRunBudgetExceededError):
        service.create_run(
            _context(),
            service_id=SERVICE_ID,
            agent_release_id=RELEASE_ID,
            idempotency_key="p403-request-0002",
            budget=budget,
            created_at=NOW,
        )


def test_untrusted_principal_and_invalid_idempotency_key_fail_closed() -> None:
    service = ToolTaskService(StubToolTaskStore())  # type: ignore[arg-type]
    missing_account = replace(_context(), user_id=None)

    with pytest.raises(ToolExecutionDeniedError):
        service.create_run(
            missing_account,
            service_id=SERVICE_ID,
            agent_release_id=RELEASE_ID,
            idempotency_key="p403-request-0003",
            budget=BUDGET,
            created_at=NOW,
        )
    with pytest.raises(ToolRunConflictError):
        service.create_run(
            _context(),
            service_id=SERVICE_ID,
            agent_release_id=RELEASE_ID,
            idempotency_key="short",
            budget=BUDGET,
            created_at=NOW,
        )


def test_cross_workspace_read_digest_and_worker_lease_inputs_are_rejected() -> None:
    store = StubToolTaskStore()
    service = ToolTaskService(store)  # type: ignore[arg-type]
    run = service.create_run(
        _context(),
        service_id=SERVICE_ID,
        agent_release_id=RELEASE_ID,
        idempotency_key="p403-request-0004",
        budget=BUDGET,
        created_at=NOW,
    )

    with pytest.raises(ToolExecutionDeniedError):
        service.get_run(_context(workspace_id=uuid4()), run.run_id)
    with pytest.raises(ToolRunConflictError):
        service.append_step(
            _context(),
            run.run_id,
            tool_id=uuid4(),
            tool_version=1,
            canonical_arguments_hash="not-a-digest",
            created_at=NOW,
        )
    for worker_id, lease_seconds in (("", 60), ("worker", 0), ("worker", 301)):
        with pytest.raises(ToolRunConflictError):
            service.claim_next(worker_id=worker_id, now=NOW, lease_seconds=lease_seconds)


def test_claim_identity_cannot_be_reinterpreted_as_workspace_request() -> None:
    claim = ClaimedToolAttempt(
        attempt_id=uuid4(),
        run_id=uuid4(),
        step_id=uuid4(),
        tool_call_id=uuid4(),
        workspace_id=WORKSPACE_ID,
        tool_id=uuid4(),
        tool_version=1,
        canonical_arguments_hash="a" * 64,
        recovery_generation=0,
        attempt_no=1,
        lease_generation=1,
        trigger="automatic",
        worker_id="p403-worker",
        lease_expires_at=NOW + timedelta(seconds=60),
    )

    assert claim.workspace_id == WORKSPACE_ID
    assert claim.attempt_no == claim.lease_generation == 1
