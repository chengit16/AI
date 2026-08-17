"""执行阶段 4 工具联合故障演练并生成可校验的本地最小证据。"""

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
MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "tool-execution" / "p4-12-v1.json"
MANIFEST_REFERENCE = "tests/fixtures/tool-execution/p4-12-v1.json"
EXPECTED_CAPABILITIES = frozenset(
    {
        "duplicate_delivery",
        "adapter_timeout",
        "response_loss",
        "credential_revocation",
        "approval_expiry",
        "cancellation_race",
        "worker_restart",
        "workspace_isolation",
    }
)
EXPECTED_SAMPLE_CLASSES = frozenset(
    {"success", "failure", "timeout", "cancellation", "recovery", "denial"}
)
EXPECTED_CASE_COUNTS = {
    capability: 2 if capability == "credential_revocation" else 1
    for capability in EXPECTED_CAPABILITIES
}
EXPECTED_SAMPLE_CLASS_BINDINGS = {
    "duplicate_delivery": ("success",),
    "adapter_timeout": ("failure", "timeout", "recovery"),
    "response_loss": ("failure", "recovery"),
    "credential_revocation": ("failure", "denial"),
    "approval_expiry": ("failure", "denial"),
    "cancellation_race": ("cancellation",),
    "worker_restart": ("timeout", "recovery"),
    "workspace_isolation": ("failure", "denial"),
}
EXPECTED_INVARIANT_BINDINGS = {
    "duplicate_delivery": (
        "p4_idempotency_before_side_effect",
        "p4_transactional_fact_consistency",
    ),
    "adapter_timeout": (
        "p4_idempotency_before_side_effect",
        "p4_bounded_retry_and_terminal_precedence",
    ),
    "response_loss": (
        "p4_idempotency_before_side_effect",
        "p4_bounded_retry_and_terminal_precedence",
    ),
    "credential_revocation": ("p4_credential_call_edge_only",),
    "approval_expiry": (
        "p4_confirmation_digest_binding",
        "p4_idempotency_before_side_effect",
    ),
    "cancellation_race": (
        "p4_bounded_retry_and_terminal_precedence",
        "p4_transactional_fact_consistency",
    ),
    "worker_restart": (
        "p4_idempotency_before_side_effect",
        "p4_bounded_retry_and_terminal_precedence",
        "p4_transactional_fact_consistency",
    ),
    "workspace_isolation": (
        "p4_current_policy_recheck",
        "p4_read_write_permission_separation",
    ),
}
EXPECTED_TOTAL_CASES = sum(EXPECTED_CASE_COUNTS.values())
EXPECTED_PARAMETER_IDS = {"credential_revocation": ("rotate", "revoke")}
EXPECTED_SCENARIO_BINDINGS = {
    "duplicate_delivery": (
        "p412-duplicate-delivery-001",
        ("p401-idempotent-replay-014",),
        "tests/integration/test_p408_side_effect_idempotency_postgres.py::test_concurrent_duplicate_delivery_has_one_adapter_call_owner",
    ),
    "adapter_timeout": (
        "p412-adapter-timeout-002",
        ("p401-unsafe-write-retry-denied-023",),
        "tests/integration/test_p409_tool_recovery_postgres.py::test_expired_lease_keeps_timeout_usage_before_retry",
    ),
    "response_loss": (
        "p412-response-loss-003",
        ("p401-outcome-unknown-manual-016",),
        "tests/integration/test_p408_side_effect_idempotency_postgres.py::test_committed_side_effect_with_lost_response_can_only_be_reconciled",
    ),
    "credential_revocation": (
        "p412-credential-revocation-004",
        ("p401-credential-result-leak-020",),
        "tests/integration/test_p407_tool_credentials_postgres.py::test_rotation_and_revocation_fail_closed_after_binding",
    ),
    "approval_expiry": (
        "p412-approval-expiry-005",
        ("p401-expired-confirmation-denied-011",),
        "tests/integration/test_p406_tool_confirmation_postgres.py::test_reject_withdraw_and_expire_never_make_step_ready",
    ),
    "cancellation_race": (
        "p412-cancellation-race-006",
        ("p401-cancel-running-attempt-018", "p401-late-success-ignored-019"),
        "tests/integration/test_p409_tool_recovery_postgres.py::test_lease_renewal_cancellation_observation_and_late_success_precedence",
    ),
    "worker_restart": (
        "p412-worker-restart-007",
        ("p401-unsafe-write-retry-denied-023", "p401-late-success-ignored-019"),
        "tests/integration/test_p403_tool_task_state_postgres.py::test_cancellation_late_success_and_worker_restart_timeout_never_overwrite_terminal",
    ),
    "workspace_isolation": (
        "p412-workspace-isolation-008",
        ("p401-cross-workspace-denied-006",),
        "tests/integration/test_p409_tool_recovery_postgres.py::test_cross_workspace_recovery_and_direct_database_bypass_fail_closed",
    ),
}
EXPECTED_INVARIANTS = frozenset(
    {
        "p4_current_policy_recheck",
        "p4_read_write_permission_separation",
        "p4_confirmation_digest_binding",
        "p4_idempotency_before_side_effect",
        "p4_credential_call_edge_only",
        "p4_bounded_retry_and_terminal_precedence",
        "p4_transactional_fact_consistency",
    }
)
PROTECTED_INPUT_REFERENCES = frozenset(
    {
        MANIFEST_REFERENCE,
        "contracts/tool-execution/stage-4-tool-drill.v1.schema.json",
        "contracts/tool-execution/stage-4-tool-drill-evidence.v1.schema.json",
        "contracts/tool-execution/tool-execution-baseline.v1.json",
        "tests/fixtures/tool-execution/p4-01-v1.json",
        *(binding[2].split("::", maxsplit=1)[0] for binding in EXPECTED_SCENARIO_BINDINGS.values()),
    }
)
ALLOWED_REASON_CODES = frozenset(
    {
        "command_unavailable",
        "command_failed",
        "runner_exception",
        "runner_invalid_result",
        "preflight_failed",
        "manifest_changed",
        "junit_not_created",
        "junit_invalid",
        "junit_case_missing",
        "junit_case_count_mismatch",
        "junit_case_identity_mismatch",
        "pytest_error",
        "pytest_failure",
        "pytest_skipped",
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
    baseline_invariants: set[str],
) -> None:
    """保证测试节点真实存在，且不变量能追溯到冻结的 P4-01 场景。"""

    for scenario in scenarios:
        scenario_id = cast(str, scenario["scenario_id"])
        source_refs = cast(list[str], scenario["source_case_refs"])
        if not set(source_refs).issubset(source_cases):
            raise ValueError(f"工具演练引用未知 P4-01 场景: {scenario_id}")
        source_invariants = {
            invariant
            for source_ref in source_refs
            for invariant in source_cases[source_ref]["expected"]["verified_invariants"]
        }
        verified = set(cast(list[str], scenario["verified_invariants"]))
        if not verified.issubset(baseline_invariants) or not verified.issubset(source_invariants):
            raise ValueError(f"工具演练不变量无法追溯到冻结场景: {scenario_id}")

        test_file, test_name = cast(str, scenario["pytest_node"]).split("::", maxsplit=1)
        test_path = project_root / test_file
        if not test_path.is_file() or test_name not in _known_test_functions(test_path):
            raise ValueError(f"工具演练测试节点不存在: {scenario['pytest_node']}")


def _validate_manifest_references(project_root: Path, manifest: dict[str, Any]) -> None:
    baseline = _load_object(project_root / cast(str, manifest["baseline_ref"]))
    source = _load_object(project_root / cast(str, manifest["source_scenarios_ref"]))
    baseline_invariants = {item["invariant_id"] for item in baseline["invariants"]}
    source_cases = {item["case_id"]: item for item in source["cases"]}
    scenarios = cast(list[dict[str, Any]], manifest["scenarios"])
    scenario_ids = [cast(str, scenario["scenario_id"]) for scenario in scenarios]
    pytest_nodes = [cast(str, scenario["pytest_node"]) for scenario in scenarios]
    pytest_names = [node.split("::", maxsplit=1)[1] for node in pytest_nodes]
    capabilities = [cast(str, scenario["capability"]) for scenario in scenarios]
    case_counts = {
        cast(str, scenario["capability"]): cast(int, scenario["expected_case_count"])
        for scenario in scenarios
    }
    sample_class_bindings = {
        cast(str, scenario["capability"]): tuple(cast(list[str], scenario["sample_classes"]))
        for scenario in scenarios
    }
    invariant_bindings = {
        cast(str, scenario["capability"]): tuple(cast(list[str], scenario["verified_invariants"]))
        for scenario in scenarios
    }

    # 清单集合使用代码常量再次冻结，防止只改 JSON 就静默降低演练覆盖。
    if frozenset(cast(list[str], manifest["required_invariants"])) != EXPECTED_INVARIANTS:
        raise ValueError("工具演练 required_invariants 未完整覆盖故障边界")
    if not EXPECTED_INVARIANTS.issubset(baseline_invariants):
        raise ValueError("工具演练 required_invariants 不属于阶段 4 基线")
    if frozenset(cast(list[str], manifest["required_capabilities"])) != EXPECTED_CAPABILITIES:
        raise ValueError("工具演练 required_capabilities 未完整覆盖八类故障")
    if frozenset(cast(list[str], manifest["required_sample_classes"])) != EXPECTED_SAMPLE_CLASSES:
        raise ValueError("工具演练 required_sample_classes 未覆盖全部样本类型")
    if (
        len(capabilities) != len(EXPECTED_CAPABILITIES)
        or set(capabilities) != EXPECTED_CAPABILITIES
    ):
        raise ValueError("工具演练每类必需能力必须且只能出现一次")
    if case_counts != EXPECTED_CASE_COUNTS:
        raise ValueError("工具演练参数化测试数量不符合冻结要求")

    # JUnit 只携带裸函数名；清单同时约束裸名唯一，避免不同文件同名用例串入错误场景。
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError("工具演练 scenario_id 不能重复")
    if len(pytest_nodes) != len(set(pytest_nodes)):
        raise ValueError("工具演练 pytest_node 不能重复")
    if len(pytest_names) != len(set(pytest_names)):
        raise ValueError("工具演练测试函数名不能重复")
    scenario_bindings = {
        cast(str, scenario["capability"]): (
            cast(str, scenario["scenario_id"]),
            tuple(cast(list[str], scenario["source_case_refs"])),
            cast(str, scenario["pytest_node"]),
        )
        for scenario in scenarios
    }
    # 能力名称不能替代具体故障断言，三项绑定共同防止清单降级到宽松测试。
    if scenario_bindings != EXPECTED_SCENARIO_BINDINGS:
        raise ValueError("工具演练场景、来源与测试节点不符合冻结绑定")
    covered_invariants = {
        invariant
        for scenario in scenarios
        for invariant in cast(list[str], scenario["verified_invariants"])
    }
    if covered_invariants != EXPECTED_INVARIANTS:
        raise ValueError("工具演练场景未完整覆盖必需不变量")
    if invariant_bindings != EXPECTED_INVARIANT_BINDINGS:
        raise ValueError("工具演练场景不变量不符合冻结绑定")
    covered_samples = {
        sample for scenario in scenarios for sample in cast(list[str], scenario["sample_classes"])
    }
    if covered_samples != EXPECTED_SAMPLE_CLASSES:
        raise ValueError("工具演练场景未完整覆盖必需样本类型")
    if sample_class_bindings != EXPECTED_SAMPLE_CLASS_BINDINGS:
        raise ValueError("工具演练场景样本类别不符合冻结绑定")
    _validate_scenario_references(
        project_root,
        scenarios,
        source_cases,
        baseline_invariants,
    )


def load_manifest(project_root: Path, manifest_path: Path) -> dict[str, Any]:
    """校验演练清单结构、冻结覆盖和测试追溯关系。"""

    manifest = _load_object(manifest_path)
    _validate_document(
        manifest,
        project_root / "contracts" / "tool-execution" / "stage-4-tool-drill.v1.schema.json",
    )
    _validate_manifest_references(project_root, manifest)
    return manifest


def run_command(command: tuple[str, ...], cwd: Path) -> CommandResult:
    """以前台输出执行固定命令，只返回无敏感内容的稳定状态。"""

    print(f"[P4-12] 执行: {' '.join(command)}", flush=True)
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


def _run_with_failure_boundary(
    runner: CommandRunner,
    command: tuple[str, ...],
    cwd: Path,
) -> CommandResult:
    """收敛 Runner 异常和非法结果，使恢复诊断仍有机会执行。"""

    started = time.monotonic()
    try:
        result: object = runner(command, cwd)
    except Exception:
        return CommandResult(
            1,
            round(time.monotonic() - started, 6),
            "runner_exception",
        )
    valid_duration = (
        isinstance(result, CommandResult)
        and isinstance(result.duration_seconds, (int, float))
        and not isinstance(result.duration_seconds, bool)
        and math.isfinite(result.duration_seconds)
        and result.duration_seconds >= 0
    )
    valid_exit_code = (
        isinstance(result, CommandResult)
        and isinstance(result.exit_code, int)
        and not isinstance(result.exit_code, bool)
        and 0 <= result.exit_code <= 255
    )
    valid_reason = (
        isinstance(result, CommandResult)
        and result.reason_code in ALLOWED_REASON_CODES | {None}
        and ((result.exit_code == 0) == (result.reason_code is None))
    )
    if not (valid_duration and valid_exit_code and valid_reason):
        return CommandResult(
            1,
            round(time.monotonic() - started, 6),
            "runner_invalid_result",
        )
    return cast(CommandResult, result)


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


def _manifest_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_matches(path: Path, expected_sha256: str) -> bool:
    try:
        return _manifest_sha256(path) == expected_sha256
    except OSError:
        return False


def _validate_evidence_path(
    project_root: Path,
    manifest_path: Path,
    evidence_path: Path,
) -> None:
    """把证据失效操作限制在当前运行证据目录，避免覆盖任意文件。"""

    resolved_root = project_root.resolve()
    resolved_evidence = evidence_path.resolve()
    protected_paths = {
        (resolved_root / reference).resolve() for reference in PROTECTED_INPUT_REFERENCES
    }
    protected_paths.add(manifest_path.resolve())
    if resolved_evidence in protected_paths:
        raise ValueError("工具演练证据路径不能与冻结输入重合")

    configured_evidence_root = _default_evidence_path(resolved_root).parent.resolve()
    if not resolved_evidence.is_relative_to(configured_evidence_root):
        raise ValueError("工具演练证据路径必须位于当前运行证据目录")


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
    observed_case_count: int = 0,
) -> dict[str, object]:
    return {
        "scenario_id": scenario["scenario_id"],
        "capability": scenario["capability"],
        "sample_classes": scenario["sample_classes"],
        "verified_invariants": scenario["verified_invariants"],
        "pytest_node": scenario["pytest_node"],
        "expected_case_count": scenario["expected_case_count"],
        "observed_case_count": observed_case_count,
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


def _expected_junit_case_names(scenario: dict[str, Any]) -> set[str]:
    """返回场景允许出现的精确 JUnit case 名称集合。"""

    test_name = cast(str, scenario["pytest_node"]).split("::", maxsplit=1)[1]
    parameter_ids = EXPECTED_PARAMETER_IDS.get(cast(str, scenario["capability"]), ())
    return (
        {f"{test_name}[{parameter_id}]" for parameter_id in parameter_ids}
        if parameter_ids
        else {test_name}
    )


def parse_junit_results(
    junit_path: Path, scenarios: list[dict[str, Any]]
) -> list[dict[str, object]]:
    """按测试函数聚合参数化 JUnit，并以稳定原因码替代错误正文。"""

    testcases: dict[str, ET.Element] = {}
    for element in ET.parse(junit_path).getroot().iter():
        if _local_name(element.tag) != "testcase":
            continue
        name = element.attrib.get("name", "")
        if name in testcases:
            raise ValueError(f"JUnit 包含重复测试结果: {name}")
        testcases[name] = element

    # 正式演练只能包含冻结清单的 case；额外结果意味着实际执行范围已经漂移。
    expected_testcases = {
        case_name for scenario in scenarios for case_name in _expected_junit_case_names(scenario)
    }
    if unexpected := (
        set(testcases).difference(expected_testcases)
        if expected_testcases.issubset(testcases)
        else set()
    ):
        raise ValueError(f"JUnit 包含未冻结测试结果: {sorted(unexpected)[0]}")

    results: list[dict[str, object]] = []
    for scenario in scenarios:
        test_name = cast(str, scenario["pytest_node"]).split("::", maxsplit=1)[1]
        matched = {
            case_name: testcase
            for case_name, testcase in testcases.items()
            if case_name == test_name or case_name.startswith(f"{test_name}[")
        }
        if not matched:
            results.append(_scenario_result(scenario, "error", 0, "junit_case_missing"))
            continue
        expected_names = _expected_junit_case_names(scenario)
        expected_case_count = cast(int, scenario["expected_case_count"])
        if len(expected_names) != expected_case_count or len(matched) != expected_case_count:
            results.append(
                _scenario_result(
                    scenario,
                    "error",
                    0,
                    "junit_case_count_mismatch",
                    len(matched),
                )
            )
            continue
        if set(matched) != expected_names:
            results.append(
                _scenario_result(
                    scenario,
                    "error",
                    0,
                    "junit_case_identity_mismatch",
                    len(matched),
                )
            )
            continue
        child_kinds = {
            _local_name(child.tag) for testcase in matched.values() for child in testcase
        }
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
                sum(_parse_duration(testcase.attrib.get("time")) for testcase in matched.values()),
                reason_code,
                len(matched),
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
    except (ET.ParseError, OSError, ValueError):
        return result, _uniform_results(scenarios, "error", "junit_invalid")


def _summary(results: list[dict[str, object]]) -> dict[str, object]:
    counts = Counter(cast(str, result["status"]) for result in results)
    sample_counts = Counter(
        sample for result in results for sample in cast(list[str], result["sample_classes"])
    )
    return {
        "total": len(results),
        "expected_case_total": sum(cast(int, result["expected_case_count"]) for result in results),
        "observed_case_total": sum(cast(int, result["observed_case_count"]) for result in results),
        "passed": counts["passed"],
        "failed": counts["failed"],
        "error": counts["error"],
        "skipped": counts["skipped"],
        "not_run": counts["not_run"],
        "sample_counts": {
            sample: sample_counts[sample] for sample in sorted(EXPECTED_SAMPLE_CLASSES)
        },
    }


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _write_evidence(
    project_root: Path,
    evidence_path: Path,
    evidence: dict[str, Any],
) -> None:
    _validate_document(
        evidence,
        project_root
        / "contracts"
        / "tool-execution"
        / "stage-4-tool-drill-evidence.v1.schema.json",
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
    manifest_sha256: str,
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
        revision != "unknown"
        and preflight.exit_code == 0
        and scenario_execution.exit_code == 0
        and recovery.exit_code == 0
        and summary["passed"] == summary["total"]
        and summary["expected_case_total"] == EXPECTED_TOTAL_CASES
        and summary["observed_case_total"] == summary["expected_case_total"]
    )
    return {
        "schema_version": 1,
        "evidence_version": "p4-12-evidence-v1",
        "drill_id": manifest["drill_id"],
        "run_id": str(uuid4()),
        "synthetic": True,
        "manifest_ref": MANIFEST_REFERENCE,
        "manifest_sha256": manifest_sha256,
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


def execute_drill(
    project_root: Path,
    manifest_path: Path,
    evidence_path: Path,
    *,
    runner: CommandRunner = run_command,
) -> tuple[int, dict[str, Any]]:
    """执行前后诊断和固定场景；任一失败都会关闭整次演练。"""

    canonical_manifest_path = (project_root / MANIFEST_REFERENCE).resolve()
    # 证据契约使用固定 manifest_ref，实际参与哈希的清单必须与该引用完全一致。
    if manifest_path.resolve() != canonical_manifest_path:
        raise ValueError("工具演练只能使用仓库冻结清单生成正式证据")

    _validate_evidence_path(project_root, manifest_path, evidence_path)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    # 正式尝试一旦开始就使旧证据失效，避免未捕获异常保留上次通过结论。
    evidence_path.unlink(missing_ok=True)
    started_at = datetime.now(UTC)
    started_clock = time.monotonic()
    manifest_sha256 = _manifest_sha256(manifest_path)
    manifest = load_manifest(project_root, manifest_path)
    if not _manifest_matches(manifest_path, manifest_sha256):
        raise ValueError("工具演练清单在校验期间发生变化")
    scenarios = cast(list[dict[str, Any]], manifest["scenarios"])
    revision, repository_dirty = _repository_state(project_root)

    def guarded_runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        return _run_with_failure_boundary(runner, command, cwd)

    # 前置诊断失败时不执行场景，避免把基础环境异常误记为工具状态机缺陷。
    preflight = guarded_runner(("./platform", "doctor"), project_root)
    with tempfile.TemporaryDirectory(prefix="p4-12-", dir=evidence_path.parent) as temporary:
        junit_path = Path(temporary) / "pytest.xml"
        if preflight.exit_code == 0:
            scenario_execution, scenario_results = _run_scenarios(
                project_root,
                scenarios,
                junit_path,
                guarded_runner,
            )
        else:
            scenario_execution = CommandResult(1, 0, "preflight_failed")
            scenario_results = _uniform_results(
                scenarios,
                "not_run",
                "preflight_failed",
            )

        # 场景失败后仍执行恢复诊断，证据 Schema 校验通过后才原子替换旧文件。
        recovery = guarded_runner(("./platform", "doctor"), project_root)
        if not _manifest_matches(manifest_path, manifest_sha256):
            scenario_execution = CommandResult(
                1,
                scenario_execution.duration_seconds,
                "manifest_changed",
            )
        evidence = _build_evidence(
            manifest_sha256,
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
        # 通过证据写入后再复核一次，关闭恢复诊断与原子替换之间的清单漂移窗口。
        if evidence["overall_status"] == "passed" and not _manifest_matches(
            manifest_path,
            manifest_sha256,
        ):
            evidence_path.unlink(missing_ok=True)
            raise ValueError("工具演练清单在证据原子写入期间发生变化")

    return (0 if evidence["overall_status"] == "passed" else 1), evidence


def _default_evidence_path(project_root: Path) -> Path:
    configured_root = Path(os.environ.get("AI_PLATFORM_ROOT", ".ai-platform"))
    if not configured_root.is_absolute():
        configured_root = project_root / configured_root
    return configured_root / "evidence" / "p4-12-latest.json"


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令参数并打印不含敏感载荷的联合演练摘要。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--evidence", type=Path, default=_default_evidence_path(PROJECT_ROOT))
    arguments = parser.parse_args(argv)
    exit_code, evidence = execute_drill(
        PROJECT_ROOT,
        arguments.manifest.resolve(),
        arguments.evidence.resolve(),
    )
    summary = cast(dict[str, object], evidence["summary"])
    print(
        "[P4-12] 演练结果: "
        f"{evidence['overall_status']}, 通过 {summary['passed']}/{summary['total']}, "
        f"证据: {arguments.evidence.resolve()}",
        flush=True,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
