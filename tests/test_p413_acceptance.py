"""校验 P4-13 联合验收清单、执行边界和本地最小证据。"""

from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

import scripts.run_p413_acceptance as acceptance
from scripts.run_p413_acceptance import (
    CommandResult,
    execute_acceptance,
    load_manifest,
)

ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / "tests" / "fixtures" / "tool-execution" / "p4-13-v1.json"
MANIFEST_SCHEMA_PATH = ROOT / "contracts" / "tool-execution" / "stage-4-acceptance.v1.schema.json"
EVIDENCE_SCHEMA_PATH = (
    ROOT / "contracts" / "tool-execution" / "stage-4-acceptance-evidence.v1.schema.json"
)


@pytest.fixture(autouse=True)
def _isolate_evidence_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例使用独立运行根，避免证据失效测试触碰正式目录。"""

    monkeypatch.setenv("AI_PLATFORM_ROOT", str(tmp_path))


def _load_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def _evidence_path(tmp_path: Path, name: str = "p4-13.json") -> Path:
    path = tmp_path / "evidence" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_junit(command: tuple[str, ...], failed_test: str | None = None) -> None:
    junit_argument = next(item for item in command if item.startswith("--junitxml="))
    junit_path = Path(junit_argument.split("=", maxsplit=1)[1])
    suite = ET.Element("testsuite")
    for node in (item for item in command if item.startswith("tests/")):
        test_name = node.split("::", maxsplit=1)[1]
        testcase = ET.SubElement(suite, "testcase", name=test_name, time="0.01")
        if test_name == failed_test:
            ET.SubElement(
                testcase,
                "failure",
                message="不得写入证据的合成敏感正文 synthetic-secret-value",
            )
    ET.ElementTree(suite).write(junit_path, encoding="utf-8", xml_declaration=True)


def _successful_runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
    del cwd
    if command == ("./platform", "doctor"):
        return CommandResult(0, 0.01)
    _write_junit(command)
    return CommandResult(0, 0.15)


def test_p413_manifest_and_evidence_schemas_are_valid() -> None:
    manifest_schema = _load_object(MANIFEST_SCHEMA_PATH)
    evidence_schema = _load_object(EVIDENCE_SCHEMA_PATH)

    Draft202012Validator.check_schema(manifest_schema)
    Draft202012Validator.check_schema(evidence_schema)
    Draft202012Validator(manifest_schema).validate(_load_object(MANIFEST_PATH))
    assert manifest_schema["properties"]["scenarios"]["maxItems"] == 15
    assert evidence_schema["properties"]["summary"]["properties"]["total"] == {"const": 15}
    assert set(evidence_schema["$defs"]["reason_code"]["enum"]) == {
        None,
        *acceptance.ALLOWED_REASON_CODES,
    }
    assert load_manifest(ROOT, MANIFEST_PATH)["acceptance_id"] == "p4-13-v1"


def test_p413_manifest_covers_all_invariants_gates_and_unique_tests() -> None:
    manifest = load_manifest(ROOT, MANIFEST_PATH)
    scenarios = cast(list[dict[str, Any]], manifest["scenarios"])
    nodes = [cast(str, scenario["pytest_node"]) for scenario in scenarios]

    assert len(scenarios) == 15
    assert set(manifest["required_invariants"]) == acceptance.EXPECTED_INVARIANTS
    assert set(manifest["required_gates"]) == acceptance.EXPECTED_GATES
    assert {scenario["capability"] for scenario in scenarios} == acceptance.EXPECTED_CAPABILITIES
    assert len(nodes) == len(set(nodes))
    assert all(scenario["expected_case_count"] == 1 for scenario in scenarios)
    assert manifest["execution_policy"]["failure_mode"] == "fail_closed"


@pytest.mark.parametrize(
    ("field_name", "unsafe_value"),
    [
        ("preflight_command", "./platform status"),
        ("recovery_command", "./platform status"),
        ("stop_scenarios_on_preflight_failure", False),
        ("always_run_recovery_check", False),
        ("failure_mode", "best_effort"),
        ("evidence_path", ".ai-platform/evidence/p4-13-other.json"),
    ],
)
def test_p413_rejects_relaxed_execution_policy(
    tmp_path: Path,
    field_name: str,
    unsafe_value: object,
) -> None:
    relaxed = copy.deepcopy(_load_object(MANIFEST_PATH))
    relaxed["execution_policy"][field_name] = unsafe_value
    relaxed_path = tmp_path / f"relaxed-{field_name}.json"
    relaxed_path.write_text(json.dumps(relaxed, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_manifest(ROOT, relaxed_path)


def test_p413_rejects_rebound_scenario_even_when_global_coverage_remains(
    tmp_path: Path,
) -> None:
    rebound = copy.deepcopy(_load_object(MANIFEST_PATH))
    rebound["scenarios"][0]["verified_gates"] = ["zero_duplicate_side_effect"]
    rebound["scenarios"][9]["verified_gates"] = ["unauthorized_tool_denial"]
    rebound_path = tmp_path / "rebound-p4-13.json"
    rebound_path.write_text(json.dumps(rebound, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="绑定不符合冻结要求"):
        load_manifest(ROOT, rebound_path)


def test_p413_platform_entry_freezes_manifest_and_evidence_paths() -> None:
    platform_script = (ROOT / "platform").read_text(encoding="utf-8")
    function_body = platform_script.split("accept_stage_4() {", maxsplit=1)[1].split(
        "\n}", maxsplit=1
    )[0]
    command_lines = [line.strip() for line in function_body.splitlines() if line.strip()]

    assert command_lines == [
        "check_runtime",
        "ensure_environment",
        "export_local_service_settings",
        "run_python scripts/run_p413_acceptance.py \\",
        '--manifest "$PROJECT_DIR/tests/fixtures/tool-execution/p4-13-v1.json" \\',
        '--evidence "$AI_PLATFORM_ROOT/evidence/p4-13-latest.json"',
    ]


def test_p413_rejects_noncanonical_manifest_and_outside_evidence_path(
    tmp_path: Path,
) -> None:
    replacement = tmp_path / "p4-13-v1.json"
    replacement.write_bytes(MANIFEST_PATH.read_bytes())
    valid_evidence = _evidence_path(tmp_path)
    valid_evidence.write_text("stale", encoding="utf-8")

    with pytest.raises(ValueError, match="仓库冻结清单"):
        execute_acceptance(ROOT, replacement, valid_evidence, runner=_successful_runner)
    assert valid_evidence.read_text(encoding="utf-8") == "stale"

    with pytest.raises(ValueError, match="当前运行证据目录"):
        execute_acceptance(
            ROOT,
            MANIFEST_PATH,
            tmp_path / "outside.json",
            runner=_successful_runner,
        )


def test_p413_success_writes_schema_valid_minimal_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = _evidence_path(tmp_path)
    monkeypatch.setattr(acceptance, "_repository_state", lambda _: ("a" * 40, False))

    exit_code, evidence = execute_acceptance(
        ROOT,
        MANIFEST_PATH,
        evidence_path,
        runner=_successful_runner,
    )

    assert exit_code == 0
    assert evidence["overall_status"] == "passed"
    assert evidence["summary"]["passed"] == 15
    assert evidence["summary"]["observed_case_total"] == 15
    assert set(evidence["summary"]["covered_invariants"]) == acceptance.EXPECTED_INVARIANTS
    assert set(evidence["summary"]["covered_gates"]) == acceptance.EXPECTED_GATES
    assert evidence["tool_drill_manifest_sha256"] == acceptance._sha256(
        ROOT / acceptance.TOOL_DRILL_REFERENCE
    )
    stored = _load_object(evidence_path)
    Draft202012Validator(
        _load_object(EVIDENCE_SCHEMA_PATH),
        format_checker=FormatChecker(),
    ).validate(stored)
    serialized = evidence_path.read_text(encoding="utf-8")
    assert "synthetic-secret-value" not in serialized
    assert "success_criteria" not in serialized


@pytest.mark.parametrize(
    ("mutation", "expected_path"),
    [
        (lambda document: document["summary"].update({"failed": 1}), "summary"),
        (
            lambda document: document["checks"]["recovery"].update(
                {"status": "failed", "exit_code": 1, "reason_code": "command_failed"}
            ),
            "checks",
        ),
        (
            lambda document: document["scenarios"][0].update(
                {"status": "skipped", "reason_code": "pytest_skipped"}
            ),
            "scenarios",
        ),
    ],
)
def test_p413_evidence_schema_rejects_contradictory_passed_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: Any,
    expected_path: str,
) -> None:
    monkeypatch.setattr(acceptance, "_repository_state", lambda _: ("b" * 40, False))
    _, evidence = execute_acceptance(
        ROOT,
        MANIFEST_PATH,
        _evidence_path(tmp_path),
        runner=_successful_runner,
    )
    mutation(evidence)

    with pytest.raises(ValidationError) as error:
        Draft202012Validator(_load_object(EVIDENCE_SCHEMA_PATH)).validate(evidence)
    assert expected_path in "/".join(str(item) for item in error.value.absolute_path)


def test_p413_unknown_repository_revision_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(acceptance, "_repository_state", lambda _: ("unknown", True))

    exit_code, evidence = execute_acceptance(
        ROOT,
        MANIFEST_PATH,
        _evidence_path(tmp_path),
        runner=_successful_runner,
    )

    assert exit_code == 1
    assert evidence["overall_status"] == "failed"
    assert evidence["summary"]["passed"] == 15


def test_p413_preflight_failure_skips_scenarios_but_runs_recovery(
    tmp_path: Path,
) -> None:
    doctor_calls = 0
    pytest_calls = 0

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        nonlocal doctor_calls, pytest_calls
        del cwd
        if command == ("./platform", "doctor"):
            doctor_calls += 1
            if doctor_calls == 1:
                return CommandResult(1, 0.01, "command_failed")
            return CommandResult(0, 0.01)
        pytest_calls += 1
        return CommandResult(0, 0.01)

    exit_code, evidence = execute_acceptance(
        ROOT,
        MANIFEST_PATH,
        _evidence_path(tmp_path),
        runner=runner,
    )

    assert exit_code == 1
    assert doctor_calls == 2
    assert pytest_calls == 0
    assert evidence["summary"]["not_run"] == 15
    assert evidence["checks"]["recovery"]["status"] == "passed"


@pytest.mark.parametrize("failure_target", ["scenario", "recovery"])
def test_p413_scenario_or_recovery_failure_closes_entire_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_target: str,
) -> None:
    doctor_calls = 0
    monkeypatch.setattr(acceptance, "_repository_state", lambda _: ("c" * 40, False))

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        nonlocal doctor_calls
        del cwd
        if command == ("./platform", "doctor"):
            doctor_calls += 1
            if failure_target == "recovery" and doctor_calls == 2:
                return CommandResult(1, 0.01, "command_failed")
            return CommandResult(0, 0.01)
        failed_test = (
            "test_concurrent_duplicate_delivery_has_one_adapter_call_owner"
            if failure_target == "scenario"
            else None
        )
        _write_junit(command, failed_test)
        return CommandResult(
            1 if failed_test else 0, 0.15, "command_failed" if failed_test else None
        )

    exit_code, evidence = execute_acceptance(
        ROOT,
        MANIFEST_PATH,
        _evidence_path(tmp_path),
        runner=runner,
    )

    assert exit_code == 1
    assert evidence["overall_status"] == "failed"
    assert doctor_calls == 2
    if failure_target == "scenario":
        assert evidence["summary"]["failed"] == 1
        assert "synthetic-secret-value" not in json.dumps(evidence, ensure_ascii=False)
    else:
        assert evidence["checks"]["recovery"]["status"] == "failed"


@pytest.mark.parametrize("fault", ["missing", "malformed", "extra", "parameterized"])
def test_p413_invalid_junit_fails_closed_and_still_runs_recovery(
    tmp_path: Path,
    fault: str,
) -> None:
    doctor_calls = 0

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        nonlocal doctor_calls
        del cwd
        if command == ("./platform", "doctor"):
            doctor_calls += 1
            return CommandResult(0, 0.01)
        junit_argument = next(item for item in command if item.startswith("--junitxml="))
        junit_path = Path(junit_argument.split("=", maxsplit=1)[1])
        if fault == "missing":
            return CommandResult(1, 0.01, "command_failed")
        if fault == "malformed":
            junit_path.write_text("<testsuite>", encoding="utf-8")
        else:
            _write_junit(command)
            tree = ET.parse(junit_path)
            if fault == "extra":
                ET.SubElement(tree.getroot(), "testcase", name="test_not_frozen", time="0.01")
            else:
                first = next(tree.getroot().iter("testcase"))
                first.set("name", f"{first.attrib['name']}[unexpected]")
            tree.write(junit_path, encoding="utf-8", xml_declaration=True)
        return CommandResult(1, 0.01, "command_failed")

    exit_code, evidence = execute_acceptance(
        ROOT,
        MANIFEST_PATH,
        _evidence_path(tmp_path),
        runner=runner,
    )

    assert exit_code == 1
    assert doctor_calls == 2
    assert evidence["summary"]["error"] == 15
    expected_reason = "junit_not_created" if fault == "missing" else "junit_invalid"
    assert {item["reason_code"] for item in evidence["scenarios"]} == {expected_reason}


@pytest.mark.parametrize("fault", ["runner_exception", "runner_invalid_result"])
def test_p413_runner_fault_is_minimized_and_recovery_still_runs(
    tmp_path: Path,
    fault: str,
) -> None:
    doctor_calls = 0

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        nonlocal doctor_calls
        del cwd
        if command == ("./platform", "doctor"):
            doctor_calls += 1
            if doctor_calls == 1:
                if fault == "runner_exception":
                    raise RuntimeError("synthetic-secret-value")
                return CommandResult(-1, float("nan"), "synthetic-secret-value")
            return CommandResult(0, 0.01)
        raise AssertionError("前置失败后不应执行场景")

    exit_code, evidence = execute_acceptance(
        ROOT,
        MANIFEST_PATH,
        _evidence_path(tmp_path),
        runner=runner,
    )

    assert exit_code == 1
    assert doctor_calls == 2
    assert evidence["checks"]["preflight"]["reason_code"] == fault
    assert "synthetic-secret-value" not in json.dumps(evidence, ensure_ascii=False)


def test_p413_input_drift_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_matches = acceptance._matches
    calls = 0

    def drifting_matches(path: Path, expected_sha256: str) -> bool:
        nonlocal calls
        result = original_matches(path, expected_sha256)
        if path.resolve() == MANIFEST_PATH.resolve():
            calls += 1
            return result and calls < 2
        return result

    monkeypatch.setattr(acceptance, "_matches", drifting_matches)
    exit_code, evidence = execute_acceptance(
        ROOT,
        MANIFEST_PATH,
        _evidence_path(tmp_path),
        runner=_successful_runner,
    )

    assert exit_code == 1
    assert evidence["checks"]["scenario_execution"]["reason_code"] == "manifest_changed"
