"""验证并演练 P5-10 L4 私有化独立实例交付边界。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).parents[1]
DEFAULT_PROFILE = ROOT / "contracts/deployment/private-instance-baseline.v1.json"
RELEASE_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")


class PrivateInstanceValidationError(ValueError):
    """私有实例配置或生命周期状态违反交付契约。"""


@dataclass(frozen=True)
class InstanceConfig:
    """描述一个实例的外部边界；不保存连接地址、凭证或密钥材料。"""

    environment: str = "synthetic_local"
    network_mode: str = "offline_capable"
    database_backend: str = "local_postgresql"
    object_storage_backend: str = "local_minio"
    secret_backend: str = "local_file"
    certificate_status: str = "not_configured"
    kms_status: str = "not_configured"
    customer_environment_status: str = "not_configured"
    model_provider_status: str = "not_configured"

    def validate(self) -> None:
        """校验本地合成验收与生产私有化必须分开，避免把本地结果冒充生产通过。"""

        allowed = {
            "environment": {"synthetic_local", "production_private"},
            "network_mode": {"offline_capable", "egress_required"},
            "database_backend": {"local_postgresql", "external_postgresql"},
            "object_storage_backend": {"local_minio", "external_s3"},
            "secret_backend": {"local_file", "kms", "vault"},
            "certificate_status": {"not_configured", "configured"},
            "kms_status": {"not_configured", "configured"},
            "customer_environment_status": {"not_configured", "configured"},
            "model_provider_status": {"not_configured", "configured"},
        }
        for field_name, values in allowed.items():
            value = cast(str, getattr(self, field_name))
            if value not in values:
                raise PrivateInstanceValidationError(f"{field_name} 的状态无效: {value}")

        if self.environment == "synthetic_local":
            expected = {
                "network_mode": "offline_capable",
                "database_backend": "local_postgresql",
                "object_storage_backend": "local_minio",
                "secret_backend": "local_file",
            }
            for field_name, value in expected.items():
                if getattr(self, field_name) != value:
                    raise PrivateInstanceValidationError(
                        f"合成本地实例必须使用 {field_name}={value}"
                    )
            if self.certificate_status != "not_configured" or self.kms_status != "not_configured":
                raise PrivateInstanceValidationError("合成本地实例不得伪造生产证书或 KMS 状态")
            return

        required = {
            "network_mode": "egress_required",
            "database_backend": "external_postgresql",
            "object_storage_backend": "external_s3",
            "certificate_status": "configured",
            "kms_status": "configured",
            "customer_environment_status": "configured",
        }
        for field_name, value in required.items():
            if getattr(self, field_name) != value:
                raise PrivateInstanceValidationError(
                    f"生产私有实例未完成 {field_name} 配置: 期望 {value}"
                )
        # 客户环境可选 KMS 或 Vault，但都必须与本地文件密钥明确隔离。
        if self.secret_backend not in {"kms", "vault"}:
            raise PrivateInstanceValidationError("生产私有实例必须使用 secret_backend=kms 或 vault")


@dataclass(frozen=True)
class InstanceRelease:
    """ReleaseManifest 的最小不可变投影。"""

    release_version: str
    schema_revision: str
    manifest_digest: str

    def validate(self) -> None:
        if RELEASE_PATTERN.fullmatch(self.release_version) is None:
            raise PrivateInstanceValidationError(f"Release 版本无效: {self.release_version}")
        if not self.schema_revision or any(char.isspace() for char in self.schema_revision):
            raise PrivateInstanceValidationError("Schema Revision 不能为空或包含空白")
        if DIGEST_PATTERN.fullmatch(self.manifest_digest) is None:
            raise PrivateInstanceValidationError("ReleaseManifest 摘要必须是 sha256")

    def order(self) -> tuple[int, int, int]:
        self.validate()
        match = RELEASE_PATTERN.fullmatch(self.release_version)
        assert match is not None
        parts = tuple(int(part) for part in match.groups())
        return cast(tuple[int, int, int], parts)


@dataclass(frozen=True)
class RecoveryBundle:
    """恢复包加密携带应用恢复密钥，但包加密密钥必须由操作员在包外保管。"""

    bundle_id: str
    release: InstanceRelease
    bundle_digest: str
    application_keys_encrypted: bool = True
    backup_encryption_key_included: bool = False

    def validate(self) -> None:
        if not self.bundle_id or any(char.isspace() for char in self.bundle_id):
            raise PrivateInstanceValidationError("恢复包标识无效")
        self.release.validate()
        if DIGEST_PATTERN.fullmatch(self.bundle_digest) is None:
            raise PrivateInstanceValidationError("恢复包摘要必须是 sha256")
        if not self.application_keys_encrypted:
            raise PrivateInstanceValidationError("恢复包必须加密携带应用恢复密钥")
        if self.backup_encryption_key_included:
            raise PrivateInstanceValidationError("恢复包不得包含自身的加密密钥")


@dataclass(frozen=True)
class OperationRecord:
    """只记录生命周期元数据，避免验收证据包含业务正文。"""

    operation: str
    from_release: str | None
    to_release: str
    reason: str


@dataclass(frozen=True)
class InstanceState:
    """通过不可变快照模拟安装、升级、回滚和恢复的单写状态机。"""

    instance_id: str
    active_release: InstanceRelease | None = None
    rollback_release: InstanceRelease | None = None
    history: tuple[OperationRecord, ...] = ()

    def install(self, release: InstanceRelease) -> InstanceState:
        release.validate()
        if self.active_release is not None:
            raise PrivateInstanceValidationError("已安装实例不能重复执行 install")
        return replace(
            self,
            active_release=release,
            history=(
                *self.history,
                OperationRecord("install", None, release.release_version, "initial"),
            ),
        )

    def upgrade(self, release: InstanceRelease) -> InstanceState:
        release.validate()
        if self.active_release is None:
            raise PrivateInstanceValidationError("未安装实例不能执行 upgrade")
        if release.order() <= self.active_release.order():
            raise PrivateInstanceValidationError("升级目标必须高于当前 Release")
        return replace(
            self,
            active_release=release,
            rollback_release=self.active_release,
            history=(
                *self.history,
                OperationRecord(
                    "upgrade",
                    self.active_release.release_version,
                    release.release_version,
                    "backup_before_upgrade",
                ),
            ),
        )

    def rollback(self) -> InstanceState:
        if self.active_release is None or self.rollback_release is None:
            raise PrivateInstanceValidationError("没有可恢复的升级前 Release")
        previous = self.rollback_release
        return replace(
            self,
            active_release=previous,
            rollback_release=None,
            history=(
                *self.history,
                OperationRecord(
                    "rollback",
                    self.active_release.release_version,
                    previous.release_version,
                    "restore_pre_upgrade_backup",
                ),
            ),
        )

    def restore(self, bundle: RecoveryBundle) -> InstanceState:
        bundle.validate()
        if self.active_release is None:
            raise PrivateInstanceValidationError("未安装实例不能执行 restore")
        if bundle.release.order() > self.active_release.order():
            raise PrivateInstanceValidationError("不能把更新版本恢复包导入旧 Release")
        return replace(
            self,
            active_release=bundle.release,
            rollback_release=self.active_release,
            history=(
                *self.history,
                OperationRecord(
                    "restore",
                    self.active_release.release_version,
                    bundle.release.release_version,
                    bundle.bundle_id,
                ),
            ),
        )


def load_profile(path: Path = DEFAULT_PROFILE) -> dict[str, Any]:
    """加载版本化部署档案，并拒绝非对象 JSON。"""

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise PrivateInstanceValidationError(f"部署档案顶层必须是对象: {path}")
    return cast(dict[str, Any], document)


def validate_profile(document: dict[str, Any]) -> None:
    """执行不依赖外部网络的关键契约检查，JSON Schema 负责结构细节。"""

    if document.get("schema_version") != 1 or document.get("contract_id") != "p5-10-v1":
        raise PrivateInstanceValidationError("部署档案版本或契约标识不匹配")
    if document.get("delivery_mode") != "independent_instance":
        raise PrivateInstanceValidationError("L4 交付必须是 independent_instance")
    external_status = document.get("external_status")
    if not isinstance(external_status, dict):
        raise PrivateInstanceValidationError("部署档案缺少外部状态")
    required_unconfigured = (
        "customer_environment",
        "production_kms_vault",
        "production_certificates",
        "production_object_storage",
        "real_model_provider",
    )
    for field_name in required_unconfigured:
        if external_status.get(field_name) != "not_configured":
            raise PrivateInstanceValidationError(
                f"未提供真实外部输入时必须保持 {field_name}=not_configured"
            )
    secret_policy = document.get("secret_policy")
    if not isinstance(secret_policy, dict):
        raise PrivateInstanceValidationError("部署档案缺少密钥策略")
    if not secret_policy.get("application_recovery_keys_encrypted_in_bundle"):
        raise PrivateInstanceValidationError("部署档案必须包含可恢复的加密应用密钥")
    if secret_policy.get("backup_encryption_key_in_bundle"):
        raise PrivateInstanceValidationError("恢复包加密密钥必须独立保管")


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_local_acceptance() -> dict[str, Any]:
    """用合成版本运行四个生命周期动作，并输出可审计的低敏证据。"""

    profile = load_profile()
    validate_profile(profile)
    config = InstanceConfig()
    config.validate()
    first = InstanceRelease("0.5.0", "20260817_0068", _digest("p5-10-release-0.5.0"))
    second = InstanceRelease("0.5.1", "20260817_0068", _digest("p5-10-release-0.5.1"))
    bundle = RecoveryBundle("synthetic-p5-10-backup-001", first, _digest("p5-10-bundle-001"))
    state = InstanceState("synthetic-p5-10-instance").install(first)
    installed = state.active_release
    state = state.upgrade(second)
    upgraded = state.active_release
    state = state.rollback()
    rolled_back = state.active_release
    state = state.restore(bundle)
    restored = state.active_release
    assert installed is not None and upgraded is not None and rolled_back is not None
    assert restored is not None
    return {
        "acceptance_id": "p5-10-v1",
        "overall_status": "passed",
        "environment": asdict(config),
        "external_status": profile["external_status"],
        "network_mode": "offline_capable",
        "operations": [record.operation for record in state.history],
        "active_release": restored.release_version,
        "schema_revision": restored.schema_revision,
        "history_count": len(state.history),
        "key_material_in_evidence": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="校验版本化私有实例部署档案")
    validate.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    accept = subparsers.add_parser("accept", help="执行本地合成安装、升级、回滚和恢复验收")
    accept.add_argument("--evidence", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.command == "validate":
        validate_profile(load_profile(arguments.profile))
        print(f"私有实例部署档案校验通过: {arguments.profile}")
        return 0
    evidence = run_local_acceptance()
    arguments.evidence.parent.mkdir(parents=True, exist_ok=True)
    arguments.evidence.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"P5-10 本地私有实例验收通过: {arguments.evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
