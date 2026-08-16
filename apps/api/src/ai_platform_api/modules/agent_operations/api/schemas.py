"""定义 AgentRelease 运营视图的稳定脱敏 HTTP Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ai_platform_api.modules.agent_operations.application import AgentOperationsReport


class ReleaseOperationsMetricsResponse(BaseModel):
    """返回单个 Release 的运行、质量和成本聚合，不包含 Run 身份。"""

    model_config = ConfigDict(extra="forbid")

    release_id: UUID
    release_version: int
    role: Literal["primary", "canary", "previous"]
    run_count: int
    terminal_count: int
    completed_count: int
    failed_count: int
    success_rate_bps: int | None
    error_rate_bps: int | None
    degradation_rate_bps: int | None
    latency_p95_ms: int | None
    total_cost_microunits: int
    average_cost_microunits: int | None
    max_run_cost_microunits: int
    max_cost_budget_microunits: int
    feedback_count: int
    helpful_rate_bps: int | None
    feedback_quality_status: Literal["measured", "not_run"]
    offline_evaluation_status: str
    offline_evaluation_score_bps: int


class OperationsAlertResponse(BaseModel):
    """返回稳定告警码和阈值，禁止携带动态正文或主体标识。"""

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Literal["warning", "critical"]
    release_role: Literal["primary", "canary", "previous"]
    metric: str
    observed_value: int
    threshold_value: int
    blocks_promotion: bool


class PromotionDecisionResponse(BaseModel):
    """返回当前 Route 在固定策略下的晋级结论和证据摘要。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "blocked", "insufficient_data", "not_applicable"]
    allowed: bool
    policy_version: str
    reason_codes: tuple[str, ...]
    evidence_hash: str


class AgentOperationsReportResponse(BaseModel):
    """返回一个服务当前 Route 的主版本和比较版本运营报告。"""

    model_config = ConfigDict(extra="forbid")

    service_id: UUID
    service_name: str
    service_status: str
    route_id: UUID
    route_version: int
    route_mode: str
    canary_percent: int
    window_started_at: datetime
    window_ended_at: datetime
    minimum_terminal_samples: int
    minimum_feedback_samples: int
    primary: ReleaseOperationsMetricsResponse
    comparison: ReleaseOperationsMetricsResponse | None
    alerts: tuple[OperationsAlertResponse, ...]
    promotion: PromotionDecisionResponse
    ai_quality_status: Literal["not_configured"]
    online_llm_grading: bool

    @classmethod
    def from_domain(cls, report: AgentOperationsReport) -> Self:
        return cls(
            service_id=report.service_id,
            service_name=report.service_name,
            service_status=report.service_status,
            route_id=report.route_id,
            route_version=report.route_version,
            route_mode=report.route_mode,
            canary_percent=report.canary_percent,
            window_started_at=report.window_started_at,
            window_ended_at=report.window_ended_at,
            minimum_terminal_samples=report.minimum_terminal_samples,
            minimum_feedback_samples=report.minimum_feedback_samples,
            primary=ReleaseOperationsMetricsResponse(**report.primary.__dict__),
            comparison=(
                ReleaseOperationsMetricsResponse(**report.comparison.__dict__)
                if report.comparison is not None
                else None
            ),
            alerts=tuple(OperationsAlertResponse(**item.__dict__) for item in report.alerts),
            promotion=PromotionDecisionResponse(**report.promotion.__dict__),
            ai_quality_status=report.ai_quality_status,
            online_llm_grading=report.online_llm_grading,
        )
