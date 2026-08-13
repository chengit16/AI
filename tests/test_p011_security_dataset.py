"""校验阶段 0 全合成 AI/RAG 安全评估 Fixture。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

from scripts.check_repository_policy import SECRET_PATTERNS

ROOT = Path(__file__).parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "security" / "p0-11-v1.json"
ERROR_CATALOG_PATH = ROOT / "contracts" / "errors" / "catalog.v1.json"
EXPECTED_CATEGORIES = {
    "direct_prompt_injection",
    "indirect_prompt_injection",
    "cross_workspace_retrieval",
    "revoked_or_stale_reference",
    "field_level_leakage",
    "citation_spoofing",
    "obfuscation_bypass",
    "query_rewrite_scope",
    "sse_replay_budget",
    "model_output_authorization",
}
EXPECTED_RESULTS = {"allow", "deny", "sanitize", "degrade"}
SYNTHETIC_ID_PATTERN = re.compile(
    r"^(?:ws|doc|ver|idx|chunk|conv|run|evt|policy|hash)-synthetic-[a-z0-9-]+$"
)


def load_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"Fixture 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def test_p011_fixture_has_expected_version_and_category_coverage() -> None:
    fixture = load_object(FIXTURE_PATH)

    assert fixture["dataset_version"] == "p0-11-v1"
    assert fixture["synthetic"] is True
    assert set(fixture["categories"]) == EXPECTED_CATEGORIES
    cases = fixture["cases"]
    assert isinstance(cases, list)
    assert len(cases) >= len(EXPECTED_CATEGORIES)
    assert {case["category"] for case in cases} == EXPECTED_CATEGORIES


def test_p011_cases_are_synthetic_and_case_ids_are_unique() -> None:
    fixture = load_object(FIXTURE_PATH)
    cases = cast(list[dict[str, Any]], fixture["cases"])
    case_ids = [case["case_id"] for case in cases]

    assert len(case_ids) == len(set(case_ids))
    assert all(case["synthetic"] is True for case in cases)
    assert all(case_id.startswith("p011-") for case_id in case_ids)
    assert all(case["forbidden_markers"] for case in cases)
    assert all(case["expected_security_result"] in EXPECTED_RESULTS for case in cases)


def test_p011_expected_errors_match_stable_error_catalog() -> None:
    fixture = load_object(FIXTURE_PATH)
    catalog = load_object(ERROR_CATALOG_PATH)
    errors = {entry["code"]: entry for entry in catalog["errors"]}

    for case in cast(list[dict[str, Any]], fixture["cases"]):
        error_code = case["expected_error_code"]
        assert error_code in errors, case["case_id"]
        assert case["expected_status"] == errors[error_code]["http_status"], case["case_id"]


def test_p011_cases_declare_scope_and_recovery_guards() -> None:
    fixture = load_object(FIXTURE_PATH)

    for case in cast(list[dict[str, Any]], fixture["cases"]):
        assert case["workspace_id"]
        assert case["policy_version"]
        assert set(case["expected_scope"]) == {"workspace_id", "resource_ids", "field_mask"}
        assert case["expected_scope"]["workspace_id"] == case["workspace_id"]
        assert isinstance(case["expected_field_mask"], list)
        assert isinstance(case["must_not_reach_model"], bool)
        assert isinstance(case["must_not_create_run"], bool)
        assert isinstance(case["must_not_replay"], bool)


def test_p011_contains_no_real_credential_patterns_or_non_synthetic_identifiers() -> None:
    fixture_text = FIXTURE_PATH.read_text(encoding="utf-8")

    assert not any(pattern.search(fixture_text) for pattern in SECRET_PATTERNS)
    assert "PRIVATE KEY" not in fixture_text

    fixture = load_object(FIXTURE_PATH)
    serialized = json.dumps(fixture, ensure_ascii=False)
    identifiers = re.findall(
        r"\b(?:ws|doc|ver|idx|chunk|conv|run|evt|policy|hash)-[a-z0-9-]+\b", serialized
    )
    assert identifiers
    assert all(SYNTHETIC_ID_PATTERN.fullmatch(identifier) for identifier in identifiers)


def test_p011_dangerous_categories_require_deny_result() -> None:
    fixture = load_object(FIXTURE_PATH)
    cases = cast(list[dict[str, Any]], fixture["cases"])

    for case in cases:
        assert case["expected_security_result"] == "deny", case["case_id"]
        if case["category"] in {
            "direct_prompt_injection",
            "indirect_prompt_injection",
            "cross_workspace_retrieval",
            "field_level_leakage",
            "obfuscation_bypass",
            "query_rewrite_scope",
        }:
            assert case["must_not_reach_model"] is True, case["case_id"]
