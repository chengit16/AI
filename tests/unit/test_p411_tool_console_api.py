"""验证 P4-11 工具控制台权限、HTTP 动作和 SSE 游标边界。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from ai_platform_api.app.errors import ErrorCatalog, register_error_handlers
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.config import Settings, get_settings
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.tool_execution.api.routes import (
    approval_instance_service,
    router,
    tool_confirmation_service,
    tool_console_service,
    tool_planning_service,
    tool_progress_service,
    tool_task_service,
)
from ai_platform_api.modules.tool_execution.application.console import (
    ToolConfirmationView,
    ToolConsoleService,
    ToolConsoleStore,
    ToolRunDetail,
    ToolRunSummary,
    ToolStepView,
)
from ai_platform_api.modules.tool_execution.application.errors import ToolExecutionDeniedError
from ai_platform_api.modules.tool_execution.domain.results import (
    ToolProgressEvent,
    ToolProgressPage,
)
from ai_platform_api.modules.tool_execution.domain.tasks import ToolRunBudget
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 8, 17, 8, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000411")
ACCOUNT_ID = UUID("20000000-0000-4000-8000-000000000411")
RUN_ID = UUID("30000000-0000-4000-8000-000000000411")
STEP_ID = UUID("40000000-0000-4000-8000-000000000411")
CONFIRMATION_ID = UUID("50000000-0000-4000-8000-000000000411")
APPROVAL_ID = UUID("60000000-0000-4000-8000-000000000411")


class FixedConsoleStore(ToolConsoleStore):
    """返回一个固定脱敏投影，并记录服务层下推的查询范围。"""

    def __init__(self, detail: ToolRunDetail) -> None:
        self.detail = detail
        self.list_scope: tuple[bool, frozenset[UUID]] | None = None

    def list_runs(
        self,
        *,
        workspace_id: UUID,
        account_id: UUID,
        workspace_scope: bool,
        resource_ids: frozenset[UUID],
        limit: int,
    ) -> tuple[ToolRunSummary, ...]:
        del account_id, limit
        self.list_scope = (workspace_scope, resource_ids)
        return (self.detail.run,) if workspace_id == WORKSPACE_ID else ()

    def get_run(
        self,
        *,
        workspace_id: UUID,
        account_id: UUID,
        run_id: UUID,
    ) -> ToolRunDetail | None:
        del account_id
        if workspace_id == WORKSPACE_ID and run_id == RUN_ID:
            return self.detail
        return None


class FakeTasks:
    """记录 HTTP 路由转交的创建与取消命令。"""

    def __init__(self) -> None:
        self.cancelled: list[UUID] = []

    def create_run(self, *_: object, **__: object) -> object:
        return SimpleNamespace(run_id=RUN_ID, state="pending")

    def request_cancellation(self, _: RequestContext, run_id: UUID, **__: object) -> object:
        self.cancelled.append(run_id)
        return SimpleNamespace(run_id=run_id, state="cancellation_requested")


class FakePlanning:
    """记录已由请求 Schema 收敛的候选工具计划。"""

    def __init__(self) -> None:
        self.run_ids: list[UUID] = []

    def freeze(self, _: RequestContext, run_id: UUID, *__: object, **___: object) -> None:
        self.run_ids.append(run_id)


class FakeApprovals:
    """模拟通用审批最终批准或驳回，不实现第二套审批状态机。"""

    def __init__(self) -> None:
        self.status = "approved"
        self.actions: list[str] = []

    def act(self, _: RequestContext, **kwargs: object) -> object:
        command = kwargs["command"]
        self.actions.append(cast(Any, command).action)
        return SimpleNamespace(state=SimpleNamespace(instance=SimpleNamespace(status=self.status)))


class FakeConfirmations:
    """记录最终批准后的当前 PDP 复核入口。"""

    def __init__(self) -> None:
        self.resumed: list[UUID] = []

    def resume(self, _: RequestContext, *, confirmation_id: UUID, **__: object) -> None:
        self.resumed.append(confirmation_id)


class FixedProgress:
    """按配置返回固定最新游标和可选终态事件。"""

    def __init__(self) -> None:
        self.latest_cursor = 2
        self.events: tuple[ToolProgressEvent, ...] = ()

    def replay(self, _: RequestContext, *, after_cursor: int, **__: object) -> ToolProgressPage:
        events = tuple(event for event in self.events if event.cursor > after_cursor)
        return ToolProgressPage(events, self.latest_cursor, False)


def _context(permission: str) -> RequestContext:
    context = RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TraceContext("1" * 32, "2" * 16),
        authentication_method="browser_session",
    )
    return replace(
        context,
        authorized_permission_code=permission,
        authorized_workspace=True,
    )


def _detail(*, state: str = "waiting_confirmation") -> ToolRunDetail:
    confirmation = ToolConfirmationView(
        confirmation_id=CONFIRMATION_ID,
        approval_instance_id=APPROVAL_ID,
        mode="personal_owner",
        state="pending",
        risk_level="high",
        confirmation_hash="c" * 64,
        expires_at=NOW + timedelta(hours=1),
        resolved_at=None,
        can_respond=True,
        version=1,
    )
    summary = ToolRunSummary(
        run_id=RUN_ID,
        service_id=uuid4(),
        service_name="合成工具服务",
        agent_release_id=uuid4(),
        agent_release_version=3,
        state=state,
        budget=ToolRunBudget(1, 2, 300, 0),
        requested_by_account_id=ACCOUNT_ID,
        step_count=1,
        completed_step_count=0,
        total_cost_microunits=0,
        pending_confirmation_count=1,
        cancel_requested_at=None,
        deadline_at=NOW + timedelta(minutes=5),
        created_at=NOW,
        updated_at=NOW,
        completed_at=NOW if state == "completed" else None,
        version=2,
    )
    step = ToolStepView(
        step_id=STEP_ID,
        sequence_no=1,
        tool_id=uuid4(),
        tool_version=1,
        tool_key="synthetic.write",
        display_name="合成写入",
        access_mode="write",
        risk_level="high",
        canonical_arguments_hash="a" * 64,
        state="waiting_confirmation",
        timeout_seconds=30,
        max_attempts=1,
        max_result_bytes=1024,
        current_attempt_no=None,
        created_at=NOW,
        updated_at=NOW,
        confirmation=confirmation,
        attempts=(),
    )
    return ToolRunDetail(summary, (step,), 2)


def test_console_service_requires_browser_permission_and_exact_resource_scope() -> None:
    """浏览器身份、权限码与资源范围三者缺一不可。"""

    store = FixedConsoleStore(_detail())
    service = ToolConsoleService(store)
    resource_context = replace(
        _context("tool.run.read"),
        authorized_workspace=False,
        authorized_resource_ids=frozenset({RUN_ID}),
    )
    assert service.get_run(resource_context, run_id=RUN_ID).run.run_id == RUN_ID
    assert service.list_runs(resource_context, limit=10)[0].run_id == RUN_ID
    assert store.list_scope == (False, frozenset({RUN_ID}))

    with pytest.raises(ToolExecutionDeniedError):
        service.get_run(replace(resource_context, authentication_method="api_key"), run_id=RUN_ID)
    with pytest.raises(ToolExecutionDeniedError):
        service.get_run(
            replace(resource_context, authorized_resource_ids=frozenset()), run_id=RUN_ID
        )


def test_http_create_confirm_reject_cancel_and_sse_cursor_boundaries() -> None:
    """真实 Router 只翻译命令，批准后重做 PDP，超前 SSE 游标返回稳定冲突。"""

    # 长函数保留原因: 同一 FastAPI 路由装配需连续证明五类浏览器动作共享一套权限和错误边界。
    current_context = [_context("tool.run.create")]
    store = FixedConsoleStore(_detail())
    console = ToolConsoleService(store)
    tasks = FakeTasks()
    planning = FakePlanning()
    approvals = FakeApprovals()
    confirmations = FakeConfirmations()
    progress = FixedProgress()
    application = FastAPI()
    application.state.tool_console_service = console
    application.include_router(router, prefix="/api/v1")
    register_error_handlers(
        application,
        ErrorCatalog.load(ROOT / "contracts/errors/catalog.v1.json"),
    )
    application.dependency_overrides[trusted_request_context] = lambda: current_context[0]
    application.dependency_overrides[tool_console_service] = lambda: console
    application.dependency_overrides[tool_task_service] = lambda: tasks
    application.dependency_overrides[tool_planning_service] = lambda: planning
    application.dependency_overrides[approval_instance_service] = lambda: approvals
    application.dependency_overrides[tool_confirmation_service] = lambda: confirmations
    application.dependency_overrides[tool_progress_service] = lambda: progress
    application.dependency_overrides[get_settings] = lambda: Settings(
        stream_heartbeat_seconds=1,
        stream_poll_interval_ms=50,
    )

    with TestClient(application, raise_server_exceptions=False) as client:
        created = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/tool-runs",
            headers={"Idempotency-Key": "synthetic-p411-create"},
            json={
                "service_id": str(store.detail.run.service_id),
                "agent_release_id": str(store.detail.run.agent_release_id),
                "tool_calls": [
                    {
                        "tool_key": "quota.get_usage",
                        "tool_id": str(store.detail.steps[0].tool_id),
                        "tool_version": 1,
                        "arguments": {},
                    }
                ],
                "max_attempts_per_step": 2,
                "max_execution_seconds": 300,
            },
        )
        assert created.status_code == 201
        assert planning.run_ids == [RUN_ID]

        current_context[0] = _context("tool.confirmation.respond")
        confirmed = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/tool-runs/{RUN_ID}/confirmations/"
            f"{CONFIRMATION_ID}/confirm",
            json={"idempotency_key": "synthetic-p411-confirm"},
        )
        assert confirmed.status_code == 200
        assert confirmations.resumed == [CONFIRMATION_ID]

        approvals.status = "rejected"
        rejected = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/tool-runs/{RUN_ID}/confirmations/"
            f"{CONFIRMATION_ID}/reject",
            json={"idempotency_key": "synthetic-p411-reject"},
        )
        assert rejected.status_code == 200
        assert RUN_ID in tasks.cancelled

        current_context[0] = _context("tool.run.cancel")
        cancelled = client.post(f"/api/v1/workspaces/{WORKSPACE_ID}/tool-runs/{RUN_ID}/cancel")
        assert cancelled.status_code == 200
        assert tasks.cancelled.count(RUN_ID) == 2

        current_context[0] = _context("tool.run.read")
        ahead = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/tool-runs/{RUN_ID}/events",
            headers={"Last-Event-ID": "3"},
        )
        assert ahead.status_code == 409
        assert ahead.json()["code"] == "TOOL_RUN_CONFLICT"

        cross_workspace = client.get(
            f"/api/v1/workspaces/{uuid4()}/tool-runs/{RUN_ID}",
        )
        assert cross_workspace.status_code == 403

        store.detail = _detail(state="completed")
        progress.latest_cursor = 1
        progress.events = (
            ToolProgressEvent(
                progress_event_id=uuid4(),
                workspace_id=WORKSPACE_ID,
                run_id=RUN_ID,
                cursor=1,
                event_type="tool.run.state_changed",
                run_state="completed",
                step_id=None,
                step_state=None,
                attempt_id=None,
                tool_call_id=None,
                error_code=None,
                occurred_at=NOW,
            ),
        )
        streamed = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/tool-runs/{RUN_ID}/events")
        assert streamed.status_code == 200
        assert "id: 1" in streamed.text
        assert '"run_state":"completed"' in streamed.text
