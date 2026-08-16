"""定义不含正文和高基数主体信息的 AgentRelease 运营读模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

OperationsRole = Literal["primary", "canary", "previous"]
QualityStatus = Literal["measured", "not_run"]
PromotionStatus = Literal["passed", "blocked", "insufficient_data", "not_applicable"]
AlertSeverity = Literal["warning", "critical"]


@dataclass(frozen=True)
class OperationsRouteContext:
    """保存运营查询所需的当前 Route 和相邻 Release 身份。"""

    service_id: UUID
    service_name: str
    service_status: str
    route_id: UUID
    route_version: int
    route_mode: str
    route_created_at: datetime
    primary_release_id: UUID
    canary_release_id: UUID | None
    previous_release_id: UUID | None
    canary_percent: int


@dataclass(frozen=True)
class ReleaseOperationsFacts:
    """表示 PostgreSQL 从既有运行、调用和反馈事实聚合出的原始计数。"""

    release_id: UUID
    release_version: int
    max_cost_microunits: int
    offline_evaluation_status: str
    offline_evaluation_score_bps: int
    run_count: int
    terminal_count: int
    completed_count: int
    failed_count: int
    degraded_count: int
    latency_p95_ms: int | None
    total_cost_microunits: int
    max_run_cost_microunits: int
    feedback_count: int
    helpful_feedback_count: int


@dataclass(frozen=True)
class ReleaseOperationsMetrics:
    """向 API 返回可比较比例和成本，不暴露 Run、Actor、Prompt 或正文。"""

    release_id: UUID
    release_version: int
    role: OperationsRole
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
    feedback_quality_status: QualityStatus
    offline_evaluation_status: str
    offline_evaluation_score_bps: int


@dataclass(frozen=True)
class OperationsAlert:
    """表示由稳定阈值推导的低基数告警，不携带自由文本或主体标识。"""

    code: str
    severity: AlertSeverity
    release_role: OperationsRole
    metric: str
    observed_value: int
    threshold_value: int
    blocks_promotion: bool


@dataclass(frozen=True)
class PromotionDecision:
    """冻结一次灰度晋级判断及其可复算证据摘要。"""

    status: PromotionStatus
    allowed: bool
    policy_version: str
    reason_codes: tuple[str, ...]
    evidence_hash: str


@dataclass(frozen=True)
class AgentOperationsReport:
    """聚合一个 Service 当前 Route 的 Release 对比、告警和晋级结论。"""

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
    primary: ReleaseOperationsMetrics
    comparison: ReleaseOperationsMetrics | None
    alerts: tuple[OperationsAlert, ...]
    promotion: PromotionDecision
    ai_quality_status: Literal["not_configured"] = "not_configured"
    online_llm_grading: bool = False


class AgentOperationsRepository(Protocol):
    """从现有发布与运行事实构建运营投影，不拥有其写入权。"""

    def get_route_context(
        self,
        workspace_id: UUID,
        service_id: UUID,
    ) -> OperationsRouteContext | None: ...

    def aggregate_releases(
        self,
        workspace_id: UUID,
        service_id: UUID,
        release_ids: tuple[UUID, ...],
        *,
        started_at: datetime,
        ended_at: datetime,
    ) -> tuple[ReleaseOperationsFacts, ...]: ...


class AgentOperationsUnitOfWork(Protocol):
    """为跨表只读聚合提供一致数据库快照。"""

    @property
    def operations(self) -> AgentOperationsRepository: ...

    def __enter__(self) -> AgentOperationsUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
