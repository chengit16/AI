"""定义知识目录、标签、收藏与回收站的领域对象和持久化端口。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID, uuid5

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter

from ai_platform_api.modules.knowledge.domain.models import (
    Document,
    KnowledgeRepository,
)

OrganizationStatus = Literal["active", "deleted"]
DEFAULT_FOLDER_NAME = "全部文件"
DEFAULT_FOLDER_NAMESPACE = UUID("3c7db2a5-9f72-4a72-8e0b-8d4db53ce5f0")


def default_folder_id(workspace_id: UUID) -> UUID:
    """为工作空间生成稳定根目录 ID，保证迁移与运行时引导幂等。"""

    return uuid5(DEFAULT_FOLDER_NAMESPACE, str(workspace_id))


class InvalidOrganizationError(Exception):
    """目录或标签的名称、状态和层级关系不合法。"""


class OrganizationWriteConflictError(Exception):
    """组织事实的并发版本或唯一约束冲突。"""


@dataclass(frozen=True)
class KnowledgeFolder:
    """工作空间内的文件夹节点；删除采用可恢复的逻辑删除。"""

    folder_id: UUID
    workspace_id: UUID
    name: str
    parent_folder_id: UUID | None
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    status: OrganizationStatus = "active"
    deleted_at: datetime | None = None
    version: int = 1
    is_default: bool = False

    def assert_valid(self) -> None:
        if not self.name.strip() or len(self.name) > 120:
            raise InvalidOrganizationError
        if self.parent_folder_id == self.folder_id:
            raise InvalidOrganizationError
        if self.version < 1 or (self.status == "deleted") != (self.deleted_at is not None):
            raise InvalidOrganizationError
        if self.is_default and (
            self.folder_id != default_folder_id(self.workspace_id)
            or self.name != DEFAULT_FOLDER_NAME
            or self.parent_folder_id is not None
            or self.status != "active"
            or self.deleted_at is not None
        ):
            raise InvalidOrganizationError

    def rename(self, name: str, *, occurred_at: datetime) -> KnowledgeFolder:
        if self.is_default:
            raise InvalidOrganizationError
        candidate = replace(
            self, name=name.strip(), updated_at=occurred_at, version=self.version + 1
        )
        candidate.assert_valid()
        return candidate

    def move(self, parent_folder_id: UUID | None, *, occurred_at: datetime) -> KnowledgeFolder:
        if self.is_default:
            raise InvalidOrganizationError
        candidate = replace(
            self,
            parent_folder_id=parent_folder_id,
            updated_at=occurred_at,
            version=self.version + 1,
        )
        candidate.assert_valid()
        return candidate

    def delete(self, *, occurred_at: datetime) -> KnowledgeFolder:
        if self.status != "active" or self.is_default:
            raise InvalidOrganizationError
        return replace(
            self,
            status="deleted",
            deleted_at=occurred_at,
            updated_at=occurred_at,
            version=self.version + 1,
        )

    def restore(self, *, occurred_at: datetime) -> KnowledgeFolder:
        if self.status != "deleted" or self.is_default:
            raise InvalidOrganizationError
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
class KnowledgeTag:
    """工作空间标签；颜色仅是展示元数据，不参与授权。"""

    tag_id: UUID
    workspace_id: UUID
    name: str
    color: str | None
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    status: OrganizationStatus = "active"
    deleted_at: datetime | None = None
    version: int = 1

    def assert_valid(self) -> None:
        if not self.name.strip() or len(self.name) > 80:
            raise InvalidOrganizationError
        if self.color is not None and (not self.color.strip() or len(self.color) > 32):
            raise InvalidOrganizationError
        if self.version < 1 or (self.status == "deleted") != (self.deleted_at is not None):
            raise InvalidOrganizationError

    def rename(self, name: str, *, occurred_at: datetime) -> KnowledgeTag:
        candidate = replace(
            self, name=name.strip(), updated_at=occurred_at, version=self.version + 1
        )
        candidate.assert_valid()
        return candidate

    def delete(self, *, occurred_at: datetime) -> KnowledgeTag:
        if self.status != "active":
            raise InvalidOrganizationError
        return replace(
            self,
            status="deleted",
            deleted_at=occurred_at,
            updated_at=occurred_at,
            version=self.version + 1,
        )


class KnowledgeOrganizationRepository(Protocol):
    """按工作空间维护目录关系、标签绑定和收藏事实。"""

    def list_folders(
        self, workspace_id: UUID, *, include_deleted: bool = False
    ) -> tuple[KnowledgeFolder, ...]: ...

    def get_folder(
        self, workspace_id: UUID, folder_id: UUID, *, for_update: bool = False
    ) -> KnowledgeFolder | None: ...

    def add_folder(self, folder: KnowledgeFolder) -> None: ...

    def get_default_folder(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> KnowledgeFolder | None: ...

    def ensure_default_folder(
        self, workspace_id: UUID, created_by_account_id: UUID, *, occurred_at: datetime
    ) -> KnowledgeFolder: ...

    def save_folder(self, folder: KnowledgeFolder) -> None: ...

    def folder_name_exists(
        self,
        workspace_id: UUID,
        parent_folder_id: UUID | None,
        name: str,
        *,
        exclude_folder_id: UUID | None = None,
    ) -> bool: ...

    def folder_has_children(self, workspace_id: UUID, folder_id: UUID) -> bool: ...

    def folder_has_documents(self, workspace_id: UUID, folder_id: UUID) -> bool: ...

    def list_tags(
        self, workspace_id: UUID, *, include_deleted: bool = False
    ) -> tuple[KnowledgeTag, ...]: ...

    def get_tag(
        self, workspace_id: UUID, tag_id: UUID, *, for_update: bool = False
    ) -> KnowledgeTag | None: ...

    def add_tag(self, tag: KnowledgeTag) -> None: ...

    def save_tag(self, tag: KnowledgeTag) -> None: ...

    def tag_name_exists(
        self, workspace_id: UUID, name: str, *, exclude_tag_id: UUID | None = None
    ) -> bool: ...

    def bind_document_folder(
        self, workspace_id: UUID, document_id: UUID, folder_id: UUID, *, occurred_at: datetime
    ) -> None: ...

    def unbind_document_folder(
        self, workspace_id: UUID, document_id: UUID, folder_id: UUID
    ) -> None: ...

    def list_document_folders(
        self, workspace_id: UUID, document_id: UUID
    ) -> tuple[KnowledgeFolder, ...]: ...

    def get_document_folder(
        self, workspace_id: UUID, document_id: UUID
    ) -> KnowledgeFolder | None: ...

    def bind_document_tag(self, workspace_id: UUID, document_id: UUID, tag_id: UUID) -> None: ...

    def unbind_document_tag(self, workspace_id: UUID, document_id: UUID, tag_id: UUID) -> None: ...

    def unbind_tag_documents(self, workspace_id: UUID, tag_id: UUID) -> None:
        """解除标签在当前工作空间内的全部文档绑定。"""

        ...

    def list_document_tags(
        self, workspace_id: UUID, document_id: UUID
    ) -> tuple[KnowledgeTag, ...]: ...

    def add_favorite(
        self, workspace_id: UUID, account_id: UUID, document_id: UUID, *, occurred_at: datetime
    ) -> None: ...

    def remove_favorite(self, workspace_id: UUID, account_id: UUID, document_id: UUID) -> None: ...

    def list_favorite_document_ids(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        limit: int,
        authorized_workspace: bool,
        department_ids: frozenset[UUID],
        account_ids: frozenset[UUID],
        resource_ids: frozenset[UUID],
    ) -> tuple[UUID, ...]: ...

    def list_trash_documents(self, workspace_id: UUID, *, limit: int) -> tuple[Document, ...]: ...

    def permanently_delete_folder(self, workspace_id: UUID, folder_id: UUID) -> None: ...

    def has_running_ingestion_jobs(self, workspace_id: UUID, document_id: UUID) -> bool: ...

    def permanently_delete_document(
        self, workspace_id: UUID, document_id: UUID
    ) -> tuple[str, ...]: ...


class KnowledgeOrganizationUnitOfWork(Protocol):
    """组织能力与知识事实共用事务、审计和 Outbox。"""

    @property
    def organization(self) -> KnowledgeOrganizationRepository: ...

    @property
    def knowledge(self) -> KnowledgeRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> KnowledgeOrganizationUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
