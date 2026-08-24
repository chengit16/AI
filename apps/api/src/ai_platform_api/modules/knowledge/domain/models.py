"""定义知识库、文档、版本、发布指针和持久化端口。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.ingestion.domain import IngestionJob, IngestionJobStatus
from ai_platform_backend.integration.domain import AuditWriter

from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.identity.domain.entitlements import UsageRepository
from ai_platform_api.modules.integration.domain.events import OutboxWriter

KnowledgeStatus = Literal["active", "deleted"]
DocumentVersionStatus = Literal["draft", "ready", "published", "superseded"]
DocumentVisibility = Literal["private", "workspace", "departments"]
DocumentSourceKind = Literal["manual", "upload", "web", "data_source"]


class InvalidKnowledgeFactError(Exception):
    """知识事实的名称、可见范围、来源或版本结构不合法。"""


class InvalidDocumentVersionTransitionError(Exception):
    """文档版本状态不允许当前转换。"""


class KnowledgeWriteConflictError(Exception):
    """并发更新或数据库唯一约束拒绝本次知识事实写入。"""


@dataclass(frozen=True)
class VisibilityPolicy:
    """约束文档可见范围与部门集合必须保持一致。"""

    visibility: DocumentVisibility
    department_ids: frozenset[UUID] = frozenset()

    def assert_valid(self) -> None:
        has_departments = bool(self.department_ids)
        if (self.visibility == "departments") != has_departments:
            raise InvalidKnowledgeFactError


@dataclass(frozen=True)
class KnowledgeBase:
    """表示工作空间知识内容的可见性与安全级别默认容器。"""

    knowledge_base_id: UUID
    workspace_id: UUID
    name: str
    description: str | None
    default_visibility: DocumentVisibility
    department_ids: frozenset[UUID]
    default_security_level: SecurityLevel
    status: KnowledgeStatus
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    version: int = 1

    def assert_valid(self) -> None:
        if not self.name.strip() or len(self.name) > 120:
            raise InvalidKnowledgeFactError
        if self.description is not None and len(self.description) > 1000:
            raise InvalidKnowledgeFactError
        VisibilityPolicy(self.default_visibility, self.department_ids).assert_valid()
        if self.version < 1 or (self.status == "deleted") != (self.deleted_at is not None):
            raise InvalidKnowledgeFactError

    def delete(self, *, occurred_at: datetime) -> KnowledgeBase:
        if self.status != "active":
            raise InvalidKnowledgeFactError
        return replace(
            self,
            status="deleted",
            deleted_at=occurred_at,
            updated_at=occurred_at,
            version=self.version + 1,
        )


@dataclass(frozen=True)
class Document:
    """保存文档权限标签、安全级别和逻辑删除状态。"""

    document_id: UUID
    workspace_id: UUID
    knowledge_base_id: UUID
    title: str
    visibility: DocumentVisibility
    department_ids: frozenset[UUID]
    security_level: SecurityLevel
    permission_labels: frozenset[str]
    status: KnowledgeStatus
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    version: int = 1

    def assert_valid(self) -> None:
        if not self.title.strip() or len(self.title) > 255:
            raise InvalidKnowledgeFactError
        VisibilityPolicy(self.visibility, self.department_ids).assert_valid()
        if any(not label or len(label) > 80 for label in self.permission_labels):
            raise InvalidKnowledgeFactError
        if self.version < 1 or (self.status == "deleted") != (self.deleted_at is not None):
            raise InvalidKnowledgeFactError

    def delete(self, *, occurred_at: datetime) -> Document:
        if self.status != "active":
            raise InvalidKnowledgeFactError
        return replace(
            self,
            status="deleted",
            deleted_at=occurred_at,
            updated_at=occurred_at,
            version=self.version + 1,
        )

    def restore(self, *, occurred_at: datetime) -> Document:
        """恢复回收站文档；恢复不改变版本内容，只递增元数据版本。"""

        if self.status != "deleted":
            raise InvalidKnowledgeFactError
        candidate = replace(
            self,
            status="active",
            deleted_at=None,
            updated_at=occurred_at,
            version=self.version + 1,
        )
        candidate.assert_valid()
        return candidate


@dataclass(frozen=True)
class DocumentVersion:
    """承载文档内容从草稿、就绪到发布或被替代的状态。"""

    document_version_id: UUID
    workspace_id: UUID
    document_id: UUID
    version_number: int
    status: DocumentVersionStatus
    content_hash: str | None
    created_by_account_id: UUID
    created_at: datetime
    published_at: datetime | None = None
    record_version: int = 1

    def assert_valid(self) -> None:
        if self.version_number < 1 or self.record_version < 1:
            raise InvalidKnowledgeFactError
        if self.content_hash is not None and (
            len(self.content_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.content_hash)
        ):
            raise InvalidKnowledgeFactError
        if (self.status in {"published", "superseded"}) != (self.published_at is not None):
            raise InvalidKnowledgeFactError

    def mark_ready(self, *, content_hash: str) -> DocumentVersion:
        if self.status != "draft":
            raise InvalidDocumentVersionTransitionError
        candidate = replace(
            self,
            status="ready",
            content_hash=content_hash,
            record_version=self.record_version + 1,
        )
        candidate.assert_valid()
        return candidate

    def publish(self, *, occurred_at: datetime) -> DocumentVersion:
        if self.status != "ready":
            raise InvalidDocumentVersionTransitionError
        return replace(
            self,
            status="published",
            published_at=occurred_at,
            record_version=self.record_version + 1,
        )

    def supersede(self) -> DocumentVersion:
        if self.status != "published":
            raise InvalidDocumentVersionTransitionError
        return replace(
            self,
            status="superseded",
            record_version=self.record_version + 1,
        )


@dataclass(frozen=True)
class DocumentSource:
    """记录文档版本的来源定位信息与上传安全扫描事实。"""

    source_id: UUID
    workspace_id: UUID
    document_version_id: UUID
    source_kind: DocumentSourceKind
    source_name: str
    original_object_key: str | None
    source_path: str | None
    source_url: str | None
    external_source_id: str | None
    captured_at: datetime | None
    created_at: datetime
    media_type: str | None = None
    size_bytes: int | None = None
    content_hash: str | None = None
    scan_status: str | None = None
    scanner_version: str | None = None
    scanned_at: datetime | None = None

    def assert_valid(self) -> None:
        # 1. 来源名称和定位字段先执行长度与空值约束，避免不可信定位信息进入持久层。
        if not self.source_name.strip() or len(self.source_name) > 255:
            raise InvalidKnowledgeFactError
        locators = (
            self.original_object_key,
            self.source_path,
            self.source_url,
            self.external_source_id,
        )
        if any(
            value is not None and (not value.strip() or len(value) > 2048) for value in locators
        ):
            raise InvalidKnowledgeFactError
        upload_metadata = (
            self.media_type,
            self.size_bytes,
            self.content_hash,
            self.scan_status,
            self.scanner_version,
            self.scanned_at,
        )
        has_upload_metadata = any(value is not None for value in upload_metadata)
        complete_upload_metadata = (
            isinstance(self.media_type, str)
            and bool(self.media_type.strip())
            and isinstance(self.size_bytes, int)
            and self.size_bytes > 0
            and isinstance(self.content_hash, str)
            and len(self.content_hash) == 64
            and all(character in "0123456789abcdef" for character in self.content_hash)
            and self.scan_status == "clean"
            and isinstance(self.scanner_version, str)
            and bool(self.scanner_version.strip())
            and self.scanned_at is not None
        )
        # 2. 兼容旧合成 upload 事实不携带扫描元数据；新上传只允许完整且 clean 的安全事实。
        if has_upload_metadata and (self.source_kind != "upload" or not complete_upload_metadata):
            raise InvalidKnowledgeFactError
        valid_shape = {
            "manual": all(value is None for value in locators),
            "upload": self.original_object_key is not None and self.source_url is None,
            "web": self.source_url is not None and self.original_object_key is None,
            "data_source": self.external_source_id is not None,
        }[self.source_kind]
        if not valid_shape:
            raise InvalidKnowledgeFactError


class KnowledgeRepository(Protocol):
    """按工作空间和授权投影读写知识聚合、版本及入库任务。"""

    def get_workspace_access(
        self, workspace_id: UUID, account_id: UUID
    ) -> tuple[str, str] | None: ...

    def departments_exist(self, workspace_id: UUID, department_ids: frozenset[UUID]) -> bool: ...

    def add_knowledge_base(self, knowledge_base: KnowledgeBase) -> None: ...

    def list_knowledge_bases(
        self,
        workspace_id: UUID,
        *,
        limit: int,
        authorized_workspace: bool,
        department_ids: frozenset[UUID],
        resource_ids: frozenset[UUID],
    ) -> tuple[KnowledgeBase, ...]: ...

    def get_knowledge_base(
        self, workspace_id: UUID, knowledge_base_id: UUID, *, for_update: bool = False
    ) -> KnowledgeBase | None: ...

    def save_knowledge_base(self, knowledge_base: KnowledgeBase) -> None: ...

    def has_active_documents(self, workspace_id: UUID, knowledge_base_id: UUID) -> bool: ...

    def add_document(self, document: Document) -> None: ...

    def list_document_summaries(
        self,
        workspace_id: UUID,
        knowledge_base_id: UUID,
        *,
        viewer_account_id: UUID,
        limit: int,
        authorized_workspace: bool,
        department_ids: frozenset[UUID],
        account_ids: frozenset[UUID],
        resource_ids: frozenset[UUID],
    ) -> tuple[KnowledgeDocumentSummary, ...]: ...

    def get_document(
        self, workspace_id: UUID, document_id: UUID, *, for_update: bool = False
    ) -> Document | None: ...

    def save_document(self, document: Document) -> None: ...

    def next_document_version_number(self, workspace_id: UUID, document_id: UUID) -> int: ...

    def add_document_version(self, version: DocumentVersion, source: DocumentSource) -> None: ...

    def add_ingestion_job(self, ingestion_job: IngestionJob) -> None: ...

    def list_ingestion_jobs(
        self,
        workspace_id: UUID,
        knowledge_base_id: UUID,
        *,
        limit: int,
        authorized_workspace: bool,
        department_ids: frozenset[UUID],
        account_ids: frozenset[UUID],
        resource_ids: frozenset[UUID],
    ) -> tuple[IngestionJob, ...]: ...

    def get_ingestion_job(
        self, workspace_id: UUID, ingestion_job_id: UUID, *, for_update: bool = False
    ) -> IngestionJob | None: ...

    def save_ingestion_job(self, ingestion_job: IngestionJob) -> None: ...

    def save_cancelled_ingestion_job(
        self,
        ingestion_job: IngestionJob,
        *,
        previous_status: IngestionJobStatus,
    ) -> None: ...

    def get_document_version(
        self,
        workspace_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
        *,
        for_update: bool = False,
    ) -> DocumentVersion | None: ...

    def save_document_version(self, version: DocumentVersion) -> None: ...

    def get_current_document_version(
        self, workspace_id: UUID, document_id: UUID, *, for_update: bool = False
    ) -> DocumentVersion | None: ...

    def set_current_document_version(
        self,
        workspace_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
        *,
        published_at: datetime,
    ) -> None: ...

    def switch_document_index(
        self,
        workspace_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
        *,
        activated_at: datetime,
    ) -> UUID | None: ...

    def deactivate_document_indexes(
        self,
        workspace_id: UUID,
        document_id: UUID,
        *,
        deactivated_at: datetime,
    ) -> None: ...


class KnowledgeUnitOfWork(Protocol):
    """保证知识事实、用量、审计和 Outbox 在同一事务内提交。"""

    @property
    def knowledge(self) -> KnowledgeRepository: ...

    @property
    def usage(self) -> UsageRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> KnowledgeUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


@dataclass(frozen=True)
class KnowledgeDocumentSummary:
    """汇总文档、最新版本和组织关系，供列表查询避免暴露内容正文。"""

    document: Document
    latest_version: DocumentVersion
    source_id: UUID
    source_kind: DocumentSourceKind
    source_name: str
    current_document_version_id: UUID | None
    folder_id: UUID
    tag_ids: tuple[UUID, ...]
    is_favorite: bool
