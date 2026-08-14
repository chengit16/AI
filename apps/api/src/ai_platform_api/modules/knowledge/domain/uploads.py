from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class InvalidUploadError(Exception):
    """上传文件为空、超限，或文件名、扩展名与真实类型不符合约束。"""


class EmptyUploadError(InvalidUploadError):
    """上传文件没有任何内容。"""


class UploadTooLargeError(InvalidUploadError):
    """上传文件超过服务端配置上限。"""


class UnsupportedUploadFormatError(InvalidUploadError):
    """文件扩展名不在首期允许列表中。"""


class UploadMediaTypeMismatchError(InvalidUploadError):
    """扩展名、声明类型与真实文件签名无法形成一致结论。"""


class UnsafeUploadError(Exception):
    """安全扫描确认文件包含恶意或首期禁止的主动内容。"""


class UploadScannerUnavailableError(Exception):
    """扫描器无法给出可信结论，上传必须失败关闭。"""


class ObjectStorageUnavailableError(Exception):
    """对象存储无法完成受控读写，不能创建可引用的来源事实。"""


@dataclass(frozen=True)
class InspectedUpload:
    safe_file_name: str
    media_type: str
    size_bytes: int
    content_hash: str
    content: bytes


@dataclass(frozen=True)
class ScanResult:
    status: str
    scanner_version: str

    def assert_clean(self) -> None:
        if self.status != "clean" or not self.scanner_version.strip():
            raise UploadScannerUnavailableError


@dataclass(frozen=True)
class WorkspaceObject:
    workspace_id: UUID
    object_key: str

    def assert_valid(self) -> None:
        expected_prefix = f"workspaces/{self.workspace_id}/"
        if not self.object_key.startswith(expected_prefix) or ".." in self.object_key.split("/"):
            raise InvalidUploadError


class UploadInspector(Protocol):
    def inspect(
        self,
        *,
        file_name: str,
        declared_media_type: str | None,
        content: bytes,
    ) -> InspectedUpload: ...


class UploadScanner(Protocol):
    def scan(self, upload: InspectedUpload) -> ScanResult: ...


class ObjectStorage(Protocol):
    def put(self, location: WorkspaceObject, upload: InspectedUpload, scan: ScanResult) -> None: ...

    def get(self, location: WorkspaceObject) -> bytes: ...

    def delete(self, location: WorkspaceObject) -> None: ...
