"""编排文件夹、标签、收藏和回收站的知识组织用例。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.integration.domain.events import IntegrationEvent
from ai_platform_api.modules.knowledge.domain.models import (
    Document,
    KnowledgeRepository,
    KnowledgeWriteConflictError,
)
from ai_platform_api.modules.knowledge.domain.organization import (
    InvalidOrganizationError,
    KnowledgeFolder,
    KnowledgeOrganizationRepository,
    KnowledgeOrganizationUnitOfWork,
    KnowledgeTag,
    OrganizationWriteConflictError,
)

__all__ = [
    "KnowledgeFolder",
    "KnowledgeOrganizationService",
    "KnowledgeTag",
]


class KnowledgeOrganizationNotFoundError(PlatformError):
    """组织资源不存在，或不属于当前工作空间。"""

    error_code = "RESOURCE_NOT_FOUND"


class KnowledgeOrganizationDeniedError(PlatformError):
    """当前主体不是活动成员或没有工作空间写权限。"""

    error_code = "POLICY_DENIED"


class KnowledgeOrganizationConflictError(PlatformError):
    """目录层级、同名、绑定或回收站状态冲突。"""

    error_code = "KNOWLEDGE_CONFLICT"


class KnowledgeOrganizationValidationError(PlatformError):
    """组织请求不满足领域校验。"""

    error_code = "VALIDATION_ERROR"


class KnowledgeOrganizationService:
    """在知识事实同一事务内维护组织关系、审计和事件意图。"""

    def __init__(self, unit_of_work: KnowledgeOrganizationUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def list_folders(
        self, context: RequestContext, *, include_deleted: bool = False
    ) -> tuple[KnowledgeFolder, ...]:
        """列出当前工作空间目录，并在缺失时引导默认根目录。"""

        account_id = _browser_account(context)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work.knowledge, context.workspace_id, account_id)
            # 兼容历史或刚创建的空间：读取目录前幂等引导固定根目录。
            unit_of_work.organization.ensure_default_folder(
                context.workspace_id, account_id, occurred_at=datetime.now(UTC)
            )
            unit_of_work.commit()
            return unit_of_work.organization.list_folders(
                context.workspace_id, include_deleted=include_deleted
            )

    def create_folder(
        self, context: RequestContext, *, name: str, parent_folder_id: UUID | None
    ) -> KnowledgeFolder:
        """创建普通目录；空父级统一落到默认根目录。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        # 1. 先构造并校验目录名称，再在事务内解析父目录和唯一性。
        folder = KnowledgeFolder(
            uuid4(),
            context.workspace_id,
            name.strip(),
            parent_folder_id,
            account_id,
            now,
            now,
        )
        try:
            folder.assert_valid()
            with self._unit_of_work as unit_of_work:
                # 2. 根目录引导、父级校验、写入及审计事件共用一个事务。
                _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
                parent_folder_id = _resolve_parent_folder(
                    unit_of_work.organization,
                    context.workspace_id,
                    parent_folder_id,
                    account_id,
                )
                folder = replace(folder, parent_folder_id=parent_folder_id)
                folder.assert_valid()
                _require_parent_folder(
                    unit_of_work.organization, context.workspace_id, parent_folder_id
                )
                _require_folder_name_available(unit_of_work.organization, folder)
                unit_of_work.organization.add_folder(folder)
                _record_organization(
                    unit_of_work,
                    context,
                    folder.folder_id,
                    folder.version,
                    "knowledge.folder.created",
                    "knowledge.folder.create",
                    "knowledge_folder",
                    now,
                )
                unit_of_work.commit()
        except InvalidOrganizationError as error:
            raise KnowledgeOrganizationValidationError from error
        except (OrganizationWriteConflictError, KnowledgeWriteConflictError) as error:
            raise KnowledgeOrganizationConflictError from error
        return folder

    def rename_folder(
        self, context: RequestContext, *, folder_id: UUID, name: str
    ) -> KnowledgeFolder:
        """修改普通目录名称；默认根目录始终拒绝改名。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
                folder = _require_folder(
                    unit_of_work.organization, context.workspace_id, folder_id, for_update=True
                )
                if folder.is_default:
                    raise KnowledgeOrganizationConflictError
                if folder.status != "active":
                    raise KnowledgeOrganizationNotFoundError
                updated = folder.rename(name, occurred_at=now)
                _require_folder_name_available(unit_of_work.organization, updated)
                unit_of_work.organization.save_folder(updated)
                _record_organization(
                    unit_of_work,
                    context,
                    folder_id,
                    updated.version,
                    "knowledge.folder.renamed",
                    "knowledge.folder.rename",
                    "knowledge_folder",
                    now,
                )
                unit_of_work.commit()
                return updated
        except InvalidOrganizationError as error:
            raise KnowledgeOrganizationValidationError from error
        except (OrganizationWriteConflictError, KnowledgeWriteConflictError) as error:
            raise KnowledgeOrganizationConflictError from error

    def move_folder(
        self, context: RequestContext, *, folder_id: UUID, parent_folder_id: UUID | None
    ) -> KnowledgeFolder:
        """移动普通目录并检查跨空间父级、循环和同级重名。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        # 1. 锁定待移动目录并确认操作者具备当前空间 owner 权限。
        try:
            with self._unit_of_work as unit_of_work:
                # 2. 解析默认根目录后检查循环、同名和乐观版本，再记录事实。
                _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
                folder = _require_folder(
                    unit_of_work.organization, context.workspace_id, folder_id, for_update=True
                )
                if folder.is_default:
                    raise KnowledgeOrganizationConflictError
                if folder.status != "active":
                    raise KnowledgeOrganizationNotFoundError
                parent_folder_id = _resolve_parent_folder(
                    unit_of_work.organization,
                    context.workspace_id,
                    parent_folder_id,
                    account_id,
                )
                _require_parent_folder(
                    unit_of_work.organization, context.workspace_id, parent_folder_id
                )
                _reject_folder_cycle(
                    unit_of_work.organization, context.workspace_id, folder_id, parent_folder_id
                )
                updated = folder.move(parent_folder_id, occurred_at=now)
                _require_folder_name_available(unit_of_work.organization, updated)
                unit_of_work.organization.save_folder(updated)
                _record_organization(
                    unit_of_work,
                    context,
                    folder_id,
                    updated.version,
                    "knowledge.folder.moved",
                    "knowledge.folder.move",
                    "knowledge_folder",
                    now,
                )
                unit_of_work.commit()
                return updated
        except InvalidOrganizationError as error:
            raise KnowledgeOrganizationValidationError from error
        except (OrganizationWriteConflictError, KnowledgeWriteConflictError) as error:
            raise KnowledgeOrganizationConflictError from error

    def delete_folder(self, context: RequestContext, *, folder_id: UUID) -> KnowledgeFolder:
        """逻辑删除空的普通目录，默认根目录和非空目录不可删除。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        # 1. 读取并锁定目录，先拦截默认根目录和非活动状态。
        try:
            with self._unit_of_work as unit_of_work:
                # 2. 活动子目录、活动或回收站文档存在时保持目录事实不变。
                _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
                folder = _require_folder(
                    unit_of_work.organization, context.workspace_id, folder_id, for_update=True
                )
                if folder.is_default:
                    raise KnowledgeOrganizationConflictError
                if folder.status != "active":
                    raise KnowledgeOrganizationNotFoundError
                if unit_of_work.organization.folder_has_children(
                    context.workspace_id, folder_id
                ) or unit_of_work.organization.folder_has_documents(
                    context.workspace_id, folder_id
                ):
                    raise KnowledgeOrganizationConflictError
                deleted = folder.delete(occurred_at=now)
                unit_of_work.organization.save_folder(deleted)
                _record_organization(
                    unit_of_work,
                    context,
                    folder_id,
                    deleted.version,
                    "knowledge.folder.deleted",
                    "knowledge.folder.delete",
                    "knowledge_folder",
                    now,
                )
                unit_of_work.commit()
                return deleted
        except InvalidOrganizationError as error:
            raise KnowledgeOrganizationConflictError from error
        except (OrganizationWriteConflictError, KnowledgeWriteConflictError) as error:
            raise KnowledgeOrganizationConflictError from error

    def restore_folder(self, context: RequestContext, *, folder_id: UUID) -> KnowledgeFolder:
        """恢复普通目录，并要求原父目录仍属于当前空间且处于活动状态。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
                folder = _require_folder(
                    unit_of_work.organization, context.workspace_id, folder_id, for_update=True
                )
                if folder.status != "deleted":
                    raise KnowledgeOrganizationNotFoundError
                _require_parent_folder(
                    unit_of_work.organization, context.workspace_id, folder.parent_folder_id
                )
                restored = folder.restore(occurred_at=now)
                _require_folder_name_available(unit_of_work.organization, restored)
                unit_of_work.organization.save_folder(restored)
                _record_organization(
                    unit_of_work,
                    context,
                    folder_id,
                    restored.version,
                    "knowledge.folder.restored",
                    "knowledge.folder.restore",
                    "knowledge_folder",
                    now,
                )
                unit_of_work.commit()
                return restored
        except InvalidOrganizationError as error:
            raise KnowledgeOrganizationConflictError from error
        except (OrganizationWriteConflictError, KnowledgeWriteConflictError) as error:
            raise KnowledgeOrganizationConflictError from error

    def permanently_delete_folder(self, context: RequestContext, *, folder_id: UUID) -> None:
        """永久删除空的已删除普通目录；默认根目录永不进入该路径。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
            folder = _require_folder(
                unit_of_work.organization, context.workspace_id, folder_id, for_update=True
            )
            if folder.is_default:
                raise KnowledgeOrganizationConflictError
            if folder.status != "deleted":
                raise KnowledgeOrganizationConflictError
            if unit_of_work.organization.folder_has_children(
                context.workspace_id, folder_id
            ) or unit_of_work.organization.folder_has_documents(context.workspace_id, folder_id):
                raise KnowledgeOrganizationConflictError
            unit_of_work.organization.permanently_delete_folder(context.workspace_id, folder_id)
            _record_organization(
                unit_of_work,
                context,
                folder_id,
                folder.version + 1,
                "knowledge.folder.purged",
                "knowledge.folder.purge",
                "knowledge_folder",
                now,
            )
            unit_of_work.commit()

    def list_tags(
        self, context: RequestContext, *, include_deleted: bool = False
    ) -> tuple[KnowledgeTag, ...]:
        """列出当前工作空间标签，删除态仅在显式查询时返回。"""

        account_id = _browser_account(context)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work.knowledge, context.workspace_id, account_id)
            return unit_of_work.organization.list_tags(
                context.workspace_id, include_deleted=include_deleted
            )

    def create_tag(self, context: RequestContext, *, name: str, color: str | None) -> KnowledgeTag:
        """创建工作空间级标签并检查活动名称唯一性。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        # 1. 先校验标签展示字段，再在 owner 事务内检查同名事实。
        tag = KnowledgeTag(
            uuid4(),
            context.workspace_id,
            name.strip(),
            color.strip() if color else None,
            account_id,
            now,
            now,
        )
        try:
            tag.assert_valid()
            with self._unit_of_work as unit_of_work:
                # 2. 标签事实、审计和 Outbox 事件必须原子提交。
                _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
                if unit_of_work.organization.tag_name_exists(context.workspace_id, tag.name):
                    raise KnowledgeOrganizationConflictError
                unit_of_work.organization.add_tag(tag)
                _record_organization(
                    unit_of_work,
                    context,
                    tag.tag_id,
                    tag.version,
                    "knowledge.tag.created",
                    "knowledge.tag.create",
                    "knowledge_tag",
                    now,
                )
                unit_of_work.commit()
        except InvalidOrganizationError as error:
            raise KnowledgeOrganizationValidationError from error
        except (OrganizationWriteConflictError, KnowledgeWriteConflictError) as error:
            raise KnowledgeOrganizationConflictError from error
        return tag

    def rename_tag(
        self, context: RequestContext, *, tag_id: UUID, name: str, color: str | None = None
    ) -> KnowledgeTag:
        """修改标签名称或颜色，不改变文档内容和标签绑定。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        # 1. 锁定标签并生成新的版本事实，避免覆盖并发更新。
        try:
            with self._unit_of_work as unit_of_work:
                # 2. 检查名称冲突后写入标签并发布审计事件。
                _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
                tag = _require_tag(
                    unit_of_work.organization, context.workspace_id, tag_id, for_update=True
                )
                if tag.status != "active":
                    raise KnowledgeOrganizationNotFoundError
                updated = tag.rename(name, occurred_at=now)
                if color is not None:
                    updated = KnowledgeTag(
                        updated.tag_id,
                        updated.workspace_id,
                        updated.name,
                        color.strip(),
                        updated.created_by_account_id,
                        updated.created_at,
                        updated.updated_at,
                        updated.status,
                        updated.deleted_at,
                        updated.version,
                    )
                    updated.assert_valid()
                if unit_of_work.organization.tag_name_exists(
                    context.workspace_id, updated.name, exclude_tag_id=tag_id
                ):
                    raise KnowledgeOrganizationConflictError
                unit_of_work.organization.save_tag(updated)
                _record_organization(
                    unit_of_work,
                    context,
                    tag_id,
                    updated.version,
                    "knowledge.tag.updated",
                    "knowledge.tag.update",
                    "knowledge_tag",
                    now,
                )
                unit_of_work.commit()
                return updated
        except InvalidOrganizationError as error:
            raise KnowledgeOrganizationValidationError from error
        except (OrganizationWriteConflictError, KnowledgeWriteConflictError) as error:
            raise KnowledgeOrganizationConflictError from error

    def delete_tag(self, context: RequestContext, *, tag_id: UUID) -> KnowledgeTag:
        """逻辑删除标签并解除全部文档绑定，但保留文档本身。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        # 1. 锁定活动标签，避免删除过程中出现并发绑定或版本覆盖。
        with self._unit_of_work as unit_of_work:
            _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
            tag = _require_tag(
                unit_of_work.organization, context.workspace_id, tag_id, for_update=True
            )
            if tag.status != "active":
                raise KnowledgeOrganizationNotFoundError
            deleted = tag.delete(occurred_at=now)
            # 2. 绑定属于标签生命周期的一部分；删除后恢复不得带回历史关系。
            unit_of_work.organization.unbind_tag_documents(context.workspace_id, tag_id)
            unit_of_work.organization.save_tag(deleted)
            _record_organization(
                unit_of_work,
                context,
                tag_id,
                deleted.version,
                "knowledge.tag.deleted",
                "knowledge.tag.delete",
                "knowledge_tag",
                now,
            )
            unit_of_work.commit()
            return deleted

    def restore_tag(self, context: RequestContext, *, tag_id: UUID) -> KnowledgeTag:
        """恢复标签；活动标签同名时拒绝恢复。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        # 1. 锁定删除标签并确认恢复后的名称不会与活动标签冲突。
        with self._unit_of_work as unit_of_work:
            _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
            tag = _require_tag(
                unit_of_work.organization, context.workspace_id, tag_id, for_update=True
            )
            if tag.status != "deleted" or unit_of_work.organization.tag_name_exists(
                context.workspace_id, tag.name
            ):
                raise KnowledgeOrganizationConflictError
            restored = KnowledgeTag(
                tag.tag_id,
                tag.workspace_id,
                tag.name,
                tag.color,
                tag.created_by_account_id,
                tag.created_at,
                now,
                "active",
                None,
                tag.version + 1,
            )
            restored.assert_valid()
            # 2. 保存恢复版本，并把审计与 Outbox 事实放入同一事务。
            unit_of_work.organization.save_tag(restored)
            _record_organization(
                unit_of_work,
                context,
                tag_id,
                restored.version,
                "knowledge.tag.restored",
                "knowledge.tag.restore",
                "knowledge_tag",
                now,
            )
            unit_of_work.commit()
            return restored

    def bind_folder(
        self, context: RequestContext, *, document_id: UUID, folder_ids: Iterable[UUID]
    ) -> tuple[KnowledgeFolder, ...]:
        """替换文档主目录，调用方必须提供且只能提供一个目录 ID。"""

        account_id = _browser_account(context)
        ids = tuple(dict.fromkeys(folder_ids))
        if len(ids) != 1:
            raise KnowledgeOrganizationConflictError
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
            _require_active_document(unit_of_work.knowledge, context.workspace_id, document_id)
            for folder_id in ids:
                folder = _require_folder(unit_of_work.organization, context.workspace_id, folder_id)
                if folder.status != "active":
                    raise KnowledgeOrganizationNotFoundError
                unit_of_work.organization.bind_document_folder(
                    context.workspace_id, document_id, folder_id, occurred_at=now
                )
            _record_organization(
                unit_of_work,
                context,
                document_id,
                1,
                "knowledge.document.folders.bound",
                "knowledge.document.folder.bind",
                "document",
                now,
            )
            unit_of_work.commit()
            return unit_of_work.organization.list_document_folders(
                context.workspace_id, document_id
            )

    def unbind_folder(
        self, context: RequestContext, *, document_id: UUID, folder_id: UUID
    ) -> tuple[KnowledgeFolder, ...]:
        """解除普通主目录时回落默认根目录，避免活动文档出现无主目录状态。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            # 1. 先验证 Owner、活动文档和当前目录关系，跨空间或非当前目录均失败关闭。
            _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
            _require_active_document(unit_of_work.knowledge, context.workspace_id, document_id)
            _require_folder(unit_of_work.organization, context.workspace_id, folder_id)
            current = unit_of_work.organization.get_document_folder(
                context.workspace_id, document_id
            )
            if current is None or current.folder_id != folder_id:
                raise KnowledgeOrganizationConflictError
            if current.is_default:
                raise KnowledgeOrganizationConflictError
            # 2. 普通目录解绑采用替换语义回落默认根目录，并与审计、Outbox 原子提交。
            default_folder = unit_of_work.organization.ensure_default_folder(
                context.workspace_id,
                account_id,
                occurred_at=now,
            )
            unit_of_work.organization.bind_document_folder(
                context.workspace_id,
                document_id,
                default_folder.folder_id,
                occurred_at=now,
            )
            _record_organization(
                unit_of_work,
                context,
                document_id,
                1,
                "knowledge.document.folder.unbound",
                "knowledge.document.folder.unbind",
                "document",
                now,
            )
            unit_of_work.commit()
            return unit_of_work.organization.list_document_folders(
                context.workspace_id, document_id
            )

    def bind_tag(
        self, context: RequestContext, *, document_id: UUID, tag_ids: Iterable[UUID]
    ) -> tuple[KnowledgeTag, ...]:
        """幂等增加文档标签绑定，标签关系始终限制在当前工作空间。"""

        account_id = _browser_account(context)
        ids = tuple(dict.fromkeys(tag_ids))
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
            _require_active_document(unit_of_work.knowledge, context.workspace_id, document_id)
            for tag_id in ids:
                tag = _require_tag(unit_of_work.organization, context.workspace_id, tag_id)
                if tag.status != "active":
                    raise KnowledgeOrganizationNotFoundError
                unit_of_work.organization.bind_document_tag(
                    context.workspace_id, document_id, tag_id
                )
            _record_organization(
                unit_of_work,
                context,
                document_id,
                1,
                "knowledge.document.tags.bound",
                "knowledge.document.tag.bind",
                "document",
                now,
            )
            unit_of_work.commit()
            return unit_of_work.organization.list_document_tags(context.workspace_id, document_id)

    def unbind_tag(
        self, context: RequestContext, *, document_id: UUID, tag_id: UUID
    ) -> tuple[KnowledgeTag, ...]:
        """解除文档标签绑定，并显式校验标签属于当前工作空间。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
            _require_active_document(unit_of_work.knowledge, context.workspace_id, document_id)
            # 标签解绑同样需要显式证明标签属于当前空间，不能把“不存在”当作成功。
            _require_tag(unit_of_work.organization, context.workspace_id, tag_id)
            unit_of_work.organization.unbind_document_tag(context.workspace_id, document_id, tag_id)
            _record_organization(
                unit_of_work,
                context,
                document_id,
                1,
                "knowledge.document.tag.unbound",
                "knowledge.document.tag.unbind",
                "document",
                now,
            )
            unit_of_work.commit()
            return unit_of_work.organization.list_document_tags(context.workspace_id, document_id)

    def set_favorite(self, context: RequestContext, *, document_id: UUID, favorite: bool) -> bool:
        """切换当前活动成员的文档收藏状态。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work.knowledge, context.workspace_id, account_id)
            _require_active_document(unit_of_work.knowledge, context.workspace_id, document_id)
            if favorite:
                unit_of_work.organization.add_favorite(
                    context.workspace_id, account_id, document_id, occurred_at=now
                )
            else:
                unit_of_work.organization.remove_favorite(
                    context.workspace_id, account_id, document_id
                )
            _record_organization(
                unit_of_work,
                context,
                document_id,
                1,
                "knowledge.document.favorite.changed",
                "knowledge.document.favorite",
                "document",
                now,
            )
            unit_of_work.commit()
            return favorite

    def list_favorites(self, context: RequestContext, *, limit: int) -> tuple[UUID, ...]:
        """列出当前成员的活动文档收藏，按最近收藏倒序返回。"""

        account_id = _browser_account(context)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work.knowledge, context.workspace_id, account_id)
            return unit_of_work.organization.list_favorite_document_ids(
                context.workspace_id,
                account_id,
                limit=limit,
                authorized_workspace=context.authorized_workspace,
                department_ids=context.authorized_department_ids,
                account_ids=context.authorized_account_ids,
                resource_ids=context.authorized_resource_ids,
            )

    def list_trash(self, context: RequestContext, *, limit: int) -> tuple[Document, ...]:
        """列出当前空间回收站文档，不跨空间读取删除事实。"""

        account_id = _browser_account(context)
        with self._unit_of_work as unit_of_work:
            _require_active_member(unit_of_work.knowledge, context.workspace_id, account_id)
            return unit_of_work.organization.list_trash_documents(context.workspace_id, limit=limit)

    def restore_document(self, context: RequestContext, *, document_id: UUID) -> Document:
        """恢复回收站文档，原目录不可用时回落到默认根目录。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        # 1. 锁定删除文档并生成只递增元数据版本的恢复事实。
        with self._unit_of_work as unit_of_work:
            # 2. 恢复组织关系时保留版本内容，目录失效才替换为默认根目录。
            _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
            document = _require_document(
                unit_of_work.knowledge, context.workspace_id, document_id, for_update=True
            )
            try:
                restored = document.restore(occurred_at=now)
            except InvalidOrganizationError as error:
                raise KnowledgeOrganizationConflictError from error
            original_folder = unit_of_work.organization.get_document_folder(
                context.workspace_id, document_id
            )
            unit_of_work.knowledge.save_document(restored)
            if original_folder is None or original_folder.status != "active":
                default_folder = unit_of_work.organization.ensure_default_folder(
                    context.workspace_id,
                    account_id,
                    occurred_at=now,
                )
                unit_of_work.organization.bind_document_folder(
                    context.workspace_id,
                    document_id,
                    default_folder.folder_id,
                    occurred_at=now,
                )
            _record_organization(
                unit_of_work,
                context,
                document_id,
                restored.version,
                "knowledge.document.restored",
                "knowledge.document.restore",
                "document",
                now,
            )
            unit_of_work.commit()
            return restored

    def permanently_delete_document(self, context: RequestContext, *, document_id: UUID) -> None:
        """永久清理已删除文档，并沿用对象、索引和 Outbox 清理链。"""

        account_id = _browser_account(context)
        now = datetime.now(UTC)
        # 1. 在同一事务锁定回收站文档并复核 Owner 身份，避免并发恢复或跨空间清理。
        with self._unit_of_work as unit_of_work:
            _require_owner(unit_of_work.knowledge, context.workspace_id, account_id)
            document = _require_document(
                unit_of_work.knowledge, context.workspace_id, document_id, for_update=True
            )
            if document.status != "deleted":
                raise KnowledgeOrganizationConflictError
            # 2. 运行中任务可能仍在事务外写对象，必须先等待成功回写或失租补偿。
            if unit_of_work.organization.has_running_ingestion_jobs(
                context.workspace_id, document_id
            ):
                raise KnowledgeOrganizationConflictError
            object_keys = unit_of_work.organization.permanently_delete_document(
                context.workspace_id, document_id
            )
            # 3. 数据库事实与外部对象清理意图原子提交，Worker 只消费已提交的稳定对象集合。
            _record_organization(
                unit_of_work,
                context,
                document_id,
                document.version + 1,
                "knowledge.document.trash_purge_requested",
                "knowledge.document.purge",
                "document",
                now,
                event_payload={
                    "resource_type": "knowledge_document",
                    "resource_id": str(document_id),
                    "external_object_keys": list(object_keys),
                    "database_facts_purged": True,
                    "external_cleanup_status": "pending",
                    "purge_trigger": "manual",
                    "requested_by": str(account_id),
                },
            )
            unit_of_work.commit()


def _browser_account(context: RequestContext) -> UUID:
    if (
        context.user_id is None
        or context.user_id != context.actor_id
        or context.authentication_method != "browser_session"
    ):
        raise KnowledgeOrganizationDeniedError
    return context.user_id


def _require_owner(repository: object, workspace_id: UUID, account_id: UUID) -> None:
    access = (
        repository.get_workspace_access(workspace_id, account_id)
        if hasattr(repository, "get_workspace_access")
        else None
    )
    if access != ("active", "owner"):
        raise KnowledgeOrganizationDeniedError


def _require_active_member(repository: object, workspace_id: UUID, account_id: UUID) -> None:
    access = (
        repository.get_workspace_access(workspace_id, account_id)
        if hasattr(repository, "get_workspace_access")
        else None
    )
    if access is None or access[0] != "active":
        raise KnowledgeOrganizationDeniedError


def _require_parent_folder(
    repository: KnowledgeOrganizationRepository, workspace_id: UUID, folder_id: UUID | None
) -> None:
    if folder_id is not None:
        parent = repository.get_folder(workspace_id, folder_id)
        if parent is None or parent.status != "active":
            raise KnowledgeOrganizationNotFoundError


def _resolve_parent_folder(
    repository: KnowledgeOrganizationRepository,
    workspace_id: UUID,
    folder_id: UUID | None,
    created_by_account_id: UUID,
) -> UUID:
    """把 API 的空父级语义统一解析为不可删除的默认根目录。"""

    if folder_id is not None:
        return folder_id
    return repository.ensure_default_folder(
        workspace_id,
        created_by_account_id,
        occurred_at=datetime.now(UTC),
    ).folder_id


def _require_folder(
    repository: KnowledgeOrganizationRepository,
    workspace_id: UUID,
    folder_id: UUID,
    *,
    for_update: bool = False,
) -> KnowledgeFolder:
    folder = repository.get_folder(workspace_id, folder_id, for_update=for_update)
    if folder is None:
        raise KnowledgeOrganizationNotFoundError
    return folder


def _require_tag(
    repository: KnowledgeOrganizationRepository,
    workspace_id: UUID,
    tag_id: UUID,
    *,
    for_update: bool = False,
) -> KnowledgeTag:
    tag = repository.get_tag(workspace_id, tag_id, for_update=for_update)
    if tag is None:
        raise KnowledgeOrganizationNotFoundError
    return tag


def _require_document(
    repository: KnowledgeRepository,
    workspace_id: UUID,
    document_id: UUID,
    *,
    for_update: bool = False,
) -> Document:
    document = repository.get_document(workspace_id, document_id, for_update=for_update)
    if document is None:
        raise KnowledgeOrganizationNotFoundError
    return document


def _require_active_document(
    repository: KnowledgeRepository, workspace_id: UUID, document_id: UUID
) -> Document:
    document = _require_document(repository, workspace_id, document_id)
    if document.status != "active":
        raise KnowledgeOrganizationNotFoundError
    return document


def _require_folder_name_available(
    repository: KnowledgeOrganizationRepository, folder: KnowledgeFolder
) -> None:
    if repository.folder_name_exists(
        folder.workspace_id,
        folder.parent_folder_id,
        folder.name,
        exclude_folder_id=folder.folder_id,
    ):
        raise KnowledgeOrganizationConflictError


def _reject_folder_cycle(
    repository: KnowledgeOrganizationRepository,
    workspace_id: UUID,
    folder_id: UUID,
    parent_folder_id: UUID | None,
) -> None:
    current = parent_folder_id
    visited: set[UUID] = set()
    while current is not None:
        if current == folder_id or current in visited:
            raise KnowledgeOrganizationConflictError
        visited.add(current)
        parent = repository.get_folder(workspace_id, current)
        if parent is None:
            raise KnowledgeOrganizationNotFoundError
        current = parent.parent_folder_id


def _record_organization(
    unit_of_work: KnowledgeOrganizationUnitOfWork,
    context: RequestContext,
    aggregate_id: UUID,
    aggregate_version: int,
    event_type: str,
    action: str,
    resource_type: str,
    occurred_at: datetime,
    *,
    event_payload: dict[str, object] | None = None,
) -> None:
    """审计只保存资源标识与版本，Outbox 只发布可重放的事实意图。"""

    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=aggregate_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes={"aggregate_version": aggregate_version},
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type=event_type,
            workspace_id=context.workspace_id,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=(
                event_payload
                if event_payload is not None
                else {"resource_type": resource_type, "resource_id": str(aggregate_id)}
            ),
        )
    )
