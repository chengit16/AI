"""创建、验证和解开 AI Platform 本地加密恢复包。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import struct
import tarfile
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, cast

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"AIPRB001"
FORMAT_VERSION = 1
HEADER = struct.Struct(">8sH12s")
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024
MANIFEST_NAME = "recovery-manifest.v1.json"
REQUIRED_FILES = {
    "database.dump",
    "objects.tar",
    "secrets/master.key",
    "secrets/master.key.version",
    "secrets/task-signing.key",
}


class RecoveryBundleError(ValueError):
    """恢复包格式、认证、路径或校验和不满足安全约束。"""


def load_recovery_key(path: Path) -> bytes:
    """读取独立恢复密钥，拒绝符号链接、错误长度和宽松 POSIX 权限。"""

    if path.is_symlink():
        raise RecoveryBundleError("恢复加密密钥不能是符号链接")
    if not path.is_file():
        raise RecoveryBundleError("恢复加密密钥文件不存在")
    key = path.read_bytes()
    if len(key) != 32:
        raise RecoveryBundleError("恢复加密密钥必须正好为 32 字节")
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RecoveryBundleError("恢复加密密钥权限必须限制为当前用户可读")
    return key


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _staging_files(staging_root: Path) -> Iterator[tuple[str, Path]]:
    for path in sorted(staging_root.rglob("*")):
        if path.is_symlink():
            raise RecoveryBundleError("恢复包暂存目录不能包含符号链接")
        if path.is_dir():
            continue
        if not path.is_file():
            raise RecoveryBundleError("恢复包暂存目录只能包含普通文件")
        relative = path.relative_to(staging_root).as_posix()
        if relative != MANIFEST_NAME:
            yield relative, path


def build_manifest(
    staging_root: Path,
    *,
    schema_revision: str,
    created_at: datetime,
) -> dict[str, Any]:
    """冻结恢复包文件清单、Schema Revision、排除边界和逐文件摘要。"""

    if not staging_root.is_dir() or staging_root.is_symlink():
        raise RecoveryBundleError("恢复包暂存目录必须是普通目录")
    if not schema_revision or any(character.isspace() for character in schema_revision):
        raise RecoveryBundleError("Schema Revision 不能为空或包含空白字符")
    if created_at.tzinfo is None:
        raise RecoveryBundleError("恢复包时间必须包含时区")

    file_entries = [
        {
            "path": relative,
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for relative, path in _staging_files(staging_root)
    ]
    actual_files = {cast(str, entry["path"]) for entry in file_entries}
    missing = REQUIRED_FILES - actual_files
    if missing:
        raise RecoveryBundleError(f"恢复包缺少必需文件数量: {len(missing)}")
    return {
        "format": "ai-platform-recovery",
        "format_version": FORMAT_VERSION,
        "created_at": created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "schema_revision": schema_revision,
        "files": file_entries,
        "excluded_runtime_state": ["valkey", "logs", "runtime"],
    }


def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _normalized_tar_info(name: str, size: int, *, mode: int = 0o600) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = mode
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def _create_plain_archive(
    staging_root: Path,
    manifest: dict[str, Any],
    archive_path: Path,
) -> None:
    manifest_payload = _manifest_bytes(manifest)
    with tarfile.open(archive_path, mode="w") as archive:
        archive.addfile(
            _normalized_tar_info(MANIFEST_NAME, len(manifest_payload)),
            fileobj=_BytesReader(manifest_payload),
        )
        for entry in cast(list[dict[str, Any]], manifest["files"]):
            relative = cast(str, entry["path"])
            source_path = staging_root / relative
            mode = 0o600 if relative.startswith("secrets/") else 0o640
            with source_path.open("rb") as source:
                archive.addfile(
                    _normalized_tar_info(relative, source_path.stat().st_size, mode=mode),
                    fileobj=source,
                )


def create_object_snapshot(source_root: Path, output_path: Path) -> int:
    """把已停止写入的 MinIO 数据目录保存为仅含普通文件的安全 Tar。"""

    if not source_root.is_dir() or source_root.is_symlink():
        raise RecoveryBundleError("对象数据源必须是普通目录")
    if output_path.exists() or output_path.is_symlink():
        raise RecoveryBundleError("对象快照输出路径已经存在")
    count = 0
    with tarfile.open(output_path, mode="w") as archive:
        for path in sorted(source_root.rglob("*")):
            if path.is_symlink() or (not path.is_file() and not path.is_dir()):
                raise RecoveryBundleError("对象数据目录只能包含普通目录和文件")
            if path.is_dir():
                continue
            relative = path.relative_to(source_root).as_posix()
            with path.open("rb") as source:
                archive.addfile(
                    _normalized_tar_info(relative, path.stat().st_size, mode=0o600),
                    fileobj=source,
                )
            count += 1
    os.chmod(output_path, 0o600)
    return count


def restore_object_snapshot(snapshot_path: Path, target_root: Path) -> int:
    """验证对象快照路径后恢复到空目录，避免覆盖未知或仍在使用的数据。"""

    if not snapshot_path.is_file() or snapshot_path.is_symlink():
        raise RecoveryBundleError("对象快照必须是普通文件")
    if target_root.exists() and (target_root.is_symlink() or any(target_root.iterdir())):
        raise RecoveryBundleError("对象恢复目标必须不存在或为空目录")
    with tempfile.TemporaryDirectory(prefix="ai-platform-objects-") as temporary:
        extracted_root = Path(temporary) / "objects"
        extracted_root.mkdir()
        _extract_plain_archive(snapshot_path, extracted_root)
        restored_count = sum(path.is_file() for path in extracted_root.rglob("*"))
        target_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(extracted_root, target_root, dirs_exist_ok=True)
    return restored_count


class _BytesReader:
    """向 tarfile 提供只读内存载荷，避免为小型 Manifest 创建中间文件。"""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._payload) - self._offset
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def _encrypt_file(source_path: Path, output_path: Path, key: bytes) -> None:
    nonce = os.urandom(12)
    header = HEADER.pack(MAGIC, FORMAT_VERSION, nonce)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    temporary = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    try:
        with source_path.open("rb") as source, temporary.open("xb") as target:
            target.write(header)
            for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
                target.write(encryptor.update(chunk))
            target.write(encryptor.finalize())
            target.write(encryptor.tag)
        os.chmod(temporary, 0o600)
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_header(source: BinaryIO) -> tuple[bytes, int, bytes, bytes]:
    raw_header = source.read(HEADER.size)
    if len(raw_header) != HEADER.size:
        raise RecoveryBundleError("恢复包头不完整")
    magic, version, nonce = HEADER.unpack(raw_header)
    if magic != MAGIC or version != FORMAT_VERSION:
        raise RecoveryBundleError("恢复包格式或版本不受支持")
    return magic, version, nonce, raw_header


def _decrypt_file(bundle_path: Path, output_path: Path, key: bytes) -> None:
    bundle_size = bundle_path.stat().st_size
    minimum_size = HEADER.size + TAG_SIZE
    if bundle_size <= minimum_size:
        raise RecoveryBundleError("恢复包没有有效密文")
    with bundle_path.open("rb") as source:
        _, _, nonce, header = _read_header(source)
        source.seek(-TAG_SIZE, os.SEEK_END)
        tag = source.read(TAG_SIZE)
        ciphertext_size = bundle_size - HEADER.size - TAG_SIZE
        source.seek(HEADER.size)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(header)
        try:
            with output_path.open("xb") as target:
                remaining = ciphertext_size
                while remaining:
                    chunk = source.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise RecoveryBundleError("恢复包密文提前结束")
                    target.write(decryptor.update(chunk))
                    remaining -= len(chunk)
                target.write(decryptor.finalize())
        except Exception as error:
            output_path.unlink(missing_ok=True)
            if isinstance(error, RecoveryBundleError):
                raise
            raise RecoveryBundleError("恢复包认证失败或已损坏") from error


def _validated_member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts or "." in path.parts:
        raise RecoveryBundleError("恢复包包含不安全路径")
    return path


def _extract_plain_archive(archive_path: Path, target_root: Path) -> None:
    with tarfile.open(archive_path, mode="r:") as archive:
        for member in archive:
            relative = _validated_member_path(member.name)
            if not member.isfile():
                raise RecoveryBundleError("恢复包只能包含普通文件")
            source = archive.extractfile(member)
            if source is None:
                raise RecoveryBundleError("恢复包成员无法读取")
            destination = target_root.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source, destination.open("xb") as target:
                shutil.copyfileobj(source, target, length=CHUNK_SIZE)
            os.chmod(destination, member.mode & 0o777)


def verify_extracted_bundle(target_root: Path) -> dict[str, Any]:
    """验证解包目录的清单结构、必需文件、额外文件和逐文件摘要。"""

    manifest_path = target_root / MANIFEST_NAME
    try:
        manifest_document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RecoveryBundleError("恢复包 Manifest 不可读取") from error
    if not isinstance(manifest_document, dict):
        raise RecoveryBundleError("恢复包 Manifest 顶层必须是对象")
    manifest = cast(dict[str, Any], manifest_document)
    if (
        manifest.get("format") != "ai-platform-recovery"
        or manifest.get("format_version") != FORMAT_VERSION
    ):
        raise RecoveryBundleError("恢复包 Manifest 格式或版本不受支持")

    entries = cast(list[dict[str, Any]], manifest.get("files"))
    expected_paths = {cast(str, entry["path"]) for entry in entries}
    actual_paths = {
        path.relative_to(target_root).as_posix()
        for path in target_root.rglob("*")
        if path.is_file() and path.relative_to(target_root).as_posix() != MANIFEST_NAME
    }
    if expected_paths != actual_paths or not REQUIRED_FILES.issubset(actual_paths):
        raise RecoveryBundleError("恢复包文件集合与 Manifest 不一致")
    for entry in entries:
        path = target_root / cast(str, entry["path"])
        if path.stat().st_size != entry["size_bytes"] or _sha256(path) != entry["sha256"]:
            raise RecoveryBundleError("恢复包文件校验和不一致")
    return manifest


def pack_bundle(
    staging_root: Path,
    output_path: Path,
    key_path: Path,
    *,
    schema_revision: str,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """生成 Manifest 和普通 Tar 后流式加密，并以原子重命名发布恢复包。"""

    if output_path.exists() or output_path.is_symlink():
        raise RecoveryBundleError("恢复包输出路径已经存在")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    key = load_recovery_key(key_path)
    manifest = build_manifest(
        staging_root,
        schema_revision=schema_revision,
        created_at=created_at or datetime.now(UTC),
    )
    with tempfile.TemporaryDirectory(prefix="ai-platform-pack-") as temporary:
        archive_path = Path(temporary) / "recovery.tar"
        _create_plain_archive(staging_root, manifest, archive_path)
        _encrypt_file(archive_path, output_path, key)
    return manifest


def unpack_bundle(bundle_path: Path, target_root: Path, key_path: Path) -> dict[str, Any]:
    """先在隔离临时目录完成认证、路径和摘要验证，再发布到空目标目录。"""

    if not bundle_path.is_file() or bundle_path.is_symlink():
        raise RecoveryBundleError("恢复包必须是普通文件")
    if target_root.exists() and (target_root.is_symlink() or any(target_root.iterdir())):
        raise RecoveryBundleError("恢复目标必须不存在或为空目录")
    key = load_recovery_key(key_path)
    with tempfile.TemporaryDirectory(prefix="ai-platform-unpack-") as temporary:
        temporary_root = Path(temporary)
        archive_path = temporary_root / "recovery.tar"
        extracted_root = temporary_root / "extracted"
        extracted_root.mkdir()
        _decrypt_file(bundle_path, archive_path, key)
        _extract_plain_archive(archive_path, extracted_root)
        manifest = verify_extracted_bundle(extracted_root)
        target_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(extracted_root, target_root, dirs_exist_ok=True)
    return manifest


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    pack = subparsers.add_parser("pack", help="创建加密恢复包")
    pack.add_argument("--staging-root", type=Path, required=True)
    pack.add_argument("--output", type=Path, required=True)
    pack.add_argument("--key-file", type=Path, required=True)
    pack.add_argument("--schema-revision", required=True)
    unpack = subparsers.add_parser("unpack", help="验证并解开加密恢复包")
    unpack.add_argument("--bundle", type=Path, required=True)
    unpack.add_argument("--target-root", type=Path, required=True)
    unpack.add_argument("--key-file", type=Path, required=True)
    snapshot_objects = subparsers.add_parser(
        "snapshot-objects", help="创建停止写入后的 MinIO 物理对象快照"
    )
    snapshot_objects.add_argument("--source-root", type=Path, required=True)
    snapshot_objects.add_argument("--output", type=Path, required=True)
    restore_objects = subparsers.add_parser("restore-objects", help="安全恢复 MinIO 物理对象快照")
    restore_objects.add_argument("--snapshot", type=Path, required=True)
    restore_objects.add_argument("--target-root", type=Path, required=True)
    arguments = parser.parse_args()

    if arguments.command == "pack":
        manifest = pack_bundle(
            arguments.staging_root,
            arguments.output,
            arguments.key_file,
            schema_revision=arguments.schema_revision,
        )
        print(f"恢复包创建完成: revision={manifest['schema_revision']}")
    elif arguments.command == "unpack":
        manifest = unpack_bundle(arguments.bundle, arguments.target_root, arguments.key_file)
        print(f"恢复包验证完成: revision={manifest['schema_revision']}")
    elif arguments.command == "snapshot-objects":
        count = create_object_snapshot(arguments.source_root, arguments.output)
        print(f"对象快照创建完成: files={count}")
    else:
        count = restore_object_snapshot(arguments.snapshot, arguments.target_root)
        print(f"对象快照恢复完成: files={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
