"""校验 P4-01 工具执行契约、安全不变量和全合成场景。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from scripts.check_repository_policy import SECRET_PATTERNS

ROOT = Path(__file__).parents[1]
CONTRACT_DIR = ROOT / "contracts" / "tool-execution"
ENTITY_SCHEMA_PATH = CONTRACT_DIR / "tool-execution.v1.schema.json"
ENTITY_FIXTURE_PATH = ROOT / "contracts" / "fixtures" / "tool-execution.v1.valid.json"
BASELINE_SCHEMA_PATH = CONTRACT_DIR / "tool-execution-baseline.v1.schema.json"
BASELINE_PATH = CONTRACT_DIR / "tool-execution-baseline.v1.json"
SCENARIO_SCHEMA_PATH = CONTRACT_DIR / "tool-execution-scenarios.v1.schema.json"
SCENARIO_PATH = ROOT / "tests" / "fixtures" / "tool-execution" / "p4-01-v1.json"
ERROR_CATALOG_PATH = ROOT / "contracts" / "errors" / "catalog.v1.json"
RESOURCE_REGISTRY_PATH = ROOT / "contracts" / "authorization" / "resource-registry.v1.json"

EXPECTED_TOOLS = {
    "knowledge.search": "knowledge.document.read",
    "document.read_authorized_range": "knowledge.document.read",
    "workflow.get_status": "workflow.run.read",
    "approval.get_status": "approval.instance.read",
    "quota.get_usage": "workspace.entitlement.read",
}
EXPECTED_INVARIANTS = {
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
EXPECTED_CATEGORIES = {
    "personal_read",
    "enterprise_read",
    "release_boundary",
    "registry_version",
    "workspace_isolation",
    "authorization_revocation",
    "confirmation",
    "approval",
    "idempotency",
    "cancellation",
    "late_result",
    "credential_boundary",
    "result_security",
    "adapter_boundary",
    "retry_boundary",
    "menu_authorization",
}
EXPECTED_ERROR_DEFINITIONS = {
    "TOOL_DEFINITION_INVALID": (422, False),
    "TOOL_VERSION_NOT_AVAILABLE": (409, False),
    "TOOL_EXECUTION_DENIED": (403, False),
    "TOOL_CONFIRMATION_REQUIRED": (409, False),
    "TOOL_CONFIRMATION_STALE": (409, False),
    "TOOL_RUN_CONFLICT": (409, True),
    "TOOL_RUN_TERMINAL": (409, False),
    "TOOL_RUN_BUDGET_EXCEEDED": (409, False),
    "TOOL_RETRY_NOT_ALLOWED": (409, False),
    "TOOL_ADAPTER_NOT_ALLOWED": (403, False),
    "TOOL_ADAPTER_UNAVAILABLE": (503, True),
    "TOOL_CREDENTIAL_UNAVAILABLE": (409, False),
    "TOOL_CREDENTIAL_EXPOSURE_DETECTED": (500, False),
    "TOOL_RESULT_REJECTED": (422, False),
    "TOOL_OUTCOME_UNKNOWN": (503, False),
}


def load_object(path: Path) -> dict[str, Any]:
    """读取对象型 JSON，避免契约测试接受错误顶层类型。"""

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def validator(path: Path) -> Draft202012Validator:
    """构造启用格式检查的 Draft 2020-12 校验器。"""

    schema = load_object(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_p401_documents_match_versioned_json_schemas() -> None:
    documents = (
        (ENTITY_SCHEMA_PATH, ENTITY_FIXTURE_PATH),
        (BASELINE_SCHEMA_PATH, BASELINE_PATH),
        (SCENARIO_SCHEMA_PATH, SCENARIO_PATH),
    )

    for schema_path, document_path in documents:
        validator(schema_path).validate(load_object(document_path))


def test_p401_entity_fixture_has_consistent_execution_identity() -> None:
    fixture = load_object(ENTITY_FIXTURE_PATH)
    run = fixture["run"]
    step = fixture["step"]
    attempt = fixture["attempt"]
    call = fixture["tool_call"]
    tool = fixture["tool_version"]

    assert {run["run_id"], step["run_id"], attempt["run_id"], call["run_id"]} == {run["run_id"]}
    workspace_ids = {
        run["workspace_id"],
        step["workspace_id"],
        attempt["workspace_id"],
        call["workspace_id"],
    }
    assert workspace_ids == {run["workspace_id"]}
    assert step["step_id"] == attempt["step_id"] == call["step_id"]
    assert attempt["attempt_id"] == call["attempt_id"]
    assert (step["tool_id"], step["tool_version"]) == (tool["tool_id"], tool["version"])
    assert (call["tool_id"], call["tool_version"]) == (tool["tool_id"], tool["version"])
    assert step["canonical_arguments_hash"] == call["canonical_arguments_hash"]


def test_p401_fixture_binds_confirmation_idempotency_and_safe_result() -> None:
    fixture = load_object(ENTITY_FIXTURE_PATH)
    call = fixture["tool_call"]
    confirmation = fixture["confirmation"]
    idempotency = fixture["idempotency_record"]
    result = fixture["result"]

    assert confirmation["tool_call_id"] == idempotency["tool_call_id"] == call["tool_call_id"]
    assert result["tool_call_id"] == call["tool_call_id"]
    assert confirmation["mode"] == "enterprise_approval"
    assert confirmation["state"] == "approved"
    assert idempotency["state"] == "succeeded"
    assert idempotency["result_hash"] is not None
    assert result["status"] == "accepted"
    assert {item["check_code"] for item in result["safety_checks"]} == {
        "schema",
        "size",
        "sensitive_fields",
        "prompt_injection",
    }
    assert all(item["status"] == "passed" for item in result["safety_checks"])
    assert result["eligible_for_model_context"] is True
    assert result["credential_exposure_detected"] is False


def test_p401_baseline_freezes_exact_tools_and_adapter_boundary() -> None:
    baseline = load_object(BASELINE_PATH)
    tools = {item["tool_key"]: item for item in baseline["initial_tool_catalog"]}

    assert baseline["baseline_id"] == "p4-01-v1"
    assert baseline["status"] == "frozen"
    assert set(tools) == set(EXPECTED_TOOLS)
    assert {key: item["permission_code"] for key, item in tools.items()} == EXPECTED_TOOLS
    assert all(item["version"] == 1 for item in tools.values())
    assert all(item["access_mode"] == "read" for item in tools.values())
    assert all(item["adapter_kind"] == "internal_read" for item in tools.values())
    assert all(item["credential_requirement"] == "none" for item in tools.values())
    assert set(baseline["allowed_adapter_kinds"]) == {
        "internal_read",
        "synthetic_internal_write",
    }


def test_p401_state_machines_freeze_terminal_precedence() -> None:
    baseline = load_object(BASELINE_PATH)
    machines = {item["entity"]: item for item in baseline["state_machines"]}

    assert set(machines) == {"run", "step", "attempt", "tool_call", "confirmation"}
    for machine in machines.values():
        terminal_states = set(machine["terminal_states"])
        assert terminal_states.issubset(set(machine["states"]))
        assert not terminal_states.intersection(
            transition["from"] for transition in machine["transitions"]
        )
    assert "ignored_late_result" in machines["attempt"]["terminal_states"]
    assert {"cancelled", "timed_out"}.issubset(machines["run"]["terminal_states"])


def test_p401_baseline_freezes_confirmation_and_idempotency_protocols() -> None:
    baseline = load_object(BASELINE_PATH)
    confirmation = baseline["confirmation_policy"]
    idempotency = baseline["idempotency_policy"]
    cancellation = baseline["cancellation_policy"]

    assert confirmation["personal_mode"] == "workspace_owner_confirmation"
    assert confirmation["enterprise_mode"] == "versioned_multilevel_approval"
    assert confirmation["maximum_enterprise_levels"] == 5
    assert {"canonical_arguments_hash", "policy_version"}.issubset(confirmation["binding_fields"])
    assert {"arguments_changed", "policy_changed", "permission_revoked", "expired"}.issubset(
        confirmation["invalidation_triggers"]
    )
    assert idempotency["reservation_order"] == "persist_before_side_effect"
    assert idempotency["same_key_same_request"] == "return_recorded_result"
    assert idempotency["same_key_different_request"] == "reject_with_idempotency_conflict"
    assert idempotency["unknown_outcome_behavior"] == ("manual_recovery_without_automatic_replay")
    assert cancellation["new_attempts_after_cancel"] == "forbidden"
    assert cancellation["late_result_behavior"] == ("audit_only_never_overwrite_terminal_state")


def test_p401_baseline_freezes_all_stage_invariants_and_deferred_scope() -> None:
    baseline = load_object(BASELINE_PATH)
    invariants = {item["invariant_id"] for item in baseline["invariants"]}
    deferred = set(baseline["deferred_capabilities"])

    assert invariants == EXPECTED_INVARIANTS
    assert {
        "saas",
        "go_runtime",
        "real_data_source_connectors",
        "llm_grading",
        "multimodal_image_qa",
        "channel_gateway",
        "durable_run",
        "arbitrary_http",
        "arbitrary_sql",
        "filesystem_access",
        "real_external_tools",
    }.issubset(deferred)


def test_p401_browser_identifiers_activate_while_internal_operations_stay_closed() -> None:
    baseline = load_object(BASELINE_PATH)
    registry = load_object(RESOURCE_REGISTRY_PATH)
    permissions = cast(list[str], baseline["reserved_permissions"])
    menus = cast(list[dict[str, Any]], baseline["reserved_menus"])
    operations = cast(list[dict[str, Any]], baseline["planned_api_operations"])
    active_permissions = {item["code"] for item in registry["permissions"]}
    active_menu_keys = {item["menu_key"] for item in registry["menus"]}
    active_operations = {item["operation_id"] for item in registry["api_resources"]}

    assert len(permissions) == len(set(permissions))
    assert len(menus) == len({item["menu_key"] for item in menus})
    assert len(operations) == len({item["operation_id"] for item in operations})
    assert {item["permission_code"] for item in menus}.issubset(set(permissions))
    assert {item["permission_code"] for item in operations}.issubset(set(permissions))
    assert all(set(item["workspace_types"]) == {"personal", "enterprise"} for item in menus)
    assert set(permissions).issubset(active_permissions)
    assert {item["menu_key"] for item in menus}.issubset(active_menu_keys)
    browser_operations = {
        item["operation_id"] for item in operations if item["surface"] == "browser"
    }
    internal_operations = {
        item["operation_id"] for item in operations if item["surface"] != "browser"
    }
    assert browser_operations.issubset(active_operations)
    assert internal_operations.isdisjoint(active_operations)


def test_p401_initial_tool_permissions_exist_in_active_registry() -> None:
    baseline = load_object(BASELINE_PATH)
    registry = load_object(RESOURCE_REGISTRY_PATH)
    active_permissions = {item["code"] for item in registry["permissions"]}
    tool_permissions = {item["permission_code"] for item in baseline["initial_tool_catalog"]}

    assert tool_permissions.issubset(active_permissions)


def test_p401_scenarios_cover_categories_workspaces_and_security_outcomes() -> None:
    fixture = load_object(SCENARIO_PATH)
    cases = cast(list[dict[str, Any]], fixture["cases"])
    case_ids = [case["case_id"] for case in cases]

    assert fixture["dataset_version"] == "p4-01-v1"
    assert fixture["synthetic"] is True
    assert set(fixture["categories"]) == EXPECTED_CATEGORIES
    assert {case["category"] for case in cases} == EXPECTED_CATEGORIES
    assert {case["workspace_type"] for case in cases} == {"personal", "enterprise"}
    assert len(cases) == 24
    assert len(case_ids) == len(set(case_ids))
    assert all(case["synthetic"] is True for case in cases)
    assert all(case["expected"]["duplicate_side_effects"] == 0 for case in cases)
    assert all(case["expected"]["cross_workspace_exposure"] is False for case in cases)
    assert all(case["expected"]["credential_exposure"] is False for case in cases)
    assert all(case["expected"]["late_result_overwrite"] is False for case in cases)


def test_p401_scenarios_reference_known_invariants_and_error_codes() -> None:
    baseline = load_object(BASELINE_PATH)
    fixture = load_object(SCENARIO_PATH)
    catalog = load_object(ERROR_CATALOG_PATH)
    invariants = {item["invariant_id"] for item in baseline["invariants"]}
    errors = {item["code"]: item for item in catalog["errors"]}

    for case in fixture["cases"]:
        expected = case["expected"]
        assert set(expected["verified_invariants"]).issubset(invariants), case["case_id"]
        error_code = expected["error_code"]
        if error_code is not None:
            assert error_code in errors, case["case_id"]
            assert expected["retryable"] == errors[error_code]["retryable"], case["case_id"]


def test_p401_scenarios_pin_critical_stage_outcomes() -> None:
    fixture = load_object(SCENARIO_PATH)
    cases = {case["case_id"]: case["expected"] for case in fixture["cases"]}

    assert cases["p401-personal-read-allowed-001"]["outcome"] == "allowed"
    assert cases["p401-enterprise-read-allowed-002"]["outcome"] == "allowed"
    assert cases["p401-draft-tool-denied-003"]["adapter_invocations"] == 0
    assert cases["p401-personal-confirmation-required-009"]["committed_side_effects"] == 0
    assert cases["p401-enterprise-write-approved-013"]["committed_side_effects"] == 1
    assert cases["p401-idempotent-replay-014"]["adapter_invocations"] == 0
    assert cases["p401-outcome-unknown-manual-016"]["outcome"] == "manual_recovery"
    assert cases["p401-late-success-ignored-019"]["late_result_overwrite"] is False
    assert cases["p401-credential-result-leak-020"]["model_context_eligible"] is False
    assert cases["p401-arbitrary-http-denied-022"]["adapter_invocations"] == 0
    assert cases["p401-hidden-menu-api-denied-024"]["error_code"] == "POLICY_DENIED"


def test_p401_error_catalog_definitions_are_stable() -> None:
    catalog = load_object(ERROR_CATALOG_PATH)
    errors = {item["code"]: item for item in catalog["errors"]}

    for code, (status, retryable) in EXPECTED_ERROR_DEFINITIONS.items():
        assert errors[code]["http_status"] == status
        assert errors[code]["retryable"] is retryable


@pytest.mark.parametrize(
    ("mutation", "value"),
    (
        (("tool_version", "adapter_kind"), "arbitrary_http"),
        (("tool_version", "immutable"), False),
        (("tool_call", "credential_ref"), "plain-text-secret"),
        (("result", "credential_exposure_detected"), True),
    ),
)
def test_p401_entity_schema_rejects_unsafe_execution_fields(
    mutation: tuple[str, str], value: object
) -> None:
    fixture = deepcopy(load_object(ENTITY_FIXTURE_PATH))
    fixture[mutation[0]][mutation[1]] = value

    with pytest.raises(ValidationError):
        validator(ENTITY_SCHEMA_PATH).validate(fixture)


def test_p401_fixtures_contain_no_real_credentials_or_payload_bodies() -> None:
    paths = (ENTITY_FIXTURE_PATH, BASELINE_PATH, SCENARIO_PATH)
    fixture_text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    entity = load_object(ENTITY_FIXTURE_PATH)

    assert "PRIVATE KEY" not in fixture_text
    assert not any(pattern.search(fixture_text) for pattern in SECRET_PATTERNS)
    assert "arguments" not in entity["tool_call"]
    assert "content" not in entity["result"]
    assert entity["tool_call"]["credential_ref"] is None
    assert set(entity["tool_call"]) == {
        "tool_call_id",
        "run_id",
        "step_id",
        "attempt_id",
        "workspace_id",
        "tool_id",
        "tool_version",
        "canonical_arguments_hash",
        "access_mode",
        "risk_level",
        "credential_ref",
        "state",
        "created_at",
    }
