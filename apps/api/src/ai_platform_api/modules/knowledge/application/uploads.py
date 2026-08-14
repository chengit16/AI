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
    error_code = "UPLOAD_EMPTY_FILE"


class UploadFileTooLargeError(PlatformError):
    error_code = "UPLOAD_FILE_TOO_LARGE"


class UploadUnsupportedFormatError(PlatformError):
    error_code = "UPLOAD_UNSUPPORTED_FORMAT"


class UploadTypeMismatchError(PlatformError):
    error_code = "UPLOAD_TYPE_MISMATCH"


class UploadUnsafeFileError(PlatformError):
    error_code = "UPLOAD_UNSAFE_FILE"


class UploadSecurityUnavailableError(PlatformError):
    error_code = "UPLOAD_SCANNER_UNAVAILABLE"


class UploadStorageUnavailableError(PlatformError):
    error_code = "OBJECT_STORAGE_UNAVAILABLE"


class UploadValidationError(PlatformError):
    error_code = "VALIDATION_ERROR"


@dataclass(frozen=True)
class UploadedDocument:
    document: Document
    document_version: DocumentVersion
    source: DocumentSource
    media_type: str
    size_bytes: int
    content_hash: str


@dataclass(frozen=True)
class UploadedDocumentVersion:
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
        self._facts.require_upload_target(
            context,
            knowledge_base_id=knowledge_base_id,
        )
        inspected, scan, location, scanned_at = self._store_clean_upload(
            context, file_name, declared_media_type, content
        )
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
        self._facts.require_upload_target(
            context,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
        inspected, scan, location, scanned_at = self._store_clean_upload(
            context, file_name, declared_media_type, content
        )
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
        try:
            inspected = self._inspector.inspect(
                file_name=file_name,
                declared_media_type=declared_media_type,
                content=content,
            )
            scan = self._scanner.scan(inspected)
            scan.assert_clean()
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
