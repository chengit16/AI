"""验证 P3-12 AgentRelease 运营指标、告警和晋级规则。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.agent_operations.application.service import (
    AgentOperationsDeniedError,
    AgentOperationsService,
)
from ai_platform_api.modules.agent_operations.domain.models import (
    OperationsRouteContext,
    ReleaseOperationsFacts,
)

WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000312")
ACCOUNT_ID = UUID("20000000-0000-4000-8000-000000000312")
SERVICE_ID = UUID("30000000-0000-4000-8000-000000000312")
ROUTE_ID = UUID("40000000-0000-4000-8000-000000000312")
PRIMARY_ID = UUID("50000000-0000-4000-8000-000000000312")
CANARY_ID = UUID("60000000-0000-4000-8000-000000000312")
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


class FakeOperationsRepository:
    """返回测试冻结的 Route 和聚合事实，不引入数据库行为。"""

    def __init__(
        self,
        route: OperationsRouteContext,
        facts: tuple[ReleaseOperationsFacts, ...],
    ) -> None:
        self.route = route
        self.facts = facts

    def get_route_context(
        self,
        workspace_id: UUID,
        service_id: UUID,
    ) -> OperationsRouteContext | None:
        return self.route if (workspace_id, service_id) == (WORKSPACE_ID, SERVICE_ID) else None

    def aggregate_releases(
        self,
        workspace_id: UUID,
        service_id: UUID,
        release_ids: tuple[UUID, ...],
        *,
        started_at: datetime,
        ended_at: datetime,
    ) -> tuple[ReleaseOperationsFacts, ...]:
        assert (workspace_id, service_id) == (WORKSPACE_ID, SERVICE_ID)
        assert started_at < ended_at
        return tuple(item for item in self.facts if item.release_id in release_ids)


class FakeOperationsUnitOfWork:
    """提供符合领域端口的可重复只读事务。"""

    def __init__(self, repository: FakeOperationsRepository) -> None:
        self._repository = repository

    @property
    def operations(self) -> FakeOperationsRepository:
        return self._repository

    def __enter__(self) -> FakeOperationsUnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


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


def _route() -> OperationsRouteContext:
    return OperationsRouteContext(
        service_id=SERVICE_ID,
        service_name="合成运营服务",
        service_status="active",
        route_id=ROUTE_ID,
        route_version=2,
        route_mode="canary",
        route_created_at=NOW - timedelta(hours=2),
        primary_release_id=PRIMARY_ID,
        canary_release_id=CANARY_ID,
        previous_release_id=PRIMARY_ID,
        canary_percent=10,
    )


def _facts(
    release_id: UUID,
    *,
    version: int,
    terminal: int = 10,
    completed: int = 10,
    failed: int = 0,
    degraded: int = 0,
    latency: int = 1_000,
    cost: int = 10_000,
    max_cost: int = 1_000,
    feedback: int = 5,
    helpful: int = 5,
) -> ReleaseOperationsFacts:
    return ReleaseOperationsFacts(
        release_id=release_id,
        release_version=version,
        max_cost_microunits=2_000,
        offline_evaluation_status="passed",
        offline_evaluation_score_bps=10_000,
        run_count=terminal,
        terminal_count=terminal,
        completed_count=completed,
        failed_count=failed,
        degraded_count=degraded,
        latency_p95_ms=latency,
        total_cost_microunits=cost,
        max_run_cost_microunits=max_cost,
        feedback_count=feedback,
        helpful_feedback_count=helpful,
    )


def _service(*facts: ReleaseOperationsFacts) -> AgentOperationsService:
    repository = FakeOperationsRepository(_route(), facts)
    return AgentOperationsService(FakeOperationsUnitOfWork(repository))


def test_report_compares_canary_and_blocks_regressed_metrics() -> None:
    """灰度错误率、降级率、延迟和成本回归都形成阻断证据。"""

    service = _service(
        _facts(PRIMARY_ID, version=1),
        _facts(
            CANARY_ID,
            version=2,
            completed=8,
            failed=2,
            degraded=3,
            latency=12_000,
            cost=30_000,
            max_cost=3_000,
            helpful=2,
        ),
    )
    report = service.get_report(
        _context(),
        workspace_id=WORKSPACE_ID,
        service_id=SERVICE_ID,
        now=NOW,
    )

    assert report.comparison is not None
    assert report.comparison.role == "canary"
    assert report.promotion.status == "blocked"
    assert report.promotion.allowed is False
    assert "ERROR_RATE_HIGH" in report.promotion.reason_codes
    assert "COST_BUDGET_EXCEEDED" in report.promotion.reason_codes
    assert len(report.promotion.evidence_hash) == 64
    assert report.ai_quality_status == "not_configured"
    assert report.online_llm_grading is False


def test_promotion_requires_minimum_samples_and_exact_route_identity() -> None:
    """小样本和锁定 Route 后发生的身份漂移都不能晋级。"""

    insufficient = _service(
        _facts(PRIMARY_ID, version=1),
        _facts(
            CANARY_ID,
            version=2,
            terminal=4,
            completed=4,
            cost=4_000,
            feedback=0,
            helpful=0,
        ),
    )
    evidence = insufficient.evaluate_promotion(
        workspace_id=WORKSPACE_ID,
        service_id=SERVICE_ID,
        route_id=ROUTE_ID,
        primary_release_id=PRIMARY_ID,
        candidate_release_id=CANARY_ID,
        route_started_at=NOW - timedelta(hours=2),
        evaluated_at=NOW,
    )
    drifted = insufficient.evaluate_promotion(
        workspace_id=WORKSPACE_ID,
        service_id=SERVICE_ID,
        route_id=UUID("70000000-0000-4000-8000-000000000312"),
        primary_release_id=PRIMARY_ID,
        candidate_release_id=CANARY_ID,
        route_started_at=NOW - timedelta(hours=2),
        evaluated_at=NOW,
    )

    assert evidence.allowed is False
    assert evidence.reason_codes == ("INSUFFICIENT_TERMINAL_SAMPLES",)
    assert drifted.allowed is False
    assert drifted.reason_codes == ("ROUTE_IDENTITY_CHANGED",)


def test_healthy_canary_passes_without_fabricating_feedback_quality() -> None:
    """无人工反馈不冒充质量已测，但健康运行指标仍可按当前策略晋级。"""

    service = _service(
        _facts(PRIMARY_ID, version=1),
        _facts(CANARY_ID, version=2, feedback=0, helpful=0),
    )
    report = service.get_report(
        _context(), workspace_id=WORKSPACE_ID, service_id=SERVICE_ID, now=NOW
    )

    assert report.comparison is not None
    assert report.comparison.feedback_quality_status == "not_run"
    assert report.comparison.helpful_rate_bps is None
    assert report.promotion.status == "passed"
    assert report.promotion.allowed is True


def test_report_rejects_unregistered_permission() -> None:
    service = _service(_facts(PRIMARY_ID, version=1), _facts(CANARY_ID, version=2))

    with pytest.raises(AgentOperationsDeniedError):
        service.get_report(
            replace(_context(), authorized_permission_code="service.definition.read"),
            workspace_id=WORKSPACE_ID,
            service_id=SERVICE_ID,
        )
