"""验证 P6A-04 工作台与搜索在真实 PostgreSQL 上的授权和发布边界。"""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.knowledge.api.routes import router as knowledge_router
from ai_platform_api.modules.knowledge.application.facts import KnowledgeNotFoundError
from ai_platform_api.persistence.tables import index_versions, ingestion_jobs, retrieval_chunks
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, insert, select, text

from tests.integration.test_p1d01_knowledge_postgres import (
    DEFAULT_DATABASE_URL,
    ROOT,
    KnowledgeHarness,
    context,
    register,
)
from tests.integration.test_p1d01_knowledge_postgres import (
    knowledge_database as _knowledge_database,
)
from tests.integration.test_p6a03_document_download_postgres import (
    _current_menu_snapshot,
    _seed_pre_0073_workspace,
)
from tests.integration.test_p403_tool_task_state_postgres import (
    migration_database as _migration_database,
)

knowledge_database = _knowledge_database
migration_database = _migration_database

_WORKBENCH_API_IDS = {
    "81000000-0000-4000-8000-000000000176",
    "81000000-0000-4000-8000-000000000177",
    "81000000-0000-4000-8000-000000000178",
}


def _migration_config(database_url: str, schema: str) -> Config:
    """为 P6A-04 往返测试建立隔离 Schema 的 Alembic 配置。"""

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "infra/migrations"))
    config.set_main_option(
        "prepend_sys_path",
        f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
    )
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("ai_platform_schema", schema)
    return config


def _create_published_document(
    harness: KnowledgeHarness,
    *,
    owner_context: RequestContext,
    workspace_id: UUID,
    knowledge_base_id: UUID,
    title: str,
    content: str,
    indexed: bool,
) -> UUID:
    """创建一份可选活动索引的合成已发布文档。"""

    content_hash = hashlib.sha256(content.encode()).hexdigest()
    document, version, source = harness.knowledge.create_document(
        owner_context,
        knowledge_base_id=knowledge_base_id,
        title=title,
        source_kind="upload",
        source_name=f"{title}.md",
        original_object_key=f"workspaces/{workspace_id}/uploads/{uuid4()}.md",
        security_level="INTERNAL",
        upload_media_type="text/markdown",
        upload_size_bytes=len(content.encode()),
        upload_content_hash=content_hash,
        upload_scan_status="clean",
        upload_scanner_version="synthetic-scanner-v1",
        upload_scanned_at=datetime.now(UTC),
    )
    if indexed:
        now = datetime.now(UTC)
        index_version_id = uuid4()
        with harness.sessions.begin() as session:
            ingestion_job_id = session.scalar(
                select(ingestion_jobs.c.ingestion_job_id).where(
                    ingestion_jobs.c.document_version_id == version.document_version_id
                )
            )
            assert isinstance(ingestion_job_id, UUID)
            session.execute(
                insert(index_versions).values(
                    index_version_id=index_version_id,
                    workspace_id=workspace_id,
                    knowledge_base_id=knowledge_base_id,
                    document_id=document.document_id,
                    document_version_id=version.document_version_id,
                    ingestion_job_id=ingestion_job_id,
                    source_id=source.source_id,
                    build_no=1,
                    artifact_object_key=f"synthetic/{index_version_id}.json",
                    source_content_hash=content_hash,
                    parsed_content_hash=content_hash,
                    chunker_version="synthetic-char-v1",
                    embedding_model_version="synthetic-embedding-v1",
                    tokenizer_version="synthetic-tokenizer-v1",
                    department_ids=[],
                    visibility="private",
                    security_level="INTERNAL",
                    permission_labels=[],
                    status="active",
                    processing_lane="indexing",
                    attempt_count=1,
                    embedding_attempt_count=1,
                    indexing_attempt_count=1,
                    max_attempts=3,
                    available_at=now,
                    started_at=now,
                    completed_at=now,
                    activated_at=now,
                    chunk_count=1,
                    staged_chunk_count=1,
                    manual_recovery_count=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.execute(
                insert(retrieval_chunks).values(
                    index_version_id=index_version_id,
                    chunk_id=uuid4(),
                    workspace_id=workspace_id,
                    knowledge_base_id=knowledge_base_id,
                    document_id=document.document_id,
                    document_version_id=version.document_version_id,
                    ingestion_job_id=None,
                    source_id=None,
                    sequence_no=1,
                    content=content,
                    content_hash=content_hash,
                    embedding=[0.001] * 1024,
                    keyword_text=content,
                    department_ids=[],
                    visibility="private",
                    security_level="INTERNAL",
                    permission_labels=[],
                    source_position={"page_number": 1},
                    parsed_content_hash=None,
                    parser_name=None,
                    ocr_used=None,
                    active=True,
                )
            )
    harness.knowledge.mark_document_version_ready(
        owner_context,
        knowledge_base_id=knowledge_base_id,
        document_id=document.document_id,
        document_version_id=version.document_version_id,
        content_hash=content_hash,
    )
    harness.knowledge.publish_document_version(
        owner_context,
        knowledge_base_id=knowledge_base_id,
        document_id=document.document_id,
        document_version_id=version.document_version_id,
    )
    return document.document_id


def test_workbench_search_and_access_share_authorized_published_scope(
    knowledge_database: KnowledgeHarness,
) -> None:
    """统计、筛选、正文、最近访问和撤权必须使用同一文档资源交集。"""

    owner = register(knowledge_database, identity="p6a04-owner")
    owner_context = replace(
        context(owner),
        authorized_workspace=True,
        authorized_maximum_security_level="RESTRICTED",
    )
    active_base = knowledge_database.knowledge.create_knowledge_base(
        owner_context,
        name="合成工作台知识库",
        default_visibility="private",
    )
    knowledge_database.knowledge.create_knowledge_base(
        owner_context,
        name="合成空知识库",
        default_visibility="private",
    )
    indexed_document_id = _create_published_document(
        knowledge_database,
        owner_context=owner_context,
        workspace_id=owner.personal_workspace_id,
        knowledge_base_id=active_base.knowledge_base_id,
        title="差旅政策",
        content="差旅报销必须在三十天内提交, 逾期需要补充说明。",
        indexed=True,
    )
    unindexed_document_id = _create_published_document(
        knowledge_database,
        owner_context=owner_context,
        workspace_id=owner.personal_workspace_id,
        knowledge_base_id=active_base.knowledge_base_id,
        title="报销表单说明",
        content="这份合成文档尚未建立活动索引。",
        indexed=False,
    )
    knowledge_database.knowledge_organization.set_favorite(
        owner_context,
        document_id=indexed_document_id,
        favorite=True,
    )

    full = knowledge_database.management.get_personal_workbench(
        owner_context,
        recent_limit=6,
        favorite_limit=6,
    )
    assert full.statistics.knowledge_base_count == 2
    assert full.statistics.document_count == 2
    assert full.statistics.published_document_count == 2
    assert full.statistics.indexed_document_count == 1
    assert full.statistics.pending_index_document_count == 1
    assert [item.document_id for item in full.favorite_documents] == [indexed_document_id]

    # 资源范围中的 UUID 是文档 ID；受限统计只能从文档反推一个知识库。
    resource_context = replace(
        owner_context,
        authorized_workspace=False,
        authorized_resource_ids=frozenset({indexed_document_id}),
    )
    scoped = knowledge_database.management.get_personal_workbench(
        resource_context,
        recent_limit=6,
        favorite_limit=6,
    )
    assert scoped.statistics.knowledge_base_count == 1
    assert scoped.statistics.document_count == 1

    all_matches = knowledge_database.management.search_published_documents(
        owner_context,
        query="报销",
        knowledge_base_id=active_base.knowledge_base_id,
        match_type="all",
        favorite_only=False,
        limit=20,
        offset=0,
    )
    assert all_matches.total == 2
    assert {item.matched_by for item in all_matches.items} == {"content", "title"}
    assert all_matches.unavailable_index_document_count == 1
    assert next(item for item in all_matches.items if item.matched_by == "content").excerpt

    favorites = knowledge_database.management.search_published_documents(
        owner_context,
        query="报销",
        knowledge_base_id=None,
        match_type="all",
        favorite_only=True,
        limit=20,
        offset=0,
    )
    assert [item.document_id for item in favorites.items] == [indexed_document_id]

    masked = knowledge_database.management.search_published_documents(
        replace(owner_context, authorized_field_mask=frozenset({"content"})),
        query="三十天",
        knowledge_base_id=None,
        match_type="content",
        favorite_only=False,
        limit=20,
        offset=0,
    )
    assert masked.items == ()
    assert masked.content_search_available is False

    # HTTP 层复用同一真实服务，验证查询参数、响应模型和 204 写入契约。
    application = FastAPI()
    application.state.knowledge_management_service = knowledge_database.management
    application.include_router(knowledge_router, prefix="/api/v1")
    application.dependency_overrides[trusted_request_context] = lambda: owner_context
    with TestClient(application) as client:
        workbench_response = client.get(
            f"/api/v1/workspaces/{owner.personal_workspace_id}/personal-workbench"
        )
        assert workbench_response.status_code == 200
        assert workbench_response.json()["statistics"]["document_count"] == 2
        search_response = client.get(
            f"/api/v1/workspaces/{owner.personal_workspace_id}/knowledge-search",
            params={"query": "报销", "match_type": "all", "limit": 20, "offset": 0},
        )
        assert search_response.status_code == 200
        assert search_response.json()["total"] == 2
        invalid_page = client.get(
            f"/api/v1/workspaces/{owner.personal_workspace_id}/knowledge-search",
            params={"query": "报销", "offset": 10_001},
        )
        assert invalid_page.status_code == 422
        access_response = client.post(
            f"/api/v1/workspaces/{owner.personal_workspace_id}/personal-workbench/accesses",
            json={"document_id": str(indexed_document_id)},
        )
        assert access_response.status_code == 204

    accessed = knowledge_database.management.get_personal_workbench(
        owner_context,
        recent_limit=6,
        favorite_limit=6,
    )
    assert accessed.recent_documents[0].document_id == indexed_document_id
    assert accessed.recent_documents[0].last_accessed_at is not None

    revoked_context = replace(resource_context, authorized_resource_ids=frozenset())
    revoked = knowledge_database.management.get_personal_workbench(
        revoked_context,
        recent_limit=6,
        favorite_limit=6,
    )
    assert revoked.statistics.document_count == 0
    assert revoked.recent_documents == ()
    with pytest.raises(KnowledgeNotFoundError):
        knowledge_database.management.record_document_access(
            revoked_context,
            document_id=unindexed_document_id,
        )


def test_p6a04_empty_migration_roundtrip() -> None:
    """空 Schema 往返必须同步最近访问表、接口绑定和 Revision 指针。"""

    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p6a04_migration_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    config = _migration_config(database_url, schema)
    try:
        command.upgrade(config, "20260825_0073")
        command.upgrade(config, "head")
        with admin_engine.connect() as connection:
            assert connection.scalar(
                text(f'SELECT version_num FROM "{schema}".alembic_version')
            ) == ("20260825_0074")
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = :schema AND table_name = 'document_accesses'"
                    ),
                    {"schema": schema},
                )
                == 1
            )
            assert (
                connection.scalar(
                    text(
                        f'SELECT count(*) FROM "{schema}".registered_menu_api_bindings '
                        "WHERE api_resource_id IN ("
                        "'81000000-0000-4000-8000-000000000176'::uuid, "
                        "'81000000-0000-4000-8000-000000000177'::uuid, "
                        "'81000000-0000-4000-8000-000000000178'::uuid)"
                    )
                )
                == 3
            )

        command.downgrade(config, "20260825_0073")
        with admin_engine.connect() as connection:
            assert connection.scalar(
                text(f'SELECT version_num FROM "{schema}".alembic_version')
            ) == ("20260825_0073")
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = :schema AND table_name = 'document_accesses'"
                    ),
                    {"schema": schema},
                )
                == 0
            )

        command.upgrade(config, "head")
        with admin_engine.connect() as connection:
            assert connection.scalar(
                text(f'SELECT version_num FROM "{schema}".alembic_version')
            ) == ("20260825_0074")
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


def test_p6a04_existing_menu_snapshot_roundtrip(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    """既有菜单发布升级到 Registry 27 后必须可精确恢复 Registry 26。"""

    config, connection, schema, _ = migration_database
    workspace = _seed_pre_0073_workspace(migration_database)

    command.upgrade(config, "head")
    connection.commit()
    release_id, snapshot = _current_menu_snapshot(connection, schema, workspace.workspace_id)
    assert connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version')) == (
        "20260825_0074"
    )
    assert snapshot["registry_version"] == 27
    assert {
        str(item["api_resource_id"])
        for item in snapshot["menu_api_bindings"]
        if str(item["api_resource_id"]) in _WORKBENCH_API_IDS
    } == _WORKBENCH_API_IDS

    command.downgrade(config, "20260825_0073")
    connection.commit()
    restored_release_id, restored_snapshot = _current_menu_snapshot(
        connection,
        schema,
        workspace.workspace_id,
    )
    assert restored_release_id != release_id
    assert restored_snapshot["registry_version"] == 26
    assert not {
        str(item["api_resource_id"])
        for item in restored_snapshot["menu_api_bindings"]
        if str(item["api_resource_id"]) in _WORKBENCH_API_IDS
    }

    command.upgrade(config, "head")
    connection.commit()
    assert connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version')) == (
        "20260825_0074"
    )
