"""校验 P3-01 Agent 控制面契约、安全不变量和全合成场景。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker

from scripts.check_repository_policy import SECRET_PATTERNS

ROOT = Path(__file__).parents[1]
CONTRACT_DIR = ROOT / "contracts" / "agent-control"
ENTITY_SCHEMA_PATH = CONTRACT_DIR / "agent-control.v1.schema.json"
ENTITY_FIXTURE_PATH = ROOT / "contracts" / "fixtures" / "agent-control.v1.valid.json"
BASELINE_SCHEMA_PATH = CONTRACT_DIR / "agent-control-baseline.v1.schema.json"
BASELINE_PATH = CONTRACT_DIR / "agent-control-baseline.v1.json"
SCENARIO_SCHEMA_PATH = CONTRACT_DIR / "agent-control-scenarios.v1.schema.json"
SCENARIO_PATH = ROOT / "tests" / "fixtures" / "agent-control" / "p3-01-v1.json"
ERROR_CATALOG_PATH = ROOT / "contracts" / "errors" / "catalog.v1.json"

EXPECTED_CATEGORIES = {
    "lifecycle",
    "runtime_boundary",
    "evaluation_gate",
    "approval_gate",
    "workspace_isolation",
    "immutability",
    "control_plane_outage",
    "routing",
    "canary",
    "rollback",
    "concurrency",
    "menu_authorization",
    "tool_boundary",
}
EXPECTED_INVARIANTS = {
    "p3_runtime_release_only",
    "p3_release_immutable",
    "p3_run_release_binding_unique",
    "p3_snapshot_evidence_complete",
    "p3_control_plane_runtime_isolation",
    "p3_publish_fail_closed",
    "p3_route_change_only",
    "p3_workspace_authorization",
    "p3_read_only_tools_only",
    "p3_offline_deterministic_evaluation",
}
EXPECTED_DEFERRED_CAPABILITIES = {
    "saas",
    "go_runtime",
    "real_data_source_connectors",
    "llm_grading",
    "multimodal_image_qa",
    "channel_gateway",
    "durable_run",
    "external_write_tools",
}
EXPECTED_ERROR_DEFINITIONS = {
    "AGENT_CONFIGURATION_INVALID": (422, False),
    "AGENT_LIFECYCLE_CONFLICT": (409, True),
    "AGENT_TEST_GATE_FAILED": (409, False),
    "AGENT_RELEASE_APPROVAL_REQUIRED": (409, False),
    "AGENT_RELEASE_IMMUTABLE": (409, False),
    "AGENT_RUNTIME_RELEASE_REQUIRED": (409, False),
    "SERVICE_ROUTE_CONFLICT": (409, True),
    "SERVICE_ROUTE_UNAVAILABLE": (503, True),
}


def load_object(path: Path) -> dict[str, Any]:
    """读取对象型 JSON，避免契约测试接受错误顶层类型。"""

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def test_p301_documents_match_versioned_json_schemas() -> None:
    documents = (
        (ENTITY_SCHEMA_PATH, ENTITY_FIXTURE_PATH),
        (BASELINE_SCHEMA_PATH, BASELINE_PATH),
        (SCENARIO_SCHEMA_PATH, SCENARIO_PATH),
    )

    for schema_path, document_path in documents:
        schema = load_object(schema_path)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(
            load_object(document_path)
        )


def test_p301_entity_fixture_freezes_release_and_run_relationships() -> None:
    fixture = load_object(ENTITY_FIXTURE_PATH)
    agent = fixture["agent"]
    draft = fixture["agent_draft"]
    candidate = fixture["release_candidate"]
    release = fixture["agent_release"]
    service = fixture["service"]
    route = fixture["service_route"]
    run_binding = fixture["run_binding"]

    assert {agent["agent_id"], draft["agent_id"], candidate["agent_id"], release["agent_id"]} == {
        agent["agent_id"]
    }
    assert {
        agent["workspace_id"],
        draft["workspace_id"],
        candidate["workspace_id"],
        release["workspace_id"],
        service["workspace_id"],
        route["workspace_id"],
        run_binding["workspace_id"],
    } == {agent["workspace_id"]}
    assert draft["draft_id"] == candidate["draft_id"] == release["snapshot"]["source_draft_id"]
    assert (
        draft["revision"]
        == candidate["draft_revision"]
        == release["snapshot"]["source_draft_revision"]
    )
    assert draft["config_hash"] == candidate["config_hash"] == release["config_hash"]
    assert candidate["candidate_hash"] == release["candidate_hash"]
    assert candidate["approval"]["candidate_hash"] == candidate["candidate_hash"]
    assert run_binding["service_id"] == service["service_id"] == route["service_id"]
    assert run_binding["service_route_id"] == route["route_id"]
    assert run_binding["service_route_version"] == route["route_version"]
    assert run_binding["agent_release_id"] in {
        route["primary_release_id"],
        route["canary_release_id"],
    }


def test_p301_release_evidence_requires_all_security_checks_and_read_only_tools() -> None:
    fixture = load_object(ENTITY_FIXTURE_PATH)
    release = fixture["agent_release"]
    snapshot = release["snapshot"]
    checks = {item["check_code"] for item in snapshot["evaluation"]["required_checks"]}

    assert checks == {
        "functional",
        "authorization",
        "prompt_injection",
        "citation",
        "output_contract",
    }
    assert snapshot["evaluation"]["status"] == "passed"
    assert snapshot["approval"]["status"] == "approved"
    assert snapshot["approval"]["candidate_hash"] == release["candidate_hash"]
    assert all(
        tool["access_mode"] == "read" for tool in snapshot["configuration"]["read_only_tools"]
    )


def test_p301_state_machines_have_no_draft_to_runtime_shortcut() -> None:
    baseline = load_object(BASELINE_PATH)
    machines = {item["entity"]: item for item in baseline["state_machines"]}

    assert set(machines) == {"agent", "agent_draft", "service"}
    assert "agent_release" not in machines
    draft_machine = machines["agent_draft"]
    transitions = {(item["from"], item["to"]) for item in draft_machine["transitions"]}
    assert ("editing", "testing") in transitions
    assert ("testing", "ready_for_approval") in transitions
    assert ("approval_pending", "approved") in transitions
    assert all(target != "released" for _, target in transitions)
    assert ("editing", "approved") not in transitions


def test_p301_baseline_freezes_invariants_and_deferred_boundaries() -> None:
    baseline = load_object(BASELINE_PATH)
    invariants = {item["invariant_id"] for item in baseline["invariants"]}
    evaluation = baseline["evaluation_policy"]

    assert baseline["baseline_id"] == "p3-01-v1"
    assert baseline["status"] == "frozen"
    assert invariants == EXPECTED_INVARIANTS
    assert set(baseline["deferred_capabilities"]) == EXPECTED_DEFERRED_CAPABILITIES
    assert evaluation["failure_handling"] == "block_release"
    assert evaluation["timeout_handling"] == "count_as_failure"
    assert evaluation["skipped_handling"] == "count_as_failure"
    assert evaluation["online_llm_grading"] is False
    assert evaluation["multimodal_image_qa"] is False


def test_p301_reserved_menu_and_api_identifiers_follow_stage_activation() -> None:
    baseline = load_object(BASELINE_PATH)
    permissions = cast(list[str], baseline["reserved_permissions"])
    menus = cast(list[dict[str, Any]], baseline["reserved_menus"])
    operations = cast(list[dict[str, Any]], baseline["planned_api_operations"])
    permission_set = set(permissions)
    menu_keys = {item["menu_key"] for item in menus}

    assert len(permissions) == len(permission_set)
    assert len(menus) == len(menu_keys)
    assert len(operations) == len({item["operation_id"] for item in operations})
    assert {item["permission_code"] for item in menus}.issubset(permission_set)
    assert {item["permission_code"] for item in operations}.issubset(permission_set)
    assert {item["menu_key"] for item in operations}.issubset(menu_keys)
    assert all(set(item["workspace_types"]) == {"personal", "enterprise"} for item in menus)
    assert {item["menu_key"] for item in menus if item["menu_type"] == "page"} == {
        "workspace.agents",
        "workspace.services",
        "workspace.agent_operations",
    }

    # P3-12 已激活全部页面权限和运营查询；Runtime 快照装载仍保持内部边界，不注册公开 API。
    active_registry = load_object(
        ROOT / "contracts" / "authorization" / "resource-registry.v1.json"
    )
    active_permissions = {item["code"] for item in active_registry["permissions"]}
    active_operations = {item["operation_id"] for item in active_registry["api_resources"]}
    assert permission_set - active_permissions == set()
    assert {item["operation_id"] for item in operations} - active_operations == {
        "loadAgentReleaseSnapshot",
    }


def test_p301_personal_and_enterprise_approval_modes_are_explicit() -> None:
    baseline = load_object(BASELINE_PATH)
    modes = {item["workspace_type"]: item for item in baseline["approval_modes"]}

    assert modes["personal"] == {
        "workspace_type": "personal",
        "approver_source": "workspace_owner",
        "minimum_levels": 1,
        "self_approval": "allowed_for_owner",
    }
    assert modes["enterprise"]["approver_source"] == "approval_policy"
    assert modes["enterprise"]["minimum_levels"] >= 1
    assert modes["enterprise"]["self_approval"] == "policy_defined"


def test_p301_scenarios_cover_all_categories_and_security_boundaries() -> None:
    fixture = load_object(SCENARIO_PATH)
    cases = cast(list[dict[str, Any]], fixture["cases"])
    case_ids = [case["case_id"] for case in cases]

    assert fixture["dataset_version"] == "p3-01-v1"
    assert fixture["synthetic"] is True
    assert set(fixture["categories"]) == EXPECTED_CATEGORIES
    assert {case["category"] for case in cases} == EXPECTED_CATEGORIES
    assert {case["workspace_type"] for case in cases} >= {"personal", "enterprise"}
    assert len(cases) >= 16
    assert len(case_ids) == len(set(case_ids))
    assert all(case["synthetic"] is True for case in cases)


def test_p301_scenarios_reference_known_invariants_and_error_codes() -> None:
    baseline = load_object(BASELINE_PATH)
    fixture = load_object(SCENARIO_PATH)
    catalog = load_object(ERROR_CATALOG_PATH)
    invariants = {item["invariant_id"] for item in baseline["invariants"]}
    errors = {item["code"]: item for item in catalog["errors"]}

    for case in fixture["cases"]:
        expected = case["expected"]
        assert set(expected["verified_invariants"]).issubset(invariants), case["case_id"]
        assert expected["duplicate_side_effects"] == 0, case["case_id"]
        assert expected["cross_workspace_exposure"] is False, case["case_id"]
        error_code = expected["error_code"]
        if error_code is not None:
            assert error_code in errors, case["case_id"]
            assert expected["retryable"] == errors[error_code]["retryable"], case["case_id"]


def test_p301_scenarios_pin_core_stage_acceptance_outcomes() -> None:
    fixture = load_object(SCENARIO_PATH)
    cases = {case["case_id"]: case for case in fixture["cases"]}

    assert cases["p301-draft-runtime-denied-003"]["expected"]["error_code"] == (
        "AGENT_RUNTIME_RELEASE_REQUIRED"
    )
    assert cases["p301-functional-test-failed-004"]["expected"]["outcome"] == "denied"
    assert cases["p301-approval-missing-006"]["expected"]["outcome"] == "denied"
    assert cases["p301-control-plane-outage-010"]["expected"]["outcome"] == "continues"
    assert cases["p301-one-click-rollback-012"]["expected"]["route_behavior"] == ("rolled_back")
    assert cases["p301-hidden-menu-api-denied-014"]["expected"]["error_code"] == ("POLICY_DENIED")
    assert cases["p301-write-tool-denied-015"]["expected"]["error_code"] == (
        "AGENT_CONFIGURATION_INVALID"
    )


def test_p301_error_catalog_definitions_are_stable() -> None:
    catalog = load_object(ERROR_CATALOG_PATH)
    errors = {item["code"]: item for item in catalog["errors"]}

    for code, (status, retryable) in EXPECTED_ERROR_DEFINITIONS.items():
        assert errors[code]["http_status"] == status
        assert errors[code]["retryable"] is retryable


def test_p301_fixtures_contain_no_real_credentials() -> None:
    fixture_text = "\n".join(
        path.read_text(encoding="utf-8") for path in (ENTITY_FIXTURE_PATH, SCENARIO_PATH)
    )

    assert "PRIVATE KEY" not in fixture_text
    assert not any(pattern.search(fixture_text) for pattern in SECRET_PATTERNS)
