"""验证 P1G-03 加密恢复包的认证、清单和安全解包边界。"""

from __future__ import annotations

import io
import json
import os
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ai_platform_api.common.security import EnvelopeSecretCipher, MasterKeyFile
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from scripts.local_recovery import (
    FORMAT_VERSION,
    HEADER,
    MAGIC,
    MANIFEST_NAME,
    RecoveryBundleError,
    create_object_snapshot,
    pack_bundle,
    restore_object_snapshot,
    unpack_bundle,
)


def _write_key(path: Path, value: bytes = b"k" * 32) -> None:
    path.write_bytes(value)
    os.chmod(path, 0o600)


def _staging_root(root: Path) -> Path:
    staging = root / "staging"
    (staging / "secrets").mkdir(parents=True)
    (staging / "database.dump").write_bytes(b"synthetic-postgresql-dump")
    (staging / "objects.tar").write_bytes(b"synthetic-minio-snapshot")
    (staging / "secrets/master.key").write_bytes(b"m" * 32)
    (staging / "secrets/master.key.version").write_text("1\n", encoding="ascii")
    (staging / "secrets/task-signing.key").write_bytes(b"t" * 32)
    return staging


def test_encrypted_bundle_roundtrip_keeps_checksums_revision_and_secrets(tmp_path: Path) -> None:
    staging = _staging_root(tmp_path)
    key_path = tmp_path / "recovery.key"
    bundle_path = tmp_path / "snapshot.aiprb"
    restored = tmp_path / "restored"
    _write_key(key_path)

    manifest = pack_bundle(
        staging,
        bundle_path,
        key_path,
        schema_revision="20260815_0035",
        created_at=datetime(2026, 8, 15, tzinfo=UTC),
    )
    restored_manifest = unpack_bundle(bundle_path, restored, key_path)

    assert bundle_path.read_bytes().startswith(MAGIC)
    assert b"synthetic-postgresql-dump" not in bundle_path.read_bytes()
    assert restored_manifest == manifest
    assert restored_manifest["excluded_runtime_state"] == ["valkey", "logs", "runtime"]
    assert (restored / "database.dump").read_bytes() == b"synthetic-postgresql-dump"
    assert (restored / "secrets/master.key").read_bytes() == b"m" * 32
    assert (restored / MANIFEST_NAME).is_file()


@pytest.mark.parametrize("mutation", ["wrong-key", "ciphertext", "tag"])
def test_bundle_rejects_wrong_key_and_authenticated_content_changes(
    tmp_path: Path,
    mutation: str,
) -> None:
    staging = _staging_root(tmp_path)
    key_path = tmp_path / "recovery.key"
    bundle_path = tmp_path / "snapshot.aiprb"
    _write_key(key_path)
    pack_bundle(staging, bundle_path, key_path, schema_revision="20260815_0035")

    if mutation == "wrong-key":
        _write_key(key_path, b"x" * 32)
    else:
        payload = bytearray(bundle_path.read_bytes())
        index = HEADER.size + 5 if mutation == "ciphertext" else len(payload) - 1
        payload[index] ^= 0x01
        bundle_path.write_bytes(payload)

    with pytest.raises(RecoveryBundleError, match="认证失败"):
        unpack_bundle(bundle_path, tmp_path / "restored", key_path)


def test_pack_rejects_missing_required_file_and_unsafe_key_permissions(tmp_path: Path) -> None:
    staging = _staging_root(tmp_path)
    key_path = tmp_path / "recovery.key"
    _write_key(key_path)
    (staging / "objects.tar").unlink()

    with pytest.raises(RecoveryBundleError, match="缺少必需文件"):
        pack_bundle(staging, tmp_path / "missing.aiprb", key_path, schema_revision="head")

    (staging / "objects.tar").write_bytes(b"objects")
    os.chmod(key_path, 0o644)
    with pytest.raises(RecoveryBundleError, match="权限"):
        pack_bundle(staging, tmp_path / "permission.aiprb", key_path, schema_revision="head")


def test_unpack_rejects_nonempty_target_before_decryption(tmp_path: Path) -> None:
    staging = _staging_root(tmp_path)
    key_path = tmp_path / "recovery.key"
    bundle_path = tmp_path / "snapshot.aiprb"
    restored = tmp_path / "restored"
    restored.mkdir()
    (restored / "keep.txt").write_text("不得覆盖", encoding="utf-8")
    _write_key(key_path)
    pack_bundle(staging, bundle_path, key_path, schema_revision="20260815_0035")

    with pytest.raises(RecoveryBundleError, match="必须不存在或为空"):
        unpack_bundle(bundle_path, restored, key_path)
    assert (restored / "keep.txt").read_text(encoding="utf-8") == "不得覆盖"


def test_unpack_rejects_path_traversal_even_with_valid_bundle_authentication(
    tmp_path: Path,
) -> None:
    key_path = tmp_path / "recovery.key"
    bundle_path = tmp_path / "unsafe.aiprb"
    _write_key(key_path)
    archive_stream = io.BytesIO()
    with tarfile.open(fileobj=archive_stream, mode="w") as archive:
        payload = json.dumps({"format": "ai-platform-recovery"}).encode()
        info = tarfile.TarInfo("../outside.txt")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    nonce = b"n" * 12
    header = HEADER.pack(MAGIC, FORMAT_VERSION, nonce)
    encryptor = Cipher(algorithms.AES(b"k" * 32), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    ciphertext = encryptor.update(archive_stream.getvalue()) + encryptor.finalize()
    bundle_path.write_bytes(header + ciphertext + encryptor.tag)

    with pytest.raises(RecoveryBundleError, match="不安全路径"):
        unpack_bundle(bundle_path, tmp_path / "restored", key_path)


def test_master_key_rewrap_keeps_provider_ciphertext_and_uses_new_version(
    tmp_path: Path,
) -> None:
    old_path = tmp_path / "old.key"
    new_path = tmp_path / "new.key"
    _write_key(old_path, b"o" * 32)
    _write_key(new_path, b"n" * 32)
    associated_data = b"model-provider-credential:v1:synthetic"
    old_cipher = EnvelopeSecretCipher(MasterKeyFile(str(old_path), version=1))
    encrypted = old_cipher.encrypt("synthetic-provider-key", associated_data=associated_data)

    rewrapped = old_cipher.rewrap(
        encrypted,
        new_master_key=MasterKeyFile(str(new_path), version=2),
        associated_data=associated_data,
    )
    new_cipher = EnvelopeSecretCipher(MasterKeyFile(str(new_path), version=2))

    assert rewrapped.key_version == 2
    assert rewrapped.ciphertext == encrypted.ciphertext
    assert rewrapped.data_nonce == encrypted.data_nonce
    assert rewrapped.encrypted_data_key != encrypted.encrypted_data_key
    assert (
        new_cipher.decrypt(rewrapped, associated_data=associated_data) == "synthetic-provider-key"
    )
    with pytest.raises(ValueError, match="版本不匹配"):
        old_cipher.decrypt(rewrapped, associated_data=associated_data)


def test_object_snapshot_roundtrip_and_nonempty_target_guard(tmp_path: Path) -> None:
    source = tmp_path / "objects"
    (source / ".minio.sys/buckets").mkdir(parents=True)
    (source / "ai-platform-documents/workspaces/synthetic").mkdir(parents=True)
    (source / ".minio.sys/buckets/state.json").write_text("{}", encoding="utf-8")
    object_path = source / "ai-platform-documents/workspaces/synthetic/document.md"
    object_path.write_text("合成对象正文", encoding="utf-8")
    snapshot = tmp_path / "objects.tar"
    restored = tmp_path / "restored-objects"

    assert create_object_snapshot(source, snapshot) == 2
    assert restore_object_snapshot(snapshot, restored) == 2
    assert (restored / "ai-platform-documents/workspaces/synthetic/document.md").read_text(
        encoding="utf-8"
    ) == "合成对象正文"

    with pytest.raises(RecoveryBundleError, match="必须不存在或为空"):
        restore_object_snapshot(snapshot, restored)
