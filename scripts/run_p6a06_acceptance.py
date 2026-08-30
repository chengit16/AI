"""执行阶段 A 联合验收并生成不含业务载荷的最小证据。"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
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
MANIFEST_REFERENCE = "tests/fixtures/e2e/p6a06-v1.json"
SCHEMA_REFERENCE = "contracts/acceptance/stage-6-a-acceptance.v1.schema.json"
EVIDENCE_SCHEMA_REFERENCE = "contracts/acceptance/stage-6-a-acceptance-evidence.v1.schema.json"
CAPABILITIES = frozenset(
    {
        "organization_lifecycle",
        "document_publication",
        "ingestion_retry",
        "index_consistency",
        "retrieval_authorization",
        "workbench_search",
        "assistant_scope",
        "migration_roundtrip",
        "cross_workspace_isolation",
    }
)


@dataclass(frozen=True)
class CommandResult:
    """记录固定命令的退出状态，避免测试输出或正文进入证据。"""

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


def load_manifest(project_root: Path, manifest_path: Path) -> dict[str, Any]:
    """校验冻结清单、能力覆盖和全部 pytest 节点引用。"""

    canonical = (project_root / MANIFEST_REFERENCE).resolve()
    if manifest_path.resolve() != canonical:
        raise ValueError("阶段 A 联合验收只能使用仓库冻结清单")
    manifest = _load_object(manifest_path)
    _validate_document(manifest, project_root / SCHEMA_REFERENCE)
    scenarios = cast(list[dict[str, Any]], manifest["scenarios"])
    scenario_ids = [cast(str, item["scenario_id"]) for item in scenarios]
    nodes = [cast(str, item["pytest_node"]) for item in scenarios]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError("阶段 A 联合验收 scenario_id 不能重复")
    if len(nodes) != len(set(nodes)):
        raise ValueError("阶段 A 联合验收 pytest_node 不能重复")
    if set(cast(list[str], manifest["required_capabilities"])) != CAPABILITIES:
        raise ValueError("阶段 A 联合验收能力集合不完整")
    if {item["capability"] for item in scenarios} != CAPABILITIES:
        raise ValueError("阶段 A 联合验收场景未覆盖全部能力")
    for node in nodes:
        relative_path, separator, function_name = node.partition("::")
        path = project_root / relative_path
        if separator != "::" or not path.is_file():
            raise ValueError(f"阶段 A 联合验收测试节点不存在: {node}")
        if function_name not in _known_test_functions(path):
            raise ValueError(f"阶段 A 联合验收测试函数不存在: {node}")
    return manifest


def run_command(command: tuple[str, ...], cwd: Path) -> CommandResult:
    """执行固定命令并把系统异常收敛为稳定原因码。"""

    print(f"[P6A-06] 执行: {' '.join(command)}", flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=cwd, check=False)
    except OSError:
        return CommandResult(127, round(time.monotonic() - started, 6), "command_unavailable")
    exit_code = (
        completed.returncode
        if completed.returncode >= 0
        else min(128 + abs(completed.returncode), 255)
    )
    reason_code = None if exit_code == 0 else "command_failed"
    return CommandResult(exit_code, round(time.monotonic() - started, 6), reason_code)


def _repository_state(project_root: Path) -> tuple[str, bool]:
    try:
        revision = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ("git", "status", "--porcelain"),
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown", True
    return revision, dirty


def _check(result: CommandResult) -> dict[str, object]:
    return {
        "status": "passed" if result.exit_code == 0 else "failed",
        "exit_code": result.exit_code,
        "duration_seconds": result.duration_seconds,
        "reason_code": result.reason_code,
    }


def _scenario_result(
    scenario: dict[str, Any], status: str, duration: float, reason: str | None, observed: int
) -> dict[str, object]:
    return {
        "scenario_id": scenario["scenario_id"],
        "capability": scenario["capability"],
        "pytest_node": scenario["pytest_node"],
        "expected_case_count": scenario["expected_case_count"],
        "observed_case_count": observed,
        "status": status,
        "duration_seconds": round(max(duration, 0), 6),
        "reason_code": reason,
    }


def _parse_junit(path: Path, scenarios: list[dict[str, Any]]) -> list[dict[str, object]]:
    testcases: dict[str, ET.Element] = {}
    for element in ET.parse(path).getroot().iter():
        if element.tag.rsplit("}", maxsplit=1)[-1] != "testcase":
            continue
        name = element.attrib.get("name", "")
        if name in testcases:
            raise ValueError(f"JUnit 包含重复测试结果: {name}")
        testcases[name] = element
    results: list[dict[str, object]] = []
    for scenario in scenarios:
        name = cast(str, scenario["pytest_node"]).split("::", maxsplit=1)[1]
        testcase = testcases.get(name)
        if testcase is None:
            results.append(_scenario_result(scenario, "error", 0, "junit_case_missing", 0))
            continue
        children = {child.tag.rsplit("}", maxsplit=1)[-1] for child in testcase}
        if "error" in children:
            status, reason = "error", "pytest_error"
        elif "failure" in children:
            status, reason = "failed", "pytest_failure"
        elif "skipped" in children:
            status, reason = "skipped", "pytest_skipped"
        else:
            status, reason = "passed", None
        try:
            duration = float(testcase.attrib.get("time", "0"))
        except ValueError:
            duration = 0
        results.append(_scenario_result(scenario, status, duration, reason, 1))
    return results


def _summary(results: list[dict[str, object]]) -> dict[str, int]:
    counts = Counter(cast(str, result["status"]) for result in results)
    return {
        "total": len(results),
        "expected_case_total": len(results),
        "observed_case_total": sum(cast(int, result["observed_case_count"]) for result in results),
        "passed": counts["passed"],
        "failed": counts["failed"],
        "error": counts["error"],
        "skipped": counts["skipped"],
        "not_run": counts["not_run"],
    }


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _write_evidence(project_root: Path, path: Path, evidence: dict[str, Any]) -> None:
    _validate_document(evidence, project_root / EVIDENCE_SCHEMA_REFERENCE)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _default_evidence_path(project_root: Path) -> Path:
    root = Path(os.environ.get("AI_PLATFORM_ROOT", ".ai-platform"))
    return (root if root.is_absolute() else project_root / root) / "evidence/p6a06-latest.json"


def execute_acceptance(
    project_root: Path,
    manifest_path: Path,
    evidence_path: Path,
    *,
    runner: CommandRunner = run_command,
) -> tuple[int, dict[str, Any]]:
    """依次执行前置诊断、冻结场景和恢复诊断，任一失败都失败关闭。"""

    started_at = datetime.now(UTC)
    started_clock = time.monotonic()
    manifest = load_manifest(project_root, manifest_path)
    scenarios = cast(list[dict[str, Any]], manifest["scenarios"])
    revision, dirty = _repository_state(project_root)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    preflight = runner(("./platform", "doctor"), project_root)
    with tempfile.TemporaryDirectory(prefix="p6a06-", dir=evidence_path.parent) as temporary:
        junit_path = Path(temporary) / "pytest.xml"
        if preflight.exit_code == 0:
            command = (
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--tb=short",
                f"--junitxml={junit_path}",
                *(cast(str, item["pytest_node"]) for item in scenarios),
            )
            scenario_execution = runner(command, project_root)
            if junit_path.is_file():
                try:
                    scenario_results = _parse_junit(junit_path, scenarios)
                except (ET.ParseError, ValueError):
                    scenario_results = [
                        _scenario_result(item, "error", 0, "junit_invalid", 0) for item in scenarios
                    ]
            else:
                scenario_results = [
                    _scenario_result(item, "error", 0, "junit_not_created", 0) for item in scenarios
                ]
        else:
            scenario_execution = CommandResult(1, 0, "preflight_failed")
            scenario_results = [
                _scenario_result(item, "not_run", 0, "preflight_failed", 0) for item in scenarios
            ]
        recovery = runner(("./platform", "doctor"), project_root)
    summary = _summary(scenario_results)
    passed = (
        revision != "unknown"
        and preflight.exit_code == 0
        and scenario_execution.exit_code == 0
        and recovery.exit_code == 0
        and summary["passed"] == summary["total"]
        and summary["observed_case_total"] == summary["expected_case_total"]
    )
    evidence = {
        "schema_version": 1,
        "evidence_version": "p6a06-evidence-v1",
        "acceptance_id": manifest["acceptance_id"],
        "run_id": str(uuid4()),
        "synthetic": True,
        "manifest_ref": MANIFEST_REFERENCE,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "repository_revision": revision,
        "repository_dirty": dirty,
        "started_at": _timestamp(started_at),
        "finished_at": _timestamp(datetime.now(UTC)),
        "duration_seconds": round(time.monotonic() - started_clock, 6),
        "overall_status": "passed" if passed else "failed",
        "checks": {
            "preflight": _check(preflight),
            "scenario_execution": _check(scenario_execution),
            "recovery": _check(recovery),
        },
        "scenarios": scenario_results,
        "summary": summary,
    }
    _write_evidence(project_root, evidence_path, evidence)
    return (0 if passed else 1), evidence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / MANIFEST_REFERENCE)
    parser.add_argument("--evidence", type=Path, default=_default_evidence_path(PROJECT_ROOT))
    arguments = parser.parse_args(argv)
    code, evidence = execute_acceptance(
        PROJECT_ROOT, arguments.manifest.resolve(), arguments.evidence.resolve()
    )
    summary = cast(dict[str, int], evidence["summary"])
    print(
        f"[P6A-06] 验收结果: {evidence['overall_status']},"
        f"通过 {summary['passed']}/{summary['total']},"
        f"证据: {arguments.evidence.resolve()}",
        flush=True,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
