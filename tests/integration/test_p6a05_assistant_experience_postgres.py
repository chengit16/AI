"""验证 P6A-05 会话范围、临时附件、检索收窄和 Migration 往返。"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from ai_platform_api.modules.assistant.api.routes import router as assistant_router
from ai_platform_api.modules.assistant.application.errors import (
    AssistantConversationBusyError,
    AssistantNotFoundError,
)
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.persistence.tables import (
    assistant_runs,
    audit_records,
    conversation_attachments,
    document_tag_bindings,
    documents,
    knowledge_bases,
    knowledge_tags,
    outbox_events,
)
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError

from tests.integration.test_p1d01_knowledge_postgres import DEFAULT_DATABASE_URL
from tests.integration.test_p1e01_assistant_postgres import (
    AssistantHarness,
    context,
    publish_runtime_config,
    register,
)
from tests.integration.test_p1e01_assistant_postgres import (
    assistant_database as _assistant_database,
)
from tests.integration.test_p1e02_retrieval_planning_postgres import (
    RetrievalHarness,
    create_indexed_document,
)
from tests.integration.test_p1e02_retrieval_planning_postgres import (
    context as retrieval_context,
)
from tests.integration.test_p1e02_retrieval_planning_postgres import (
    publish_runtime_config as publish_retrieval_runtime,
)
from tests.integration.test_p1e02_retrieval_planning_postgres import (
    register as register_retrieval,
)
from tests.integration.test_p1e02_retrieval_planning_postgres import (
    retrieval_database as _retrieval_database,
)
from tests.integration.test_p6a03_document_download_postgres import (
    _current_menu_snapshot,
    _seed_pre_0073_workspace,
)
from tests.integration.test_p6a04_personal_workbench_postgres import _migration_config
from tests.integration.test_p403_tool_task_state_postgres import (
    migration_database as _migration_database,
)

assistant_database = _assistant_database
retrieval_database = _retrieval_database
migration_database = _migration_database

_P6A05_API_IDS = {
    "81000000-0000-4000-8000-000000000179",
    "81000000-0000-4000-8000-000000000180",
    "81000000-0000-4000-8000-000000000181",
    "81000000-0000-4000-8000-000000000182",
}
_P6A05_PERMISSIONS = {
    "assistant.attachment.manage",
    "assistant.conversation.scope.manage",
}


def test_scope_attachment_http_freeze_and_archive_cleanup(
    assistant_database: AssistantHarness,
) -> None:
    """HTTP 元数据不得泄露正文，活动 Run 锁定输入，归档后正文必须清除。"""

    owner = register(assistant_database, "p6a05-owner")
    outsider = register(assistant_database, "p6a05-outsider")
    owner_context = context(owner)
    publish_runtime_config(assistant_database, owner.account_id, version=7)
    conversation = assistant_database.assistant.create_conversation(
        owner_context,
        title="合成 P6A-05 会话",
    )
    owner_base_id, owner_tag_id, owner_document_id = _seed_scope_resources(
        assistant_database,
        owner.account_id,
        owner.workspace_id,
    )
    _, outsider_tag_id, _ = _seed_scope_resources(
        assistant_database,
        outsider.account_id,
        outsider.workspace_id,
    )

    with pytest.raises(AssistantNotFoundError):
        assistant_database.assistant.update_conversation_scope(
            owner_context,
            conversation_id=conversation.conversation_id,
            scope_mode="selected",
            knowledge_base_ids=(),
            tag_ids=(outsider_tag_id,),
        )

    # 1. 通过真实 multipart 和 JSON 路由保存范围及附件，响应只返回低敏元数据。
    app = FastAPI()
    app.state.assistant_conversation_service = assistant_database.assistant
    app.include_router(assistant_router, prefix="/api/v1")
    app.dependency_overrides[trusted_request_context] = lambda: owner_context
    prefix = f"/api/v1/workspaces/{owner.workspace_id}/conversations/{conversation.conversation_id}"
    with TestClient(app) as client:
        scope_response = client.put(
            f"{prefix}/scope",
            json={
                "scope_mode": "selected",
                "knowledge_base_ids": [str(owner_base_id)],
                "tag_ids": [str(owner_tag_id)],
            },
        )
        upload_response = client.post(
            f"{prefix}/attachments",
            files={
                "file": (
                    "synthetic-context.md",
                    "仅供 P6A-05 合成测试使用的临时上下文。".encode(),
                    "text/markdown",
                )
            },
        )
        attachment_response = client.get(f"{prefix}/attachments")

    assert scope_response.status_code == 200
    assert scope_response.json()["knowledge_base_ids"] == [str(owner_base_id)]
    assert upload_response.status_code == 201
    assert "content" not in upload_response.json()
    assert attachment_response.status_code == 200
    assert "仅供 P6A-05" not in attachment_response.text
    attachment_id = UUID(upload_response.json()["attachment_id"])

    # 2. 消息提交冻结范围和附件；直到 Run 终态前，任何上下文变更都失败关闭。
    submission = assistant_database.assistant.create_user_message(
        owner_context,
        conversation_id=conversation.conversation_id,
        texts=("请只依据合成范围回答",),
        attachment_ids=(attachment_id,),
        idempotency_key="synthetic-p6a05-message-0001",
    )
    assert submission.run.knowledge_base_ids == frozenset({owner_base_id})
    assert submission.run.document_ids == frozenset({owner_document_id})
    assert submission.run.attachment_ids == (attachment_id,)
    with pytest.raises(AssistantConversationBusyError):
        assistant_database.assistant.delete_attachment(
            owner_context,
            conversation_id=conversation.conversation_id,
            attachment_id=attachment_id,
        )
    with pytest.raises(AssistantConversationBusyError):
        assistant_database.assistant.update_conversation_scope(
            owner_context,
            conversation_id=conversation.conversation_id,
            scope_mode="workspace",
            knowledge_base_ids=(),
            tag_ids=(),
        )
    with pytest.raises(DBAPIError), assistant_database.sessions.begin() as session:
        session.execute(
            update(assistant_runs)
            .where(assistant_runs.c.run_id == submission.run.run_id)
            .values(attachment_ids=[])
        )

    # 3. 终止 Run 后归档会话，附件正文、列表投影和审计/事件敏感内容同步收敛。
    assistant_database.assistant.cancel_run(
        owner_context,
        conversation_id=conversation.conversation_id,
        run_id=submission.run.run_id,
    )
    archived = assistant_database.assistant.archive_conversation(
        owner_context,
        conversation_id=conversation.conversation_id,
    )
    assert archived.status == "archived"
    assert (
        assistant_database.assistant.list_attachments(
            owner_context,
            conversation_id=conversation.conversation_id,
        )
        == ()
    )
    with assistant_database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(conversation_attachments)
                .where(conversation_attachments.c.conversation_id == conversation.conversation_id)
            )
            == 0
        )
        exported = json.dumps(
            {
                "audits": list(
                    session.scalars(
                        select(audit_records.c.attributes).where(
                            audit_records.c.workspace_id == owner.workspace_id,
                            audit_records.c.resource_id.in_(
                                (conversation.conversation_id, attachment_id)
                            ),
                        )
                    )
                ),
                "events": list(
                    session.scalars(
                        select(outbox_events.c.payload).where(
                            outbox_events.c.workspace_id == owner.workspace_id,
                            outbox_events.c.aggregate_id.in_(
                                (conversation.conversation_id, attachment_id)
                            ),
                        )
                    )
                ),
            },
            ensure_ascii=False,
        )
    assert "仅供 P6A-05" not in exported


def test_selected_tags_narrow_retrieval_and_empty_match_does_not_fallback(
    retrieval_database: RetrievalHarness,
) -> None:
    """标签并集必须冻结为文档集合，空集合不得被解释为工作空间全量。"""

    owner = register_retrieval(retrieval_database, "p6a05-scope")
    owner_context = retrieval_context(owner)
    publish_retrieval_runtime(retrieval_database, owner.account_id)
    knowledge_base = retrieval_database.knowledge.create_knowledge_base(
        owner_context,
        name="P6A-05 合成范围知识库",
        default_visibility="workspace",
        default_security_level="INTERNAL",
    )
    selected_document_id = create_indexed_document(
        retrieval_database,
        owner,
        knowledge_base.knowledge_base_id,
        title="指定范围制度",
        content="指定范围内的差旅规则要求十个工作日内提交。",
        visibility="workspace",
        security_level="INTERNAL",
    )
    create_indexed_document(
        retrieval_database,
        owner,
        knowledge_base.knowledge_base_id,
        title="范围外制度",
        content="这份内容与问题相似但不得进入指定范围。",
        visibility="workspace",
        security_level="INTERNAL",
    )
    selected_tag_id = uuid4()
    empty_tag_id = uuid4()
    now = datetime.now(UTC)
    with retrieval_database.sessions.begin() as session:
        session.execute(
            insert(knowledge_tags),
            [
                _tag_row(selected_tag_id, owner.account_id, owner.workspace_id, "指定范围", now),
                _tag_row(empty_tag_id, owner.account_id, owner.workspace_id, "空范围", now),
            ],
        )
        session.execute(
            insert(document_tag_bindings).values(
                workspace_id=owner.workspace_id,
                document_id=selected_document_id,
                tag_id=selected_tag_id,
                created_at=now,
            )
        )

    # 1. Run 创建时冻结标签并集，即使标签随后失效也只检索已冻结文档。
    conversation = retrieval_database.assistant.create_conversation(
        owner_context,
        title="指定标签范围",
    )
    retrieval_database.assistant.update_conversation_scope(
        owner_context,
        conversation_id=conversation.conversation_id,
        scope_mode="selected",
        knowledge_base_ids=(knowledge_base.knowledge_base_id,),
        tag_ids=(selected_tag_id,),
    )
    submission = retrieval_database.assistant.create_user_message(
        owner_context,
        conversation_id=conversation.conversation_id,
        texts=("请总结差旅规则",),
        idempotency_key="synthetic-p6a05-scope-0001",
    )
    with retrieval_database.sessions.begin() as session:
        session.execute(
            update(knowledge_tags)
            .where(knowledge_tags.c.tag_id == selected_tag_id)
            .values(status="deleted", deleted_at=datetime.now(UTC), version=2)
        )
    plan = retrieval_database.planning.retrieve(owner_context, submission.run.run_id)
    assert submission.run.document_ids == frozenset({selected_document_id})
    assert {candidate.document_id for candidate in plan.candidates} == {selected_document_id}

    # 2. 有效标签没有绑定文档时冻结空集合，检索必须返回空候选而非全空间内容。
    empty_conversation = retrieval_database.assistant.create_conversation(
        owner_context,
        title="空标签范围",
    )
    retrieval_database.assistant.update_conversation_scope(
        owner_context,
        conversation_id=empty_conversation.conversation_id,
        scope_mode="selected",
        knowledge_base_ids=(),
        tag_ids=(empty_tag_id,),
    )
    empty_submission = retrieval_database.assistant.create_user_message(
        owner_context,
        conversation_id=empty_conversation.conversation_id,
        texts=("不得回退全空间",),
        idempotency_key="synthetic-p6a05-scope-0002",
    )
    empty_plan = retrieval_database.planning.retrieve(
        owner_context,
        empty_submission.run.run_id,
    )
    assert empty_submission.run.document_ids == frozenset()
    assert empty_plan.candidates == ()


def test_p6a05_empty_migration_roundtrip() -> None:
    """空库升级、降级和再升级必须稳定创建附件表、冻结列与触发器。"""

    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p6a05_migration_{uuid4().hex}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    config = _migration_config(database_url, schema)
    try:
        command.upgrade(config, "20260825_0074")
        command.upgrade(config, "20260830_0075")
        with engine.connect() as connection:
            assert _revision(connection, schema) == "20260830_0075"
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = :schema AND table_name = 'conversation_attachments'"
                    ),
                    {"schema": schema},
                )
                == 1
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.triggers "
                        "WHERE trigger_schema = :schema "
                        "AND trigger_name = 'assistant_runs_scope_immutable'"
                    ),
                    {"schema": schema},
                )
                == 1
            )

        command.downgrade(config, "20260825_0074")
        with engine.connect() as connection:
            assert _revision(connection, schema) == "20260825_0074"
        command.upgrade(config, "20260830_0075")
        with engine.connect() as connection:
            assert _revision(connection, schema) == "20260830_0075"
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_p6a05_existing_menu_snapshot_roundtrip_and_fact_guard(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    """Registry 28 必须精确恢复 27，产生范围事实后拒绝破坏性降级。"""

    config, connection, schema, _ = migration_database
    workspace = _seed_pre_0073_workspace(migration_database)
    connection.execute(
        text(
            f"""
            DELETE FROM "{schema}".role_permission_grants
            WHERE workspace_id = :workspace_id
              AND permission_code = ANY(CAST(:permissions AS varchar[]))
            """
        ),
        {
            "workspace_id": workspace.workspace_id,
            "permissions": sorted(_P6A05_PERMISSIONS),
        },
    )
    connection.commit()
    command.upgrade(config, "20260825_0074")
    connection.commit()
    release_27, snapshot_27 = _current_menu_snapshot(
        connection,
        schema,
        workspace.workspace_id,
    )

    command.upgrade(config, "20260830_0075")
    connection.commit()
    _, snapshot_28 = _current_menu_snapshot(connection, schema, workspace.workspace_id)
    assert _revision(connection, schema) == "20260830_0075"
    assert snapshot_28["registry_version"] == 28
    assert {
        str(item["api_resource_id"])
        for item in snapshot_28["menu_api_bindings"]
        if str(item["api_resource_id"]) in _P6A05_API_IDS
    } == _P6A05_API_IDS

    command.downgrade(config, "20260825_0074")
    connection.commit()
    restored_release, restored_snapshot = _current_menu_snapshot(
        connection,
        schema,
        workspace.workspace_id,
    )
    assert restored_release == release_27
    assert restored_snapshot == snapshot_27

    command.upgrade(config, "20260830_0075")
    connection.commit()
    conversation_id = uuid4()
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".conversations (
                conversation_id, workspace_id, created_by_account_id, conversation_kind,
                title, status, scope_mode, knowledge_base_ids, tag_ids,
                created_at, updated_at, version
            ) VALUES (
                :conversation_id, :workspace_id, :account_id, 'private',
                '合成降级保护会话', 'active', 'selected',
                ARRAY[CAST(:knowledge_base_id AS uuid)], ARRAY[]::uuid[],
                :now, :now, 1
            )
            """
        ),
        {
            "conversation_id": conversation_id,
            "workspace_id": workspace.workspace_id,
            "account_id": workspace.account_id,
            "knowledge_base_id": uuid4(),
            "now": datetime.now(UTC),
        },
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="拒绝破坏性降级"):
        command.downgrade(config, "20260825_0074")
    connection.rollback()
    assert _revision(connection, schema) == "20260830_0075"


def _seed_scope_resources(
    harness: AssistantHarness,
    account_id: UUID,
    workspace_id: UUID,
) -> tuple[UUID, UUID, UUID]:
    """建立范围解析所需的最小合成知识库、标签和活动文档。"""

    knowledge_base_id = uuid4()
    tag_id = uuid4()
    document_id = uuid4()
    now = datetime.now(UTC)
    with harness.sessions.begin() as session:
        session.execute(
            insert(knowledge_bases).values(
                knowledge_base_id=knowledge_base_id,
                workspace_id=workspace_id,
                name=f"合成范围知识库 {knowledge_base_id}",
                description=None,
                default_visibility="private",
                department_ids=[],
                default_security_level="INTERNAL",
                status="active",
                created_by_account_id=account_id,
                created_at=now,
                updated_at=now,
                deleted_at=None,
                version=1,
            )
        )
        session.execute(
            insert(knowledge_tags).values(
                tag_id=tag_id,
                workspace_id=workspace_id,
                name=f"合成范围标签 {tag_id}",
                color=None,
                created_by_account_id=account_id,
                created_at=now,
                updated_at=now,
                status="active",
                deleted_at=None,
                version=1,
            )
        )
        session.execute(
            insert(documents).values(
                document_id=document_id,
                workspace_id=workspace_id,
                knowledge_base_id=knowledge_base_id,
                title=f"合成范围文档 {document_id}",
                visibility="private",
                department_ids=[],
                security_level="INTERNAL",
                permission_labels=[],
                status="active",
                created_by_account_id=account_id,
                created_at=now,
                updated_at=now,
                deleted_at=None,
                version=1,
            )
        )
        session.execute(
            insert(document_tag_bindings).values(
                workspace_id=workspace_id,
                document_id=document_id,
                tag_id=tag_id,
                created_at=now,
            )
        )
    return knowledge_base_id, tag_id, document_id


def _tag_row(
    tag_id: UUID,
    account_id: UUID,
    workspace_id: UUID,
    name: str,
    now: datetime,
) -> dict[str, object]:
    return {
        "tag_id": tag_id,
        "workspace_id": workspace_id,
        "name": name,
        "color": None,
        "created_by_account_id": account_id,
        "created_at": now,
        "updated_at": now,
        "status": "active",
        "deleted_at": None,
        "version": 1,
    }


def _revision(connection: Connection, schema: str) -> str:
    value = connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version'))
    assert isinstance(value, str)
    return value
