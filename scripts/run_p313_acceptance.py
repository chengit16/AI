"""执行阶段 3 联合验收并生成可校验的本地最小证据。"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "agent-control" / "p3-13-v1.json"
MANIFEST_REFERENCE = "tests/fixtures/agent-control/p3-13-v1.json"
EXPECTED_CAPABILITIES = frozenset(
    {
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
)


@dataclass(frozen=True)
class CommandResult:
    """保存命令状态和耗时，避免把测试输出或业务正文写入证据。"""

    exit_code: int
    duration_seconds: float
    reason_code: str | None = None


CommandRunner = Callable[[tuple[str, ...], Path], CommandResult]


def _load_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def _validate_document(document: dict[str, Any], schema_path: Path) -> None:
    schema = _load_object(schema_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(document)


def _known_test_functions(path: Path) -> frozenset[str]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return frozenset(
        node.name
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def _validate_scenario_references(
    project_root: Path,
    scenarios: list[dict[str, Any]],
    source_cases: dict[str, dict[str, Any]],
    invariant_ids: set[str],
) -> None:
    """保证每个测试节点真实存在，且声明的不变量能追溯到冻结场景。"""

    for scenario in scenarios:
        scenario_id = cast(str, scenario["scenario_id"])
        source_refs = cast(list[str], scenario["source_case_refs"])
        if not set(source_refs).issubset(source_cases):
            raise ValueError(f"联合验收引用未知 P3-01 场景: {scenario_id}")
        source_invariants = {
            invariant
            for source_ref in source_refs
            for invariant in source_cases[source_ref]["expected"]["verified_invariants"]
        }
        verified = set(cast(list[str], scenario["verified_invariants"]))
        if not verified.issubset(invariant_ids) or not verified.issubset(source_invariants):
            raise ValueError(f"联合验收不变量无法追溯到冻结场景: {scenario_id}")

        test_file, test_name = cast(str, scenario["pytest_node"]).split("::", maxsplit=1)
        test_path = project_root / test_file
        if not test_path.is_file() or test_name not in _known_test_functions(test_path):
            raise ValueError(f"联合验收测试节点不存在: {scenario['pytest_node']}")


def _validate_manifest_references(project_root: Path, manifest: dict[str, Any]) -> None:
    baseline = _load_object(project_root / cast(str, manifest["baseline_ref"]))
    source = _load_object(project_root / cast(str, manifest["source_scenarios_ref"]))
    invariant_ids = {item["invariant_id"] for item in baseline["invariants"]}
    source_cases = {item["case_id"]: item for item in source["cases"]}
    scenarios = cast(list[dict[str, Any]], manifest["scenarios"])
    scenario_ids = [cast(str, scenario["scenario_id"]) for scenario in scenarios]
    pytest_nodes = [cast(str, scenario["pytest_node"]) for scenario in scenarios]

    # 1. 清单必须完整覆盖冻结基线和本阶段能力，不能通过删减场景降低关闭门槛。
    if set(cast(list[str], manifest["required_invariants"])) != invariant_ids:
        raise ValueError("联合验收 required_invariants 必须完整覆盖阶段 3 基线")
    if frozenset(cast(list[str], manifest["required_capabilities"])) != EXPECTED_CAPABILITIES:
        raise ValueError("联合验收 required_capabilities 必须完整覆盖冻结能力")
    if {scenario["capability"] for scenario in scenarios} != EXPECTED_CAPABILITIES:
        raise ValueError("联合验收场景未完整覆盖全部必需能力")

    # 2. 重复测试不能冒充独立证据，所有基线不变量都必须由场景实际声明覆盖。
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError("联合验收 scenario_id 不能重复")
    if len(pytest_nodes) != len(set(pytest_nodes)):
        raise ValueError("联合验收 pytest_node 不能重复")
    covered = {
        invariant
        for scenario in scenarios
        for invariant in cast(list[str], scenario["verified_invariants"])
    }
    if covered != invariant_ids:
        raise ValueError("联合验收场景未完整覆盖阶段 3 不变量")
    _validate_scenario_references(project_root, scenarios, source_cases, invariant_ids)


def load_manifest(project_root: Path, manifest_path: Path) -> dict[str, Any]:
    """校验联合验收清单结构、能力覆盖与测试追溯关系。"""

    manifest = _load_object(manifest_path)
    _validate_document(
        manifest,
        project_root / "contracts" / "agent-control" / "stage-3-acceptance.v1.schema.json",
    )
    _validate_manifest_references(project_root, manifest)
    return manifest


def run_command(command: tuple[str, ...], cwd: Path) -> CommandResult:
    """以前台输出执行固定命令，只返回无敏感内容的稳定状态。"""

    print(f"[P3-13] 执行: {' '.join(command)}", flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=cwd, check=False)
    except OSError:
        return CommandResult(127, round(time.monotonic() - started, 6), "command_unavailable")
    duration = round(time.monotonic() - started, 6)
    exit_code = (
        completed.returncode
        if completed.returncode >= 0
        else min(128 + abs(completed.returncode), 255)
    )
    return CommandResult(exit_code, duration, None if exit_code == 0 else "command_failed")


def _repository_state(project_root: Path) -> tuple[str, bool]:
    try:
        revision = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ("git", "status", "--porcelain"),
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return "unknown", True
    return revision, bool(status.strip())


def _check_evidence(result: CommandResult) -> dict[str, object]:
    return {
        "status": "passed" if result.exit_code == 0 else "failed",
        "exit_code": result.exit_code,
        "duration_seconds": result.duration_seconds,
        "reason_code": result.reason_code,
    }


def _scenario_result(
    scenario: dict[str, Any],
    status: str,
    duration_seconds: float,
    reason_code: str | None,
) -> dict[str, object]:
    return {
        "scenario_id": scenario["scenario_id"],
        "pytest_node": scenario["pytest_node"],
        "status": status,
        "duration_seconds": round(max(duration_seconds, 0), 6),
        "reason_code": reason_code,
    }


def _uniform_results(
    scenarios: list[dict[str, Any]], status: str, reason_code: str
) -> list[dict[str, object]]:
    return [_scenario_result(scenario, status, 0, reason_code) for scenario in scenarios]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


def _parse_duration(raw: str | None) -> float:
    try:
        value = float(raw or "0")
    except ValueError:
        return 0
    return max(value, 0) if math.isfinite(value) else 0


def parse_junit_results(
    junit_path: Path, scenarios: list[dict[str, Any]]
) -> list[dict[str, object]]:
    """按唯一测试函数名关联 JUnit，并以稳定原因码替代错误正文。"""

    testcases: dict[str, ET.Element] = {}
    for element in ET.parse(junit_path).getroot().iter():
        if _local_name(element.tag) != "testcase":
            continue
        name = element.attrib.get("name", "")
        if name in testcases:
            raise ValueError(f"JUnit 包含重复测试结果: {name}")
        testcases[name] = element

    results: list[dict[str, object]] = []
    for scenario in scenarios:
        test_name = cast(str, scenario["pytest_node"]).split("::", maxsplit=1)[1]
        testcase = testcases.get(test_name)
        if testcase is None:
            results.append(_scenario_result(scenario, "error", 0, "junit_case_missing"))
            continue
        child_kinds = {_local_name(child.tag) for child in testcase}
        if "error" in child_kinds:
            status, reason_code = "error", "pytest_error"
        elif "failure" in child_kinds:
            status, reason_code = "failed", "pytest_failure"
        elif "skipped" in child_kinds:
            status, reason_code = "skipped", "pytest_skipped"
        else:
            status, reason_code = "passed", None
        results.append(
            _scenario_result(
                scenario,
                status,
                _parse_duration(testcase.attrib.get("time")),
                reason_code,
            )
        )
    return results


def _run_scenarios(
    project_root: Path,
    scenarios: list[dict[str, Any]],
    junit_path: Path,
    runner: CommandRunner,
) -> tuple[CommandResult, list[dict[str, object]]]:
    test_nodes = tuple(cast(str, scenario["pytest_node"]) for scenario in scenarios)
    command = (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        f"--junitxml={junit_path}",
        *test_nodes,
    )
    result = runner(command, project_root)
    if not junit_path.is_file():
        return result, _uniform_results(scenarios, "error", "junit_not_created")
    try:
        return result, parse_junit_results(junit_path, scenarios)
    except (ET.ParseError, ValueError):
        return result, _uniform_results(scenarios, "error", "junit_invalid")


def _summary(results: list[dict[str, object]]) -> dict[str, int]:
    counts = Counter(cast(str, result["status"]) for result in results)
    return {
        "total": len(results),
        "passed": counts["passed"],
        "failed": counts["failed"],
        "error": counts["error"],
        "skipped": counts["skipped"],
        "not_run": counts["not_run"],
    }


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _write_evidence(project_root: Path, evidence_path: Path, evidence: dict[str, Any]) -> None:
    _validate_document(
        evidence,
        project_root / "contracts" / "agent-control" / "stage-3-acceptance-evidence.v1.schema.json",
    )
    temporary_path = evidence_path.with_name(f".{evidence_path.name}.{uuid4().hex}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(evidence_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _build_evidence(
    manifest_path: Path,
    manifest: dict[str, Any],
    revision: str,
    repository_dirty: bool,
    started_at: datetime,
    duration_seconds: float,
    preflight: CommandResult,
    scenario_execution: CommandResult,
    recovery: CommandResult,
    scenario_results: list[dict[str, object]],
) -> dict[str, Any]:
    summary = _summary(scenario_results)
    passed = (
        preflight.exit_code == 0
        and scenario_execution.exit_code == 0
        and recovery.exit_code == 0
        and summary["passed"] == summary["total"]
    )
    return {
        "schema_version": 1,
        "evidence_version": "p3-13-evidence-v1",
        "acceptance_id": manifest["acceptance_id"],
        "run_id": str(uuid4()),
        "synthetic": True,
        "manifest_ref": MANIFEST_REFERENCE,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "repository_revision": revision,
        "repository_dirty": repository_dirty,
        "started_at": _timestamp(started_at),
        "finished_at": _timestamp(datetime.now(UTC)),
        "duration_seconds": round(duration_seconds, 6),
        "overall_status": "passed" if passed else "failed",
        "checks": {
            "preflight": _check_evidence(preflight),
            "scenario_execution": _check_evidence(scenario_execution),
            "recovery": _check_evidence(recovery),
        },
        "scenarios": scenario_results,
        "summary": summary,
    }


def execute_acceptance(
    project_root: Path,
    manifest_path: Path,
    evidence_path: Path,
    *,
    runner: CommandRunner = run_command,
) -> tuple[int, dict[str, Any]]:
    """执行诊断、固定场景和恢复检查；任一失败都会关闭阶段验收。"""

    started_at = datetime.now(UTC)
    started_clock = time.monotonic()
    manifest = load_manifest(project_root, manifest_path)
    scenarios = cast(list[dict[str, Any]], manifest["scenarios"])
    revision, repository_dirty = _repository_state(project_root)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. 前置诊断失败时跳过业务场景，防止异常基础环境产生误导性结果。
    preflight = runner(("./platform", "doctor"), project_root)
    with tempfile.TemporaryDirectory(prefix="p3-13-", dir=evidence_path.parent) as temporary:
        junit_path = Path(temporary) / "pytest.xml"
        if preflight.exit_code == 0:
            scenario_execution, scenario_results = _run_scenarios(
                project_root, scenarios, junit_path, runner
            )
        else:
            scenario_execution = CommandResult(1, 0, "preflight_failed")
            scenario_results = _uniform_results(scenarios, "not_run", "preflight_failed")

        # 2. 场景失败也必须执行恢复诊断，证据只在 Schema 校验后原子替换。
        recovery = runner(("./platform", "doctor"), project_root)
        evidence = _build_evidence(
            manifest_path,
            manifest,
            revision,
            repository_dirty,
            started_at,
            time.monotonic() - started_clock,
            preflight,
            scenario_execution,
            recovery,
            scenario_results,
        )
        _write_evidence(project_root, evidence_path, evidence)

    return (0 if evidence["overall_status"] == "passed" else 1), evidence


def _default_evidence_path(project_root: Path) -> Path:
    configured_root = Path(os.environ.get("AI_PLATFORM_ROOT", ".ai-platform"))
    if not configured_root.is_absolute():
        configured_root = project_root / configured_root
    return configured_root / "evidence" / "p3-13-latest.json"


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令参数并打印不含敏感载荷的联合验收摘要。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--evidence", type=Path, default=_default_evidence_path(PROJECT_ROOT))
    arguments = parser.parse_args(argv)
    exit_code, evidence = execute_acceptance(
        PROJECT_ROOT,
        arguments.manifest.resolve(),
        arguments.evidence.resolve(),
    )
    summary = cast(dict[str, int], evidence["summary"])
    print(
        "[P3-13] 验收结果: "
        f"{evidence['overall_status']}, 通过 {summary['passed']}/{summary['total']}, "
        f"证据: {arguments.evidence.resolve()}",
        flush=True,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
