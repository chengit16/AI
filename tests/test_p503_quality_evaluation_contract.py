"""校验 P5-03 六层评估契约、失败关闭聚合和证据摘要边界。"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from ai_platform_api.modules.quality.application.errors import QualityValidationError
from ai_platform_api.modules.quality.application.evaluation import (
    build_quality_evaluation_report,
)
from ai_platform_api.modules.quality.application.evaluation_policy import (
    AGGREGATE_REASON_CODES,
    HARD_FAILURE_REASON_CODES,
    QUALITY_EVALUATION_LAYERS,
    QUALITY_EVALUATION_POLICY,
)
from ai_platform_api.modules.quality.domain.evaluation import (
    QualityEvaluationBatch,
    QualityEvaluationLayer,
    QualityEvaluationObservation,
    QualityEvaluationOutcome,
    QualityEvaluationReport,
    QualityEvaluationTarget,
)
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).parents[1]
CONTRACT_DIR = ROOT / "contracts" / "quality"
SCHEMA_PATH = CONTRACT_DIR / "quality-evaluation-baseline.v1.schema.json"
BASELINE_PATH = CONTRACT_DIR / "quality-evaluation-baseline.v1.json"
ACTOR_ID = UUID("55000000-0000-4000-8000-000000000501")
COMPLETED_AT = datetime(2026, 8, 17, 1, 0, tzinfo=UTC)


def load_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def target(document: dict[str, Any]) -> tuple[UUID, QualityEvaluationTarget]:
    value = cast(dict[str, str], document["target"])
    return UUID(value["workspace_id"]), QualityEvaluationTarget(
        UUID(value["dataset_version_id"]),
        value["dataset_digest"],
        UUID(value["service_id"]),
        UUID(value["agent_release_id"]),
        value["run_configuration_digest"],
    )


def observations(document: dict[str, Any]) -> tuple[QualityEvaluationObservation, ...]:
    values = cast(list[dict[str, object]], document["passing_observations"])
    return tuple(
        QualityEvaluationObservation(
            UUID(cast(str, value["sample_version_id"])),
            cast(QualityEvaluationLayer, value["layer"]),
            cast(QualityEvaluationOutcome, value["outcome"]),
            cast(int, value["score_bps"]),
            cast(int, value["duration_ms"]),
            tuple(cast(list[str], value["reason_codes"])),
            cast(dict[str, object], value["evidence"]),
        )
        for value in values
    )


def build_report(
    document: dict[str, Any],
    *,
    values: tuple[QualityEvaluationObservation, ...] | None = None,
    batch: QualityEvaluationBatch | None = None,
) -> QualityEvaluationReport:
    workspace_id, evaluation_target = target(document)
    members = tuple(UUID(value) for value in cast(list[str], document["member_sample_ids"]))
    evaluation_batch = batch or QualityEvaluationBatch(
        workspace_id,
        evaluation_target,
        QUALITY_EVALUATION_POLICY.evaluator_version,
        observations(document) if values is None else values,
    )
    return build_quality_evaluation_report(
        workspace_id=workspace_id,
        authoritative_dataset_digest=evaluation_target.dataset_digest,
        member_sample_ids=members,
        target=evaluation_target,
        batch=evaluation_batch,
        policy=QUALITY_EVALUATION_POLICY,
        actor_id=ACTOR_ID,
        completed_at=COMPLETED_AT,
    )


def test_p503_baseline_matches_versioned_schema() -> None:
    schema = load_object(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        load_object(BASELINE_PATH)
    )


def test_p503_contract_matches_frozen_policy_and_reason_registry() -> None:
    baseline = load_object(BASELINE_PATH)
    policies = cast(list[dict[str, object]], baseline["layers"])

    assert tuple(item["layer"] for item in policies) == QUALITY_EVALUATION_LAYERS
    assert [
        (
            item.layer,
            item.minimum_sample_count,
            item.minimum_score_bps,
            sorted(item.allowed_reason_codes),
        )
        for item in QUALITY_EVALUATION_POLICY.layers
    ] == [
        (
            item["layer"],
            item["minimum_sample_count"],
            item["minimum_score_bps"],
            item["allowed_reason_codes"],
        )
        for item in policies
    ]
    assert baseline["hard_failure_reason_codes"] == sorted(HARD_FAILURE_REASON_CODES)
    assert baseline["aggregate_reason_codes"] == sorted(AGGREGATE_REASON_CODES)
    assert baseline["llm_grading"] is False
    assert baseline["external_evidence"] == {
        "real_model_quality": "not_run",
        "real_online_feedback": "not_run",
    }


def test_p503_passing_fixture_produces_six_layers_without_evidence_body() -> None:
    baseline = load_object(BASELINE_PATH)
    report = build_report(baseline)

    assert report.run.status == "passed"
    assert report.run.observation_count == 12
    assert tuple(item.layer for item in report.layers) == QUALITY_EVALUATION_LAYERS
    assert all(item.status == "passed" for item in report.layers)
    serialized = repr(report)
    for value in cast(list[dict[str, Any]], baseline["passing_observations"]):
        assert cast(dict[str, str], value["evidence"])["detail"] not in serialized


@pytest.mark.parametrize(
    ("outcome", "reason_code", "aggregate_code"),
    (
        ("failed", "evaluation_failed", "observation_failed"),
        ("timeout", "evaluation_timeout", "observation_timeout"),
        ("skipped", "evaluation_skipped", "observation_skipped"),
    ),
)
def test_p503_non_passing_outcomes_fail_closed(
    outcome: QualityEvaluationOutcome,
    reason_code: str,
    aggregate_code: str,
) -> None:
    baseline = load_object(BASELINE_PATH)
    values = list(observations(baseline))
    values[0] = replace(
        values[0],
        outcome=outcome,
        score_bps=0,
        reason_codes=(reason_code,),
    )

    report = build_report(baseline, values=tuple(values))

    retrieval = report.layers[0]
    assert report.run.status == "failed"
    assert retrieval.status == "failed"
    assert aggregate_code in retrieval.reason_codes


def test_p503_sample_shortage_fails_only_affected_layer_and_whole_run() -> None:
    baseline = load_object(BASELINE_PATH)
    values = tuple(
        item
        for item in observations(baseline)
        if not (item.layer == "tool" and item.sample_version_id.hex.endswith("522"))
    )

    report = build_report(baseline, values=values)

    assert report.run.status == "failed"
    assert report.layers[-1].status == "failed"
    assert "sample_insufficient" in report.layers[-1].reason_codes
    assert all(item.status == "passed" for item in report.layers[:-1])


def test_p503_identity_drift_fails_all_layers_with_specific_reason() -> None:
    baseline = load_object(BASELINE_PATH)
    workspace_id, evaluation_target = target(baseline)
    drifted_target = replace(
        evaluation_target,
        service_id=UUID("55000000-0000-4000-8000-000000000599"),
    )
    batch = QualityEvaluationBatch(
        workspace_id,
        drifted_target,
        QUALITY_EVALUATION_POLICY.evaluator_version,
        observations(baseline),
    )

    report = build_report(baseline, batch=batch)

    assert report.run.status == "failed"
    assert all("service_identity_drift" in item.reason_codes for item in report.layers)


def test_p503_security_hard_failure_cannot_be_offset_by_full_scores() -> None:
    baseline = load_object(BASELINE_PATH)
    values = list(observations(baseline))
    citation_index = next(index for index, item in enumerate(values) if item.layer == "citation")
    values[citation_index] = replace(
        values[citation_index],
        outcome="failed",
        score_bps=10_000,
        reason_codes=("invalid_citation",),
    )

    report = build_report(baseline, values=tuple(values))

    citation = next(item for item in report.layers if item.layer == "citation")
    assert citation.score_bps == 10_000
    assert citation.status == "failed"
    assert "invalid_citation" in report.run.reason_codes


def test_p503_unknown_reason_code_is_rejected() -> None:
    baseline = load_object(BASELINE_PATH)
    values = list(observations(baseline))
    values[0] = replace(
        values[0],
        outcome="failed",
        reason_codes=("unbounded_dynamic_reason",),
    )

    with pytest.raises(QualityValidationError):
        build_report(baseline, values=tuple(values))
