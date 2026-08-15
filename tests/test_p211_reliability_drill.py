"""校验 P2-11 联合故障演练清单、执行边界和本地证据。"""

from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from scripts.run_p211_reliability_drill import (
    CommandResult,
    execute_drill,
    load_manifest,
)

ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / "tests" / "fixtures" / "reliability" / "p2-11-v1.json"
MANIFEST_SCHEMA_PATH = ROOT / "contracts" / "reliability" / "stage-2-drill.v1.schema.json"
EVIDENCE_SCHEMA_PATH = ROOT / "contracts" / "reliability" / "stage-2-drill-evidence.v1.schema.json"
EXPECTED_COMPONENTS = {
    "postgresql",
    "minio",
    "object_snapshot",
    "derived_index",
    "worker_lease",
    "api_instance",
    "sse_notification",
    "valkey",
    "outbox",
    "authorization",
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


def test_p211_manifest_and_evidence_schemas_are_valid() -> None:
    manifest_schema = _load_object(MANIFEST_SCHEMA_PATH)
    evidence_schema = _load_object(EVIDENCE_SCHEMA_PATH)

    Draft202012Validator.check_schema(manifest_schema)
    Draft202012Validator.check_schema(evidence_schema)
    Draft202012Validator(manifest_schema).validate(_load_object(MANIFEST_PATH))
    assert load_manifest(ROOT, MANIFEST_PATH)["drill_id"] == "p2-11-v1"


def test_p211_manifest_covers_frozen_components_and_unique_real_tests() -> None:
    manifest = load_manifest(ROOT, MANIFEST_PATH)
    scenarios = cast(list[dict[str, Any]], manifest["scenarios"])
    nodes = [cast(str, scenario["pytest_node"]) for scenario in scenarios]

    assert len(scenarios) == 12
    assert {scenario["component"] for scenario in scenarios} == EXPECTED_COMPONENTS
    assert len(nodes) == len(set(nodes))
    assert all((ROOT / node.split("::", maxsplit=1)[0]).is_file() for node in nodes)
    assert manifest["execution_policy"]["failure_mode"] == "fail_closed"


def test_p211_rejects_duplicate_test_nodes(tmp_path: Path) -> None:
    manifest = copy.deepcopy(_load_object(MANIFEST_PATH))
    manifest["scenarios"][1]["pytest_node"] = manifest["scenarios"][0]["pytest_node"]
    invalid_path = tmp_path / "invalid-p2-11.json"
    invalid_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="pytest_node 不能重复"):
        load_manifest(ROOT, invalid_path)


def test_p211_success_writes_schema_valid_minimal_evidence(tmp_path: Path) -> None:
    evidence_path = tmp_path / "evidence" / "p2-11-latest.json"
    doctor_calls = 0

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        nonlocal doctor_calls
        assert cwd == ROOT
        if command == ("./platform", "doctor"):
            doctor_calls += 1
            return CommandResult(0, 0.01)
        _write_junit(command)
        return CommandResult(0, 0.12)

    exit_code, evidence = execute_drill(
        ROOT,
        MANIFEST_PATH,
        evidence_path,
        runner=runner,
    )

    persisted = _load_object(evidence_path)
    schema = _load_object(EVIDENCE_SCHEMA_PATH)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(persisted)
    assert exit_code == 0
    assert doctor_calls == 2
    assert evidence["overall_status"] == "passed"
    assert persisted["summary"] == {
        "total": 12,
        "passed": 12,
        "failed": 0,
        "error": 0,
        "skipped": 0,
        "not_run": 0,
    }
    assert "stdout" not in evidence and "stderr" not in evidence


def test_p211_preflight_failure_skips_scenarios_but_still_checks_recovery(
    tmp_path: Path,
) -> None:
    evidence_path = tmp_path / "p2-11-failed.json"
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        calls.append(command)
        if len(calls) == 1:
            return CommandResult(1, 0.01, "command_failed")
        return CommandResult(0, 0.01)

    exit_code, evidence = execute_drill(
        ROOT,
        MANIFEST_PATH,
        evidence_path,
        runner=runner,
    )

    assert exit_code == 1
    assert calls == [("./platform", "doctor"), ("./platform", "doctor")]
    assert evidence["overall_status"] == "failed"
    assert evidence["summary"]["not_run"] == 12
    assert {item["reason_code"] for item in evidence["scenarios"]} == {"preflight_failed"}


def test_p211_scenario_or_recovery_failure_closes_entire_drill(tmp_path: Path) -> None:
    evidence_path = tmp_path / "p2-11-partial-failure.json"
    doctor_calls = 0
    failed_test = "test_valkey_outage_does_not_block_stream_fact_commit"

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        nonlocal doctor_calls
        if command == ("./platform", "doctor"):
            doctor_calls += 1
            if doctor_calls == 2:
                return CommandResult(1, 0.01, "command_failed")
            return CommandResult(0, 0.01)
        _write_junit(command, failed_test)
        return CommandResult(1, 0.12, "command_failed")

    exit_code, evidence = execute_drill(
        ROOT,
        MANIFEST_PATH,
        evidence_path,
        runner=runner,
    )

    assert exit_code == 1
    assert evidence["overall_status"] == "failed"
    assert evidence["summary"]["passed"] == 11
    assert evidence["summary"]["failed"] == 1
    assert evidence["checks"]["recovery"]["status"] == "failed"
    failed = next(item for item in evidence["scenarios"] if item["status"] == "failed")
    assert failed["reason_code"] == "pytest_failure"


def test_p211_missing_junit_is_recorded_as_execution_error(tmp_path: Path) -> None:
    evidence_path = tmp_path / "p2-11-no-junit.json"

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        if command == ("./platform", "doctor"):
            return CommandResult(0, 0.01)
        return CommandResult(2, 0.02, "command_failed")

    exit_code, evidence = execute_drill(
        ROOT,
        MANIFEST_PATH,
        evidence_path,
        runner=runner,
    )

    assert exit_code == 1
    assert evidence["summary"]["error"] == 12
    assert {item["reason_code"] for item in evidence["scenarios"]} == {"junit_not_created"}
