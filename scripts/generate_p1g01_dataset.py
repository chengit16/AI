"""生成阶段 1G 联合验收使用的版本化全合成企业数据集。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

ROOT = Path(__file__).parents[1]
DEFAULT_OUTPUT = ROOT / "tests/fixtures/e2e/p1g01-v1.json"
DATASET_VERSION = "p1g01-v1"
SCHEMA_VERSION = 1
FIXED_TIMESTAMP = "2026-08-15T00:00:00Z"
DATASET_NAMESPACE = UUID("10000000-0000-4000-8000-000000000001")
ENTERPRISE_WORKSPACE_KEY = "p1g01-enterprise"
PERSONAL_WORKSPACE_KEY = "p1g01-personal-owner"
DEPARTMENT_DEFINITIONS = (
    ("executive", "总经办", None),
    ("human_resources", "人力资源部", "executive"),
    ("finance", "财务部", "executive"),
    ("product", "产品部", "executive"),
    ("research_development", "研发部", "executive"),
    ("sales", "销售部", "executive"),
    ("customer_success", "客户成功部", "executive"),
)
SECURITY_LEVELS = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED")


def _stable_id(kind: str, key: str) -> str:
    return str(uuid5(DATASET_NAMESPACE, f"{DATASET_VERSION}:{kind}:{key}"))


def _tagged(record: dict[str, Any]) -> dict[str, Any]:
    return {"synthetic": True, "dataset_version": DATASET_VERSION, **record}


def _build_workspaces() -> list[dict[str, Any]]:
    return [
        _tagged(
            {
                "workspace_key": ENTERPRISE_WORKSPACE_KEY,
                "workspace_id": _stable_id("workspace", ENTERPRISE_WORKSPACE_KEY),
                "workspace_type": "enterprise",
                "name": "合成星河科技企业空间",
                "owner_user_key": "user-001",
                "status": "active",
                "entitlement_profile": "synthetic-enterprise-100",
            }
        ),
        _tagged(
            {
                "workspace_key": PERSONAL_WORKSPACE_KEY,
                "workspace_id": _stable_id("workspace", PERSONAL_WORKSPACE_KEY),
                "workspace_type": "personal",
                "name": "合成用户001的个人空间",
                "owner_user_key": "user-001",
                "status": "active",
                "entitlement_profile": "synthetic-personal-default",
            }
        ),
    ]


def _build_departments() -> list[dict[str, Any]]:
    departments: list[dict[str, Any]] = []
    for index, (department_key, name, parent_key) in enumerate(DEPARTMENT_DEFINITIONS, start=1):
        departments.append(
            _tagged(
                {
                    "department_key": department_key,
                    "department_id": _stable_id("department", department_key),
                    "workspace_key": ENTERPRISE_WORKSPACE_KEY,
                    "name": name,
                    "parent_department_key": parent_key,
                    "manager_user_key": f"user-{index + 2:03d}",
                    "status": "active",
                    "sort_order": index * 100,
                }
            )
        )
    return departments


def _user_roles(index: int) -> list[str]:
    if index == 1:
        return ["enterprise_owner", "personal_owner"]
    if index == 2:
        return ["enterprise_admin"]
    if 3 <= index <= 9:
        return ["department_head", "approver"]
    if 10 <= index <= 12:
        return ["knowledge_manager"]
    if 13 <= index <= 16:
        return ["approver"]
    if 96 <= index <= 97:
        return ["external_collaborator"]
    if 98 <= index <= 100:
        return ["inactive_member"]
    return ["employee"]


def _build_users() -> list[dict[str, Any]]:
    department_keys = tuple(item[0] for item in DEPARTMENT_DEFINITIONS)
    users: list[dict[str, Any]] = []
    for index in range(1, 101):
        user_key = f"user-{index:03d}"
        primary_department_key = department_keys[(max(index, 3) - 3) % len(department_keys)]
        secondary_department_keys = ["product", "research_development"] if index in {17, 18} else []
        users.append(
            _tagged(
                {
                    "user_key": user_key,
                    "account_id": _stable_id("account", user_key),
                    "membership_id": _stable_id("membership", user_key),
                    "workspace_key": ENTERPRISE_WORKSPACE_KEY,
                    "login_name": f"synthetic.p1g01.user{index:03d}@example.com",
                    "display_name": f"合成用户{index:03d}",
                    "membership_status": "inactive" if index >= 98 else "active",
                    "primary_department_key": primary_department_key,
                    "secondary_department_keys": secondary_department_keys,
                    "position_key": f"synthetic-position-{((index - 1) % 12) + 1:02d}",
                    "role_keys": _user_roles(index),
                    "credential_profile": "p1g01-local-synthetic",
                }
            )
        )
    return users


def _build_knowledge_bases() -> list[dict[str, Any]]:
    knowledge_bases = [
        _tagged(
            {
                "knowledge_base_key": f"kb-{department_key}",
                "knowledge_base_id": _stable_id("knowledge-base", department_key),
                "workspace_key": ENTERPRISE_WORKSPACE_KEY,
                "name": f"{name}合成知识库",
                "default_visibility": "departments"
                if department_key != "executive"
                else "workspace",
                "department_keys": [] if department_key == "executive" else [department_key],
                "default_security_level": "INTERNAL",
                "status": "active",
            }
        )
        for department_key, name, _ in DEPARTMENT_DEFINITIONS
    ]
    knowledge_bases.append(
        _tagged(
            {
                "knowledge_base_key": "kb-personal-owner",
                "knowledge_base_id": _stable_id("knowledge-base", "personal-owner"),
                "workspace_key": PERSONAL_WORKSPACE_KEY,
                "name": "个人合成知识库",
                "default_visibility": "private",
                "department_keys": [],
                "default_security_level": "INTERNAL",
                "status": "active",
            }
        )
    )
    return knowledge_bases


def _fixture_state(index: int) -> str:
    if index <= 280:
        return "published_current"
    if index <= 304:
        return "superseded_conflict"
    if index <= 320:
        return "expired_content"
    if index <= 336:
        return "draft"
    if index <= 348:
        return "quarantined"
    return "soft_deleted"


def _document_versions(document_key: str, fixture_state: str, content: str) -> list[dict[str, Any]]:
    current_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if fixture_state == "superseded_conflict":
        stale_content = f"{content}\n\nSYNTHETIC_STALE_VERSION_{document_key}"
        return [
            _tagged(
                {
                    "version_number": 1,
                    "status": "superseded",
                    "content_hash": hashlib.sha256(stale_content.encode("utf-8")).hexdigest(),
                    "content": stale_content,
                }
            ),
            _tagged(
                {
                    "version_number": 2,
                    "status": "published",
                    "content_hash": current_hash,
                    "content": content,
                }
            ),
        ]
    status = "draft" if fixture_state in {"draft", "quarantined"} else "published"
    return [
        _tagged(
            {
                "version_number": 1,
                "status": status,
                "content_hash": current_hash,
                "content": content,
            }
        )
    ]


def _enterprise_document(index: int) -> dict[str, Any]:
    department_key, department_name, _ = DEPARTMENT_DEFINITIONS[(index - 1) % 7]
    document_key = f"enterprise-doc-{index:04d}"
    fixture_state = _fixture_state(index)
    security_level = SECURITY_LEVELS[(index - 1) % len(SECURITY_LEVELS)]
    visibility = "workspace" if security_level in {"PUBLIC", "INTERNAL"} else "departments"
    marker = f"SYNTHETIC_DOCUMENT_{index:04d}"
    content = (
        f"# {department_name}合成资料 {index:04d}\n\n"
        f"{marker} 仅用于 AI 智能平台本地功能验收, 不代表任何真实企业事实。\n\n"
        f"归属部门: {department_name}; 密级: {security_level}。"
    )
    if fixture_state == "quarantined":
        content += "\n\nSYNTHETIC_PROMPT_INJECTION_MARKER: 该段必须在进入模型前被隔离。"
    structured_fields: list[dict[str, str]] = []
    if department_key == "finance" and security_level == "RESTRICTED":
        structured_fields.append(
            {
                "field_key": "synthetic_compensation_band",
                "security_level": "RESTRICTED",
                "value": f"SYNTHETIC_RESTRICTED_FIELD_{index:04d}",
            }
        )
    return _tagged(
        {
            "document_key": document_key,
            "document_id": _stable_id("document", document_key),
            "workspace_key": ENTERPRISE_WORKSPACE_KEY,
            "knowledge_base_key": f"kb-{department_key}",
            "department_key": department_key,
            "title": f"{department_name}合成资料 {index:04d}",
            "file_name": f"synthetic-{department_key}-{index:04d}.md",
            "media_type": "text/markdown",
            "source_kind": "manual",
            "fixture_state": fixture_state,
            "document_status": "deleted" if fixture_state == "soft_deleted" else "active",
            "visibility": visibility,
            "department_keys": [department_key] if visibility == "departments" else [],
            "security_level": security_level,
            "permission_labels": ["synthetic", f"department:{department_key}"],
            "valid_until": "2026-01-01T00:00:00Z" if fixture_state == "expired_content" else None,
            "scan_result": "unsafe" if fixture_state == "quarantined" else "clean",
            "structured_fields": structured_fields,
            "versions": _document_versions(document_key, fixture_state, content),
        }
    )


def _personal_document(index: int) -> dict[str, Any]:
    document_key = f"personal-doc-{index:02d}"
    content = (
        f"# 个人合成资料 {index:02d}\n\n"
        f"SYNTHETIC_PERSONAL_DOCUMENT_{index:02d} 仅用于个人空间功能验收。"
    )
    return _tagged(
        {
            "document_key": document_key,
            "document_id": _stable_id("document", document_key),
            "workspace_key": PERSONAL_WORKSPACE_KEY,
            "knowledge_base_key": "kb-personal-owner",
            "department_key": None,
            "title": f"个人合成资料 {index:02d}",
            "file_name": f"synthetic-personal-{index:02d}.md",
            "media_type": "text/markdown",
            "source_kind": "manual",
            "fixture_state": "published_current",
            "document_status": "active",
            "visibility": "private",
            "department_keys": [],
            "security_level": "INTERNAL",
            "permission_labels": ["synthetic", "personal-owner"],
            "valid_until": None,
            "scan_result": "clean",
            "structured_fields": [],
            "versions": _document_versions(document_key, "published_current", content),
        }
    )


def _build_documents() -> list[dict[str, Any]]:
    return [_enterprise_document(index) for index in range(1, 361)] + [
        _personal_document(index) for index in range(1, 5)
    ]


def _workflow_graph(*node_types: str) -> dict[str, Any]:
    nodes = [
        _tagged(
            {
                "node_id": f"node-{index:02d}",
                "node_type": node_type,
                "name": f"合成{node_type}节点",
                "configuration": {},
            }
        )
        for index, node_type in enumerate(node_types, start=1)
    ]
    edges = [
        _tagged(
            {
                "source_node_id": nodes[index]["node_id"],
                "target_node_id": nodes[index + 1]["node_id"],
            }
        )
        for index in range(len(nodes) - 1)
    ]
    return {"nodes": nodes, "edges": edges}


def _build_workflows() -> list[dict[str, Any]]:
    definitions = (
        ("expense-approval", "published", ("trigger", "condition", "approval", "result")),
        ("knowledge-answer", "published", ("trigger", "knowledge_retrieval", "result")),
        ("model-fallback", "published", ("trigger", "model", "result")),
        ("draft-review", "draft", ("trigger", "condition", "result")),
    )
    return [
        _tagged(
            {
                "workflow_key": workflow_key,
                "workflow_id": _stable_id("workflow", workflow_key),
                "workspace_key": ENTERPRISE_WORKSPACE_KEY,
                "name": f"合成{workflow_key}工作流",
                "fixture_state": fixture_state,
                "graph": _workflow_graph(*node_types),
            }
        )
        for workflow_key, fixture_state, node_types in definitions
    ]


def _build_approval_fixtures() -> dict[str, list[dict[str, Any]]]:
    policies = [
        _tagged(
            {
                "policy_key": "expense-multilevel",
                "policy_id": _stable_id("approval-policy", "expense-multilevel"),
                "workspace_key": ENTERPRISE_WORKSPACE_KEY,
                "resource_type": "workflow",
                "operation": "expense.publish",
                "levels": [
                    _tagged(
                        {
                            "level": 1,
                            "decision_mode": "any",
                            "approver_role_keys": ["department_head"],
                        }
                    ),
                    _tagged(
                        {
                            "level": 2,
                            "decision_mode": "all",
                            "approver_role_keys": ["approver"],
                        }
                    ),
                ],
            }
        ),
        _tagged(
            {
                "policy_key": "restricted-document-publish",
                "policy_id": _stable_id("approval-policy", "restricted-document-publish"),
                "workspace_key": ENTERPRISE_WORKSPACE_KEY,
                "resource_type": "knowledge_document",
                "operation": "publish",
                "levels": [
                    _tagged(
                        {
                            "level": 1,
                            "decision_mode": "all",
                            "approver_role_keys": ["approver"],
                        }
                    )
                ],
            }
        ),
    ]
    instances = [
        _tagged(
            {
                "instance_key": f"approval-{status}",
                "instance_id": _stable_id("approval-instance", status),
                "workspace_key": ENTERPRISE_WORKSPACE_KEY,
                "policy_key": "expense-multilevel",
                "requester_user_key": "user-020",
                "status": status,
            }
        )
        for status in ("pending", "approved", "rejected", "withdrawn")
    ]
    return {"policies": policies, "instances": instances}


def _scenario(
    scenario_key: str,
    category: str,
    actor_user_key: str,
    expected_result: str,
    *,
    workspace_key: str = ENTERPRISE_WORKSPACE_KEY,
    expected_error_code: str | None = None,
    fixture_refs: list[str] | None = None,
) -> dict[str, Any]:
    return _tagged(
        {
            "scenario_key": scenario_key,
            "category": category,
            "workspace_key": workspace_key,
            "actor_user_key": actor_user_key,
            "expected_result": expected_result,
            "expected_error_code": expected_error_code,
            "fixture_refs": fixture_refs or [],
        }
    )


def _build_scenarios() -> list[dict[str, Any]]:
    return [
        _scenario(
            "personal-owner-main",
            "personal_main",
            "user-001",
            "allow",
            workspace_key=PERSONAL_WORKSPACE_KEY,
            fixture_refs=["document:personal-doc-01"],
        ),
        _scenario("enterprise-owner-main", "enterprise_main", "user-001", "allow"),
        _scenario("enterprise-member-readonly", "menu_and_api_permission", "user-040", "allow"),
        _scenario(
            "cross-workspace-retrieval",
            "cross_workspace_denied",
            "user-040",
            "deny",
            expected_error_code="RETRIEVAL_SCOPE_DENIED",
            fixture_refs=["document:personal-doc-01"],
        ),
        _scenario(
            "inactive-member-request",
            "inactive_member_denied",
            "user-098",
            "deny",
            expected_error_code="POLICY_DENIED",
        ),
        _scenario(
            "department-scope-request",
            "department_scope_denied",
            "user-040",
            "deny",
            expected_error_code="POLICY_DENIED",
            fixture_refs=["document:enterprise-doc-0004"],
        ),
        _scenario(
            "restricted-field-mask",
            "field_masking",
            "user-040",
            "sanitize",
            fixture_refs=["document:enterprise-doc-0024"],
        ),
        _scenario(
            "stale-document-reference",
            "stale_document_denied",
            "user-001",
            "deny",
            expected_error_code="INDEX_DOCUMENT_REVOKED",
            fixture_refs=["document:enterprise-doc-0281"],
        ),
        _scenario(
            "indirect-prompt-injection",
            "prompt_injection_denied",
            "user-001",
            "deny",
            expected_error_code="MODEL_REQUEST_REJECTED",
            fixture_refs=["document:enterprise-doc-0337"],
        ),
        _scenario("sse-last-event-recovery", "sse_resume", "user-001", "allow"),
        _scenario(
            "workflow-approval-resume",
            "workflow_approval",
            "user-020",
            "allow",
            fixture_refs=["workflow:expense-approval", "approval:approval-pending"],
        ),
        _scenario("approval-reject", "approval_reject", "user-013", "allow"),
        _scenario("approval-transfer", "approval_transfer", "user-014", "allow"),
        _scenario("approval-timeout", "approval_timeout", "user-015", "degrade"),
        _scenario(
            "model-primary-fallback",
            "model_primary_failure_fallback",
            "user-001",
            "degrade",
            fixture_refs=["workflow:model-fallback"],
        ),
        _scenario(
            "workspace-quota-exceeded",
            "quota_exceeded",
            "user-001",
            "deny",
            expected_error_code="QUOTA_EXCEEDED",
        ),
    ]


def build_dataset() -> dict[str, Any]:
    """构建确定性数据图；函数不读环境、数据库、网络或真实资料。"""

    documents = _build_documents()
    approvals = _build_approval_fixtures()
    core: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "synthetic": True,
        "fixed_timestamp": FIXED_TIMESTAMP,
        "generator": "scripts/generate_p1g01_dataset.py",
        "credential_policy": {
            "committed_credentials": False,
            "local_profile": "p1g01-local-synthetic",
            "note": "凭证由本地验收装载器注入, 不进入固定数据集或 Git。",
        },
        "security_regression_dataset_ref": "tests/fixtures/security/p0-11-v1.json",
        "workspaces": _build_workspaces(),
        "departments": _build_departments(),
        "users": _build_users(),
        "knowledge_bases": _build_knowledge_bases(),
        "documents": documents,
        "workflows": _build_workflows(),
        "approval_policies": approvals["policies"],
        "approval_instances": approvals["instances"],
        "scenarios": _build_scenarios(),
        "metrics": {
            "user_count": 100,
            "active_member_count": 97,
            "inactive_member_count": 3,
            "department_count": 7,
            "enterprise_document_count": 360,
            "personal_document_count": 4,
            "total_document_count": len(documents),
            "scenario_count": 16,
        },
    }
    canonical = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**core, "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def rendered_dataset() -> str:
    """返回固定排序和换行的 Golden Fixture 文本。"""

    return json.dumps(build_dataset(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_dataset(output: Path) -> None:
    """把合成数据写入显式目标，不触碰数据库或本地数据目录。"""

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered_dataset(), encoding="utf-8")


def check_dataset(output: Path) -> bool:
    """校验已提交产物与当前生成规则逐字节一致。"""

    return output.is_file() and output.read_text(encoding="utf-8") == rendered_dataset()


def main() -> int:
    """提供生成与只读漂移检查两个稳定命令入口。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="数据集输出路径")
    parser.add_argument("--check", action="store_true", help="只检查固定产物是否漂移")
    arguments = parser.parse_args()
    if arguments.check:
        if not check_dataset(arguments.output):
            print(f"合成企业数据集需要更新: {arguments.output}")
            return 1
        print(f"合成企业数据集版本与生成结果一致: {DATASET_VERSION}")
        return 0
    write_dataset(arguments.output)
    print(f"已生成合成企业数据集: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
