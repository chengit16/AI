"""编排文档上传安全校验、对象写入和入库任务创建事务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.knowledge.application.facts import KnowledgeFactService
from ai_platform_api.modules.knowledge.domain.models import (
    Document,
    DocumentSource,
    DocumentVersion,
    DocumentVisibility,
)
from ai_platform_api.modules.knowledge.domain.uploads import (
    EmptyUploadError,
    InspectedUpload,
    InvalidUploadError,
    ObjectStorage,
    ObjectStorageUnavailableError,
    ScanResult,
    UnsafeUploadError,
    UnsupportedUploadFormatError,
    UploadInspector,
    UploadMediaTypeMismatchError,
    UploadScanner,
    UploadScannerUnavailableError,
    UploadTooLargeError,
    WorkspaceObject,
)


class UploadEmptyFileError(PlatformError):
    """表示上传空文件错误，由协议层映射为稳定错误码。"""

    error_code = "UPLOAD_EMPTY_FILE"


class UploadFileTooLargeError(PlatformError):
    """表示上传文件过大型错误，由协议层映射为稳定错误码。"""

    error_code = "UPLOAD_FILE_TOO_LARGE"


class UploadUnsupportedFormatError(PlatformError):
    """表示上传不支持格式错误，由协议层映射为稳定错误码。"""

    error_code = "UPLOAD_UNSUPPORTED_FORMAT"


class UploadTypeMismatchError(PlatformError):
    """表示上传类型不匹配错误，由协议层映射为稳定错误码。"""

    error_code = "UPLOAD_TYPE_MISMATCH"


class UploadUnsafeFileError(PlatformError):
    """表示上传不安全文件错误，由协议层映射为稳定错误码。"""

    error_code = "UPLOAD_UNSAFE_FILE"


class UploadSecurityUnavailableError(PlatformError):
    """表示上传安全不可用错误，由协议层映射为稳定错误码。"""

    error_code = "UPLOAD_SCANNER_UNAVAILABLE"


class UploadStorageUnavailableError(PlatformError):
    """表示上传存储不可用错误，由协议层映射为稳定错误码。"""

    error_code = "OBJECT_STORAGE_UNAVAILABLE"


class UploadValidationError(PlatformError):
    """表示上传校验错误，由协议层映射为稳定错误码。"""

    error_code = "VALIDATION_ERROR"


@dataclass(frozen=True)
class UploadedDocument:
    """返回新文档、首版、来源和已验证的上传摘要。"""

    document: Document
    document_version: DocumentVersion
    source: DocumentSource
    media_type: str
    size_bytes: int
    content_hash: str


@dataclass(frozen=True)
class UploadedDocumentVersion:
    """返回新文档版本、来源和已验证的上传摘要。"""

    document_version: DocumentVersion
    source: DocumentSource
    media_type: str
    size_bytes: int
    content_hash: str


class KnowledgeUploadService:
    """先完成受控对象写入，再以补偿删除保证失败事实不会留下孤立对象。"""

    def __init__(
        self,
        facts: KnowledgeFactService,
        storage: ObjectStorage,
        inspector: UploadInspector,
        scanner: UploadScanner,
    ) -> None:
        self._facts = facts
        self._storage = storage
        self._inspector = inspector
        self._scanner = scanner

    def upload_document(
        self,
        context: RequestContext,
        *,
        knowledge_base_id: UUID,
        title: str,
        file_name: str,
        declared_media_type: str | None,
        content: bytes,
        visibility: DocumentVisibility | None,
        department_ids: frozenset[UUID] | None,
        security_level: SecurityLevel | None,
        permission_labels: frozenset[str],
    ) -> UploadedDocument:
        """先检查文件类型、大小、摘要和恶意内容，再原子创建文档与入库任务。"""

        # 1. 在读取和存储文件前确认调用者确实有权向目标知识库上传。
        self._facts.require_upload_target(
            context,
            knowledge_base_id=knowledge_base_id,
        )
        # 2. 检查真实类型、计算摘要并完成恶意扫描后，才把对象写入工作空间路径。
        inspected, scan, location, scanned_at = self._store_clean_upload(
            context, file_name, declared_media_type, content
        )
        # 3. 数据库事实写入失败时补偿删除对象，成功后对象由文档来源事实接管。
        try:
            document, version, source = self._facts.create_document(
                context,
                knowledge_base_id=knowledge_base_id,
                title=title,
                source_kind="upload",
                source_name=inspected.safe_file_name,
                original_object_key=location.object_key,
                visibility=visibility,
                department_ids=department_ids,
                security_level=security_level,
                permission_labels=permission_labels,
                upload_media_type=inspected.media_type,
                upload_size_bytes=inspected.size_bytes,
                upload_content_hash=inspected.content_hash,
                upload_scan_status=scan.status,
                upload_scanner_version=scan.scanner_version,
                upload_scanned_at=scanned_at,
            )
        except Exception:
            self._compensate(location)
            raise
        return UploadedDocument(
            document,
            version,
            source,
            inspected.media_type,
            inspected.size_bytes,
            inspected.content_hash,
        )

    def upload_document_version(
        self,
        context: RequestContext,
        *,
        knowledge_base_id: UUID,
        document_id: UUID,
        file_name: str,
        declared_media_type: str | None,
        content: bytes,
    ) -> UploadedDocumentVersion:
        """为既有文档安全上传新版本，失败时清理尚未提交的对象。"""

        # 1. 先授权目标文档，避免无权请求借扫描和存储消耗资源。
        self._facts.require_upload_target(
            context,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
        # 2. 安全检查通过后存储对象，再创建尚未发布的新版本事实。
        inspected, scan, location, scanned_at = self._store_clean_upload(
            context, file_name, declared_media_type, content
        )
        # 3. 版本事务失败时补偿对象；补偿失败由生命周期任务清理且不覆盖根因。
        try:
            version, source = self._facts.create_document_version(
                context,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                source_kind="upload",
                source_name=inspected.safe_file_name,
                original_object_key=location.object_key,
                upload_media_type=inspected.media_type,
                upload_size_bytes=inspected.size_bytes,
                upload_content_hash=inspected.content_hash,
                upload_scan_status=scan.status,
                upload_scanner_version=scan.scanner_version,
                upload_scanned_at=scanned_at,
            )
        except Exception:
            self._compensate(location)
            raise
        return UploadedDocumentVersion(
            version,
            source,
            inspected.media_type,
            inspected.size_bytes,
            inspected.content_hash,
        )

    def _store_clean_upload(
        self,
        context: RequestContext,
        file_name: str,
        declared_media_type: str | None,
        content: bytes,
    ) -> tuple[InspectedUpload, ScanResult, WorkspaceObject, datetime]:
        # 1. 文件检查负责名称、真实媒体类型、大小和摘要；扫描器只接收检查后的事实。
        try:
            inspected = self._inspector.inspect(
                file_name=file_name,
                declared_media_type=declared_media_type,
                content=content,
            )
            scan = self._scanner.scan(inspected)
            scan.assert_clean()
            # 2. 只有扫描为 clean 的文件才能生成工作空间对象键并进入存储。
            location = WorkspaceObject(
                context.workspace_id,
                _object_key(context.workspace_id, inspected.safe_file_name),
            )
            self._storage.put(location, inspected, scan)
            return inspected, scan, location, datetime.now(UTC)
        except EmptyUploadError as error:
            raise UploadEmptyFileError from error
        except UploadTooLargeError as error:
            raise UploadFileTooLargeError from error
        except UnsupportedUploadFormatError as error:
            raise UploadUnsupportedFormatError from error
        except UploadMediaTypeMismatchError as error:
            raise UploadTypeMismatchError from error
        except UnsafeUploadError as error:
            raise UploadUnsafeFileError from error
        except UploadScannerUnavailableError as error:
            raise UploadSecurityUnavailableError from error
        except ObjectStorageUnavailableError as error:
            raise UploadStorageUnavailableError from error
        except InvalidUploadError as error:
            raise UploadValidationError from error

    def _compensate(self, location: WorkspaceObject) -> None:
        try:
            self._storage.delete(location)
        except ObjectStorageUnavailableError:
            # 原异常仍是调用方需要处理的根因；孤立对象由后续生命周期任务清理。
            return


def _object_key(workspace_id: UUID, file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    return f"workspaces/{workspace_id}/uploads/{uuid4().hex}{suffix}"
