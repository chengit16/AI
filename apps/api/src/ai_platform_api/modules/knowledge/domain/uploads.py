"""定义文档上传校验、安全扫描和对象存储领域端口。"""

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
    """保存安全文件名、真实媒体类型、大小、内容摘要和原始字节。"""

    safe_file_name: str
    media_type: str
    size_bytes: int
    content_hash: str
    content: bytes


@dataclass(frozen=True)
class ScanResult:
    """记录恶意内容扫描结论、扫描器版本和可选稳定原因码。"""

    status: str
    scanner_version: str

    def assert_clean(self) -> None:
        if self.status != "clean" or not self.scanner_version.strip():
            raise UploadScannerUnavailableError


@dataclass(frozen=True)
class WorkspaceObject:
    """标识对象存储中严格归属于一个工作空间的对象键。"""

    workspace_id: UUID
    object_key: str

    def assert_valid(self) -> None:
        expected_prefix = f"workspaces/{self.workspace_id}/"
        if not self.object_key.startswith(expected_prefix) or ".." in self.object_key.split("/"):
            raise InvalidUploadError


class UploadInspector(Protocol):
    """验证文件名、大小和真实媒体类型并计算内容摘要。"""

    def inspect(
        self,
        *,
        file_name: str,
        declared_media_type: str | None,
        content: bytes,
    ) -> InspectedUpload: ...


class UploadScanner(Protocol):
    """扫描已检查文件，服务不可用与不安全结果必须明确区分。"""

    def scan(self, upload: InspectedUpload) -> ScanResult: ...


class ObjectStorage(Protocol):
    """按工作空间对象键写入、读取和补偿删除已扫描文件。"""

    def put(self, location: WorkspaceObject, upload: InspectedUpload, scan: ScanResult) -> None: ...

    def get(self, location: WorkspaceObject) -> bytes: ...

    def delete(self, location: WorkspaceObject) -> None: ...
