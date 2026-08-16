"""验证 AgentRelease 运营 HTTP 查询契约不泄露高基数和正文信息。"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.agent_operations.api.routes import (
    agent_operations_service,
    router,
)
from ai_platform_api.modules.agent_operations.application.service import AgentOperationsService
from ai_platform_api.modules.agent_operations.domain.models import (
    AgentOperationsReport,
    OperationsAlert,
    PromotionDecision,
    ReleaseOperationsMetrics,
)
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from fastapi import FastAPI
from fastapi.testclient import TestClient

WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000312")
ACCOUNT_ID = UUID("20000000-0000-4000-8000-000000000312")
SERVICE_ID = UUID("30000000-0000-4000-8000-000000000312")
RELEASE_ID = UUID("40000000-0000-4000-8000-000000000312")
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


class StubAgentOperationsService:
    """记录查询边界并返回不含正文的合成报告。"""

    def __init__(self) -> None:
        self.arguments: tuple[UUID, UUID, int] | None = None

    def get_report(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        service_id: UUID,
        window_hours: int,
    ) -> AgentOperationsReport:
        assert context.authorized_permission_code == "agent.operations.read"
        self.arguments = (workspace_id, service_id, window_hours)
        metrics = _metrics()
        return AgentOperationsReport(
            service_id=SERVICE_ID,
            service_name="合成运营服务",
            service_status="active",
            route_id=UUID("50000000-0000-4000-8000-000000000312"),
            route_version=4,
            route_mode="active",
            canary_percent=0,
            window_started_at=NOW,
            window_ended_at=NOW,
            minimum_terminal_samples=5,
            minimum_feedback_samples=3,
            primary=metrics,
            comparison=None,
            alerts=(
                OperationsAlert(
                    code="SYNTHETIC_ALERT",
                    severity="warning",
                    release_role="primary",
                    metric="terminal_count",
                    observed_value=8,
                    threshold_value=5,
                    blocks_promotion=False,
                ),
            ),
            promotion=PromotionDecision(
                status="not_applicable",
                allowed=False,
                policy_version="agent-operations-v1",
                reason_codes=(),
                evidence_hash="a" * 64,
            ),
        )


def _metrics() -> ReleaseOperationsMetrics:
    return ReleaseOperationsMetrics(
        release_id=RELEASE_ID,
        release_version=3,
        role="primary",
        run_count=8,
        terminal_count=8,
        completed_count=8,
        failed_count=0,
        success_rate_bps=10_000,
        error_rate_bps=0,
        degradation_rate_bps=0,
        latency_p95_ms=1_200,
        total_cost_microunits=8_000,
        average_cost_microunits=1_000,
        max_run_cost_microunits=1_000,
        max_cost_budget_microunits=2_000,
        feedback_count=0,
        helpful_rate_bps=None,
        feedback_quality_status="not_run",
        offline_evaluation_status="passed",
        offline_evaluation_score_bps=10_000,
    )


def _context() -> RequestContext:
    base = RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TraceContext.new(),
        authentication_method="browser_session",
    )
    return replace(
        base,
        authorized_permission_code="agent.operations.read",
        authorized_workspace=True,
    )


def test_agent_operations_http_forwards_window_and_returns_safe_projection() -> None:
    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    stub = StubAgentOperationsService()
    application.dependency_overrides[trusted_request_context] = _context
    application.dependency_overrides[agent_operations_service] = lambda: cast(
        AgentOperationsService,
        stub,
    )

    with TestClient(application) as client:
        response = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/agent-release-operations",
            params={"service_id": str(SERVICE_ID), "window_hours": 48},
        )

    assert response.status_code == 200
    assert stub.arguments == (WORKSPACE_ID, SERVICE_ID, 48)
    assert response.json()["primary"]["release_id"] == str(RELEASE_ID)
    lowered = response.text.lower()
    for forbidden in ("prompt", "message", "run_id", "actor_id", "trace_id"):
        assert forbidden not in lowered
