"""验证 P1G-01 全合成企业数据集的规模、边界和可重复性。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, cast

from scripts.check_repository_policy import SECRET_PATTERNS
from scripts.generate_p1g01_dataset import DATASET_VERSION, build_dataset, rendered_dataset

ROOT = Path(__file__).parents[1]
FIXTURE_PATH = ROOT / "tests/fixtures/e2e/p1g01-v1.json"
ERROR_CATALOG_PATH = ROOT / "contracts/errors/catalog.v1.json"
ENTITY_COLLECTIONS = (
    "workspaces",
    "departments",
    "users",
    "knowledge_bases",
    "documents",
    "workflows",
    "approval_policies",
    "approval_instances",
    "scenarios",
)
EXPECTED_ROLE_KEYS = {
    "enterprise_owner",
    "enterprise_admin",
    "department_head",
    "employee",
    "knowledge_manager",
    "approver",
    "external_collaborator",
    "inactive_member",
    "personal_owner",
}
EXPECTED_DOCUMENT_STATES = {
    "published_current",
    "superseded_conflict",
    "expired_content",
    "draft",
    "quarantined",
    "soft_deleted",
}
EXPECTED_SCENARIO_CATEGORIES = {
    "personal_main",
    "enterprise_main",
    "menu_and_api_permission",
    "cross_workspace_denied",
    "inactive_member_denied",
    "department_scope_denied",
    "field_masking",
    "stale_document_denied",
    "prompt_injection_denied",
    "sse_resume",
    "workflow_approval",
    "approval_reject",
    "approval_transfer",
    "approval_timeout",
    "model_primary_failure_fallback",
    "quota_exceeded",
}


def load_object(path: Path) -> dict[str, Any]:
    """读取对象型 JSON Fixture，拒绝静默接受错误顶层结构。"""

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"Fixture 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def test_p1g01_fixture_is_deterministic_and_has_valid_digest() -> None:
    fixture = load_object(FIXTURE_PATH)
    generated = build_dataset()
    content_sha256 = fixture.pop("content_sha256")
    canonical = json.dumps(fixture, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    assert FIXTURE_PATH.read_text(encoding="utf-8") == rendered_dataset()
    assert generated["content_sha256"] == content_sha256
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == content_sha256


def test_p1g01_fixture_has_required_scale_and_synthetic_markers() -> None:
    fixture = load_object(FIXTURE_PATH)
    metrics = fixture["metrics"]

    assert fixture["schema_version"] == 1
    assert fixture["dataset_version"] == DATASET_VERSION
    assert fixture["synthetic"] is True
    assert metrics == {
        "active_member_count": 97,
        "department_count": 7,
        "enterprise_document_count": 360,
        "inactive_member_count": 3,
        "personal_document_count": 4,
        "scenario_count": 16,
        "total_document_count": 364,
        "user_count": 100,
    }
    for collection_name in ENTITY_COLLECTIONS:
        records = cast(list[dict[str, Any]], fixture[collection_name])
        assert records
        assert all(record["synthetic"] is True for record in records)
        assert all(record["dataset_version"] == DATASET_VERSION for record in records)


def test_p1g01_users_and_department_tree_cover_enterprise_roles() -> None:
    fixture = load_object(FIXTURE_PATH)
    users = cast(list[dict[str, Any]], fixture["users"])
    departments = cast(list[dict[str, Any]], fixture["departments"])
    department_keys = {item["department_key"] for item in departments}
    role_keys = {role_key for user in users for role_key in user["role_keys"]}

    assert len(users) == 100
    assert len(departments) == 7
    assert sum(user["membership_status"] == "inactive" for user in users) == 3
    assert role_keys == EXPECTED_ROLE_KEYS
    assert sum(item["parent_department_key"] is None for item in departments) == 1
    assert all(
        item["parent_department_key"] is None or item["parent_department_key"] in department_keys
        for item in departments
    )
    assert all(user["primary_department_key"] in department_keys for user in users)
    assert all(
        re.fullmatch(r"synthetic\.p1g01\.user\d{3}@example\.com", user["login_name"])
        for user in users
    )
    assert all(user["display_name"].startswith("合成用户") for user in users)


def test_p1g01_entity_keys_and_relationships_are_unambiguous() -> None:
    fixture = load_object(FIXTURE_PATH)
    workspace_keys = {item["workspace_key"] for item in fixture["workspaces"]}
    department_keys = {item["department_key"] for item in fixture["departments"]}
    user_keys = {item["user_key"] for item in fixture["users"]}
    knowledge_bases = {item["knowledge_base_key"]: item for item in fixture["knowledge_bases"]}
    document_keys = [item["document_key"] for item in fixture["documents"]]

    assert workspace_keys == {"p1g01-enterprise", "p1g01-personal-owner"}
    assert len(department_keys) == len(fixture["departments"])
    assert len(user_keys) == len(fixture["users"])
    assert len(document_keys) == len(set(document_keys))
    assert all(item["manager_user_key"] in user_keys for item in fixture["departments"])
    assert all(item["workspace_key"] in workspace_keys for item in knowledge_bases.values())
    for document in fixture["documents"]:
        knowledge_base = knowledge_bases[document["knowledge_base_key"]]
        assert knowledge_base["workspace_key"] == document["workspace_key"]
        assert document["department_key"] is None or document["department_key"] in department_keys


def test_p1g01_documents_cover_permissions_versions_and_exception_states() -> None:
    fixture = load_object(FIXTURE_PATH)
    documents = cast(list[dict[str, Any]], fixture["documents"])
    enterprise_documents = [
        document for document in documents if document["workspace_key"] == "p1g01-enterprise"
    ]
    states = Counter(document["fixture_state"] for document in enterprise_documents)
    levels = {document["security_level"] for document in enterprise_documents}

    assert len(enterprise_documents) == 360
    assert 300 <= len(documents) <= 500
    assert set(states) == EXPECTED_DOCUMENT_STATES
    assert states["published_current"] == 280
    assert states["superseded_conflict"] == 24
    assert states["quarantined"] == 12
    assert levels == {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"}
    assert all(document["source_kind"] == "manual" for document in documents)
    assert all(document["media_type"] == "text/markdown" for document in documents)
    assert any(document["structured_fields"] for document in enterprise_documents)
    assert all(
        version["synthetic"] is True and version["dataset_version"] == DATASET_VERSION
        for document in documents
        for version in document["versions"]
    )
    assert all(
        len(document["versions"]) == 2
        for document in enterprise_documents
        if document["fixture_state"] == "superseded_conflict"
    )


def test_p1g01_scenarios_cover_main_flows_and_stable_exception_codes() -> None:
    fixture = load_object(FIXTURE_PATH)
    scenarios = cast(list[dict[str, Any]], fixture["scenarios"])
    catalog = load_object(ERROR_CATALOG_PATH)
    error_codes = {entry["code"] for entry in catalog["errors"]}
    categories = {scenario["category"] for scenario in scenarios}

    assert categories == EXPECTED_SCENARIO_CATEGORIES
    assert {scenario["expected_result"] for scenario in scenarios} == {
        "allow",
        "degrade",
        "deny",
        "sanitize",
    }
    for scenario in scenarios:
        error_code = scenario["expected_error_code"]
        if scenario["expected_result"] == "deny":
            assert error_code in error_codes, scenario["scenario_key"]
        else:
            assert error_code is None, scenario["scenario_key"]


def test_p1g01_authorization_scenarios_reference_the_intended_boundaries() -> None:
    fixture = load_object(FIXTURE_PATH)
    users = {item["user_key"]: item for item in fixture["users"]}
    documents = {item["document_key"]: item for item in fixture["documents"]}
    scenarios = {item["scenario_key"]: item for item in fixture["scenarios"]}
    cross_workspace = scenarios["cross-workspace-retrieval"]
    department_scope = scenarios["department-scope-request"]
    field_mask = scenarios["restricted-field-mask"]

    cross_document = documents[cross_workspace["fixture_refs"][0].removeprefix("document:")]
    assert cross_document["workspace_key"] != cross_workspace["workspace_key"]

    scoped_document = documents[department_scope["fixture_refs"][0].removeprefix("document:")]
    scoped_actor = users[department_scope["actor_user_key"]]
    actor_departments = {
        scoped_actor["primary_department_key"],
        *scoped_actor["secondary_department_keys"],
    }
    assert scoped_document["visibility"] == "departments"
    assert scoped_document["department_key"] not in actor_departments

    masked_document = documents[field_mask["fixture_refs"][0].removeprefix("document:")]
    assert masked_document["security_level"] == "RESTRICTED"
    assert masked_document["structured_fields"]


def test_p1g01_references_are_resolvable_and_credentials_are_not_committed() -> None:
    fixture = load_object(FIXTURE_PATH)
    documents = {item["document_key"] for item in fixture["documents"]}
    workflows = {item["workflow_key"] for item in fixture["workflows"]}
    approvals = {item["instance_key"] for item in fixture["approval_instances"]}
    allowed_refs = {
        *(f"document:{key}" for key in documents),
        *(f"workflow:{key}" for key in workflows),
        *(f"approval:{key}" for key in approvals),
    }

    assert fixture["credential_policy"]["committed_credentials"] is False
    assert fixture["security_regression_dataset_ref"] == "tests/fixtures/security/p0-11-v1.json"
    assert all(
        reference in allowed_refs
        for scenario in fixture["scenarios"]
        for reference in scenario["fixture_refs"]
    )
    assert all(
        item["synthetic"] is True and item["dataset_version"] == DATASET_VERSION
        for workflow in fixture["workflows"]
        for item in (*workflow["graph"]["nodes"], *workflow["graph"]["edges"])
    )
    assert all(
        level["synthetic"] is True and level["dataset_version"] == DATASET_VERSION
        for policy in fixture["approval_policies"]
        for level in policy["levels"]
    )
    fixture_text = FIXTURE_PATH.read_text(encoding="utf-8")
    assert not any(pattern.search(fixture_text) for pattern in SECRET_PATTERNS)
    assert "PRIVATE KEY" not in fixture_text
    assert '"phone"' not in fixture_text
    assert '"id_card"' not in fixture_text
