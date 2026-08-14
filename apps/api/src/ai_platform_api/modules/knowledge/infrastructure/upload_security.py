from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

from ai_platform_api.modules.knowledge.domain.uploads import (
    EmptyUploadError,
    InspectedUpload,
    InvalidUploadError,
    ScanResult,
    UnsafeUploadError,
    UnsupportedUploadFormatError,
    UploadMediaTypeMismatchError,
    UploadScannerUnavailableError,
    UploadTooLargeError,
)

MEBIBYTE = 1024 * 1024
ALLOWED_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
GENERIC_MEDIA_TYPES = {"", "application/octet-stream"}
EXECUTABLE_SIGNATURES = (
    b"MZ",
    b"\x7fELF",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
)
EICAR_MARKER = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class BoundedUploadInspector:
    """在写入对象存储前按真实字节识别类型，并固定内容摘要。"""

    def __init__(self, max_size_bytes: int) -> None:
        if not MEBIBYTE <= max_size_bytes <= 100 * MEBIBYTE:
            raise ValueError("上传上限必须位于 1 MiB 到 100 MiB 之间")
        self._max_size_bytes = max_size_bytes

    def inspect(
        self,
        *,
        file_name: str,
        declared_media_type: str | None,
        content: bytes,
    ) -> InspectedUpload:
        safe_name = Path(file_name).name
        if (
            not safe_name
            or safe_name in {".", ".."}
            or len(safe_name) > 255
            or any(ord(character) < 32 for character in safe_name)
        ):
            raise InvalidUploadError
        if not content:
            raise EmptyUploadError
        if len(content) > self._max_size_bytes:
            raise UploadTooLargeError

        expected_media_type = ALLOWED_TYPES.get(Path(safe_name).suffix.lower())
        if expected_media_type is None:
            raise UnsupportedUploadFormatError
        detected_media_type = _detect_media_type(content, expected_media_type)
        if detected_media_type != expected_media_type:
            raise UploadMediaTypeMismatchError
        normalized_declared = (declared_media_type or "").split(";", 1)[0].strip().lower()
        if (
            normalized_declared not in GENERIC_MEDIA_TYPES
            and normalized_declared != detected_media_type
        ):
            raise UploadMediaTypeMismatchError
        return InspectedUpload(
            safe_file_name=safe_name,
            media_type=detected_media_type,
            size_bytes=len(content),
            content_hash=hashlib.sha256(content).hexdigest(),
            content=content,
        )


class DeterministicUploadScanner:
    """提供本地可复验的失败关闭扫描边界，后续可替换为独立恶意文件引擎。"""

    scanner_version = "deterministic-upload-scanner-v1"

    def scan(self, upload: InspectedUpload) -> ScanResult:
        try:
            _reject_common_malware(upload.content)
            if upload.media_type == "application/pdf":
                _reject_active_pdf(upload.content)
            if upload.media_type.endswith("wordprocessingml.document"):
                _inspect_docx(upload.content)
        except UnsafeUploadError:
            raise
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            # 损坏容器或扫描器内部错误都不能降级为“安全”。
            raise UploadScannerUnavailableError from error
        return ScanResult(status="clean", scanner_version=self.scanner_version)


def _detect_media_type(content: bytes, expected_media_type: str) -> str:
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if zipfile.is_zipfile(io.BytesIO(content)):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    try:
        content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "application/octet-stream"
    if b"\x00" in content:
        return "application/octet-stream"
    return (
        expected_media_type
        if expected_media_type in {"text/plain", "text/markdown"}
        else "text/plain"
    )


def _reject_common_malware(content: bytes) -> None:
    upper_content = content.upper()
    if EICAR_MARKER in upper_content or any(
        content.startswith(signature) for signature in EXECUTABLE_SIGNATURES
    ):
        raise UnsafeUploadError


def _reject_active_pdf(content: bytes) -> None:
    lowered = content.lower()
    if any(marker in lowered for marker in (b"/javascript", b"/launch", b"/embeddedfile")):
        raise UnsafeUploadError


def _inspect_docx(content: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        names = {entry.filename for entry in entries}
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise UnsafeUploadError
        if len(entries) > 10_000:
            raise UnsafeUploadError
        total_uncompressed = 0
        for entry in entries:
            normalized = Path(entry.filename)
            lowered = entry.filename.lower()
            if normalized.is_absolute() or ".." in normalized.parts:
                raise UnsafeUploadError
            if (
                "vbaproject.bin" in lowered
                or lowered.startswith("word/activex/")
                or lowered.startswith("word/embeddings/")
            ):
                raise UnsafeUploadError
            total_uncompressed += entry.file_size
            if entry.compress_size and entry.file_size / entry.compress_size > 100:
                raise UnsafeUploadError
        if total_uncompressed > 200 * MEBIBYTE:
            raise UnsafeUploadError
