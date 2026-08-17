"""校验 P5-05 成本归因契约、整数账本、失败重试和账单对账。"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from ai_platform_api.modules.quality.application.cost_policy import (
    COST_COLLECTOR_VERSION,
    COST_COMPONENTS,
    DIFFERENCE_REASON_CODES,
    SOURCE_COMPONENTS,
)
from ai_platform_api.modules.quality.application.costs import build_cost_attribution_report
from ai_platform_api.modules.quality.application.errors import QualityValidationError
from ai_platform_api.modules.quality.domain.costs import (
    CostAttributionReport,
    CostAttributionTarget,
    CostCollectionBatch,
    CostReleaseContext,
    CostSupplierStatement,
    CostUsageObservation,
)
from jsonschema import Draft202012Validator, FormatChecker

from tests.support.p505_costs import as_authorized_real, synthetic_observations

ROOT = Path(__file__).parents[1]
CONTRACT_DIR = ROOT / "contracts" / "quality"
SCHEMA_PATH = CONTRACT_DIR / "quality-cost-attribution-baseline.v1.schema.json"
BASELINE_PATH = CONTRACT_DIR / "quality-cost-attribution-baseline.v1.json"
WORKSPACE_ID = UUID("55000000-0000-4000-8000-000000000571")
SERVICE_ID = UUID("55000000-0000-4000-8000-000000000572")
RELEASE_ID = UUID("55000000-0000-4000-8000-000000000573")
RUNTIME_ID = UUID("55000000-0000-4000-8000-000000000574")
ACTOR_ID = UUID("55000000-0000-4000-8000-000000000575")
WINDOW_END = datetime(2026, 8, 17, 8, 0, tzinfo=UTC)


def load_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def _target(*, price_version: str = "synthetic-price-v1") -> CostAttributionTarget:
    return CostAttributionTarget(
        SERVICE_ID,
        RELEASE_ID,
        "a" * 64,
        price_version,
        "b" * 64,
        "CNY",
        "cn-east.synthetic",
        WINDOW_END - timedelta(days=1),
        WINDOW_END,
    )


def _release() -> CostReleaseContext:
    return CostReleaseContext(SERVICE_ID, RELEASE_ID, RUNTIME_ID, "a" * 64)


def _statement(
    amount_minor: int,
    *,
    reasons: tuple[str, ...] = (),
    currency: str = "CNY",
) -> CostSupplierStatement:
    return CostSupplierStatement(
        "provided",
        "c" * 64,
        "d" * 64,
        amount_minor,
        currency,
        reasons,
        {"marker": "synthetic-p505-statement-body"},
    )


def _report(
    *,
    observations: tuple[CostUsageObservation, ...] | None = None,
    statement: CostSupplierStatement | None = None,
    target: CostAttributionTarget | None = None,
) -> CostAttributionReport:
    selected_target = target or _target(price_version="reviewed-price-v1")
    selected = observations or as_authorized_real(synthetic_observations())
    return build_cost_attribution_report(
        workspace_id=WORKSPACE_ID,
        target=selected_target,
        batch=CostCollectionBatch(
            WORKSPACE_ID,
            selected_target,
            "authorized_real",
            selected,
            statement or CostSupplierStatement("not_run", None, None, None, None, (), {}),
        ),
        collector_version=COST_COLLECTOR_VERSION,
        release=_release(),
        actor_id=ACTOR_ID,
        completed_at=WINDOW_END,
    )


def test_p505_baseline_matches_versioned_schema() -> None:
    schema = load_object(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        load_object(BASELINE_PATH)
    )


def test_p505_contract_matches_frozen_components_and_amount_policy() -> None:
    baseline = load_object(BASELINE_PATH)

    assert tuple(baseline["components"]) == COST_COMPONENTS
    assert baseline["source_components"] == SOURCE_COMPONENTS
    assert set(baseline["reconciliation_reasons"]) == DIFFERENCE_REASON_CODES
    assert baseline["amount_policy"] == {
        "currency": "ISO_4217_uppercase",
        "amount_unit": "integer_minimum_currency_unit",
        "rounding": "ceil_per_ledger_entry",
        "failed_and_retried_attempts": "included",
        "evidence_body_persistence": False,
    }


def test_p505_synthetic_prices_attribute_all_components_without_real_claim() -> None:
    target = _target()
    report = build_cost_attribution_report(
        workspace_id=WORKSPACE_ID,
        target=target,
        batch=CostCollectionBatch(
            WORKSPACE_ID,
            target,
            "synthetic",
            synthetic_observations(),
            CostSupplierStatement("not_configured", None, None, None, None, (), {}),
        ),
        collector_version=COST_COLLECTOR_VERSION,
        release=_release(),
        actor_id=ACTOR_ID,
        completed_at=WINDOW_END,
    )

    assert report.window.attribution_status == "passed"
    assert report.window.price_verification_status == "not_configured"
    assert report.window.reconciliation_status == "not_configured"
    assert report.window.failed_entry_count == 1
    assert report.window.retry_entry_count == 1
    assert tuple(item.component for item in report.lines) == COST_COMPONENTS
    assert all(item.entry_count >= 1 for item in report.lines)
    assert "real_price_not_configured" in report.window.reason_codes


def test_p505_integer_rounding_and_provider_reported_amount_are_explicit() -> None:
    base = as_authorized_real(synthetic_observations())[0]
    observations = (
        replace(base, quantity=1, unit_size=1_000, unit_price_minor=1),
        replace(
            base,
            source_record_id=UUID("55000000-0000-4000-8000-000000000576"),
            meter_key="model.reported",
            reported_amount_minor=7,
        ),
    )
    preliminary = _report(observations=observations)
    report = _report(
        observations=observations,
        statement=_statement(preliminary.window.recognized_amount_minor),
    )

    assert report.entries[0].estimated_amount_minor == 1
    assert report.entries[0].amount_source == "contract_rate"
    assert report.entries[1].reported_amount_minor == 7
    assert report.entries[1].recognized_amount_minor == 7
    assert report.entries[1].amount_source == "provider_reported"
    assert report.window.reconciliation_status == "matched"


def test_p505_supplier_difference_requires_an_allowed_explanation() -> None:
    preliminary = _report()
    explained = _report(
        statement=_statement(
            preliminary.window.recognized_amount_minor + 3,
            reasons=("provider_rounding",),
        )
    )
    unexplained = _report(statement=_statement(preliminary.window.recognized_amount_minor + 3))

    assert explained.window.reconciliation_status == "explained"
    assert explained.window.reconciliation_difference_minor == 3
    assert "provider_rounding" in explained.window.reason_codes
    assert unexplained.window.reconciliation_status == "failed"
    assert "supplier_statement_unexplained_difference" in unexplained.window.reason_codes


def test_p505_identity_drift_and_currency_mismatch_fail_closed() -> None:
    target = _target(price_version="reviewed-price-v1")
    drifted = replace(target, price_version="reviewed-price-v2")
    observations = tuple(
        replace(item, price_version="reviewed-price-v2")
        for item in as_authorized_real(synthetic_observations())
    )
    report = build_cost_attribution_report(
        workspace_id=WORKSPACE_ID,
        target=target,
        batch=CostCollectionBatch(
            WORKSPACE_ID,
            drifted,
            "authorized_real",
            observations,
            CostSupplierStatement("not_configured", None, None, None, None, (), {}),
        ),
        collector_version=COST_COLLECTOR_VERSION,
        release=_release(),
        actor_id=ACTOR_ID,
        completed_at=WINDOW_END,
    )

    assert report.window.attribution_status == "failed"
    assert report.window.price_verification_status == "failed"
    assert {"collector_identity_drift", "price_version_mismatch"}.issubset(
        report.window.reason_codes
    )

    currency_failed = _report(statement=_statement(10, currency="USD"))
    assert currency_failed.window.reconciliation_status == "failed"
    assert "supplier_statement_currency_mismatch" in currency_failed.window.reason_codes


def test_p505_duplicate_meter_and_invalid_retry_are_rejected() -> None:
    base = as_authorized_real(synthetic_observations())[0]
    with pytest.raises(QualityValidationError):
        _report(observations=(base, base))
    with pytest.raises(QualityValidationError):
        _report(observations=(replace(base, attempt_no=2, is_retry=False),))
    with pytest.raises(QualityValidationError):
        _report(observations=(replace(base, unit_price_minor=True),))
