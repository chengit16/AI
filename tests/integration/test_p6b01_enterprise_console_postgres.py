"""验证 P6B-01 企业控制台真实统计、隔离和 Migration 往返。"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.persistence.tables import (
    documents,
    index_versions,
    ingestion_jobs,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, insert, select, text, update

from tests.integration.test_p1d01_knowledge_postgres import (
    KnowledgeHarness,
    context,
    join_enterprise,
    register,
)
from tests.integration.test_p1d01_knowledge_postgres import (
    knowledge_database as _knowledge_database,
)
from tests.integration.test_p6a03_document_download_postgres import (
    _append_followup_menu_release,
    _current_menu_snapshot,
    _seed_pre_0073_workspace,
)
from tests.integration.test_p403_tool_task_state_postgres import (
    migration_database as _migration_database,
)

knowledge_database = _knowledge_database
migration_database = _migration_database

_CONSOLE_API_ID = "81000000-0000-4000-8000-000000000183"
_CONSOLE_MENU_ID = "82000000-0000-4000-8000-000000000258"


def _authorized(
    context_value: RequestContext, *, clearance: SecurityLevel = "RESTRICTED"
) -> RequestContext:
    """冻结企业控制台所需的 PDP 全空间许可和密级上限。"""

    return replace(
        context_value,
        authorized_permission_code="workspace.overview.access",
        authorized_workspace=True,
        authorized_maximum_security_level=clearance,
    )


def _add_months(value: datetime, offset: int) -> datetime:
    """按日历月份移动 UTC 月初，测试不依赖固定天数。"""

    absolute = value.year * 12 + value.month - 1 + offset
    year, month_index = divmod(absolute, 12)
    return value.replace(year=year, month=month_index + 1, day=1)


def _create_document(
    harness: KnowledgeHarness,
    owner_context: RequestContext,
    knowledge_base_id: UUID,
    *,
    title: str,
    security_level: SecurityLevel,
    size_bytes: int,
    published: bool,
) -> tuple[UUID, UUID]:
    """创建带真实用量与入库任务的合成文档，可选择发布首版。"""

    content_hash = hashlib.sha256(title.encode()).hexdigest()
    document, version, _ = harness.knowledge.create_document(
        owner_context,
        knowledge_base_id=knowledge_base_id,
        title=title,
        source_kind="upload",
        source_name=f"{title}.txt",
        original_object_key=(f"workspaces/{owner_context.workspace_id}/uploads/{uuid4().hex}.txt"),
        security_level=security_level,
        upload_media_type="text/plain",
        upload_size_bytes=size_bytes,
        upload_content_hash=content_hash,
        upload_scan_status="clean",
        upload_scanner_version="synthetic-p6b01-scanner-v1",
        upload_scanned_at=datetime.now(UTC),
    )
    if published:
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
    return document.document_id, version.document_version_id


def _set_ingestion_status(
    harness: KnowledgeHarness,
    document_id: UUID,
    status: str,
) -> None:
    """把合成入库任务收敛到合法终态，精确构造控制台状态计数。"""

    now = datetime.now(UTC)
    values: dict[str, object] = {"status": status, "attempt_count": 1, "updated_at": now}
    if status == "failed":
        values.update(
            completed_at=now,
            failure_stage="parse",
            error_code="SYNTHETIC_P6B01_FAILURE",
            error_message="合成入库失败",
        )
    elif status == "succeeded":
        values.update(
            completed_at=now,
            parsed_content_hash="a" * 64,
            parser_name="synthetic-p6b01-parser-v1",
            ocr_used=False,
            page_count=1,
            block_count=1,
        )
    with harness.sessions.begin() as session:
        session.execute(
            update(ingestion_jobs)
            .where(ingestion_jobs.c.document_id == document_id)
            .values(**values)
        )


def _add_queued_index(harness: KnowledgeHarness, document_id: UUID) -> None:
    """为同一处理中文档追加 queued 索引，验证跨任务类型按文档去重。"""

    with harness.sessions.begin() as session:
        job = session.execute(
            select(
                ingestion_jobs.c.ingestion_job_id,
                ingestion_jobs.c.workspace_id,
                ingestion_jobs.c.knowledge_base_id,
                ingestion_jobs.c.document_version_id,
                ingestion_jobs.c.source_id,
                ingestion_jobs.c.source_content_hash,
            ).where(ingestion_jobs.c.document_id == document_id)
        ).one()
        now = datetime.now(UTC)
        session.execute(
            insert(index_versions).values(
                index_version_id=uuid4(),
                workspace_id=job.workspace_id,
                knowledge_base_id=job.knowledge_base_id,
                document_id=document_id,
                document_version_id=job.document_version_id,
                ingestion_job_id=job.ingestion_job_id,
                source_id=job.source_id,
                build_no=1,
                artifact_object_key=f"synthetic/p6b01/{document_id}.json",
                source_content_hash=job.source_content_hash,
                parsed_content_hash=job.source_content_hash,
                chunker_version="synthetic-p6b01-chunker-v1",
                embedding_model_version="synthetic-p6b01-embedding-v1",
                tokenizer_version="synthetic-p6b01-tokenizer-v1",
                department_ids=[],
                visibility="workspace",
                security_level="PUBLIC",
                permission_labels=[],
                status="queued",
                processing_lane="embedding",
                attempt_count=0,
                embedding_attempt_count=0,
                indexing_attempt_count=0,
                max_attempts=3,
                available_at=now,
                manual_recovery_count=0,
                created_at=now,
                updated_at=now,
            )
        )


def test_enterprise_console_aggregates_real_postgres_and_isolates_workspaces(
    knowledge_database: KnowledgeHarness,
) -> None:
    """成员、内容、状态、用量、趋势和最近内容必须共享空间与密级边界。"""

    owner = register(knowledge_database, identity="p6b01-owner")
    member = register(knowledge_database, identity="p6b01-member")
    outsider = register(knowledge_database, identity="p6b01-outsider")
    workspace_id, owner_context, _ = join_enterprise(knowledge_database, owner, member)
    knowledge_base = knowledge_database.knowledge.create_knowledge_base(
        owner_context,
        name="合成企业知识库",
        default_visibility="workspace",
    )
    public_id, _ = _create_document(
        knowledge_database,
        owner_context,
        knowledge_base.knowledge_base_id,
        title="合成公开制度",
        security_level="PUBLIC",
        size_bytes=100,
        published=True,
    )
    internal_id, _ = _create_document(
        knowledge_database,
        owner_context,
        knowledge_base.knowledge_base_id,
        title="合成内部制度",
        security_level="INTERNAL",
        size_bytes=200,
        published=True,
    )
    confidential_id, _ = _create_document(
        knowledge_database,
        owner_context,
        knowledge_base.knowledge_base_id,
        title="合成机密草稿",
        security_level="CONFIDENTIAL",
        size_bytes=300,
        published=False,
    )
    restricted_id, _ = _create_document(
        knowledge_database,
        owner_context,
        knowledge_base.knowledge_base_id,
        title="合成受限制度",
        security_level="RESTRICTED",
        size_bytes=400,
        published=True,
    )
    deleted_base = knowledge_database.knowledge.create_knowledge_base(
        owner_context,
        name="合成已删除知识库",
        default_visibility="workspace",
    )
    deleted_id, _ = _create_document(
        knowledge_database,
        owner_context,
        deleted_base.knowledge_base_id,
        title="合成已删除文档",
        security_level="PUBLIC",
        size_bytes=500,
        published=False,
    )
    knowledge_database.knowledge.delete_document(
        owner_context,
        knowledge_base_id=deleted_base.knowledge_base_id,
        document_id=deleted_id,
    )
    knowledge_database.knowledge.delete_knowledge_base(
        owner_context,
        knowledge_base_id=deleted_base.knowledge_base_id,
    )

    # 同一文档同时存在入库与索引处理中事实，聚合仍只能计为一篇。
    _add_queued_index(knowledge_database, public_id)
    _set_ingestion_status(knowledge_database, internal_id, "failed")
    _set_ingestion_status(knowledge_database, confidential_id, "succeeded")
    _set_ingestion_status(knowledge_database, restricted_id, "succeeded")

    # 另一个企业空间创建同名高敏内容，任何统计和最近内容都不得串入目标空间。
    outsider_workspace = knowledge_database.enterprise.create(
        context(outsider), name="合成隔离企业"
    )
    outsider_context = context(outsider, outsider_workspace.workspace_id)
    outsider_base = knowledge_database.knowledge.create_knowledge_base(
        outsider_context,
        name="合成隔离知识库",
        default_visibility="workspace",
    )
    outsider_id, _ = _create_document(
        knowledge_database,
        outsider_context,
        outsider_base.knowledge_base_id,
        title="绝不能出现在目标企业",
        security_level="PUBLIC",
        size_bytes=9_999,
        published=True,
    )

    current_month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    first_month = _add_months(current_month, -5)
    third_month = _add_months(current_month, -3)
    with knowledge_database.engine.begin() as connection:
        dates = {
            public_id: first_month,
            internal_id: third_month,
            confidential_id: current_month,
            restricted_id: current_month,
            deleted_id: first_month,
            outsider_id: current_month,
        }
        for order, (document_id, created_at) in enumerate(dates.items()):
            connection.execute(
                update(documents)
                .where(documents.c.document_id == document_id)
                .values(
                    created_at=created_at,
                    updated_at=created_at.replace(hour=min(order + 1, 23)),
                )
            )

    full = knowledge_database.enterprise.get_console_snapshot(
        _authorized(context(owner, workspace_id)),
        workspace_id=workspace_id,
    )
    assert full.statistics.active_member_count == 2
    assert full.statistics.active_knowledge_base_count == 1
    assert full.statistics.active_document_count == 4
    assert full.statistics.published_document_count == 3
    assert full.statistics.processing_document_count == 1
    assert full.statistics.failed_document_count == 1
    assert full.statistics.storage_used_bytes == 1_500
    assert full.statistics.storage_limit_bytes > full.statistics.storage_used_bytes
    assert [point.period for point in full.trend] == sorted(point.period for point in full.trend)
    assert [point.document_count for point in full.trend] == [1, 0, 1, 0, 0, 2]
    assert [item.title for item in full.recent_documents[:2]] == [
        "合成受限制度",
        "合成机密草稿",
    ]
    assert "绝不能出现在目标企业" not in {item.title for item in full.recent_documents}
    assert "合成已删除文档" not in {item.title for item in full.recent_documents}

    internal_only = knowledge_database.enterprise.get_console_snapshot(
        _authorized(context(owner, workspace_id), clearance="INTERNAL"),
        workspace_id=workspace_id,
    )
    assert internal_only.statistics.active_document_count == 2
    assert internal_only.statistics.published_document_count == 2
    assert {item.title for item in internal_only.recent_documents} == {
        "合成公开制度",
        "合成内部制度",
    }
    masked = knowledge_database.enterprise.get_console_snapshot(
        replace(
            _authorized(context(owner, workspace_id)),
            authorized_field_mask=frozenset({"title"}),
        ),
        workspace_id=workspace_id,
    )
    assert masked.recent_documents == ()


def test_revision_0076_empty_schema_roundtrip(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    """空 Schema 升级、降级和再升级必须精确维护控制台绑定。"""

    config, connection, schema, _ = migration_database
    command.upgrade(config, "20260830_0075")
    command.upgrade(config, "20260831_0076")
    connection.commit()
    assert connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version')) == (
        "20260831_0076"
    )
    assert (
        connection.scalar(
            text(
                f'SELECT count(*) FROM "{schema}".registered_menu_api_bindings '
                "WHERE menu_id = CAST(:menu_id AS uuid) AND api_resource_id = CAST(:api_id AS uuid)"
            ),
            {"menu_id": _CONSOLE_MENU_ID, "api_id": _CONSOLE_API_ID},
        )
        == 1
    )

    command.downgrade(config, "20260830_0075")
    connection.commit()
    assert connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version')) == (
        "20260830_0075"
    )
    assert (
        connection.scalar(
            text(
                f'SELECT count(*) FROM "{schema}".registered_menu_api_bindings '
                "WHERE api_resource_id = CAST(:api_id AS uuid)"
            ),
            {"api_id": _CONSOLE_API_ID},
        )
        == 0
    )
    command.upgrade(config, "20260831_0076")
    connection.commit()
    assert connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version')) == (
        "20260831_0076"
    )


def test_revision_0076_existing_snapshot_roundtrip_and_followup_guard(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    """非空菜单快照可精确恢复，后续管理员发布存在时拒绝破坏性降级。"""

    config, connection, schema, _ = migration_database
    workspace = _seed_pre_0073_workspace(migration_database)
    command.upgrade(config, "20260830_0075")
    connection.commit()
    source_release_id, source_snapshot = _current_menu_snapshot(
        connection, schema, workspace.workspace_id
    )

    command.upgrade(config, "20260831_0076")
    connection.commit()
    upgraded_release_id, upgraded_snapshot = _current_menu_snapshot(
        connection, schema, workspace.workspace_id
    )
    assert upgraded_release_id != source_release_id
    assert upgraded_snapshot["registry_version"] == 29
    console_menu = next(
        item for item in upgraded_snapshot["menus"] if str(item["menu_id"]) == _CONSOLE_MENU_ID
    )
    assert str(console_menu["parent_menu_id"]) == "82000000-0000-4000-8000-000000000002"
    assert any(
        str(item["menu_id"]) == _CONSOLE_MENU_ID and str(item["api_resource_id"]) == _CONSOLE_API_ID
        for item in upgraded_snapshot["menu_api_bindings"]
    )

    command.downgrade(config, "20260830_0075")
    connection.commit()
    restored_release_id, restored_snapshot = _current_menu_snapshot(
        connection, schema, workspace.workspace_id
    )
    assert restored_release_id == source_release_id
    assert restored_snapshot == source_snapshot

    command.upgrade(config, "20260831_0076")
    connection.commit()
    _append_followup_menu_release(connection, schema, workspace)
    with pytest.raises(RuntimeError, match="当前菜单发布已在 P6B-01 后变化"):
        command.downgrade(config, "20260830_0075")
    connection.rollback()
    assert connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version')) == (
        "20260831_0076"
    )
