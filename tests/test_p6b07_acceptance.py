"""校验阶段 B 联合验收清单、执行策略和最小证据边界。"""

from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

import scripts.run_p6b07_acceptance as acceptance
from scripts.run_p6b07_acceptance import CommandResult, execute_acceptance, load_manifest

ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / "tests/fixtures/e2e/p6b07-v1.json"
MANIFEST_SCHEMA_PATH = ROOT / "contracts/acceptance/stage-6-b-acceptance.v1.schema.json"
EVIDENCE_SCHEMA_PATH = ROOT / "contracts/acceptance/stage-6-b-acceptance-evidence.v1.schema.json"


def _load_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def _write_junit(command: tuple[str, ...]) -> None:
    target = next(
        item.split("=", maxsplit=1)[1] for item in command if item.startswith("--junitxml=")
    )
    suite = ET.Element("testsuite")
    for node in (item for item in command if item.startswith("tests/")):
        ET.SubElement(suite, "testcase", name=node.split("::", maxsplit=1)[1], time="0.01")
    ET.ElementTree(suite).write(target, encoding="utf-8", xml_declaration=True)


def test_p6b07_manifest_and_evidence_schemas_are_valid() -> None:
    manifest_schema = _load_object(MANIFEST_SCHEMA_PATH)
    evidence_schema = _load_object(EVIDENCE_SCHEMA_PATH)
    Draft202012Validator.check_schema(manifest_schema)
    Draft202012Validator.check_schema(evidence_schema)
    Draft202012Validator(manifest_schema).validate(_load_object(MANIFEST_PATH))
    assert load_manifest(ROOT, MANIFEST_PATH)["acceptance_id"] == "p6b07-v1"


def test_p6b07_manifest_covers_capabilities_and_unique_real_tests() -> None:
    manifest = load_manifest(ROOT, MANIFEST_PATH)
    scenarios = cast(list[dict[str, Any]], manifest["scenarios"])
    assert len(scenarios) == 12
    assert len({item["scenario_id"] for item in scenarios}) == 12
    assert len({item["pytest_node"] for item in scenarios}) == 12
    assert manifest["manual_acceptance"]["browser_viewports"] == ["1440x900", "390x844"]


@pytest.mark.parametrize(
    ("field_name", "unsafe_value"),
    [
        ("preflight_command", "./platform status"),
        ("failure_mode", "best_effort"),
        ("stop_scenarios_on_preflight_failure", False),
    ],
)
def test_p6b07_rejects_relaxed_execution_policy(
    tmp_path: Path, field_name: str, unsafe_value: object
) -> None:
    manifest = copy.deepcopy(_load_object(MANIFEST_PATH))
    manifest["execution_policy"][field_name] = unsafe_value
    path = tmp_path / "invalid-p6b07.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValidationError):
        acceptance._validate_document(manifest, MANIFEST_SCHEMA_PATH)


def test_p6b07_success_writes_schema_valid_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_path = tmp_path / "evidence/p6b07-latest.json"
    monkeypatch.setattr(acceptance, "_repository_state", lambda _: ("a" * 40, False))

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        assert cwd == ROOT
        if command == ("./platform", "doctor"):
            return CommandResult(0, 0.01)
        _write_junit(command)
        return CommandResult(0, 0.1)

    code, evidence = execute_acceptance(ROOT, MANIFEST_PATH, evidence_path, runner=runner)
    assert code == 0
    assert evidence["overall_status"] == "passed"
    assert evidence["summary"] == {
        "total": 12,
        "expected_case_total": 12,
        "observed_case_total": 12,
        "passed": 12,
        "failed": 0,
        "error": 0,
        "skipped": 0,
        "not_run": 0,
    }
    Draft202012Validator(
        _load_object(EVIDENCE_SCHEMA_PATH), format_checker=FormatChecker()
    ).validate(_load_object(evidence_path))


def test_p6b07_preflight_failure_is_fail_closed(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        del cwd
        calls.append(command)
        return (
            CommandResult(1, 0.01, "command_failed") if len(calls) == 1 else CommandResult(0, 0.01)
        )

    code, evidence = execute_acceptance(
        ROOT, MANIFEST_PATH, tmp_path / "evidence/p6b07.json", runner=runner
    )
    assert code == 1
    assert calls == [("./platform", "doctor"), ("./platform", "doctor")]
    assert evidence["summary"]["not_run"] == 12
    assert evidence["checks"]["scenario_execution"]["reason_code"] == "preflight_failed"
