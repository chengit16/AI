"""验证 P2-08 生命周期分类和确定性导出包契约。"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from ai_platform_api.modules.lifecycle.application.bundle import build_workspace_export_bundle
from ai_platform_api.modules.lifecycle.domain.models import ExportObject
from ai_platform_api.modules.lifecycle.domain.registry import load_workspace_table_registry

ROOT = Path(__file__).parents[1]
REGISTRY_PATH = ROOT / "contracts/lifecycle/workspace-table-registry.v1.json"
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000208")
EXPORT_ID = UUID("70000000-0000-4000-8000-000000000208")
CREATED_AT = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)


def test_registry_classifies_credentials_and_dependent_facts() -> None:
    registry = load_workspace_table_registry(REGISTRY_PATH)
    policies = {item.table: item for item in registry.tables}
    dependent = {item.table: item for item in registry.dependent_tables}

    assert registry.schema_version == 1
    assert registry.registry_version == 14
    assert policies["workspaces"].classification == "governance"
    assert policies["workspaces"].purge is False
    assert policies["workspace_resources"].classification == "business"
    assert policies["workspace_resources"].purge is True
    assert policies["audit_records"].classification == "retained"
    assert policies["open_api_keys"].excluded_columns == frozenset({"secret_digest"})
    assert policies["workspace_invitations"].excluded_columns == frozenset()
    assert policies["tool_policy_decisions"].classification == "business"
    assert policies["tool_policy_decisions"].purge is True
    assert policies["tool_confirmations"].classification == "business"
    assert policies["tool_confirmation_invalidations"].purge is True
    assert policies["tool_credentials"].classification == "business"
    assert policies["tool_credentials"].purge is True
    assert policies["tool_credentials"].excluded_columns == frozenset(
        {"encrypted_data_key", "data_key_nonce", "ciphertext", "data_nonce", "last_four"}
    )
    assert policies["tool_idempotency_records"].classification == "business"
    assert policies["tool_idempotency_records"].purge is True
    assert policies["synthetic_tool_side_effects"].classification == "business"
    assert policies["synthetic_tool_side_effects"].purge is True
    assert policies["tool_safe_results"].purge is True
    assert policies["tool_usage_records"].purge is True
    assert policies["tool_progress_events"].purge is True
    assert policies["quality_sample_versions"].classification == "business"
    assert policies["quality_sample_versions"].export is True
    assert policies["quality_sample_versions"].purge is True
    assert policies["quality_dataset_versions"].purge is True
    assert policies["quality_dataset_members"].purge is True
    assert policies["quality_evaluation_runs"].purge is True
    assert policies["quality_evaluation_layer_results"].purge is True
    assert policies["quality_evaluation_sample_results"].purge is True
    assert policies["quality_operation_windows"].purge is True
    assert policies["quality_operation_source_results"].purge is True
    assert policies["workspace_isolation_policy_versions"].classification == "governance"
    assert policies["workspace_isolation_policy_versions"].purge is False
    assert policies["workspace_isolation_migration_plans"].purge is False
    assert policies["workspace_isolation_route_versions"].purge is False
    assert dependent["consumer_receipts"].parent_table == "outbox_events"
    assert dependent["retrieval_query_variants"].parent_table == "retrieval_plans"
    assert dependent["retrieval_candidate_snapshots"].parent_table == "retrieval_plans"
    assert dependent["retrieval_evidence_items"].parent_table == "retrieval_evidence_sets"


def test_workspace_export_bundle_is_deterministic_and_fully_checkable() -> None:
    rows: dict[str, tuple[dict[str, object], ...]] = {
        "open_api_keys": (
            {
                "key_id": "synthetic-key",
                "last_four": "0208",
            },
        ),
        "workspace_resources": (
            {
                "resource_id": "synthetic-resource",
                "title": "合成生命周期资源",
            },
        ),
    }
    objects = (
        ExportObject(
            object_key=f"workspaces/{WORKSPACE_ID}/uploads/synthetic.md",
            content=b"synthetic-p208-object",
            sha256=hashlib.sha256(b"synthetic-p208-object").hexdigest(),
        ),
    )

    first = build_workspace_export_bundle(
        workspace_id=WORKSPACE_ID,
        export_id=EXPORT_ID,
        created_at=CREATED_AT,
        registry_version=1,
        table_rows=rows,
        objects=objects,
    )
    second = build_workspace_export_bundle(
        workspace_id=WORKSPACE_ID,
        export_id=EXPORT_ID,
        created_at=CREATED_AT,
        registry_version=1,
        table_rows=rows,
        objects=objects,
    )

    assert first == second
    assert first.sha256 == hashlib.sha256(first.content).hexdigest()
    with zipfile.ZipFile(io.BytesIO(first.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == "workspace-export.v1"
        assert manifest["workspace_id"] == str(WORKSPACE_ID)
        for table in manifest["tables"]:
            content = archive.read(table["path"])
            assert table["size_bytes"] == len(content)
            assert table["sha256"] == hashlib.sha256(content).hexdigest()
        object_entry = manifest["objects"][0]
        object_content = archive.read(f"objects/{object_entry['object_key']}")
        assert object_entry["sha256"] == hashlib.sha256(object_content).hexdigest()


def test_registry_rejects_duplicate_table(tmp_path: Path) -> None:
    document = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    document["tables"].append(document["tables"][0])
    path = tmp_path / "duplicate-registry.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="重复表名"):
        load_workspace_table_registry(path)
