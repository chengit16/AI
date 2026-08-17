"""冻结 P5-04 质量运营来源下限、质量阈值和发布阶段依赖。"""

from __future__ import annotations

from dataclasses import dataclass

from ai_platform_api.modules.quality.domain.operations import (
    QualityGateStage,
    QualityObservationSource,
)

QUALITY_OPERATION_SOURCES: tuple[QualityObservationSource, ...] = (
    "offline",
    "canary",
    "online_feedback",
)
QUALITY_OPERATION_POLICY_VERSION = "quality-operations-v1"
QUALITY_OPERATION_COLLECTOR_VERSION = "quality-window-collector-v1"
MINIMUM_CITATION_PRESENCE_RATE_BPS = 10_000
MINIMUM_CITATION_SUPPORT_RATE_BPS = 9_500
MINIMUM_ANSWER_ACCEPTANCE_RATE_BPS = 8_500
MINIMUM_POSITIVE_FEEDBACK_RATE_BPS = 8_000


@dataclass(frozen=True)
class QualitySourcePolicy:
    """声明单一来源的样本下限和必须具备的指标分母。"""

    source: QualityObservationSource
    minimum_sample_count: int
    require_citations: bool
    require_answers: bool
    require_feedback: bool


SOURCE_POLICIES: tuple[QualitySourcePolicy, ...] = (
    QualitySourcePolicy("offline", 10, True, True, False),
    QualitySourcePolicy("canary", 5, True, True, False),
    QualitySourcePolicy("online_feedback", 3, False, False, True),
)

REQUIRED_SOURCES: dict[QualityGateStage, frozenset[QualityObservationSource]] = {
    "offline_release": frozenset({"offline"}),
    "canary_promotion": frozenset({"offline", "canary"}),
    "online_continuation": frozenset(QUALITY_OPERATION_SOURCES),
}

HARD_FAILURE_REASON_CODES = frozenset(
    {
        "unauthorized_access",
        "restricted_field_leakage",
        "unauthorized_tool_call",
    }
)

QUALITY_OPERATION_REASON_CODES = frozenset(
    {
        *HARD_FAILURE_REASON_CODES,
        "evaluation_failed",
        "collector_identity_drift",
        "source_not_run",
        "source_sample_insufficient",
        "citation_evidence_missing",
        "citation_presence_below_threshold",
        "citation_support_below_threshold",
        "answer_evidence_missing",
        "answer_acceptance_below_threshold",
        "feedback_evidence_missing",
        "positive_feedback_below_threshold",
        "provider_not_configured",
        "provider_review_pending",
        "provider_review_rejected",
        "provider_probe_not_run",
        "provider_probe_failed",
        "provider_inactive",
        "provider_route_mismatch",
        "real_evidence_not_configured",
    }
)
