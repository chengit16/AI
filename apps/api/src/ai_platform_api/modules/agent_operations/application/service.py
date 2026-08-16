"""聚合 AgentRelease 运行指标，并为灰度晋级生成确定性证据。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.agent_operations.domain.models import (
    AgentOperationsReport,
    AgentOperationsUnitOfWork,
    AlertSeverity,
    OperationsAlert,
    OperationsRole,
    OperationsRouteContext,
    PromotionDecision,
    PromotionStatus,
    ReleaseOperationsFacts,
    ReleaseOperationsMetrics,
)
from ai_platform_api.modules.service_governance.domain.models import (
    ServicePromotionEvidence,
)

READ_PERMISSION = "agent.operations.read"
POLICY_VERSION = "agent-operations-v1"
DEFAULT_WINDOW_HOURS = 24
MINIMUM_TERMINAL_SAMPLES = 5
MINIMUM_FEEDBACK_SAMPLES = 3
MAX_ERROR_RATE_BPS = 1_000
MAX_DEGRADATION_RATE_BPS = 2_000
MAX_LATENCY_P95_MS = 10_000
MIN_HELPFUL_RATE_BPS = 7_000
MAX_ERROR_REGRESSION_BPS = 500
MAX_DEGRADATION_REGRESSION_BPS = 1_000
MAX_LATENCY_RATIO_BPS = 15_000
MAX_COST_RATIO_BPS = 15_000


class AgentOperationsDeniedError(PlatformError):
    """当前主体没有 Agent 运营汇总读取权限。"""

    error_code = "POLICY_DENIED"


class AgentOperationsNotFoundError(PlatformError):
    """目标服务或当前 Route 在工作空间内不存在。"""

    error_code = "RESOURCE_NOT_FOUND"


class AgentOperationsValidationError(PlatformError):
    """运营窗口或服务标识不符合稳定查询契约。"""

    error_code = "VALIDATION_ERROR"


class AgentOperationsService:
    """提供只读运营报告，并实现服务治理使用的窄晋级门禁协议。"""

    def __init__(self, unit_of_work: AgentOperationsUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def get_report(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        service_id: UUID,
        window_hours: int = DEFAULT_WINDOW_HOURS,
        now: datetime | None = None,
    ) -> AgentOperationsReport:
        """读取当前 Route 的主版本和灰度或上一版本运营对比。"""

        if (
            context.workspace_id != workspace_id
            or context.authorized_permission_code != READ_PERMISSION
            or not context.authorized_workspace
            or context.authentication_method != "browser_session"
            or not 1 <= window_hours <= 168
        ):
            raise AgentOperationsDeniedError
        ended_at = now or datetime.now(UTC)
        return self._build_report(
            workspace_id,
            service_id,
            started_at=ended_at - timedelta(hours=window_hours),
            ended_at=ended_at,
            promotion_only=False,
        )

    def evaluate_promotion(
        self,
        *,
        workspace_id: UUID,
        service_id: UUID,
        route_id: UUID,
        primary_release_id: UUID,
        candidate_release_id: UUID,
        route_started_at: datetime,
        evaluated_at: datetime,
    ) -> ServicePromotionEvidence:
        """按当前灰度开始时间评估晋级；身份漂移时直接返回阻断证据。"""

        report = self._build_report(
            workspace_id,
            service_id,
            started_at=max(route_started_at, evaluated_at - timedelta(hours=DEFAULT_WINDOW_HOURS)),
            ended_at=evaluated_at,
            promotion_only=True,
        )
        comparison = report.comparison
        identity_matches = (
            report.route_id == route_id
            and report.primary.release_id == primary_release_id
            and comparison is not None
            and comparison.role == "canary"
            and comparison.release_id == candidate_release_id
        )
        if not identity_matches:
            return ServicePromotionEvidence(
                allowed=False,
                policy_version=POLICY_VERSION,
                evidence_hash=_digest({"reason": "ROUTE_IDENTITY_CHANGED"}),
                reason_codes=("ROUTE_IDENTITY_CHANGED",),
            )
        return ServicePromotionEvidence(
            allowed=report.promotion.allowed,
            policy_version=report.promotion.policy_version,
            evidence_hash=report.promotion.evidence_hash,
            reason_codes=report.promotion.reason_codes,
        )

    def _build_report(
        self,
        workspace_id: UUID,
        service_id: UUID,
        *,
        started_at: datetime,
        ended_at: datetime,
        promotion_only: bool,
    ) -> AgentOperationsReport:
        """在同一只读快照中取 Route 和汇总，随后使用纯规则生成告警。"""

        # 1. Route 身份和跨表指标必须来自同一只读事务，避免并发切换后拼出混合版本报告。
        with self._unit_of_work as unit_of_work:
            route = unit_of_work.operations.get_route_context(workspace_id, service_id)
            if route is None:
                raise AgentOperationsNotFoundError
            comparison_id, comparison_role = _comparison_target(route)
            release_ids = tuple(
                dict.fromkeys(
                    value
                    for value in (route.primary_release_id, comparison_id)
                    if value is not None
                )
            )
            facts = unit_of_work.operations.aggregate_releases(
                workspace_id,
                service_id,
                release_ids,
                started_at=started_at,
                ended_at=ended_at,
            )
        # 2. 事务外只执行确定性转换与阈值判断，使 HTTP 查询和晋级门禁复用完全相同的证据。
        by_release = {item.release_id: item for item in facts}
        try:
            primary = _metrics(by_release[route.primary_release_id], "primary")
            comparison = (
                _metrics(by_release[comparison_id], comparison_role)
                if comparison_id is not None and comparison_role is not None
                else None
            )
        except KeyError as error:
            raise AgentOperationsNotFoundError from error
        alerts = _alerts(primary, comparison)
        promotion = _promotion(route, primary, comparison, alerts)
        if promotion_only and route.route_mode != "canary":
            promotion = replace(
                promotion,
                status="blocked",
                allowed=False,
                reason_codes=("CANARY_ROUTE_REQUIRED",),
                evidence_hash=_digest({"reason": "CANARY_ROUTE_REQUIRED"}),
            )
        return AgentOperationsReport(
            service_id=route.service_id,
            service_name=route.service_name,
            service_status=route.service_status,
            route_id=route.route_id,
            route_version=route.route_version,
            route_mode=route.route_mode,
            canary_percent=route.canary_percent,
            window_started_at=started_at,
            window_ended_at=ended_at,
            minimum_terminal_samples=MINIMUM_TERMINAL_SAMPLES,
            minimum_feedback_samples=MINIMUM_FEEDBACK_SAMPLES,
            primary=primary,
            comparison=comparison,
            alerts=alerts,
            promotion=promotion,
        )


def _comparison_target(
    route: OperationsRouteContext,
) -> tuple[UUID | None, OperationsRole | None]:
    if route.canary_release_id is not None:
        return route.canary_release_id, "canary"
    if route.previous_release_id is not None:
        return route.previous_release_id, "previous"
    return None, None


def _rate(numerator: int, denominator: int) -> int | None:
    return round(numerator * 10_000 / denominator) if denominator else None


def _metrics(facts: ReleaseOperationsFacts, role: OperationsRole) -> ReleaseOperationsMetrics:
    """把计数转换为基点比例；无样本保持空值，禁止用零伪装已测。"""

    return ReleaseOperationsMetrics(
        release_id=facts.release_id,
        release_version=facts.release_version,
        role=role,
        run_count=facts.run_count,
        terminal_count=facts.terminal_count,
        completed_count=facts.completed_count,
        failed_count=facts.failed_count,
        success_rate_bps=_rate(facts.completed_count, facts.terminal_count),
        error_rate_bps=_rate(facts.failed_count, facts.terminal_count),
        degradation_rate_bps=_rate(facts.degraded_count, facts.completed_count),
        latency_p95_ms=facts.latency_p95_ms,
        total_cost_microunits=facts.total_cost_microunits,
        average_cost_microunits=(
            round(facts.total_cost_microunits / facts.terminal_count)
            if facts.terminal_count
            else None
        ),
        max_run_cost_microunits=facts.max_run_cost_microunits,
        max_cost_budget_microunits=facts.max_cost_microunits,
        feedback_count=facts.feedback_count,
        helpful_rate_bps=_rate(facts.helpful_feedback_count, facts.feedback_count),
        feedback_quality_status=(
            "measured" if facts.feedback_count >= MINIMUM_FEEDBACK_SAMPLES else "not_run"
        ),
        offline_evaluation_status=facts.offline_evaluation_status,
        offline_evaluation_score_bps=facts.offline_evaluation_score_bps,
    )


def _alerts(
    primary: ReleaseOperationsMetrics,
    comparison: ReleaseOperationsMetrics | None,
) -> tuple[OperationsAlert, ...]:
    alerts = [*_absolute_alerts(primary)]
    if comparison is not None:
        alerts.extend(_absolute_alerts(comparison))
        if comparison.role == "canary":
            alerts.extend(_regression_alerts(primary, comparison))
    return tuple(alerts)


def _absolute_alerts(metrics: ReleaseOperationsMetrics) -> list[OperationsAlert]:
    alerts: list[OperationsAlert] = []
    # 1. 小样本只报告样本不足，不能继续计算并放大不稳定的比例告警。
    if metrics.terminal_count < MINIMUM_TERMINAL_SAMPLES:
        alerts.append(
            _alert(
                "INSUFFICIENT_TERMINAL_SAMPLES",
                "warning",
                metrics,
                "terminal_count",
                metrics.terminal_count,
                MINIMUM_TERMINAL_SAMPLES,
                blocks=metrics.role == "canary",
            )
        )
        return alerts
    # 2. 运行健康和成本执行统一绝对阈值；只有灰度版本的异常参与晋级阻断。
    thresholds = (
        ("ERROR_RATE_HIGH", "error_rate_bps", metrics.error_rate_bps, MAX_ERROR_RATE_BPS),
        (
            "DEGRADATION_RATE_HIGH",
            "degradation_rate_bps",
            metrics.degradation_rate_bps,
            MAX_DEGRADATION_RATE_BPS,
        ),
        ("LATENCY_P95_HIGH", "latency_p95_ms", metrics.latency_p95_ms, MAX_LATENCY_P95_MS),
        (
            "COST_BUDGET_EXCEEDED",
            "max_run_cost_microunits",
            metrics.max_run_cost_microunits,
            metrics.max_cost_budget_microunits,
        ),
    )
    for code, metric, observed, threshold in thresholds:
        if observed is not None and observed > threshold:
            alerts.append(
                _alert(
                    code,
                    "critical",
                    metrics,
                    metric,
                    observed,
                    threshold,
                    blocks=metrics.role == "canary",
                )
            )
    # 3. 人工反馈达到最低样本后才形成质量告警，未测状态不得按零分处理。
    if (
        metrics.feedback_quality_status == "measured"
        and metrics.helpful_rate_bps is not None
        and metrics.helpful_rate_bps < MIN_HELPFUL_RATE_BPS
    ):
        alerts.append(
            _alert(
                "QUALITY_FEEDBACK_LOW",
                "critical",
                metrics,
                "helpful_rate_bps",
                metrics.helpful_rate_bps,
                MIN_HELPFUL_RATE_BPS,
                blocks=metrics.role == "canary",
            )
        )
    return alerts


def _regression_alerts(
    primary: ReleaseOperationsMetrics,
    canary: ReleaseOperationsMetrics,
) -> list[OperationsAlert]:
    """只在两个版本都有最小样本时比较，避免小样本比例被放大。"""

    # 1. 两个版本都达到终态样本门槛后，版本间回归才具有阻断意义。
    if (
        primary.terminal_count < MINIMUM_TERMINAL_SAMPLES
        or canary.terminal_count < MINIMUM_TERMINAL_SAMPLES
    ):
        return []
    alerts: list[OperationsAlert] = []
    # 2. 错误率和降级率使用百分点容忍值，候选必须明显恶化才报告回归。
    pairs = (
        (
            "ERROR_RATE_REGRESSION",
            "error_rate_bps",
            canary.error_rate_bps,
            primary.error_rate_bps,
            MAX_ERROR_REGRESSION_BPS,
        ),
        (
            "DEGRADATION_RATE_REGRESSION",
            "degradation_rate_bps",
            canary.degradation_rate_bps,
            primary.degradation_rate_bps,
            MAX_DEGRADATION_REGRESSION_BPS,
        ),
    )
    for code, metric, candidate, baseline, tolerance in pairs:
        if candidate is not None and baseline is not None and candidate > baseline + tolerance:
            alerts.append(
                _alert(
                    code,
                    "critical",
                    canary,
                    metric,
                    candidate,
                    baseline + tolerance,
                    blocks=True,
                )
            )
    # 3. 延迟和平均成本使用主版本倍率阈值，兼容不同服务的绝对量级差异。
    ratio_pairs = (
        (
            "LATENCY_REGRESSION",
            "latency_p95_ms",
            canary.latency_p95_ms,
            primary.latency_p95_ms,
            MAX_LATENCY_RATIO_BPS,
        ),
        (
            "COST_REGRESSION",
            "average_cost_microunits",
            canary.average_cost_microunits,
            primary.average_cost_microunits,
            MAX_COST_RATIO_BPS,
        ),
    )
    for code, metric, candidate, baseline, ratio_bps in ratio_pairs:
        threshold = round(baseline * ratio_bps / 10_000) if baseline is not None else None
        if candidate is not None and threshold is not None and candidate > threshold:
            alerts.append(
                _alert(code, "critical", canary, metric, candidate, threshold, blocks=True)
            )
    return alerts


def _alert(
    code: str,
    severity: AlertSeverity,
    metrics: ReleaseOperationsMetrics,
    metric: str,
    observed: int,
    threshold: int,
    *,
    blocks: bool,
) -> OperationsAlert:
    return OperationsAlert(
        code=code,
        severity=severity,
        release_role=metrics.role,
        metric=metric,
        observed_value=observed,
        threshold_value=threshold,
        blocks_promotion=blocks,
    )


def _promotion(
    route: OperationsRouteContext,
    primary: ReleaseOperationsMetrics,
    comparison: ReleaseOperationsMetrics | None,
    alerts: tuple[OperationsAlert, ...],
) -> PromotionDecision:
    status: PromotionStatus
    reasons: tuple[str, ...]
    if route.route_mode != "canary" or comparison is None or comparison.role != "canary":
        status, allowed, reasons = "not_applicable", False, ()
    elif comparison.terminal_count < MINIMUM_TERMINAL_SAMPLES:
        status, allowed, reasons = "insufficient_data", False, ("INSUFFICIENT_TERMINAL_SAMPLES",)
    else:
        reasons = tuple(
            dict.fromkeys(
                item.code
                for item in alerts
                if item.release_role == "canary" and item.blocks_promotion
            )
        )
        status, allowed = ("blocked", False) if reasons else ("passed", True)
    evidence = {
        "policy_version": POLICY_VERSION,
        "route_id": str(route.route_id),
        "route_version": route.route_version,
        "primary": asdict(primary),
        "comparison": asdict(comparison) if comparison is not None else None,
        "reason_codes": reasons,
    }
    return PromotionDecision(status, allowed, POLICY_VERSION, reasons, _digest(evidence))


def _digest(document: object) -> str:
    return hashlib.sha256(
        json.dumps(
            document,
            default=str,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
