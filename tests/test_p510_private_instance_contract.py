"""验证 P5-10 私有实例部署档案、离线依赖和生命周期状态机。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from scripts.private_instance import (
    InstanceConfig,
    InstanceRelease,
    InstanceState,
    PrivateInstanceValidationError,
    RecoveryBundle,
    run_local_acceptance,
    validate_profile,
)

ROOT = Path(__file__).parents[1]
CONTRACT_DIR = ROOT / "contracts" / "deployment"


def _load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError(f"JSON 顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def _release(version: str) -> InstanceRelease:
    return InstanceRelease(version, "20260817_0068", "sha256:" + "a" * 64)


def test_p510_baseline_schema_and_external_status_are_frozen() -> None:
    schema = _load(CONTRACT_DIR / "private-instance-baseline.v1.schema.json")
    baseline = _load(CONTRACT_DIR / "private-instance-baseline.v1.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(baseline)
    validate_profile(baseline)
    assert baseline["lifecycle"] == ["install", "upgrade", "rollback", "restore"]
    assert baseline["external_status"]["production_kms_vault"] == "not_configured"


def test_p510_synthetic_instance_is_offline_capable_and_production_requires_real_inputs() -> None:
    InstanceConfig().validate()
    for secret_backend in ("kms", "vault"):
        InstanceConfig(
            environment="production_private",
            network_mode="egress_required",
            database_backend="external_postgresql",
            object_storage_backend="external_s3",
            secret_backend=secret_backend,
            certificate_status="configured",
            kms_status="configured",
            customer_environment_status="configured",
        ).validate()
    with pytest.raises(PrivateInstanceValidationError, match=r"secret_backend|kms_status"):
        InstanceConfig(
            environment="production_private",
            network_mode="egress_required",
            database_backend="external_postgresql",
            object_storage_backend="external_s3",
            secret_backend="local_file",
            certificate_status="configured",
            kms_status="configured",
            customer_environment_status="configured",
        ).validate()


def test_p510_install_upgrade_rollback_restore_is_single_writer() -> None:
    first = _release("0.5.0")
    second = _release("0.5.1")
    state = InstanceState("synthetic-instance").install(first).upgrade(second)
    assert state.active_release == second
    state = state.rollback()
    assert state.active_release == first
    bundle = RecoveryBundle("bundle-1", first, "sha256:" + "b" * 64)
    restored = state.restore(bundle)
    assert restored.active_release == first
    assert [record.operation for record in restored.history] == [
        "install",
        "upgrade",
        "rollback",
        "restore",
    ]
    with pytest.raises(PrivateInstanceValidationError, match="重复执行 install"):
        restored.install(first)


def test_p510_newer_backup_cannot_be_imported_into_older_release() -> None:
    state = InstanceState("synthetic-instance").install(_release("0.5.0"))
    newer = RecoveryBundle("bundle-new", _release("0.6.0"), "sha256:" + "c" * 64)
    with pytest.raises(PrivateInstanceValidationError, match="更新版本恢复包"):
        state.restore(newer)


def test_p510_backup_encryption_key_must_remain_outside_bundle() -> None:
    bundle = RecoveryBundle(
        "bundle-key-error",
        _release("0.5.0"),
        "sha256:" + "d" * 64,
        backup_encryption_key_included=True,
    )
    with pytest.raises(PrivateInstanceValidationError, match="自身的加密密钥"):
        bundle.validate()


def test_p510_local_acceptance_evidence_has_no_secret_material() -> None:
    evidence = run_local_acceptance()
    assert evidence["overall_status"] == "passed"
    assert evidence["operations"] == ["install", "upgrade", "rollback", "restore"]
    assert evidence["key_material_in_evidence"] is False
    assert evidence["external_status"]["customer_environment"] == "not_configured"
