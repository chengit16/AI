"""校验 P5-04 质量运营契约、分阶段来源门禁和真实证据边界。"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

import pytest
from ai_platform_api.modules.quality.application.errors import QualityValidationError
from ai_platform_api.modules.quality.application.operations import (
    build_quality_operation_report,
)
from ai_platform_api.modules.quality.application.operations_policy import (
    HARD_FAILURE_REASON_CODES,
    MINIMUM_ANSWER_ACCEPTANCE_RATE_BPS,
    MINIMUM_CITATION_PRESENCE_RATE_BPS,
    MINIMUM_CITATION_SUPPORT_RATE_BPS,
    MINIMUM_POSITIVE_FEEDBACK_RATE_BPS,
    QUALITY_OPERATION_COLLECTOR_VERSION,
    QUALITY_OPERATION_SOURCES,
    REQUIRED_SOURCES,
    SOURCE_POLICIES,
)
from ai_platform_api.modules.quality.domain.operations import (
    QualityEvaluationOperationContext,
    QualityEvidenceKind,
    QualityGateStage,
    QualityObservationSource,
    QualityOperationBatch,
    QualityOperationReport,
    QualityOperationTarget,
    QualityProviderEvidence,
    QualityProviderSnapshot,
    QualitySourceObservation,
)
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).parents[1]
CONTRACT_DIR = ROOT / "contracts" / "quality"
SCHEMA_PATH = CONTRACT_DIR / "quality-operations-baseline.v1.schema.json"
BASELINE_PATH = CONTRACT_DIR / "quality-operations-baseline.v1.json"
WORKSPACE_ID = UUID("55000000-0000-4000-8000-000000000541")
EVALUATION_RUN_ID = UUID("55000000-0000-4000-8000-000000000542")
DATASET_VERSION_ID = UUID("55000000-0000-4000-8000-000000000543")
SERVICE_ID = UUID("55000000-0000-4000-8000-000000000544")
RELEASE_ID = UUID("55000000-0000-4000-8000-000000000545")
RUNTIME_ID = UUID("55000000-0000-4000-8000-000000000546")
PROVIDER_ID = UUID("55000000-0000-4000-8000-000000000547")
ACTOR_ID = UUID("55000000-0000-4000-8000-000000000548")
WINDOW_END = datetime(2026, 8, 17, 4, 0, tzinfo=UTC)


def load_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def _target(stage: QualityGateStage = "offline_release") -> QualityOperationTarget:
    return QualityOperationTarget(
        EVALUATION_RUN_ID,
        stage,
        WINDOW_END - timedelta(hours=24),
        WINDOW_END,
        "cn-east.synthetic",
        "a" * 64,
    )


def _evaluation(
    status: Literal["passed", "failed"] = "passed",
) -> QualityEvaluationOperationContext:
    return QualityEvaluationOperationContext(
        EVALUATION_RUN_ID,
        status,
        "b" * 64,
        DATASET_VERSION_ID,
        "c" * 64,
        SERVICE_ID,
        RELEASE_ID,
        RUNTIME_ID,
        "d" * 64,
    )


def _provider() -> QualityProviderSnapshot:
    return QualityProviderSnapshot(
        PROVIDER_ID,
        "synthetic-reviewed-provider",
        7,
        "approved",
        "passed",
        "active",
        True,
    )


def _provider_evidence() -> QualityProviderEvidence:
    return QualityProviderEvidence(PROVIDER_ID, 7, "synthetic-quality-model")


def _observation(source: QualityObservationSource) -> QualitySourceObservation:
    if source == "online_feedback":
        return QualitySourceObservation(
            source,
            5,
            0,
            0,
            0,
            0,
            0,
            5,
            4,
            0,
            0,
            0,
            0,
            {"marker": "synthetic-p504-online"},
        )
    sample_count = 20 if source == "offline" else 10
    return QualitySourceObservation(
        source,
        sample_count,
        20,
        20,
        19,
        sample_count,
        sample_count - 1,
        0,
        0,
        8,
        0,
        0,
        0,
        {"marker": f"synthetic-p504-{source}"},
    )


def _report(
    *,
    stage: QualityGateStage = "offline_release",
    evidence_kind: QualityEvidenceKind = "authorized_real",
    observations: tuple[QualitySourceObservation, ...] | None = None,
    provider: QualityProviderSnapshot | None = None,
) -> QualityOperationReport:
    target = _target(stage)
    provider_evidence = _provider_evidence() if evidence_kind == "authorized_real" else None
    batch = QualityOperationBatch(
        WORKSPACE_ID,
        target,
        evidence_kind,
        provider_evidence,
        observations or (_observation("offline"),),
    )
    return build_quality_operation_report(
        workspace_id=WORKSPACE_ID,
        target=target,
        batch=batch,
        collector_version=QUALITY_OPERATION_COLLECTOR_VERSION,
        evaluation=_evaluation(),
        provider=(
            _provider() if provider is None and evidence_kind == "authorized_real" else provider
        ),
        actor_id=ACTOR_ID,
        completed_at=WINDOW_END,
    )


def test_p504_baseline_matches_versioned_schema() -> None:
    schema = load_object(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        load_object(BASELINE_PATH)
    )


def test_p504_contract_matches_frozen_thresholds_and_stage_dependencies() -> None:
    baseline = load_object(BASELINE_PATH)
    thresholds = cast(dict[str, int], baseline["thresholds"])
    policies = cast(list[dict[str, object]], baseline["source_policies"])
    stages = cast(list[dict[str, object]], baseline["gate_stages"])

    assert thresholds == {
        "citation_presence_rate_bps": MINIMUM_CITATION_PRESENCE_RATE_BPS,
        "citation_support_rate_bps": MINIMUM_CITATION_SUPPORT_RATE_BPS,
        "answer_acceptance_rate_bps": MINIMUM_ANSWER_ACCEPTANCE_RATE_BPS,
        "positive_feedback_rate_bps": MINIMUM_POSITIVE_FEEDBACK_RATE_BPS,
        "unauthorized_access_count": 0,
        "restricted_field_leakage_count": 0,
        "unauthorized_tool_call_count": 0,
    }
    assert [item.source for item in SOURCE_POLICIES] == list(QUALITY_OPERATION_SOURCES)
    assert [item.minimum_sample_count for item in SOURCE_POLICIES] == [
        item["minimum_sample_count"] for item in policies
    ]
    assert {
        cast(QualityGateStage, item["stage"]): frozenset(
            cast(list[QualityObservationSource], item["required_sources"])
        )
        for item in stages
    } == REQUIRED_SOURCES
    assert baseline["hard_failure_reason_codes"] == sorted(HARD_FAILURE_REASON_CODES)
    assert baseline["llm_grading"] is False


def test_p504_synthetic_evidence_remains_blocked_without_real_quality_claim() -> None:
    report = _report(evidence_kind="synthetic")

    assert report.window.core_functional_status == "passed"
    assert report.window.provider_integration_status == "not_configured"
    assert report.window.ai_quality_status == "not_configured"
    assert report.window.capacity_certification_status == "not_run"
    assert report.window.release_gate_status == "blocked"
    assert "real_evidence_not_configured" in report.window.reason_codes


def test_p504_gate_requires_only_sources_relevant_to_current_release_stage() -> None:
    offline = _report()
    canary_missing = _report(stage="canary_promotion")
    canary = _report(
        stage="canary_promotion",
        observations=(_observation("offline"), _observation("canary")),
    )
    online = _report(
        stage="online_continuation",
        observations=tuple(_observation(source) for source in QUALITY_OPERATION_SOURCES),
    )

    assert offline.window.release_gate_status == "passed"
    assert canary_missing.window.ai_quality_status == "not_run"
    assert canary_missing.window.release_gate_status == "blocked"
    assert canary.window.release_gate_status == "passed"
    assert online.window.release_gate_status == "passed"


def test_p504_security_failure_cannot_be_offset_by_quality_rates() -> None:
    compromised = replace(
        _observation("offline"),
        unauthorized_tool_call_count=1,
        tool_call_count=20,
    )
    report = _report(observations=(compromised,))

    assert report.sources[0].citation_support_rate_bps == 9_500
    assert report.sources[0].answer_acceptance_rate_bps == 9_500
    assert report.sources[0].status == "failed"
    assert report.window.ai_quality_status == "failed"
    assert report.window.release_gate_status == "failed"
    assert "unauthorized_tool_call" in report.window.reason_codes


def test_p504_low_quality_and_invalid_counts_fail_closed() -> None:
    low_quality = replace(
        _observation("offline"),
        valid_citation_count=19,
        supported_citation_count=18,
        acceptable_answer_count=16,
    )
    report = _report(observations=(low_quality,))
    assert report.sources[0].status == "failed"
    assert {
        "citation_presence_below_threshold",
        "citation_support_below_threshold",
        "answer_acceptance_below_threshold",
    }.issubset(report.window.reason_codes)

    invalid = replace(_observation("offline"), valid_citation_count=21)
    with pytest.raises(QualityValidationError):
        _report(observations=(invalid,))


def test_p504_collector_identity_drift_fails_closed() -> None:
    target = _target()
    drifted = replace(target, network_region="cn-west.synthetic")
    batch = QualityOperationBatch(
        WORKSPACE_ID,
        drifted,
        "authorized_real",
        _provider_evidence(),
        (_observation("offline"),),
    )
    report = build_quality_operation_report(
        workspace_id=WORKSPACE_ID,
        target=target,
        batch=batch,
        collector_version=QUALITY_OPERATION_COLLECTOR_VERSION,
        evaluation=_evaluation(),
        provider=_provider(),
        actor_id=ACTOR_ID,
        completed_at=WINDOW_END,
    )

    assert report.window.core_functional_status == "failed"
    assert report.window.ai_quality_status == "failed"
    assert report.window.release_gate_status == "failed"
    assert all("collector_identity_drift" in item.reason_codes for item in report.sources)
