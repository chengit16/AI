"""验证 P6A-02 的默认根目录、单主目录和存量迁移不变量。"""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.knowledge.application.organization import (
    KnowledgeOrganizationConflictError,
    KnowledgeOrganizationService,
)
from ai_platform_api.modules.knowledge.domain.organization import (
    DEFAULT_FOLDER_NAME,
    InvalidOrganizationError,
    KnowledgeFolder,
    KnowledgeOrganizationUnitOfWork,
    KnowledgeTag,
    default_folder_id,
)

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000202")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000202")
NOW = datetime(2026, 8, 23, 8, 0, tzinfo=UTC)


class RecordingWriter:
    """记录应用服务写入的审计或 Outbox 事实。"""

    def __init__(self) -> None:
        self.records: list[Any] = []

    def add(self, record: Any) -> None:
        self.records.append(record)


class OrganizationScenario:
    """提供目录解绑和永久删除所需的最小组织 Repository 行为。"""

    def __init__(self, current: KnowledgeFolder, *, object_keys: tuple[str, ...] = ()) -> None:
        self.current = current
        self.root = default_folder()
        self.tag: KnowledgeTag | None = None
        self.object_keys = object_keys
        self.purged_document_id: UUID | None = None
        self.unbound_tag_id: UUID | None = None
        self.running_ingestion = False
        self.favorite_scope: (
            tuple[bool, frozenset[UUID], frozenset[UUID], frozenset[UUID]] | None
        ) = None

    def get_folder(
        self, workspace_id: UUID, folder_id: UUID, *, for_update: bool = False
    ) -> KnowledgeFolder | None:
        del for_update
        if workspace_id != WORKSPACE_ID:
            return None
        return next(
            (folder for folder in (self.current, self.root) if folder.folder_id == folder_id),
            None,
        )

    def get_document_folder(self, workspace_id: UUID, document_id: UUID) -> KnowledgeFolder | None:
        del document_id
        return self.current if workspace_id == WORKSPACE_ID else None

    def ensure_default_folder(
        self, workspace_id: UUID, created_by_account_id: UUID, *, occurred_at: datetime
    ) -> KnowledgeFolder:
        del created_by_account_id, occurred_at
        assert workspace_id == WORKSPACE_ID
        return self.root

    def bind_document_folder(
        self,
        workspace_id: UUID,
        document_id: UUID,
        folder_id: UUID,
        *,
        occurred_at: datetime,
    ) -> None:
        del document_id, occurred_at
        assert workspace_id == WORKSPACE_ID
        assert folder_id == self.root.folder_id
        self.current = self.root

    def list_document_folders(
        self, workspace_id: UUID, document_id: UUID
    ) -> tuple[KnowledgeFolder, ...]:
        del document_id
        return (self.current,) if workspace_id == WORKSPACE_ID else ()

    def get_tag(
        self, workspace_id: UUID, tag_id: UUID, *, for_update: bool = False
    ) -> KnowledgeTag | None:
        del for_update
        if workspace_id == WORKSPACE_ID and self.tag is not None and self.tag.tag_id == tag_id:
            return self.tag
        return None

    def unbind_tag_documents(self, workspace_id: UUID, tag_id: UUID) -> None:
        assert workspace_id == WORKSPACE_ID
        self.unbound_tag_id = tag_id

    def save_tag(self, tag: KnowledgeTag) -> None:
        self.tag = tag

    def permanently_delete_document(self, workspace_id: UUID, document_id: UUID) -> tuple[str, ...]:
        assert workspace_id == WORKSPACE_ID
        self.purged_document_id = document_id
        return self.object_keys

    def has_running_ingestion_jobs(self, workspace_id: UUID, document_id: UUID) -> bool:
        del document_id
        assert workspace_id == WORKSPACE_ID
        return self.running_ingestion

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
    ) -> tuple[UUID, ...]:
        assert workspace_id == WORKSPACE_ID
        assert account_id == ACCOUNT_ID
        assert limit == 100
        self.favorite_scope = (
            authorized_workspace,
            department_ids,
            account_ids,
            resource_ids,
        )
        return tuple(resource_ids)


class KnowledgeScenario:
    """提供 Owner 身份和单篇文档读取事实。"""

    def __init__(self, document_id: UUID, *, status: str, version: int = 1) -> None:
        self.document_id = document_id
        self.document = SimpleNamespace(status=status, version=version)

    def get_workspace_access(self, workspace_id: UUID, account_id: UUID) -> tuple[str, str]:
        assert workspace_id == WORKSPACE_ID
        assert account_id == ACCOUNT_ID
        return ("active", "owner")

    def get_document(
        self, workspace_id: UUID, document_id: UUID, *, for_update: bool = False
    ) -> Any:
        del for_update
        if workspace_id == WORKSPACE_ID and document_id == self.document_id:
            return self.document
        return None


class OrganizationUnitOfWorkScenario:
    """把组织、审计和 Outbox 合并为可断言的单事务场景。"""

    def __init__(self, organization: OrganizationScenario, knowledge: KnowledgeScenario) -> None:
        self.organization = organization
        self.knowledge = knowledge
        self.audit = RecordingWriter()
        self.outbox = RecordingWriter()
        self.committed = False

    def __enter__(self) -> "OrganizationUnitOfWorkScenario":
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def commit(self) -> None:
        self.committed = True


def default_folder() -> KnowledgeFolder:
    """构造合法的工作空间默认根目录。"""

    return KnowledgeFolder(
        folder_id=default_folder_id(WORKSPACE_ID),
        workspace_id=WORKSPACE_ID,
        name=DEFAULT_FOLDER_NAME,
        parent_folder_id=None,
        created_by_account_id=ACCOUNT_ID,
        created_at=NOW,
        updated_at=NOW,
        is_default=True,
    )


def browser_context() -> RequestContext:
    """生成只用于应用层边界测试的可信浏览器上下文。"""

    return RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        authentication_method="browser_session",
        trace=TraceContext("a" * 32, "b" * 16),
    )


def test_default_folder_is_stable_and_cannot_change_shape() -> None:
    root = default_folder()
    root.assert_valid()
    assert default_folder_id(WORKSPACE_ID) == root.folder_id

    with pytest.raises(InvalidOrganizationError):
        root.rename("用户目录", occurred_at=NOW)
    with pytest.raises(InvalidOrganizationError):
        root.move(None, occurred_at=NOW)
    with pytest.raises(InvalidOrganizationError):
        root.delete(occurred_at=NOW)


def test_default_folder_rejects_tampered_identity_or_state() -> None:
    invalid = KnowledgeFolder(
        folder_id=UUID("20000000-0000-4000-8000-000000000299"),
        workspace_id=WORKSPACE_ID,
        name=DEFAULT_FOLDER_NAME,
        parent_folder_id=None,
        created_by_account_id=ACCOUNT_ID,
        created_at=NOW,
        updated_at=NOW,
        is_default=True,
    )

    with pytest.raises(InvalidOrganizationError):
        invalid.assert_valid()


def test_bind_folder_requires_exactly_one_primary_folder() -> None:
    service = KnowledgeOrganizationService(cast("KnowledgeOrganizationUnitOfWork", None))
    with pytest.raises(KnowledgeOrganizationConflictError):
        service.bind_folder(
            browser_context(),
            document_id=UUID("40000000-0000-4000-8000-000000000202"),
            folder_ids=(
                default_folder_id(WORKSPACE_ID),
                UUID("20000000-0000-4000-8000-000000000203"),
            ),
        )


def test_unbind_folder_moves_document_to_default_root() -> None:
    document_id = UUID("40000000-0000-4000-8000-000000000202")
    ordinary = KnowledgeFolder(
        folder_id=UUID("20000000-0000-4000-8000-000000000203"),
        workspace_id=WORKSPACE_ID,
        name="合成目录",
        parent_folder_id=default_folder_id(WORKSPACE_ID),
        created_by_account_id=ACCOUNT_ID,
        created_at=NOW,
        updated_at=NOW,
    )
    scenario = OrganizationUnitOfWorkScenario(
        OrganizationScenario(ordinary),
        KnowledgeScenario(document_id, status="active"),
    )

    folders = KnowledgeOrganizationService(
        cast("KnowledgeOrganizationUnitOfWork", scenario)
    ).unbind_folder(browser_context(), document_id=document_id, folder_id=ordinary.folder_id)

    assert folders == (scenario.organization.root,)
    assert scenario.committed is True
    assert scenario.outbox.records[0].event_type == "knowledge.document.folder.unbound"


def test_unbind_default_folder_is_rejected() -> None:
    document_id = UUID("40000000-0000-4000-8000-000000000203")
    scenario = OrganizationUnitOfWorkScenario(
        OrganizationScenario(default_folder()),
        KnowledgeScenario(document_id, status="active"),
    )

    with pytest.raises(KnowledgeOrganizationConflictError):
        KnowledgeOrganizationService(
            cast("KnowledgeOrganizationUnitOfWork", scenario)
        ).unbind_folder(
            browser_context(),
            document_id=document_id,
            folder_id=default_folder_id(WORKSPACE_ID),
        )

    assert scenario.committed is False


def test_manual_purge_emits_external_cleanup_intent_with_collected_keys() -> None:
    document_id = UUID("40000000-0000-4000-8000-000000000204")
    object_key = f"workspaces/{WORKSPACE_ID}/uploads/synthetic.txt"
    scenario = OrganizationUnitOfWorkScenario(
        OrganizationScenario(default_folder(), object_keys=(object_key,)),
        KnowledgeScenario(document_id, status="deleted", version=4),
    )

    KnowledgeOrganizationService(
        cast("KnowledgeOrganizationUnitOfWork", scenario)
    ).permanently_delete_document(browser_context(), document_id=document_id)

    event = scenario.outbox.records[0]
    assert scenario.organization.purged_document_id == document_id
    assert event.event_type == "knowledge.document.trash_purge_requested"
    assert event.aggregate_version == 5
    assert event.payload["external_object_keys"] == [object_key]
    assert event.payload["database_facts_purged"] is True
    assert scenario.committed is True


def test_manual_purge_rejects_running_ingestion_job() -> None:
    document_id = UUID("40000000-0000-4000-8000-000000000205")
    organization = OrganizationScenario(default_folder())
    organization.running_ingestion = True
    scenario = OrganizationUnitOfWorkScenario(
        organization,
        KnowledgeScenario(document_id, status="deleted", version=2),
    )

    with pytest.raises(KnowledgeOrganizationConflictError):
        KnowledgeOrganizationService(
            cast("KnowledgeOrganizationUnitOfWork", scenario)
        ).permanently_delete_document(browser_context(), document_id=document_id)

    assert scenario.organization.purged_document_id is None
    assert scenario.committed is False


def test_favorite_collection_passes_authorized_resource_scope_to_repository() -> None:
    document_id = UUID("40000000-0000-4000-8000-000000000206")
    scenario = OrganizationUnitOfWorkScenario(
        OrganizationScenario(default_folder()),
        KnowledgeScenario(document_id, status="active"),
    )
    context = replace(browser_context(), authorized_resource_ids=frozenset({document_id}))

    favorites = KnowledgeOrganizationService(
        cast("KnowledgeOrganizationUnitOfWork", scenario)
    ).list_favorites(context, limit=100)

    assert favorites == (document_id,)
    assert scenario.organization.favorite_scope == (
        False,
        frozenset(),
        frozenset(),
        frozenset({document_id}),
    )


def test_delete_tag_unbinds_all_documents_without_deleting_document() -> None:
    document_id = UUID("40000000-0000-4000-8000-000000000207")
    tag = KnowledgeTag(
        tag_id=UUID("30000000-0000-4000-8000-000000000202"),
        workspace_id=WORKSPACE_ID,
        name="合成待删除标签",
        color="#1677ff",
        created_by_account_id=ACCOUNT_ID,
        created_at=NOW,
        updated_at=NOW,
    )
    organization = OrganizationScenario(default_folder())
    organization.tag = tag
    scenario = OrganizationUnitOfWorkScenario(
        organization,
        KnowledgeScenario(document_id, status="active"),
    )

    deleted = KnowledgeOrganizationService(
        cast("KnowledgeOrganizationUnitOfWork", scenario)
    ).delete_tag(browser_context(), tag_id=tag.tag_id)

    assert deleted.status == "deleted"
    assert scenario.organization.unbound_tag_id == tag.tag_id
    assert scenario.knowledge.document.status == "active"
    assert scenario.committed is True
    assert scenario.outbox.records[0].event_type == "knowledge.tag.deleted"


def test_p6a02_migration_declares_root_backfill_and_single_binding_key() -> None:
    migration = (
        Path(__file__).parents[2]
        / "infra/migrations/versions/20260823_0072_p6a02_knowledge_organization.py"
    )
    source = migration.read_text(encoding="utf-8")

    assert 'PrimaryKeyConstraint("workspace_id", "document_id")' in source
    assert "ON CONFLICT (workspace_id, document_id) DO NOTHING" in source
    assert "membership_type = 'owner'" in source
    assert "uuid5(_DEFAULT_FOLDER_NAMESPACE, str(workspace_id))" in source
    assert 'snapshot["registry_version"] = 25' in source
    assert "knowledge.document.folder.bind" in source
    assert "knowledge.document.tag.bind" in source
    assert "_publish_upgraded_menu_snapshots(schema)" in source
    assert "_reject_destructive_downgrade(schema)" in source
