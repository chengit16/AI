"""校验 P4-12 工具联合故障演练清单、执行边界和本地最小证据。"""

from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

import scripts.run_p412_tool_drill as tool_drill
from scripts.run_p412_tool_drill import (
    CommandResult,
    execute_drill,
    load_manifest,
)

ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / "tests" / "fixtures" / "tool-execution" / "p4-12-v1.json"
MANIFEST_SCHEMA_PATH = ROOT / "contracts" / "tool-execution" / "stage-4-tool-drill.v1.schema.json"
EVIDENCE_SCHEMA_PATH = (
    ROOT / "contracts" / "tool-execution" / "stage-4-tool-drill-evidence.v1.schema.json"
)
EXPECTED_CAPABILITIES = {
    "duplicate_delivery",
    "adapter_timeout",
    "response_loss",
    "credential_revocation",
    "approval_expiry",
    "cancellation_race",
    "worker_restart",
    "workspace_isolation",
}
EXPECTED_SAMPLES = {"success", "failure", "timeout", "cancellation", "recovery", "denial"}


@pytest.fixture(autouse=True)
def _isolate_evidence_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例使用独立运行根，避免证据失效测试触碰仓库正式目录。"""

    monkeypatch.setenv("AI_PLATFORM_ROOT", str(tmp_path))


def _evidence_path(tmp_path: Path, name: str) -> Path:
    path = tmp_path / "evidence" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def _write_junit(
    command: tuple[str, ...],
    failed_test: str | None = None,
) -> None:
    junit_argument = next(item for item in command if item.startswith("--junitxml="))
    junit_path = Path(junit_argument.split("=", maxsplit=1)[1])
    suite = ET.Element("testsuite")
    for node in (item for item in command if item.startswith("tests/")):
        test_name = node.split("::", maxsplit=1)[1]
        case_names = (
            [f"{test_name}[rotate]", f"{test_name}[revoke]"]
            if test_name == "test_rotation_and_revocation_fail_closed_after_binding"
            else [test_name]
        )
        for index, case_name in enumerate(case_names):
            testcase = ET.SubElement(suite, "testcase", name=case_name, time="0.01")
            if test_name == failed_test and index == 0:
                ET.SubElement(
                    testcase,
                    "failure",
                    message="不得写入证据的合成敏感正文 synthetic-secret-value",
                )
    ET.ElementTree(suite).write(junit_path, encoding="utf-8", xml_declaration=True)


def test_p412_manifest_and_evidence_schemas_are_valid() -> None:
    manifest_schema = _load_object(MANIFEST_SCHEMA_PATH)
    evidence_schema = _load_object(EVIDENCE_SCHEMA_PATH)

    Draft202012Validator.check_schema(manifest_schema)
    Draft202012Validator.check_schema(evidence_schema)
    Draft202012Validator(manifest_schema).validate(_load_object(MANIFEST_PATH))
    assert manifest_schema["properties"]["scenarios"]["maxItems"] == 8
    assert evidence_schema["properties"]["scenarios"]["maxItems"] == 8
    assert evidence_schema["properties"]["summary"]["properties"]["total"] == {"const": 8}
    assert evidence_schema["properties"]["summary"]["properties"]["expected_case_total"] == {
        "const": 9
    }
    assert set(evidence_schema["$defs"]["reason_code"]["enum"]) == {
        None,
        *tool_drill.ALLOWED_REASON_CODES,
    }
    assert load_manifest(ROOT, MANIFEST_PATH)["drill_id"] == "p4-12-v1"


def test_p412_manifest_covers_faults_samples_invariants_and_unique_tests() -> None:
    manifest = load_manifest(ROOT, MANIFEST_PATH)
    scenarios = cast(list[dict[str, Any]], manifest["scenarios"])
    nodes = [cast(str, scenario["pytest_node"]) for scenario in scenarios]

    assert len(scenarios) == 8
    assert {scenario["capability"] for scenario in scenarios} == EXPECTED_CAPABILITIES
    assert set(manifest["required_sample_classes"]) == EXPECTED_SAMPLES
    assert len(nodes) == len(set(nodes))
    assert all(node.startswith("tests/integration/") for node in nodes)
    assert manifest["execution_policy"]["failure_mode"] == "fail_closed"


@pytest.mark.parametrize(
    ("field_name", "unsafe_value"),
    [
        ("preflight_command", "./platform status"),
        ("recovery_command", "./platform status"),
        ("stop_scenarios_on_preflight_failure", False),
        ("always_run_recovery_check", False),
        ("failure_mode", "best_effort"),
        ("evidence_path", ".ai-platform/evidence/p4-12-other.json"),
    ],
)
def test_p412_rejects_relaxed_execution_policy(
    tmp_path: Path,
    field_name: str,
    unsafe_value: object,
) -> None:
    """执行策略任一字段放宽都不能继续进入正式演练。"""

    relaxed = copy.deepcopy(_load_object(MANIFEST_PATH))
    relaxed["execution_policy"][field_name] = unsafe_value
    relaxed_path = tmp_path / f"relaxed-{field_name}-p4-12.json"
    relaxed_path.write_text(json.dumps(relaxed, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_manifest(ROOT, relaxed_path)


def test_p412_platform_entry_freezes_manifest_and_evidence_paths() -> None:
    """正式 Shell 入口必须固定运行准备、冻结清单和当前运行根下的证据路径。"""

    platform_script = (ROOT / "platform").read_text(encoding="utf-8")
    function_body = platform_script.split("accept_stage_4_tools() {", maxsplit=1)[1].split(
        "\n}", maxsplit=1
    )[0]
    command_lines = [line.strip() for line in function_body.splitlines() if line.strip()]

    assert command_lines == [
        "check_runtime",
        "ensure_environment",
        "export_local_service_settings",
        "run_python scripts/run_p412_tool_drill.py \\",
        '--manifest "$PROJECT_DIR/tests/fixtures/tool-execution/p4-12-v1.json" \\',
        '--evidence "$AI_PLATFORM_ROOT/evidence/p4-12-latest.json"',
    ]


def test_p412_rejects_duplicate_test_nodes_or_missing_sample_coverage(tmp_path: Path) -> None:
    duplicate = copy.deepcopy(_load_object(MANIFEST_PATH))
    duplicate["scenarios"][1]["pytest_node"] = duplicate["scenarios"][0]["pytest_node"]
    duplicate_path = tmp_path / "duplicate-p4-12.json"
    duplicate_path.write_text(json.dumps(duplicate, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="pytest_node 不能重复"):
        load_manifest(ROOT, duplicate_path)

    missing_sample = copy.deepcopy(_load_object(MANIFEST_PATH))
    missing_sample["scenarios"][5]["sample_classes"] = ["failure"]
    missing_sample_path = tmp_path / "missing-sample-p4-12.json"
    missing_sample_path.write_text(
        json.dumps(missing_sample, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="未完整覆盖必需样本类型"):
        load_manifest(ROOT, missing_sample_path)


def test_p412_rejects_duplicate_junit_function_names(tmp_path: Path) -> None:
    """不同文件的同名函数不能共享 JUnit 结果。"""

    duplicate_name = copy.deepcopy(_load_object(MANIFEST_PATH))
    first_node = cast(str, duplicate_name["scenarios"][0]["pytest_node"])
    first_name = first_node.split("::", maxsplit=1)[1]
    second_file = cast(str, duplicate_name["scenarios"][1]["pytest_node"]).split("::", maxsplit=1)[
        0
    ]
    duplicate_name["scenarios"][1]["pytest_node"] = f"{second_file}::{first_name}"
    duplicate_name_path = tmp_path / "duplicate-name-p4-12.json"
    duplicate_name_path.write_text(
        json.dumps(duplicate_name, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="测试函数名不能重复"):
        load_manifest(ROOT, duplicate_name_path)


def test_p412_rejects_rebound_scenario_test_node(tmp_path: Path) -> None:
    """能力名不变时也不能把真实故障节点替换为宽松单元测试。"""

    rebound = copy.deepcopy(_load_object(MANIFEST_PATH))
    rebound["scenarios"][0]["pytest_node"] = (
        "tests/unit/test_p409_tool_worker.py::"
        "test_unknown_outcome_never_enters_automatic_finish_path"
    )
    rebound_path = tmp_path / "rebound-p4-12.json"
    rebound_path.write_text(json.dumps(rebound, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="场景、来源与测试节点不符合冻结绑定"):
        load_manifest(ROOT, rebound_path)


def test_p412_rejects_rebound_scenario_sample_classes(tmp_path: Path) -> None:
    """总样本并集不变时，单场景也不能虚增未验证的样本类别。"""

    rebound = copy.deepcopy(_load_object(MANIFEST_PATH))
    rebound["scenarios"][1]["sample_classes"].append("denial")
    rebound_path = tmp_path / "rebound-sample-classes-p4-12.json"
    rebound_path.write_text(json.dumps(rebound, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="场景样本类别不符合冻结绑定"):
        load_manifest(ROOT, rebound_path)


def test_p412_rejects_rebound_scenario_invariants(tmp_path: Path) -> None:
    """全局不变量并集不变时，单场景也不能减少自身验证责任。"""

    rebound = copy.deepcopy(_load_object(MANIFEST_PATH))
    rebound["scenarios"][0]["verified_invariants"] = ["p4_idempotency_before_side_effect"]
    rebound_path = tmp_path / "rebound-invariants-p4-12.json"
    rebound_path.write_text(json.dumps(rebound, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="场景不变量不符合冻结绑定"):
        load_manifest(ROOT, rebound_path)


def test_p412_rejects_noncanonical_manifest_for_evidence(tmp_path: Path) -> None:
    """正式证据不能把替代清单伪装成仓库冻结引用。"""

    alternate_manifest = tmp_path / "p4-12-v1.json"
    alternate_manifest.write_text(MANIFEST_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    evidence_path = _evidence_path(tmp_path, "p4-12-evidence.json")
    evidence_path.write_text("stale-passed-evidence", encoding="utf-8")

    with pytest.raises(ValueError, match="只能使用仓库冻结清单"):
        execute_drill(
            ROOT,
            alternate_manifest,
            evidence_path,
        )
    assert evidence_path.read_text(encoding="utf-8") == "stale-passed-evidence"


def test_p412_rejects_evidence_paths_that_overlap_inputs_or_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """证据失效只能删除运行目录中的证据，不能删除仓库输入或源码。"""

    project_root = tmp_path / "project"
    manifest_path = project_root / tool_drill.MANIFEST_REFERENCE
    baseline_path = (
        project_root / "contracts" / "tool-execution" / "tool-execution-baseline.v1.json"
    )
    source_path = project_root / "platform"
    for path, content in (
        (manifest_path, "synthetic-manifest"),
        (baseline_path, "synthetic-baseline"),
        (source_path, "synthetic-platform"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    monkeypatch.setenv("AI_PLATFORM_ROOT", str(project_root / ".ai-platform"))
    for evidence_path in (manifest_path, baseline_path, source_path):
        original = evidence_path.read_text(encoding="utf-8")
        with pytest.raises(ValueError, match="证据路径"):
            execute_drill(project_root, manifest_path, evidence_path)
        assert evidence_path.read_text(encoding="utf-8") == original


def test_p412_configured_evidence_root_keeps_stale_invalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自定义运行根目录内的旧证据仍应在清单校验前失效。"""

    project_root = tmp_path / "project"
    manifest_path = project_root / tool_drill.MANIFEST_REFERENCE
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("{}", encoding="utf-8")
    configured_root = project_root / "runtime"
    evidence_path = configured_root / "evidence" / "p4-12-latest.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text("stale-passed-evidence", encoding="utf-8")
    monkeypatch.setenv("AI_PLATFORM_ROOT", str(configured_root))

    def reject_manifest(project_root: Path, manifest_path: Path) -> dict[str, Any]:
        del project_root, manifest_path
        raise ValueError("synthetic manifest validation failure")

    monkeypatch.setattr(tool_drill, "load_manifest", reject_manifest)
    with pytest.raises(ValueError, match="synthetic manifest validation failure"):
        execute_drill(project_root, manifest_path, evidence_path)
    assert not evidence_path.exists()


def test_p412_rejects_evidence_outside_configured_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式证据参数不能把仓库外任意文件当作可失效的旧证据。"""

    configured_root = tmp_path / "runtime"
    monkeypatch.setenv("AI_PLATFORM_ROOT", str(configured_root))
    outside_path = tmp_path / "unrelated.json"
    outside_path.write_text("unrelated-user-content", encoding="utf-8")

    with pytest.raises(ValueError, match="必须位于当前运行证据目录"):
        execute_drill(ROOT, MANIFEST_PATH, outside_path)
    assert outside_path.read_text(encoding="utf-8") == "unrelated-user-content"


def test_p412_canonical_run_invalidates_stale_evidence_before_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """冻结清单校验异常时不得继续暴露上次通过证据。"""

    evidence_path = _evidence_path(tmp_path, "p4-12-evidence.json")
    evidence_path.write_text("stale-passed-evidence", encoding="utf-8")

    def reject_manifest(project_root: Path, manifest_path: Path) -> dict[str, Any]:
        del project_root, manifest_path
        raise ValueError("synthetic manifest validation failure")

    monkeypatch.setattr(tool_drill, "load_manifest", reject_manifest)
    with pytest.raises(ValueError, match="synthetic manifest validation failure"):
        execute_drill(ROOT, MANIFEST_PATH, evidence_path)
    assert not evidence_path.exists()


@pytest.mark.parametrize("failure_call", ["preflight", "scenario", "recovery"])
def test_p412_runner_exception_fails_closed_and_still_runs_recovery(
    tmp_path: Path,
    failure_call: str,
) -> None:
    """Runner 任一阶段异常都必须形成稳定失败，且不能跳过恢复诊断。"""

    evidence_path = _evidence_path(tmp_path, "p4-12-evidence.json")
    evidence_path.write_text("stale-passed-evidence", encoding="utf-8")
    doctor_calls = 0

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        nonlocal doctor_calls
        del cwd
        if command == ("./platform", "doctor"):
            doctor_calls += 1
            current_call = "preflight" if doctor_calls == 1 else "recovery"
        else:
            current_call = "scenario"
        if current_call == failure_call:
            raise RuntimeError("synthetic runner failure")
        if current_call == "scenario":
            _write_junit(command)
        return CommandResult(0, 0.01)

    exit_code, evidence = execute_drill(ROOT, MANIFEST_PATH, evidence_path, runner=runner)

    assert exit_code == 1
    assert doctor_calls == 2
    assert evidence["overall_status"] == "failed"
    assert "stale-passed-evidence" not in evidence_path.read_text(encoding="utf-8")
    if failure_call == "preflight":
        assert evidence["checks"]["preflight"]["reason_code"] == "runner_exception"
        assert evidence["summary"]["not_run"] == 8
    elif failure_call == "scenario":
        assert evidence["checks"]["scenario_execution"]["reason_code"] == "runner_exception"
        assert evidence["summary"]["error"] == 8
    else:
        assert evidence["checks"]["recovery"]["reason_code"] == "runner_exception"
        assert evidence["summary"]["passed"] == 8


def test_p412_invalid_runner_result_is_normalized_without_sensitive_reason(
    tmp_path: Path,
) -> None:
    """Runner 非法退出码、耗时或原因不得原样进入最小证据。"""

    invalid_results = (
        CommandResult(0, 0.01, "synthetic_secret_value"),
        CommandResult(1, float("nan"), "command_failed"),
        CommandResult(256, 0.01, "command_failed"),
    )
    for index, invalid_result in enumerate(invalid_results):
        evidence_path = _evidence_path(tmp_path, f"p4-12-invalid-runner-{index}.json")

        def runner(
            command: tuple[str, ...],
            cwd: Path,
            result: CommandResult = invalid_result,
        ) -> CommandResult:
            del cwd
            if command == ("./platform", "doctor"):
                return CommandResult(0, 0.01)
            return result

        exit_code, evidence = execute_drill(ROOT, MANIFEST_PATH, evidence_path, runner=runner)

        assert exit_code == 1
        assert evidence["checks"]["scenario_execution"]["reason_code"] == ("runner_invalid_result")
        assert "synthetic_secret_value" not in evidence_path.read_text(encoding="utf-8")


def test_p412_manifest_change_during_run_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """场景执行期间发生的清单漂移不能继承校验前的通过结论。"""

    evidence_path = _evidence_path(tmp_path, "p4-12-manifest-changed.json")
    match_results = iter((True, False))

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        del cwd
        if command != ("./platform", "doctor"):
            _write_junit(command)
        return CommandResult(0, 0.01)

    monkeypatch.setattr(
        tool_drill,
        "_manifest_matches",
        lambda path, expected_sha256: next(match_results),
    )
    exit_code, evidence = execute_drill(
        ROOT,
        MANIFEST_PATH,
        evidence_path,
        runner=runner,
    )

    assert exit_code == 1
    assert evidence["overall_status"] == "failed"
    assert evidence["summary"]["passed"] == 8
    assert evidence["checks"]["scenario_execution"] == {
        "status": "failed",
        "exit_code": 1,
        "duration_seconds": 0.01,
        "reason_code": "manifest_changed",
    }
    assert evidence["manifest_sha256"] == tool_drill._manifest_sha256(MANIFEST_PATH)


def test_p412_manifest_change_during_atomic_write_removes_passed_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """清单在最终写入窗口漂移时不能留下短暂生成的通过证据。"""

    evidence_path = _evidence_path(tmp_path, "p4-12-write-race.json")
    match_results = iter((True, True, False))

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        del cwd
        if command != ("./platform", "doctor"):
            _write_junit(command)
        return CommandResult(0, 0.01)

    monkeypatch.setattr(
        tool_drill,
        "_manifest_matches",
        lambda path, expected_sha256: next(match_results),
    )
    with pytest.raises(ValueError, match="证据原子写入期间发生变化"):
        execute_drill(
            ROOT,
            MANIFEST_PATH,
            evidence_path,
            runner=runner,
        )
    assert not evidence_path.exists()


def test_p412_rejects_lowered_parameterized_case_count(tmp_path: Path) -> None:
    lowered = copy.deepcopy(_load_object(MANIFEST_PATH))
    lowered["scenarios"][3]["expected_case_count"] = 1
    lowered_path = tmp_path / "lowered-case-count-p4-12.json"
    lowered_path.write_text(json.dumps(lowered, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="参数化测试数量不符合冻结要求"):
        load_manifest(ROOT, lowered_path)


def test_p412_success_writes_schema_valid_minimal_evidence(tmp_path: Path) -> None:
    evidence_path = _evidence_path(tmp_path, "p4-12-latest.json")
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
        "total": 8,
        "expected_case_total": 9,
        "observed_case_total": 9,
        "passed": 8,
        "failed": 0,
        "error": 0,
        "skipped": 0,
        "not_run": 0,
        "sample_counts": {
            "cancellation": 1,
            "denial": 3,
            "failure": 5,
            "recovery": 3,
            "success": 1,
            "timeout": 2,
        },
    }
    serialized = json.dumps(persisted, ensure_ascii=False, sort_keys=True)
    assert "synthetic-secret-value" not in serialized
    for forbidden_key in {
        "arguments",
        "parameters",
        "payload",
        "result_body",
        "credential_ref",
        "credential_value",
        "stdout",
        "stderr",
        "failure_message",
    }:
        assert forbidden_key not in persisted
        assert f'"{forbidden_key}"' not in serialized


def test_p412_evidence_schema_rejects_contradictory_passed_facts(tmp_path: Path) -> None:
    """通过态不能与失败诊断、场景归属漂移或错误汇总并存。"""

    evidence_path = _evidence_path(tmp_path, "p4-12-schema-contradiction.json")

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        del cwd
        if command != ("./platform", "doctor"):
            _write_junit(command)
        return CommandResult(0, 0.01)

    exit_code, evidence = execute_drill(
        ROOT,
        MANIFEST_PATH,
        evidence_path,
        runner=runner,
    )
    assert exit_code == 0
    validator = Draft202012Validator(
        _load_object(EVIDENCE_SCHEMA_PATH),
        format_checker=FormatChecker(),
    )

    contradictions: list[dict[str, Any]] = []
    failed_check = copy.deepcopy(evidence)
    failed_check["checks"]["preflight"] = {
        "status": "failed",
        "exit_code": 1,
        "duration_seconds": 0.01,
        "reason_code": "command_failed",
    }
    contradictions.append(failed_check)

    missing_case = copy.deepcopy(evidence)
    missing_case["scenarios"][0]["observed_case_count"] = 0
    contradictions.append(missing_case)

    failed_summary = copy.deepcopy(evidence)
    failed_summary["summary"]["passed"] = 7
    failed_summary["summary"]["failed"] = 1
    contradictions.append(failed_summary)

    duplicate_scenario = copy.deepcopy(evidence)
    duplicate_scenario["scenarios"][1] = copy.deepcopy(duplicate_scenario["scenarios"][0])
    contradictions.append(duplicate_scenario)

    rebound_scenario = copy.deepcopy(evidence)
    rebound_scenario["scenarios"][0]["pytest_node"] = rebound_scenario["scenarios"][1][
        "pytest_node"
    ]
    contradictions.append(rebound_scenario)

    rebound_invariants = copy.deepcopy(evidence)
    rebound_invariants["scenarios"][0]["verified_invariants"] = [
        "p4_idempotency_before_side_effect"
    ]
    contradictions.append(rebound_invariants)

    incorrect_sample_counts = copy.deepcopy(evidence)
    incorrect_sample_counts["summary"]["sample_counts"]["failure"] = 4
    contradictions.append(incorrect_sample_counts)

    for contradiction in contradictions:
        with pytest.raises(ValidationError):
            validator.validate(contradiction)


def test_p412_unknown_repository_revision_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = _evidence_path(tmp_path, "p4-12-unknown-revision.json")

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        del cwd
        if command != ("./platform", "doctor"):
            _write_junit(command)
        return CommandResult(0, 0.01)

    monkeypatch.setattr(tool_drill, "_repository_state", lambda project_root: ("unknown", True))
    exit_code, evidence = execute_drill(
        ROOT,
        MANIFEST_PATH,
        evidence_path,
        runner=runner,
    )

    assert exit_code == 1
    assert evidence["repository_revision"] == "unknown"
    assert evidence["summary"]["passed"] == 8
    assert evidence["overall_status"] == "failed"


def test_p412_missing_or_rebound_parameterized_case_fails_closed(tmp_path: Path) -> None:
    for mutation in ("missing", "rebound"):
        evidence_path = _evidence_path(tmp_path, f"p4-12-{mutation}-parameter.json")

        def runner(
            command: tuple[str, ...],
            cwd: Path,
            current_mutation: str = mutation,
        ) -> CommandResult:
            del cwd
            if command == ("./platform", "doctor"):
                return CommandResult(0, 0.01)
            _write_junit(command)
            junit_argument = next(item for item in command if item.startswith("--junitxml="))
            junit_path = Path(junit_argument.split("=", maxsplit=1)[1])
            tree = ET.parse(junit_path)
            suite = tree.getroot()
            revoke = next(
                testcase
                for testcase in suite
                if testcase.attrib.get("name", "").endswith("[revoke]")
            )
            if current_mutation == "missing":
                suite.remove(revoke)
            else:
                revoke.set(
                    "name",
                    revoke.attrib["name"].removesuffix("[revoke]") + "[replacement]",
                )
            tree.write(junit_path, encoding="utf-8", xml_declaration=True)
            return CommandResult(0, 0.01)

        exit_code, evidence = execute_drill(
            ROOT,
            MANIFEST_PATH,
            evidence_path,
            runner=runner,
        )

        assert exit_code == 1
        credential = next(
            item for item in evidence["scenarios"] if item["capability"] == "credential_revocation"
        )
        assert credential["status"] == "error"
        assert credential["expected_case_count"] == 2
        assert credential["observed_case_count"] == (1 if mutation == "missing" else 2)
        assert credential["reason_code"] == (
            "junit_case_count_mismatch" if mutation == "missing" else "junit_case_identity_mismatch"
        )


def test_p412_preflight_failure_skips_scenarios_but_runs_recovery(tmp_path: Path) -> None:
    evidence_path = _evidence_path(tmp_path, "p4-12-preflight-failed.json")
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        del cwd
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
    assert evidence["summary"]["not_run"] == 8
    assert {item["reason_code"] for item in evidence["scenarios"]} == {"preflight_failed"}


def test_p412_scenario_or_recovery_failure_closes_entire_drill(tmp_path: Path) -> None:
    evidence_path = _evidence_path(tmp_path, "p4-12-partial-failure.json")
    doctor_calls = 0
    failed_test = "test_rotation_and_revocation_fail_closed_after_binding"

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        nonlocal doctor_calls
        del cwd
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
    assert evidence["summary"]["passed"] == 7
    assert evidence["summary"]["failed"] == 1
    assert evidence["checks"]["recovery"]["status"] == "failed"
    failed = next(item for item in evidence["scenarios"] if item["status"] == "failed")
    assert failed["reason_code"] == "pytest_failure"
    assert "synthetic-secret-value" not in evidence_path.read_text(encoding="utf-8")


def test_p412_missing_junit_is_recorded_as_execution_error(tmp_path: Path) -> None:
    evidence_path = _evidence_path(tmp_path, "p4-12-no-junit.json")

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        del cwd
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
    assert evidence["summary"]["error"] == 8
    assert {item["reason_code"] for item in evidence["scenarios"]} == {"junit_not_created"}


@pytest.mark.parametrize("junit_fault", ["malformed", "duplicate", "unexpected", "unreadable"])
def test_p412_invalid_junit_fails_closed_and_still_runs_recovery(
    tmp_path: Path,
    junit_fault: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = _evidence_path(tmp_path, f"p4-12-{junit_fault}.json")
    doctor_calls = 0

    def runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        nonlocal doctor_calls
        del cwd
        if command == ("./platform", "doctor"):
            doctor_calls += 1
            return CommandResult(0, 0.01)
        junit_argument = next(item for item in command if item.startswith("--junitxml="))
        junit_path = Path(junit_argument.split("=", maxsplit=1)[1])
        if junit_fault == "malformed":
            junit_path.write_text("<testsuite>", encoding="utf-8")
        elif junit_fault == "duplicate":
            suite = ET.Element("testsuite")
            for _ in range(2):
                ET.SubElement(suite, "testcase", name="duplicated_case", time="0.01")
            ET.ElementTree(suite).write(junit_path, encoding="utf-8", xml_declaration=True)
        elif junit_fault == "unexpected":
            _write_junit(command)
            tree = ET.parse(junit_path)
            ET.SubElement(
                tree.getroot(),
                "testcase",
                name="test_not_in_frozen_manifest",
                time="0.01",
            )
            tree.write(junit_path, encoding="utf-8", xml_declaration=True)
        else:
            junit_path.write_text("<testsuite />", encoding="utf-8")

            def reject_read(path: Path, scenarios: list[dict[str, Any]]) -> list[dict[str, object]]:
                del path, scenarios
                raise OSError("synthetic unreadable junit")

            monkeypatch.setattr(tool_drill, "parse_junit_results", reject_read)
        return CommandResult(1, 0.02, "command_failed")

    exit_code, evidence = execute_drill(
        ROOT,
        MANIFEST_PATH,
        evidence_path,
        runner=runner,
    )

    assert exit_code == 1
    assert doctor_calls == 2
    assert evidence["overall_status"] == "failed"
    assert evidence["summary"]["error"] == 8
    assert {item["reason_code"] for item in evidence["scenarios"]} == {"junit_invalid"}
