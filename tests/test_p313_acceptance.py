"""校验 P3-13 联合验收清单、执行边界和本地最小证据。"""

from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from scripts.run_p313_acceptance import (
    CommandResult,
    execute_acceptance,
    load_manifest,
)

ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / "tests" / "fixtures" / "agent-control" / "p3-13-v1.json"
MANIFEST_SCHEMA_PATH = ROOT / "contracts" / "agent-control" / "stage-3-acceptance.v1.schema.json"
EVIDENCE_SCHEMA_PATH = (
    ROOT / "contracts" / "agent-control" / "stage-3-acceptance-evidence.v1.schema.json"
)
EXPECTED_CAPABILITIES = {
    "personal_approval",
    "enterprise_approval",
    "evaluation_gate",
    "workspace_isolation",
    "release_immutability",
    "tool_boundary",
    "runtime_boundary",
    "control_plane_isolation",
    "run_binding",
    "inflight_completion",
    "rollout_rollback",
    "concurrent_promotion",
    "service_delivery",
    "console_workflow",
    "promotion_gate",
}


def _load_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def _write_junit(command: tuple[str, ...], failed_test: str | None = None) -> None:
    junit_argument = next(item for item in command if item.startswith("--junitxml="))
    junit_path = Path(junit_argument.split("=", maxsplit=1)[1])
    suite = ET.Element("testsuite")
    for node in (item for item in command if item.startswith("tests/")):
        test_name = node.split("::", maxsplit=1)[1]
        testcase = ET.SubElement(suite, "testcase", name=test_name, time="0.01")
        if test_name == failed_test:
            ET.SubElement(testcase, "failure", message="合成断言失败")
    ET.ElementTree(suite).write(junit_path, encoding="utf-8", xml_declaration=True)


def test_p313_manifest_and_evidence_schemas_are_valid() -> None:
    manifest_schema = _load_object(MANIFEST_SCHEMA_PATH)
    evidence_schema = _load_object(EVIDENCE_SCHEMA_PATH)

    Draft202012Validator.check_schema(manifest_schema)
    Draft202012Validator.check_schema(evidence_schema)
    Draft202012Validator(manifest_schema).validate(_load_object(MANIFEST_PATH))
    assert load_manifest(ROOT, MANIFEST_PATH)["acceptance_id"] == "p3-13-v1"


def test_p313_manifest_covers_all_invariants_capabilities_and_unique_tests() -> None:
    manifest = load_manifest(ROOT, MANIFEST_PATH)
    baseline = _load_object(ROOT / cast(str, manifest["baseline_ref"]))
    scenarios = cast(list[dict[str, Any]], manifest["scenarios"])
    nodes = [cast(str, scenario["pytest_node"]) for scenario in scenarios]

    assert len(scenarios) == 15
    assert {scenario["capability"] for scenario in scenarios} == EXPECTED_CAPABILITIES
    assert set(manifest["required_invariants"]) == {
        item["invariant_id"] for item in baseline["invariants"]
    }
    assert len(nodes) == len(set(nodes))
    assert manifest["execution_policy"]["failure_mode"] == "fail_closed"


def test_p313_rejects_duplicate_test_nodes(tmp_path: Path) -> None:
    manifest = copy.deepcopy(_load_object(MANIFEST_PATH))
    manifest["scenarios"][1]["pytest_node"] = manifest["scenarios"][0]["pytest_node"]
    invalid_path = tmp_path / "invalid-p3-13.json"
    invalid_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="pytest_node 不能重复"):
        load_manifest(ROOT, invalid_path)


def test_p313_success_writes_schema_valid_minimal_evidence(tmp_path: Path) -> None:
    evidence_path = tmp_path / "evidence" / "p3-13-latest.json"
    doctor_calls = 0

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        nonlocal doctor_calls
        assert cwd == ROOT
        if command == ("./platform", "doctor"):
            doctor_calls += 1
            return CommandResult(0, 0.01)
        _write_junit(command)
        return CommandResult(0, 0.15)

    exit_code, evidence = execute_acceptance(
        ROOT,
        MANIFEST_PATH,
        evidence_path,
        runner=runner,
    )

    persisted = _load_object(evidence_path)
    Draft202012Validator(
        _load_object(EVIDENCE_SCHEMA_PATH), format_checker=FormatChecker()
    ).validate(persisted)
    assert exit_code == 0
    assert doctor_calls == 2
    assert evidence["overall_status"] == "passed"
    assert persisted["summary"] == {
        "total": 15,
        "passed": 15,
        "failed": 0,
        "error": 0,
        "skipped": 0,
        "not_run": 0,
    }
    assert "stdout" not in evidence and "stderr" not in evidence


def test_p313_preflight_failure_skips_scenarios_but_checks_recovery(
    tmp_path: Path,
) -> None:
    evidence_path = tmp_path / "p3-13-failed.json"
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        calls.append(command)
        if len(calls) == 1:
            return CommandResult(1, 0.01, "command_failed")
        return CommandResult(0, 0.01)

    exit_code, evidence = execute_acceptance(
        ROOT,
        MANIFEST_PATH,
        evidence_path,
        runner=runner,
    )

    assert exit_code == 1
    assert calls == [("./platform", "doctor"), ("./platform", "doctor")]
    assert evidence["summary"]["not_run"] == 15
    assert evidence["checks"]["scenario_execution"]["reason_code"] == "preflight_failed"


def test_p313_scenario_or_recovery_failure_closes_entire_acceptance(
    tmp_path: Path,
) -> None:
    evidence_path = tmp_path / "p3-13-partial-failure.json"
    doctor_calls = 0
    failed_test = "test_same_generation_concurrent_promotions_have_one_winner"

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        nonlocal doctor_calls
        if command == ("./platform", "doctor"):
            doctor_calls += 1
            if doctor_calls == 2:
                return CommandResult(1, 0.01, "command_failed")
            return CommandResult(0, 0.01)
        _write_junit(command, failed_test)
        return CommandResult(1, 0.15, "command_failed")

    exit_code, evidence = execute_acceptance(
        ROOT,
        MANIFEST_PATH,
        evidence_path,
        runner=runner,
    )

    assert exit_code == 1
    assert evidence["summary"]["passed"] == 14
    assert evidence["summary"]["failed"] == 1
    assert evidence["checks"]["scenario_execution"]["status"] == "failed"
    assert evidence["checks"]["recovery"]["status"] == "failed"


def test_p313_missing_junit_is_recorded_as_execution_error(tmp_path: Path) -> None:
    evidence_path = tmp_path / "p3-13-no-junit.json"

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        if command == ("./platform", "doctor"):
            return CommandResult(0, 0.01)
        return CommandResult(2, 0.02, "command_failed")

    exit_code, evidence = execute_acceptance(
        ROOT,
        MANIFEST_PATH,
        evidence_path,
        runner=runner,
    )

    assert exit_code == 1
    assert evidence["summary"]["error"] == 15
    assert {item["reason_code"] for item in evidence["scenarios"]} == {"junit_not_created"}
