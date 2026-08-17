"""验证 P5-09 法规策略、法律保留与失败关闭契约。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from ai_platform_api.modules.lifecycle.application.compliance import (
    LifecycleComplianceValidationError,
    _validate_configuration,
)
from ai_platform_api.modules.lifecycle.domain.compliance import (
    COMPLIANCE_REASON_CODES,
    RETENTION_CLASSES,
    RegulatoryPolicyConfiguration,
)
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).parents[1]
CONTRACT_DIR = ROOT / "contracts" / "lifecycle"
SCHEMA_PATH = CONTRACT_DIR / "regulatory-compliance-baseline.v1.schema.json"
BASELINE_PATH = CONTRACT_DIR / "regulatory-compliance-baseline.v1.json"


def _load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def test_p509_baseline_matches_schema_and_domain_constants() -> None:
    schema = _load(SCHEMA_PATH)
    baseline = _load(BASELINE_PATH)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(baseline)

    assert tuple(baseline["retention_classes"]) == RETENTION_CLASSES
    assert frozenset(baseline["reason_codes"]) == COMPLIANCE_REASON_CODES
    assert baseline["rules"]["hold_scope_expands_read_access"] is False
    assert baseline["external_status"]["applicable_jurisdictions"] == "not_configured"


def test_p509_unconfigured_jurisdiction_cannot_smuggle_rules_or_review() -> None:
    invalid = RegulatoryPolicyConfiguration(
        jurisdiction_status="not_configured",
        jurisdiction_codes=("SYNTHETIC-P5-09",),
        retention_period_days={"minimum_records": 365},
        external_review_status="approved",
        external_review_digest="a" * 64,
    )

    with pytest.raises(LifecycleComplianceValidationError):
        _validate_configuration(invalid)


def test_p509_configured_policy_requires_complete_bounded_retention_rules() -> None:
    valid = RegulatoryPolicyConfiguration(
        jurisdiction_status="configured",
        jurisdiction_codes=("SYNTHETIC-P5-09",),
        retention_period_days={
            "stream_events": 1,
            "published_outbox": 30,
            "attempts_and_dead_letters": 90,
            "minimum_records": 365,
        },
        external_review_status="not_configured",
        external_review_digest=None,
    )
    _validate_configuration(valid)

    with pytest.raises(LifecycleComplianceValidationError):
        _validate_configuration(
            RegulatoryPolicyConfiguration(
                **{**valid.__dict__, "retention_period_days": {"minimum_records": 365}}
            )
        )
