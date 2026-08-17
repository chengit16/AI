"""验证 P5-11 服务拆分与 Go 演进触发条件，并生成低敏审计证据。"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).parents[1]
DEFAULT_PROFILE = ROOT / "contracts/architecture/service-evolution-audit.v1.json"
PROFILE_SCHEMA = ROOT / "contracts/architecture/service-evolution-audit.v1.schema.json"
EVIDENCE_SCHEMA = ROOT / "contracts/architecture/service-evolution-audit-evidence.v1.schema.json"
SERVICE_TRIGGER_IDS = (
    "independent_scaling_limit",
    "ownership_release_cadence_divergence",
    "explicit_fault_isolation_requirement",
    "compliance_independent_deployment_requirement",
    "measured_monolith_bottleneck",
)
GO_TRIGGER_IDS = (
    "sse_connection_threshold_or_python_instability",
    "gateway_proxy_primary_bottleneck",
    "runtime_independent_scaling_or_fault_isolation",
    "go_ownership_operations_and_rollback",
)
TRIGGER_STATUSES = {"not_run", "not_configured", "not_triggered", "triggered"}
GUARDRAILS = (
    "adr_before_implementation",
    "shared_versioned_contracts",
    "shadow_traffic_result_comparison",
    "security_and_workspace_isolation_regression",
    "single_writer_cutover",
    "python_route_rollback",
    "no_long_term_dual_write",
)
FORBIDDEN_GO_PATHS = (
    Path("services/edge-gateway"),
    Path("services/agent-runtime-go"),
)


class ServiceEvolutionAuditError(ValueError):
    """审计事实不完整、矛盾或试图绕过触发门禁。"""


def load_profile(path: Path = DEFAULT_PROFILE) -> dict[str, Any]:
    """读取版本化审计档案，并拒绝非对象 JSON。"""

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ServiceEvolutionAuditError(f"审计档案顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def validate_document_schema(document: dict[str, Any], schema_path: Path) -> None:
    """在业务规则前执行 Draft 2020-12 结构校验。"""

    schema = load_profile(schema_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(document)


def _validate_triggers(
    raw_triggers: object,
    expected_ids: tuple[str, ...],
    *,
    group_name: str,
) -> list[dict[str, Any]]:
    """验证触发项身份、状态和证据闭包，避免缺项或重复项被静默忽略。"""

    if not isinstance(raw_triggers, list):
        raise ServiceEvolutionAuditError(f"{group_name} 必须是列表")
    triggers: list[dict[str, Any]] = []
    for raw_trigger in raw_triggers:
        if not isinstance(raw_trigger, dict):
            raise ServiceEvolutionAuditError(f"{group_name} 包含非对象触发项")
        trigger = cast(dict[str, Any], raw_trigger)
        trigger_id = trigger.get("trigger_id")
        status = trigger.get("status")
        reason_code = trigger.get("reason_code")
        evidence_ref = trigger.get("evidence_ref")
        if not isinstance(trigger_id, str) or not trigger_id:
            raise ServiceEvolutionAuditError(f"{group_name} 的 trigger_id 无效")
        if status not in TRIGGER_STATUSES:
            raise ServiceEvolutionAuditError(f"{trigger_id} 的状态无效: {status}")
        if not isinstance(reason_code, str) or not reason_code:
            raise ServiceEvolutionAuditError(f"{trigger_id} 缺少稳定 reason_code")
        if evidence_ref is not None and (not isinstance(evidence_ref, str) or not evidence_ref):
            raise ServiceEvolutionAuditError(f"{trigger_id} 的 evidence_ref 无效")
        if status == "triggered" and evidence_ref is None:
            raise ServiceEvolutionAuditError(f"{trigger_id} 标记触发时必须引用可复核证据")
        triggers.append(trigger)

    actual_ids = tuple(cast(str, trigger["trigger_id"]) for trigger in triggers)
    if actual_ids != expected_ids:
        raise ServiceEvolutionAuditError(
            f"{group_name} 必须按 17.8 节顺序完整列出: {', '.join(expected_ids)}"
        )
    return triggers


def _triggered(triggers: list[dict[str, Any]]) -> bool:
    return any(trigger["status"] == "triggered" for trigger in triggers)


def _trigger_map(triggers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {cast(str, item["trigger_id"]): item for item in triggers}


def _validate_external_status(
    document: dict[str, Any],
    service_triggers: list[dict[str, Any]],
    go_triggers: list[dict[str, Any]],
) -> None:
    """把外部输入状态与触发结论绑定，未知数据不能被解释成已触发。"""

    external = document.get("external_status")
    if not isinstance(external, dict):
        raise ServiceEvolutionAuditError("审计档案缺少 external_status")
    service_by_id = _trigger_map(service_triggers)
    go_by_id = _trigger_map(go_triggers)
    if external.get("capacity_certification") != "passed":
        protected = (
            service_by_id["independent_scaling_limit"],
            go_by_id["sse_connection_threshold_or_python_instability"],
        )
        if any(item["status"] == "triggered" for item in protected):
            raise ServiceEvolutionAuditError("容量认证未通过时不能声明扩缩容或 SSE 条件已触发")
    if external.get("production_load_profile") != "configured":
        protected = (
            service_by_id["measured_monolith_bottleneck"],
            go_by_id["gateway_proxy_primary_bottleneck"],
        )
        if any(item["status"] == "triggered" for item in protected):
            raise ServiceEvolutionAuditError("生产负载画像未配置时不能声明性能主瓶颈已触发")
    if (
        external.get("team_ownership_model") != "configured"
        and service_by_id["ownership_release_cadence_divergence"]["status"] == "triggered"
    ):
        raise ServiceEvolutionAuditError("团队所有权未配置时不能声明发布节奏分化已触发")
    if (
        external.get("go_operations_capability") != "configured"
        and go_by_id["go_ownership_operations_and_rollback"]["status"] == "triggered"
    ):
        raise ServiceEvolutionAuditError("Go 运维能力未配置时不能声明负责人条件已触发")


def _validate_decision(
    document: dict[str, Any],
    service_triggers: list[dict[str, Any]],
    go_triggers: list[dict[str, Any]],
) -> None:
    """只允许已验证触发项驱动拆分，Go 还必须具有通过的基准报告。"""

    decision = document.get("decision")
    reason = document.get("decision_reason")
    service_triggered = _triggered(service_triggers)
    go_triggered = _triggered(go_triggers)
    benchmark_status = document.get("benchmark_report_status")
    if decision == "retain_modular_monolith":
        if service_triggered or go_triggered or reason != "no_verified_trigger":
            raise ServiceEvolutionAuditError("保留模块化单体时不能存在已验证触发项")
        return
    if decision == "split_python_service":
        if not service_triggered or reason != "verified_service_split_trigger":
            raise ServiceEvolutionAuditError("Python 服务拆分必须具有已验证的 17.8 触发项")
        if go_triggered:
            raise ServiceEvolutionAuditError("仅拆分 Python 服务时不能同时声明 Go 条件已触发")
        return
    if decision == "start_go_evolution":
        if not service_triggered or not go_triggered:
            raise ServiceEvolutionAuditError("启动 Go 演进必须同时具有拆分和 Go 已验证触发项")
        if benchmark_status != "passed" or reason != "verified_go_trigger":
            raise ServiceEvolutionAuditError("启动 Go 演进必须先形成通过的可归因基准报告")
        return
    raise ServiceEvolutionAuditError(f"未知演进决策: {decision}")


def _validate_runtime(document: dict[str, Any], project_root: Path) -> None:
    """当前审计不能在第二套运行时已经落地后倒填授权。"""

    runtime = document.get("current_runtime")
    if not isinstance(runtime, dict):
        raise ServiceEvolutionAuditError("审计档案缺少 current_runtime")
    expected = {
        "go_service_directories_present": False,
        "go_runtime_present": False,
        "long_term_dual_write_present": False,
        "python_remains_single_writer": True,
    }
    if runtime != expected:
        raise ServiceEvolutionAuditError("P5-11 审计必须发生在 Go 实现和数据写入权迁移之前")
    present_paths = [str(path) for path in FORBIDDEN_GO_PATHS if (project_root / path).exists()]
    if present_paths:
        raise ServiceEvolutionAuditError(f"未授权的 Go 服务目录已存在: {', '.join(present_paths)}")


def validate_profile(document: dict[str, Any], project_root: Path = ROOT) -> None:
    """验证 P5-11 审计身份、九项触发条件、决策和迁移前置边界。"""

    validate_document_schema(document, PROFILE_SCHEMA)
    if document.get("schema_version") != 1 or document.get("contract_id") != "p5-11-v1":
        raise ServiceEvolutionAuditError("审计档案版本或契约标识不匹配")
    if document.get("audit_status") != "passed":
        raise ServiceEvolutionAuditError("只有完整通过的审计才能形成演进决策")
    service_triggers = _validate_triggers(
        document.get("service_split_triggers"),
        SERVICE_TRIGGER_IDS,
        group_name="服务拆分触发项",
    )
    go_triggers = _validate_triggers(
        document.get("go_evolution_triggers"),
        GO_TRIGGER_IDS,
        group_name="Go 演进触发项",
    )
    if tuple(document.get("future_migration_guardrails", ())) != GUARDRAILS:
        raise ServiceEvolutionAuditError("未来迁移门禁缺失或顺序漂移")
    _validate_external_status(document, service_triggers, go_triggers)
    _validate_decision(document, service_triggers, go_triggers)
    _validate_runtime(document, project_root)


def build_evidence(document: dict[str, Any]) -> dict[str, Any]:
    """只输出状态与计数，不复制团队、负载或未来基准报告正文。"""

    service = cast(list[dict[str, Any]], document["service_split_triggers"])
    go = cast(list[dict[str, Any]], document["go_evolution_triggers"])
    return {
        "evidence_version": "p5-11-evidence-v1",
        "overall_status": "passed",
        "decision": document["decision"],
        "decision_reason": document["decision_reason"],
        "service_trigger_summary": dict(
            sorted(Counter(item["status"] for item in service).items())
        ),
        "go_trigger_summary": dict(sorted(Counter(item["status"] for item in go).items())),
        "benchmark_report_status": document["benchmark_report_status"],
        "external_status": document["external_status"],
        "go_implementation_started": False,
        "long_term_dual_write_present": False,
        "sensitive_detail_in_evidence": False,
    }


def write_evidence(path: Path, evidence: dict[str, Any]) -> None:
    """原子替换低敏证据，避免中断留下半写 JSON。"""

    validate_document_schema(evidence, EVIDENCE_SCHEMA)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary_path = Path(stream.name)
        json.dump(evidence, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary_path.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--evidence", type=Path, required=True)
    arguments = parser.parse_args()
    document = load_profile(arguments.profile)
    validate_profile(document)
    write_evidence(arguments.evidence, build_evidence(document))
    print(
        "P5-11 服务演进审计通过: "
        f"decision={document['decision']}, evidence={arguments.evidence.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
