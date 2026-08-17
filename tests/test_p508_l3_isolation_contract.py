"""验证 P5-08 L3 资源、检查点、单一写入和恢复契约。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from ai_platform_api.modules.isolation.domain.l3 import L3_CHECKPOINT_TYPES
from ai_platform_api.modules.isolation.infrastructure.l3_migration import (
    L3DatabaseTableSpec,
    L3MigrationInfrastructureSettings,
)
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).parents[1]
CONTRACT_DIR = ROOT / "contracts" / "isolation"
SCHEMA_PATH = CONTRACT_DIR / "l3-isolation-migration-baseline.v1.schema.json"
BASELINE_PATH = CONTRACT_DIR / "l3-isolation-migration-baseline.v1.json"


def _load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def _settings(tmp_path: Path) -> L3MigrationInfrastructureSettings:
    return L3MigrationInfrastructureSettings(
        source_database_url="postgresql+psycopg://local@127.0.0.1/source",
        target_database_url="postgresql+psycopg://local@127.0.0.1/target",
        recovery_database_url="postgresql+psycopg://local@127.0.0.1/recovery",
        source_schema="public",
        target_schema="public",
        recovery_schema="public",
        minio_endpoint="http://127.0.0.1:9000",
        minio_access_key="synthetic-local-access",
        minio_secret_key="synthetic-local-secret",
        source_bucket="p508-source",
        target_bucket="p508-target",
        recovery_bucket="p508-recovery",
        source_key_path=tmp_path / "source.key",
        target_key_path=tmp_path / "target.key",
        tables=(L3DatabaseTableSpec("workspace_facts"),),
    )


def test_p508_baseline_matches_schema_and_frozen_checkpoint_order() -> None:
    schema = _load(SCHEMA_PATH)
    baseline = _load(BASELINE_PATH)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(baseline)

    assert tuple(baseline["checkpoint_order"]) == L3_CHECKPOINT_TYPES
    assert baseline["write_authority"] == {
        "before_switch": "source",
        "after_switch": "target",
        "long_term_dual_write": False,
        "rollback_authority": "source",
    }
    assert baseline["resource_profile"]["credentials_persisted"] is False
    assert baseline["resource_profile"]["key_material_persisted"] is False
    assert baseline["external_status"]["real_customer_environment"] == "not_configured"
    assert baseline["external_status"]["production_kms_vault"] == "not_configured"


def test_p508_infrastructure_rejects_shared_target_resources_and_unsafe_tables(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.validate()

    with pytest.raises(ValueError, match="彼此独立"):
        L3MigrationInfrastructureSettings(
            **{
                **settings.__dict__,
                "target_database_url": settings.source_database_url,
            }
        ).validate()
    with pytest.raises(ValueError, match="彼此独立"):
        L3MigrationInfrastructureSettings(
            **{
                **settings.__dict__,
                "target_database_url": (
                    "postgresql+psycopg://other:credential@127.0.0.1/source?sslmode=disable"
                ),
            }
        ).validate()
    with pytest.raises(ValueError, match="安全标识符"):
        L3DatabaseTableSpec("workspace_facts;drop").validate()
