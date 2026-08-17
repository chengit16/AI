"""验证 P5-11 服务拆分与 Go 演进审计的失败关闭边界。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from scripts.service_evolution_audit import (
    EVIDENCE_SCHEMA,
    GO_TRIGGER_IDS,
    PROFILE_SCHEMA,
    ROOT,
    SERVICE_TRIGGER_IDS,
    ServiceEvolutionAuditError,
    build_evidence,
    load_profile,
    validate_profile,
    write_evidence,
)

PROFILE_PATH = ROOT / "contracts/architecture/service-evolution-audit.v1.json"


def _load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def _trigger(document: dict[str, Any], group: str, trigger_id: str) -> dict[str, Any]:
    triggers = cast(list[dict[str, Any]], document[group])
    return next(item for item in triggers if item["trigger_id"] == trigger_id)


def test_p511_profile_and_evidence_schemas_are_valid() -> None:
    profile_schema = _load(PROFILE_SCHEMA)
    evidence_schema = _load(EVIDENCE_SCHEMA)
    profile = load_profile(PROFILE_PATH)
    for schema in (profile_schema, evidence_schema):
        Draft202012Validator.check_schema(schema)
    Draft202012Validator(
        profile_schema,
        format_checker=FormatChecker(),
    ).validate(profile)
    validate_profile(profile)
    Draft202012Validator(
        evidence_schema,
        format_checker=FormatChecker(),
    ).validate(build_evidence(profile))


def test_p511_all_design_triggers_are_explicit_and_unknowns_do_not_authorize_split() -> None:
    profile = load_profile(PROFILE_PATH)
    service_ids = tuple(item["trigger_id"] for item in profile["service_split_triggers"])
    go_ids = tuple(item["trigger_id"] for item in profile["go_evolution_triggers"])
    assert service_ids == SERVICE_TRIGGER_IDS
    assert go_ids == GO_TRIGGER_IDS
    assert profile["decision"] == "retain_modular_monolith"
    assert {item["status"] for item in profile["service_split_triggers"]} <= {
        "not_run",
        "not_configured",
    }
    assert {item["status"] for item in profile["go_evolution_triggers"]} <= {
        "not_run",
        "not_configured",
    }


def test_p511_triggered_claim_requires_evidence() -> None:
    profile = copy.deepcopy(load_profile(PROFILE_PATH))
    trigger = _trigger(profile, "service_split_triggers", "measured_monolith_bottleneck")
    trigger["status"] = "triggered"
    trigger["reason_code"] = "verified_trigger"
    trigger["evidence_ref"] = None
    with pytest.raises(ServiceEvolutionAuditError, match="必须引用可复核证据"):
        validate_profile(profile)


def test_p511_unknown_external_inputs_cannot_be_relabelled_as_triggered() -> None:
    profile = copy.deepcopy(load_profile(PROFILE_PATH))
    trigger = _trigger(
        profile,
        "go_evolution_triggers",
        "sse_connection_threshold_or_python_instability",
    )
    trigger["status"] = "triggered"
    trigger["reason_code"] = "verified_trigger"
    trigger["evidence_ref"] = "benchmarks/sse-5000.json"
    with pytest.raises(ServiceEvolutionAuditError, match="容量认证未通过"):
        validate_profile(profile)


def test_p511_go_start_requires_service_trigger_benchmark_and_operations() -> None:
    profile = copy.deepcopy(load_profile(PROFILE_PATH))
    profile["decision"] = "start_go_evolution"
    profile["decision_reason"] = "verified_go_trigger"
    _trigger(profile, "service_split_triggers", "measured_monolith_bottleneck").update(
        status="triggered",
        reason_code="verified_trigger",
        evidence_ref="benchmarks/attributed-gateway.json",
    )
    _trigger(profile, "go_evolution_triggers", "gateway_proxy_primary_bottleneck").update(
        status="triggered",
        reason_code="verified_trigger",
        evidence_ref="benchmarks/attributed-gateway.json",
    )
    external = cast(dict[str, str], profile["external_status"])
    external["production_load_profile"] = "configured"
    with pytest.raises(ServiceEvolutionAuditError, match="可归因基准报告"):
        validate_profile(profile)
    profile["benchmark_report_status"] = "passed"
    validate_profile(profile)


def test_p511_runtime_audit_rejects_premature_go_directory(tmp_path: Path) -> None:
    profile = load_profile(PROFILE_PATH)
    forbidden = tmp_path / "services" / "edge-gateway"
    forbidden.mkdir(parents=True)
    with pytest.raises(ServiceEvolutionAuditError, match="未授权的 Go 服务目录"):
        validate_profile(profile, tmp_path)


def test_p511_evidence_is_low_sensitivity_and_platform_entry_is_frozen(tmp_path: Path) -> None:
    profile = load_profile(PROFILE_PATH)
    evidence = build_evidence(profile)
    evidence_path = tmp_path / "p5-11-latest.json"
    write_evidence(evidence_path, evidence)
    persisted = _load(evidence_path)
    assert persisted["overall_status"] == "passed"
    assert persisted["decision"] == "retain_modular_monolith"
    assert persisted["sensitive_detail_in_evidence"] is False
    assert "service_split_triggers" not in persisted
    platform_script = (ROOT / "platform").read_text(encoding="utf-8")
    assert "accept-stage-5-service-evolution" in platform_script
    assert "scripts/service_evolution_audit.py" in platform_script
    assert '--evidence "$AI_PLATFORM_ROOT/evidence/p5-11-latest.json"' in platform_script
