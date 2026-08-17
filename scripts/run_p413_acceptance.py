"""执行阶段 4 联合验收并生成可校验的本地最小证据。"""

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
MANIFEST_REFERENCE = "tests/fixtures/tool-execution/p4-13-v1.json"
MANIFEST_PATH = PROJECT_ROOT / MANIFEST_REFERENCE
TOOL_DRILL_REFERENCE = "tests/fixtures/tool-execution/p4-12-v1.json"
EXPECTED_INVARIANTS = frozenset(
    {
        "p4_release_tool_allowlist",
        "p4_deterministic_execution_boundary",
        "p4_tool_version_immutable",
        "p4_current_policy_recheck",
        "p4_read_write_permission_separation",
        "p4_confirmation_digest_binding",
        "p4_idempotency_before_side_effect",
        "p4_credential_call_edge_only",
        "p4_bounded_retry_and_terminal_precedence",
        "p4_transactional_fact_consistency",
        "p4_untrusted_result_quarantine",
        "p4_restricted_adapter_surface",
    }
)
EXPECTED_GATES = frozenset(
    {
        "unauthorized_tool_denial",
        "unconfirmed_side_effect_denial",
        "zero_duplicate_side_effect",
        "step_attempt_traceability",
        "zero_credential_exposure",
        "cancellation_late_result_precedence",
    }
)
EXPECTED_TOOL_DRILL_CAPABILITIES = frozenset(
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
EXPECTED_SCENARIO_BINDINGS = {
    "catalog_allowlist": (
        "p413-catalog-allowlist-001",
        ("p401-personal-read-allowed-001", "p401-release-allowlist-denied-004"),
        "tests/integration/test_p402_tool_catalog_postgres.py::test_real_personal_plan_and_rbac_only_narrow_platform_catalog",
        ("p4_release_tool_allowlist", "p4_current_policy_recheck"),
        ("unauthorized_tool_denial",),
    ),
    "immutable_tool_version": (
        "p413-immutable-version-002",
        ("p401-unregistered-version-denied-005", "p401-arbitrary-http-denied-022"),
        "tests/integration/test_p402_tool_catalog_postgres.py::test_database_rejects_mutation_unsafe_adapter_and_destructive_downgrade",
        ("p4_tool_version_immutable", "p4_restricted_adapter_surface"),
        ("unauthorized_tool_denial",),
    ),
    "task_traceability": (
        "p413-task-traceability-003",
        ("p401-cancel-before-attempt-017", "p401-late-success-ignored-019"),
        "tests/integration/test_p403_tool_task_state_postgres.py::test_idempotent_ordered_lifecycle_and_terminal_database_protection",
        ("p4_bounded_retry_and_terminal_precedence", "p4_transactional_fact_consistency"),
        ("step_attempt_traceability", "cancellation_late_result_precedence"),
    ),
    "internal_read_adapters": (
        "p413-read-adapters-004",
        ("p401-personal-read-allowed-001", "p401-arbitrary-http-denied-022"),
        "tests/integration/test_p404_internal_read_adapters_postgres.py::test_real_catalog_executes_all_five_internal_read_adapters",
        (
            "p4_release_tool_allowlist",
            "p4_current_policy_recheck",
            "p4_restricted_adapter_surface",
        ),
        ("unauthorized_tool_denial",),
    ),
    "deterministic_planning": (
        "p413-planning-policy-005",
        ("p401-draft-tool-denied-003", "p401-policy-unavailable-denied-008"),
        "tests/integration/test_p405_tool_planning_postgres.py::test_real_release_plan_freezes_budget_policy_and_blocks_database_bypass",
        (
            "p4_release_tool_allowlist",
            "p4_deterministic_execution_boundary",
            "p4_current_policy_recheck",
        ),
        ("unauthorized_tool_denial",),
    ),
    "current_policy_authorization": (
        "p413-current-policy-006",
        ("p401-cross-workspace-denied-006", "p401-permission-revoked-denied-007"),
        "tests/unit/test_p402_tool_catalog.py::test_catalog_cross_workspace_inactive_plan_and_unknown_version_fail_closed",
        ("p4_current_policy_recheck", "p4_read_write_permission_separation"),
        ("unauthorized_tool_denial",),
    ),
    "personal_confirmation": (
        "p413-personal-confirmation-007",
        ("p401-personal-confirmation-required-009", "p401-stale-confirmation-denied-010"),
        "tests/integration/test_p406_tool_confirmation_postgres.py::test_personal_confirmation_is_idempotent_and_requires_fresh_pdp",
        ("p4_confirmation_digest_binding", "p4_idempotency_before_side_effect"),
        ("unconfirmed_side_effect_denial",),
    ),
    "enterprise_approval": (
        "p413-enterprise-approval-008",
        ("p401-enterprise-approval-pending-012", "p401-enterprise-write-approved-013"),
        "tests/integration/test_p406_tool_confirmation_postgres.py::test_enterprise_two_level_approval_and_agent_lifecycle_coexist",
        (
            "p4_confirmation_digest_binding",
            "p4_idempotency_before_side_effect",
            "p4_transactional_fact_consistency",
        ),
        ("unconfirmed_side_effect_denial",),
    ),
    "credential_boundary": (
        "p413-credential-boundary-009",
        ("p401-credential-result-leak-020",),
        "tests/integration/test_p407_tool_credentials_postgres.py::test_callback_result_and_exception_cannot_expose_plaintext",
        ("p4_credential_call_edge_only", "p4_untrusted_result_quarantine"),
        ("zero_credential_exposure",),
    ),
    "side_effect_idempotency": (
        "p413-side-effect-idempotency-010",
        ("p401-idempotent-replay-014",),
        "tests/integration/test_p408_side_effect_idempotency_postgres.py::test_concurrent_duplicate_delivery_has_one_adapter_call_owner",
        ("p4_idempotency_before_side_effect", "p4_transactional_fact_consistency"),
        ("zero_duplicate_side_effect",),
    ),
    "response_loss_recovery": (
        "p413-response-loss-011",
        ("p401-outcome-unknown-manual-016",),
        "tests/integration/test_p408_side_effect_idempotency_postgres.py::test_committed_side_effect_with_lost_response_can_only_be_reconciled",
        ("p4_idempotency_before_side_effect", "p4_bounded_retry_and_terminal_precedence"),
        ("zero_duplicate_side_effect",),
    ),
    "cancellation_precedence": (
        "p413-cancellation-precedence-012",
        ("p401-cancel-running-attempt-018", "p401-late-success-ignored-019"),
        "tests/integration/test_p409_tool_recovery_postgres.py::test_lease_renewal_cancellation_observation_and_late_success_precedence",
        ("p4_bounded_retry_and_terminal_precedence", "p4_transactional_fact_consistency"),
        ("cancellation_late_result_precedence",),
    ),
    "result_usage_sse_audit": (
        "p413-result-operations-013",
        ("p401-cross-workspace-denied-006", "p401-prompt-injection-result-021"),
        "tests/integration/test_p410_tool_observability_postgres.py::test_accepted_result_usage_and_progress_replay_are_minimal_and_isolated",
        ("p4_current_policy_recheck", "p4_untrusted_result_quarantine"),
        ("zero_credential_exposure", "step_attempt_traceability"),
    ),
    "console_http_authorization": (
        "p413-console-http-014",
        (
            "p401-hidden-menu-api-denied-024",
            "p401-personal-confirmation-required-009",
            "p401-cancel-running-attempt-018",
        ),
        "tests/unit/test_p411_tool_console_api.py::test_http_create_confirm_reject_cancel_and_sse_cursor_boundaries",
        (
            "p4_current_policy_recheck",
            "p4_read_write_permission_separation",
            "p4_confirmation_digest_binding",
            "p4_bounded_retry_and_terminal_precedence",
        ),
        (
            "unauthorized_tool_denial",
            "unconfirmed_side_effect_denial",
            "cancellation_late_result_precedence",
        ),
    ),
    "fault_drill_contract": (
        "p413-fault-drill-015",
        (
            "p401-cross-workspace-denied-006",
            "p401-expired-confirmation-denied-011",
            "p401-idempotent-replay-014",
            "p401-outcome-unknown-manual-016",
            "p401-cancel-running-attempt-018",
            "p401-late-success-ignored-019",
            "p401-credential-result-leak-020",
            "p401-unsafe-write-retry-denied-023",
        ),
        "tests/test_p412_tool_drill.py::test_p412_manifest_covers_faults_samples_invariants_and_unique_tests",
        (
            "p4_current_policy_recheck",
            "p4_read_write_permission_separation",
            "p4_confirmation_digest_binding",
            "p4_idempotency_before_side_effect",
            "p4_credential_call_edge_only",
            "p4_bounded_retry_and_terminal_precedence",
            "p4_transactional_fact_consistency",
            "p4_untrusted_result_quarantine",
        ),
        (
            "unauthorized_tool_denial",
            "unconfirmed_side_effect_denial",
            "zero_duplicate_side_effect",
            "step_attempt_traceability",
            "zero_credential_exposure",
            "cancellation_late_result_precedence",
        ),
    ),
}
EXPECTED_CAPABILITIES = frozenset(EXPECTED_SCENARIO_BINDINGS)
EXPECTED_TOTAL_CASES = len(EXPECTED_SCENARIO_BINDINGS)
PROTECTED_INPUT_REFERENCES = frozenset(
    {
        MANIFEST_REFERENCE,
        TOOL_DRILL_REFERENCE,
        "contracts/tool-execution/stage-4-acceptance.v1.schema.json",
        "contracts/tool-execution/stage-4-acceptance-evidence.v1.schema.json",
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
        "tool_drill_manifest_changed",
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


def _validate_manifest_references(project_root: Path, manifest: dict[str, Any]) -> None:
    """固定阶段不变量、关闭门禁、P4-12 依赖和每个真实测试节点。"""

    baseline = _load_object(project_root / cast(str, manifest["baseline_ref"]))
    source = _load_object(project_root / cast(str, manifest["source_scenarios_ref"]))
    tool_drill = _load_object(project_root / cast(str, manifest["tool_drill_manifest_ref"]))
    baseline_invariants = {item["invariant_id"] for item in baseline["invariants"]}
    source_cases = {item["case_id"]: item for item in source["cases"]}
    scenarios = cast(list[dict[str, Any]], manifest["scenarios"])

    if baseline_invariants != EXPECTED_INVARIANTS:
        raise ValueError("阶段 4 基线不变量与关闭验收冻结集合不一致")
    if frozenset(cast(list[str], manifest["required_invariants"])) != EXPECTED_INVARIANTS:
        raise ValueError("联合验收 required_invariants 必须完整覆盖阶段 4 基线")
    if frozenset(cast(list[str], manifest["required_gates"])) != EXPECTED_GATES:
        raise ValueError("联合验收 required_gates 必须完整覆盖六项关闭门禁")
    if frozenset(cast(list[str], manifest["required_capabilities"])) != EXPECTED_CAPABILITIES:
        raise ValueError("联合验收 required_capabilities 未完整覆盖冻结能力")
    if (
        tool_drill.get("drill_id") != "p4-12-v1"
        or tool_drill.get("status") != "frozen"
        or frozenset(cast(list[str], tool_drill.get("required_capabilities", [])))
        != EXPECTED_TOOL_DRILL_CAPABILITIES
    ):
        raise ValueError("联合验收依赖的 P4-12 固定故障演练发生漂移")

    capabilities = [cast(str, scenario["capability"]) for scenario in scenarios]
    scenario_ids = [cast(str, scenario["scenario_id"]) for scenario in scenarios]
    pytest_nodes = [cast(str, scenario["pytest_node"]) for scenario in scenarios]
    pytest_names = [node.split("::", maxsplit=1)[1] for node in pytest_nodes]
    if len(capabilities) != EXPECTED_TOTAL_CASES or set(capabilities) != EXPECTED_CAPABILITIES:
        raise ValueError("联合验收每类必需能力必须且只能出现一次")
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError("联合验收 scenario_id 不能重复")
    if len(pytest_nodes) != len(set(pytest_nodes)):
        raise ValueError("联合验收 pytest_node 不能重复")
    if len(pytest_names) != len(set(pytest_names)):
        raise ValueError("联合验收测试函数名不能重复")

    actual_bindings = {
        cast(str, scenario["capability"]): (
            cast(str, scenario["scenario_id"]),
            tuple(cast(list[str], scenario["source_case_refs"])),
            cast(str, scenario["pytest_node"]),
            tuple(cast(list[str], scenario["verified_invariants"])),
            tuple(cast(list[str], scenario["verified_gates"])),
        )
        for scenario in scenarios
    }
    if actual_bindings != EXPECTED_SCENARIO_BINDINGS:
        raise ValueError("联合验收场景、来源、测试与门禁绑定不符合冻结要求")

    covered_invariants: set[str] = set()
    covered_gates: set[str] = set()
    for scenario in scenarios:
        scenario_id = cast(str, scenario["scenario_id"])
        source_refs = cast(list[str], scenario["source_case_refs"])
        if not set(source_refs).issubset(source_cases):
            raise ValueError(f"联合验收引用未知 P4-01 场景: {scenario_id}")
        source_invariants = {
            invariant
            for source_ref in source_refs
            for invariant in source_cases[source_ref]["expected"]["verified_invariants"]
        }
        verified_invariants = set(cast(list[str], scenario["verified_invariants"]))
        verified_gates = set(cast(list[str], scenario["verified_gates"]))
        if not verified_invariants.issubset(source_invariants):
            raise ValueError(f"联合验收不变量无法追溯到冻结场景: {scenario_id}")
        if not verified_gates.issubset(EXPECTED_GATES):
            raise ValueError(f"联合验收引用未知关闭门禁: {scenario_id}")
        covered_invariants.update(verified_invariants)
        covered_gates.update(verified_gates)

        test_file, test_name = cast(str, scenario["pytest_node"]).split("::", maxsplit=1)
        test_path = project_root / test_file
        if not test_path.is_file() or test_name not in _known_test_functions(test_path):
            raise ValueError(f"联合验收测试节点不存在: {scenario['pytest_node']}")

    if covered_invariants != EXPECTED_INVARIANTS:
        raise ValueError("联合验收场景未完整覆盖阶段 4 不变量")
    if covered_gates != EXPECTED_GATES:
        raise ValueError("联合验收场景未完整覆盖六项关闭门禁")


def load_manifest(project_root: Path, manifest_path: Path) -> dict[str, Any]:
    """校验联合验收清单结构、冻结覆盖和测试追溯关系。"""

    manifest = _load_object(manifest_path)
    _validate_document(
        manifest,
        project_root / "contracts" / "tool-execution" / "stage-4-acceptance.v1.schema.json",
    )
    _validate_manifest_references(project_root, manifest)
    return manifest


def run_command(command: tuple[str, ...], cwd: Path) -> CommandResult:
    """以前台输出执行固定命令，只返回无敏感内容的稳定状态。"""

    print(f"[P4-13] 执行: {' '.join(command)}", flush=True)
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
        return CommandResult(1, round(time.monotonic() - started, 6), "runner_exception")
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
        return CommandResult(1, round(time.monotonic() - started, 6), "runner_invalid_result")
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _matches(path: Path, expected_sha256: str) -> bool:
    try:
        return _sha256(path) == expected_sha256
    except OSError:
        return False


def _default_evidence_path(project_root: Path) -> Path:
    configured_root = Path(os.environ.get("AI_PLATFORM_ROOT", ".ai-platform"))
    if not configured_root.is_absolute():
        configured_root = project_root / configured_root
    return configured_root / "evidence" / "p4-13-latest.json"


def _validate_evidence_path(
    project_root: Path,
    manifest_path: Path,
    evidence_path: Path,
) -> None:
    """限制证据失效范围，避免正式入口覆盖冻结输入或任意文件。"""

    resolved_root = project_root.resolve()
    resolved_evidence = evidence_path.resolve()
    protected_paths = {
        (resolved_root / reference).resolve() for reference in PROTECTED_INPUT_REFERENCES
    }
    protected_paths.add(manifest_path.resolve())
    if resolved_evidence in protected_paths:
        raise ValueError("联合验收证据路径不能与冻结输入重合")
    configured_evidence_root = _default_evidence_path(resolved_root).parent.resolve()
    if not resolved_evidence.is_relative_to(configured_evidence_root):
        raise ValueError("联合验收证据路径必须位于当前运行证据目录")


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
        "verified_invariants": scenario["verified_invariants"],
        "verified_gates": scenario["verified_gates"],
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


def parse_junit_results(
    junit_path: Path,
    scenarios: list[dict[str, Any]],
) -> list[dict[str, object]]:
    """按精确测试身份解析 JUnit，拒绝缺失、参数化或额外结果。"""

    testcases: dict[str, ET.Element] = {}
    for element in ET.parse(junit_path).getroot().iter():
        if _local_name(element.tag) != "testcase":
            continue
        name = element.attrib.get("name", "")
        if name in testcases:
            raise ValueError(f"JUnit 包含重复测试结果: {name}")
        testcases[name] = element

    expected_names = {
        cast(str, scenario["pytest_node"]).split("::", maxsplit=1)[1] for scenario in scenarios
    }
    if set(testcases).difference(expected_names):
        raise ValueError("JUnit 包含未冻结测试结果")

    results: list[dict[str, object]] = []
    for scenario in scenarios:
        test_name = cast(str, scenario["pytest_node"]).split("::", maxsplit=1)[1]
        testcase = testcases.get(test_name)
        if testcase is None:
            parameterized = [name for name in testcases if name.startswith(f"{test_name}[")]
            reason = "junit_case_identity_mismatch" if parameterized else "junit_case_missing"
            results.append(_scenario_result(scenario, "error", 0, reason, len(parameterized)))
            continue
        if cast(int, scenario["expected_case_count"]) != 1:
            results.append(_scenario_result(scenario, "error", 0, "junit_case_count_mismatch", 1))
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
                1,
            )
        )
    return results


def _run_scenarios(
    project_root: Path,
    scenarios: list[dict[str, Any]],
    junit_path: Path,
    runner: CommandRunner,
) -> tuple[CommandResult, list[dict[str, object]]]:
    command = (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        f"--junitxml={junit_path}",
        *(cast(str, scenario["pytest_node"]) for scenario in scenarios),
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
    return {
        "total": len(results),
        "expected_case_total": sum(cast(int, result["expected_case_count"]) for result in results),
        "observed_case_total": sum(cast(int, result["observed_case_count"]) for result in results),
        "passed": counts["passed"],
        "failed": counts["failed"],
        "error": counts["error"],
        "skipped": counts["skipped"],
        "not_run": counts["not_run"],
        "covered_invariants": sorted(
            {
                invariant
                for result in results
                for invariant in cast(list[str], result["verified_invariants"])
            }
        ),
        "covered_gates": sorted(
            {gate for result in results for gate in cast(list[str], result["verified_gates"])}
        ),
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
        / "stage-4-acceptance-evidence.v1.schema.json",
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
    tool_drill_sha256: str,
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
        and summary["observed_case_total"] == EXPECTED_TOTAL_CASES
        and frozenset(cast(list[str], summary["covered_invariants"])) == EXPECTED_INVARIANTS
        and frozenset(cast(list[str], summary["covered_gates"])) == EXPECTED_GATES
    )
    return {
        "schema_version": 1,
        "evidence_version": "p4-13-evidence-v1",
        "acceptance_id": manifest["acceptance_id"],
        "run_id": str(uuid4()),
        "synthetic": True,
        "manifest_ref": MANIFEST_REFERENCE,
        "manifest_sha256": manifest_sha256,
        "tool_drill_manifest_ref": TOOL_DRILL_REFERENCE,
        "tool_drill_manifest_sha256": tool_drill_sha256,
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
    """执行前后诊断和固定场景；任一失败都会关闭整次验收。"""

    canonical_manifest_path = (project_root / MANIFEST_REFERENCE).resolve()
    if manifest_path.resolve() != canonical_manifest_path:
        raise ValueError("阶段 4 验收只能使用仓库冻结清单生成正式证据")
    _validate_evidence_path(project_root, manifest_path, evidence_path)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.unlink(missing_ok=True)

    started_at = datetime.now(UTC)
    started_clock = time.monotonic()
    tool_drill_path = (project_root / TOOL_DRILL_REFERENCE).resolve()
    manifest_sha256 = _sha256(manifest_path)
    tool_drill_sha256 = _sha256(tool_drill_path)
    manifest = load_manifest(project_root, manifest_path)
    if not _matches(manifest_path, manifest_sha256):
        raise ValueError("阶段 4 验收清单在校验期间发生变化")
    if not _matches(tool_drill_path, tool_drill_sha256):
        raise ValueError("P4-12 故障演练清单在校验期间发生变化")
    scenarios = cast(list[dict[str, Any]], manifest["scenarios"])
    revision, repository_dirty = _repository_state(project_root)

    def guarded_runner(command: tuple[str, ...], cwd: Path) -> CommandResult:
        return _run_with_failure_boundary(runner, command, cwd)

    preflight = guarded_runner(("./platform", "doctor"), project_root)
    with tempfile.TemporaryDirectory(prefix="p4-13-", dir=evidence_path.parent) as temporary:
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
            scenario_results = _uniform_results(scenarios, "not_run", "preflight_failed")

        recovery = guarded_runner(("./platform", "doctor"), project_root)
        if not _matches(manifest_path, manifest_sha256):
            scenario_execution = CommandResult(
                1,
                scenario_execution.duration_seconds,
                "manifest_changed",
            )
        elif not _matches(tool_drill_path, tool_drill_sha256):
            scenario_execution = CommandResult(
                1,
                scenario_execution.duration_seconds,
                "tool_drill_manifest_changed",
            )
        evidence = _build_evidence(
            manifest_sha256,
            tool_drill_sha256,
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
        inputs_match = _matches(manifest_path, manifest_sha256) and _matches(
            tool_drill_path,
            tool_drill_sha256,
        )
        if evidence["overall_status"] == "passed" and not inputs_match:
            evidence_path.unlink(missing_ok=True)
            raise ValueError("阶段 4 验收输入在证据原子写入期间发生变化")

    return (0 if evidence["overall_status"] == "passed" else 1), evidence


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
    summary = cast(dict[str, object], evidence["summary"])
    print(
        "[P4-13] 验收结果: "
        f"{evidence['overall_status']}, 通过 {summary['passed']}/{summary['total']}, "
        f"证据: {arguments.evidence.resolve()}",
        flush=True,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
