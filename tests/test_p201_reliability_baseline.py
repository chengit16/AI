"""校验 P2-01 可靠性基线与全合成故障场景。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator

from scripts.check_repository_policy import SECRET_PATTERNS

ROOT = Path(__file__).parents[1]
BASELINE_SCHEMA_PATH = ROOT / "contracts" / "reliability" / "reliability-baseline.v1.schema.json"
BASELINE_PATH = ROOT / "contracts" / "reliability" / "reliability-baseline.v1.json"
SCENARIO_SCHEMA_PATH = ROOT / "contracts" / "reliability" / "failure-scenarios.v1.schema.json"
SCENARIO_PATH = ROOT / "tests" / "fixtures" / "reliability" / "p2-01-v1.json"
ERROR_CATALOG_PATH = ROOT / "contracts" / "errors" / "catalog.v1.json"

EXPECTED_CATEGORIES = {
    "timeout",
    "duplicate",
    "out_of_order",
    "partial_failure",
    "dependency_unavailable",
    "recovery_integrity",
    "authorization_propagation",
    "observability_leak",
}
EXPECTED_METRICS = {
    "ingestion_recovery_duration_seconds": ("p99", "less_than_or_equal", 180),
    "duplicate_side_effect_ratio": ("ratio", "equal", 0),
    "index_reference_inconsistency_count": ("count", "equal", 0),
    "sse_cross_instance_recovery_seconds": ("p99", "less_than_or_equal", 5),
    "authorization_revocation_propagation_seconds": ("p99", "less_than_or_equal", 5),
    "outbox_oldest_pending_age_seconds": ("max", "less_than_or_equal", 300),
    "high_risk_audit_coverage_ratio": ("ratio", "greater_than_or_equal", 1),
    "workspace_delete_completion_seconds": ("p99", "less_than_or_equal", 3600),
    "recovery_validation_failure_count": ("count", "equal", 0),
    "critical_trace_coverage_ratio": ("ratio", "greater_than_or_equal", 0.99),
}
EXPECTED_RETENTION_SECONDS = {
    "sse_events_and_cursors": 24 * 60 * 60,
    "job_attempts": 90 * 24 * 60 * 60,
    "outbox_dead_letters": 90 * 24 * 60 * 60,
    "delivered_outbox_records": 30 * 24 * 60 * 60,
    "audit_and_usage_records": 365 * 24 * 60 * 60,
    "minimal_deletion_proofs": 365 * 24 * 60 * 60,
}


def load_object(path: Path) -> dict[str, Any]:
    """读取对象型 JSON，避免测试静默接受错误的顶层结构。"""

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def test_p201_documents_match_versioned_json_schemas() -> None:
    baseline_schema = load_object(BASELINE_SCHEMA_PATH)
    scenario_schema = load_object(SCENARIO_SCHEMA_PATH)

    Draft202012Validator.check_schema(baseline_schema)
    Draft202012Validator.check_schema(scenario_schema)
    Draft202012Validator(baseline_schema).validate(load_object(BASELINE_PATH))
    Draft202012Validator(scenario_schema).validate(load_object(SCENARIO_PATH))


def test_p201_freezes_metric_thresholds_and_fact_sources() -> None:
    baseline = load_object(BASELINE_PATH)
    metrics = {metric["metric_id"]: metric for metric in baseline["metrics"]}

    assert baseline["baseline_id"] == "p2-01-v1"
    assert baseline["status"] == "frozen"
    assert set(metrics) == set(EXPECTED_METRICS)
    for metric_id, expected in EXPECTED_METRICS.items():
        metric = metrics[metric_id]
        assert (metric["aggregation"], metric["operator"], metric["threshold"]) == expected
        assert metric["fact_sources"], metric_id
        assert metric["measurement_window_seconds"] >= 60, metric_id
        assert metric["failure_condition"], metric_id


def test_p201_percentiles_require_twenty_samples_and_keep_failures() -> None:
    baseline = load_object(BASELINE_PATH)
    policy = baseline["measurement_policy"]

    assert policy["percentile_method"] == "nearest_rank"
    assert policy["percentile_minimum_samples"] >= 20
    assert policy["insufficient_sample_status"] == "not_run"
    assert policy["failed_samples_included"] is True
    for metric in baseline["metrics"]:
        assert metric["insufficient_sample_status"] == "not_run"
        if metric["aggregation"] == "p99":
            assert metric["minimum_sample_count"] >= 20, metric["metric_id"]


def test_p201_freezes_retention_and_revocation_propagation() -> None:
    baseline = load_object(BASELINE_PATH)
    retention = {
        policy["resource"]: policy["duration_seconds"] for policy in baseline["retention_policies"]
    }
    propagation = baseline["authorization_propagation"]

    assert retention == EXPECTED_RETENTION_SECONDS
    assert propagation["revocation_deadline_seconds"] == 5
    assert propagation["minimum_sample_count"] >= 20
    assert set(propagation["surfaces"]) == {"menu", "api", "retrieval", "field_projection"}
    assert propagation["notification_failure_mode"] == "fail_closed"
    assert propagation["stale_cache_behavior"] == "reject_and_revalidate"


def test_p201_scenarios_cover_all_failure_classes_with_unique_synthetic_cases() -> None:
    fixture = load_object(SCENARIO_PATH)
    cases = cast(list[dict[str, Any]], fixture["cases"])
    case_ids = [case["case_id"] for case in cases]

    assert fixture["dataset_version"] == "p2-01-v1"
    assert fixture["synthetic"] is True
    assert set(fixture["categories"]) == EXPECTED_CATEGORIES
    assert len(cases) >= 12
    assert len(case_ids) == len(set(case_ids))
    assert {case["category"] for case in cases} == EXPECTED_CATEGORIES
    assert all(case["synthetic"] is True for case in cases)


def test_p201_scenarios_reference_known_invariants_and_error_codes() -> None:
    baseline = load_object(BASELINE_PATH)
    fixture = load_object(SCENARIO_PATH)
    catalog = load_object(ERROR_CATALOG_PATH)
    invariant_ids = {item["invariant_id"] for item in baseline["invariants"]}
    errors = {item["code"]: item for item in catalog["errors"]}

    for case in fixture["cases"]:
        expected = case["expected"]
        assert set(expected["verified_invariants"]).issubset(invariant_ids), case["case_id"]
        error_code = expected["error_code"]
        if error_code is not None:
            assert error_code in errors, case["case_id"]
            assert expected["retryable"] == errors[error_code]["retryable"], case["case_id"]


def test_p201_failure_expectations_preserve_security_and_recovery_limits() -> None:
    fixture = load_object(SCENARIO_PATH)
    cases = cast(list[dict[str, Any]], fixture["cases"])

    for case in cases:
        expected = case["expected"]
        assert expected["duplicate_side_effects"] == 0, case["case_id"]
        assert expected["cross_workspace_exposure"] is False, case["case_id"]
        assert expected["evidence_sources"], case["case_id"]
        if case["category"] in {"authorization_propagation", "observability_leak"}:
            assert expected["authorization_behavior"] == "fail_closed", case["case_id"]

    maximum_by_case = {
        case["case_id"]: case["expected"]["maximum_recovery_seconds"] for case in cases
    }
    assert maximum_by_case["p201-api-instance-interrupt-005"] <= 5
    assert maximum_by_case["p201-sse-notification-reorder-006"] <= 5
    assert maximum_by_case["p201-authorization-cache-stale-007"] <= 5
    assert maximum_by_case["p201-outbox-backlog-timeout-009"] <= 300
    assert maximum_by_case["p201-lifecycle-partial-delete-010"] <= 3600


def test_p201_fixture_contains_no_real_credentials() -> None:
    fixture_text = SCENARIO_PATH.read_text(encoding="utf-8")

    assert "PRIVATE KEY" not in fixture_text
    assert not any(pattern.search(fixture_text) for pattern in SECRET_PATTERNS)
