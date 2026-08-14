"""验证 P1E-01 会话、不可变消息和系统助手发布快照的 PostgreSQL 闭环。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.assistant.api.routes import router as assistant_router
from ai_platform_api.modules.assistant.application.errors import (
    AssistantConversationBusyError,
    AssistantDeniedError,
    AssistantIdempotencyConflictError,
    AssistantValidationError,
)
from ai_platform_api.modules.assistant.application.service import AssistantConversationService
from ai_platform_api.modules.assistant.infrastructure.sqlalchemy import (
    SqlAlchemyAssistantUnitOfWork,
)
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.modules.streaming.application.service import TransactionalStreamService
from ai_platform_api.modules.streaming.domain.models import StreamPolicy
from ai_platform_api.modules.streaming.infrastructure.sqlalchemy import (
    SqlAlchemyStreamUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    agent_publications,
    agent_releases,
    agents,
    ai_runtime_config_publication,
    ai_runtime_config_versions,
    assistant_runs,
    audit_records,
    message_feedbacks,
    message_parts,
    outbox_events,
)
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext("a" * 32, "b" * 16)


@dataclass(frozen=True)
class RegisteredAccount:
    """保留测试创建的账号与默认个人空间标识。"""

    account_id: UUID
    workspace_id: UUID


@dataclass(frozen=True)
class AssistantHarness:
    """集中持有临时 Schema 的注册、助手服务与查询入口。"""

    engine: Engine
    sessions: sessionmaker[Session]
    registration: RegistrationService
    assistant: AssistantConversationService


@pytest.fixture(scope="module")
def assistant_database() -> Iterator[AssistantHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1e01_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "infra/migrations"))
    config.set_main_option(
        "prepend_sys_path",
        f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
    )
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("ai_platform_schema", schema)
    command.upgrade(config, "head")

    engine = create_platform_engine(database_url, schema)
    sessions = create_session_factory(engine)
    try:
        yield AssistantHarness(
            engine,
            sessions,
            RegistrationService(
                SqlAlchemyIdentityReader(sessions),
                SqlAlchemyRegistrationUnitOfWork(sessions),
                Argon2idPasswordAdapter(),
            ),
            AssistantConversationService(SqlAlchemyAssistantUnitOfWork(sessions)),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def register(harness: AssistantHarness, identity: str) -> RegisteredAccount:
    result = harness.registration.register(
        login_name=f"synthetic.assistant.{identity}.{uuid4().hex}@example.com",
        display_name=f"合成问答用户 {identity}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return RegisteredAccount(result.account_id, result.personal_workspace_id)


def context(account: RegisteredAccount, *, workspace_id: UUID | None = None) -> RequestContext:
    return RequestContext.trusted(
        actor_id=account.account_id,
        user_id=account.account_id,
        workspace_id=workspace_id or account.workspace_id,
        trace=TRACE,
        authentication_method="browser_session",
    )


def publish_runtime_config(
    harness: AssistantHarness,
    account_id: UUID,
    *,
    version: int,
) -> UUID:
    """直接发布满足数据库约束的合成运行配置，测试不依赖真实供应商。"""

    runtime_config_version_id = uuid4()
    now = datetime.now(UTC)
    content_hash = f"{version:x}" * 64
    with harness.sessions.begin() as session:
        session.execute(
            insert(ai_runtime_config_versions).values(
                runtime_config_version_id=runtime_config_version_id,
                version_number=version,
                display_name=f"合成问答运行配置 V{version}",
                content_hash=content_hash,
                system_prompt_template="只使用经过授权的合成知识证据回答。",
                system_prompt_hash=f"{version + 8:x}" * 64,
                component_versions={"retrieval": f"synthetic-v{version}"},
                attempt_timeout_ms=500,
                total_timeout_ms=2_000,
                max_attempts_per_route=1,
                max_prompt_characters=4_000,
                max_output_tokens=256,
                max_response_characters=8_000,
                circuit_failure_threshold=3,
                circuit_recovery_ms=30_000,
                rule_degradation_message=None,
                max_estimated_cost_microunits=5_000_000,
                created_by_account_id=account_id,
                created_at=now,
            )
        )
        current = session.execute(select(ai_runtime_config_publication)).one_or_none()
        if current is None:
            session.execute(
                insert(ai_runtime_config_publication).values(
                    publication_key="current",
                    runtime_config_version_id=runtime_config_version_id,
                    generation=1,
                    published_by_account_id=account_id,
                    published_at=now,
                )
            )
        else:
            session.execute(
                update(ai_runtime_config_publication)
                .where(ai_runtime_config_publication.c.publication_key == "current")
                .values(
                    runtime_config_version_id=runtime_config_version_id,
                    generation=current.generation + 1,
                    published_by_account_id=account_id,
                    published_at=now,
                )
            )
    return runtime_config_version_id


def test_message_run_freezes_release_and_runtime_config(
    assistant_database: AssistantHarness,
) -> None:
    owner = register(assistant_database, "owner")
    owner_context = context(owner)
    first_runtime_id = publish_runtime_config(assistant_database, owner.account_id, version=1)
    conversation = assistant_database.assistant.create_conversation(
        owner_context,
        title="  合成知识问答  ",
    )

    first = assistant_database.assistant.create_user_message(
        owner_context,
        conversation_id=conversation.conversation_id,
        texts=(" 合成问题第一段 ", "合成问题第二段"),
        idempotency_key="synthetic-message-0001",
    )
    repeated = assistant_database.assistant.create_user_message(
        owner_context,
        conversation_id=conversation.conversation_id,
        texts=("合成问题第一段", "合成问题第二段"),
        idempotency_key="synthetic-message-0001",
    )

    assert conversation.title == "合成知识问答"
    assert repeated == first
    assert first.run.status == "queued"
    assert first.run.runtime_config_version_id == first_runtime_id
    assert first.assistant_message is not None
    assert first.assistant_message.message_id == first.run.assistant_message_id
    assert first.assistant_message.status == "streaming"
    assert [part.sequence_no for part in first.message.parts] == [1, 2]
    assert [part.text for part in first.message.parts] == ["合成问题第一段", "合成问题第二段"]

    with pytest.raises(AssistantIdempotencyConflictError):
        assistant_database.assistant.create_user_message(
            owner_context,
            conversation_id=conversation.conversation_id,
            texts=("不同的合成问题",),
            idempotency_key="synthetic-message-0001",
        )
    with pytest.raises(AssistantConversationBusyError):
        assistant_database.assistant.create_user_message(
            owner_context,
            conversation_id=conversation.conversation_id,
            texts=("并发合成问题",),
            idempotency_key="synthetic-message-0002",
        )

    # Run 只允许认领一次；完成事务必须同步落下助手正文和 Run 终态。
    streamed = assistant_database.assistant.get_run_for_stream(
        owner_context,
        conversation_id=conversation.conversation_id,
        run_id=first.run.run_id,
    )
    claimed = assistant_database.assistant.claim_run(owner_context, run_id=first.run.run_id)
    assert streamed.assistant_message_id == first.assistant_message.message_id
    assert claimed is not None and claimed.status == "running"
    assert assistant_database.assistant.claim_run(owner_context, run_id=first.run.run_id) is None

    completed = assistant_database.assistant.complete_run(
        owner_context,
        run_id=first.run.run_id,
        text="  只依据合成证据生成的最终答案。  ",
    )
    completed_messages = assistant_database.assistant.list_messages(
        owner_context,
        conversation_id=conversation.conversation_id,
        limit=100,
    )
    completed_message = next(
        message
        for message in completed_messages
        if message.message_id == first.assistant_message.message_id
    )
    assert completed.status == "completed"
    assert completed_message.status == "completed"
    assert tuple(part.text for part in completed_message.parts) == (
        "只依据合成证据生成的最终答案。",
    )
    with pytest.raises(AssistantConversationBusyError):
        assistant_database.assistant.complete_run(
            owner_context,
            run_id=first.run.run_id,
            text="不允许重复完成",
        )

    # 发布 V2 后新会话生成新 AgentRelease，首个 Run 仍固定指向 V1。
    second_runtime_id = publish_runtime_config(assistant_database, owner.account_id, version=2)
    second_conversation = assistant_database.assistant.create_conversation(
        owner_context,
        title="合成知识问答 V2",
    )
    second = assistant_database.assistant.create_user_message(
        owner_context,
        conversation_id=second_conversation.conversation_id,
        texts=("新版本合成问题",),
        idempotency_key="synthetic-message-0003",
    )
    assert second.run.runtime_config_version_id == second_runtime_id
    assert second.run.agent_release_id != first.run.agent_release_id
    assert second.assistant_message is not None
    assert (
        assistant_database.assistant.claim_run(owner_context, run_id=second.run.run_id) is not None
    )
    failed = assistant_database.assistant.fail_run(
        owner_context,
        run_id=second.run.run_id,
        error_code="SYNTHETIC_PROVIDER_FAILED",
    )
    failed_messages = assistant_database.assistant.list_messages(
        owner_context,
        conversation_id=second_conversation.conversation_id,
        limit=100,
    )
    failed_message = next(
        message
        for message in failed_messages
        if message.message_id == second.assistant_message.message_id
    )
    assert failed.status == "failed"
    assert failed.error_code == "SYNTHETIC_PROVIDER_FAILED"
    assert failed_message.status == "failed"
    assert failed_message.parts == ()

    with assistant_database.sessions() as session:
        release_rows = [
            tuple(row)
            for row in session.execute(
                select(
                    agent_releases.c.release_id,
                    agent_releases.c.version,
                    agent_releases.c.runtime_config_version_id,
                ).order_by(agent_releases.c.version)
            )
        ]
        assert release_rows == [
            (first.run.agent_release_id, 1, first_runtime_id),
            (second.run.agent_release_id, 2, second_runtime_id),
        ]
        assert session.scalar(select(func.count()).select_from(agents)) == 1
        assert session.scalar(select(func.count()).select_from(agent_publications)) == 1
        assert (session.scalar(select(func.count()).select_from(audit_records)) or 0) >= 4
        assert (session.scalar(select(func.count()).select_from(outbox_events)) or 0) >= 4

    # 历史 Part 与 Release 是证据快照，维护 SQL 也不能直接覆写。
    with pytest.raises(DBAPIError), assistant_database.sessions.begin() as session:
        session.execute(
            update(message_parts)
            .where(message_parts.c.part_id == first.message.parts[0].part_id)
            .values(text_content="被篡改的内容")
        )
    with pytest.raises(DBAPIError), assistant_database.sessions.begin() as session:
        session.execute(
            update(agent_releases)
            .where(agent_releases.c.release_id == first.run.agent_release_id)
            .values(config_hash="f" * 64)
        )


def test_conversation_privacy_archive_and_http_contract(
    assistant_database: AssistantHarness,
) -> None:
    owner = register(assistant_database, "http-owner")
    outsider = register(assistant_database, "outsider")
    publish_runtime_config(assistant_database, owner.account_id, version=3)
    owner_context = context(owner)
    conversation = assistant_database.assistant.create_conversation(
        owner_context,
        title="合成 HTTP 会话",
    )

    with pytest.raises(AssistantDeniedError):
        assistant_database.assistant.list_conversations(
            context(outsider, workspace_id=owner.workspace_id),
            limit=100,
        )

    app = FastAPI()
    app.state.assistant_conversation_service = assistant_database.assistant
    app.include_router(assistant_router, prefix="/api/v1")
    app.dependency_overrides[trusted_request_context] = lambda: owner_context
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/workspaces/{owner.workspace_id}/conversations",
        )
        assert response.status_code == 200
        assert response.json()["items"][0]["conversation_id"] == str(conversation.conversation_id)
        archived = client.post(
            f"/api/v1/workspaces/{owner.workspace_id}/conversations/"
            f"{conversation.conversation_id}/archive",
        )
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"

    with assistant_database.sessions() as session:
        # 另一个测试留下的 queued Run 不影响本会话，归档仅修改会话头事实。
        assert (
            session.scalar(
                select(func.count())
                .select_from(assistant_runs)
                .where(assistant_runs.c.conversation_id == conversation.conversation_id)
            )
            == 0
        )


def test_http_sse_replays_only_events_after_last_event_id(
    assistant_database: AssistantHarness,
) -> None:
    owner = register(assistant_database, "sse-owner")
    owner_context = context(owner)
    publish_runtime_config(assistant_database, owner.account_id, version=4)
    conversation = assistant_database.assistant.create_conversation(
        owner_context,
        title="合成 SSE 会话",
    )
    submission = assistant_database.assistant.create_user_message(
        owner_context,
        conversation_id=conversation.conversation_id,
        texts=("合成 SSE 问题",),
        idempotency_key="synthetic-sse-message-0001",
    )
    claimed = assistant_database.assistant.claim_run(
        owner_context,
        run_id=submission.run.run_id,
    )
    assert claimed is not None and claimed.assistant_message_id is not None

    streams = TransactionalStreamService(
        SqlAlchemyStreamUnitOfWork(assistant_database.sessions),
        StreamPolicy(),
    )
    now = datetime.now(UTC)
    streams.start_run(
        owner.workspace_id,
        conversation.conversation_id,
        claimed.assistant_message_id,
        claimed.run_id,
        now=now,
    )
    first = streams.append(
        claimed.run_id,
        "message.delta",
        claimed.trace_id,
        claimed.traceparent,
        {"delta": "第一段"},
        now=now,
    )
    completed_event = streams.append(
        claimed.run_id,
        "message.completed",
        claimed.trace_id,
        claimed.traceparent,
        {"text": "第一段第二段"},
        now=now,
    )
    assistant_database.assistant.complete_run(
        owner_context,
        run_id=claimed.run_id,
        text="第一段第二段",
    )
    streams.finish(
        claimed.run_id,
        "completed",
        {"status": "completed", "text": "第一段第二段"},
        now=now,
    )

    app = FastAPI()
    app.state.assistant_conversation_service = assistant_database.assistant
    app.state.streaming_service = streams
    app.include_router(assistant_router, prefix="/api/v1")
    app.dependency_overrides[trusted_request_context] = lambda: owner_context
    path = (
        f"/api/v1/workspaces/{owner.workspace_id}/conversations/"
        f"{conversation.conversation_id}/runs/{claimed.run_id}/events"
    )
    with TestClient(app) as client:
        full = client.get(path)
        resumed = client.get(path, headers={"Last-Event-ID": str(first.event_id)})
        acknowledged = client.get(
            path,
            headers={"Last-Event-ID": str(completed_event.event_id)},
        )

    assert full.status_code == 200
    assert full.headers["content-type"].startswith("text/event-stream")
    assert f"id: {first.event_id}" in full.text
    assert f"id: {completed_event.event_id}" in full.text
    assert "event: message.delta" in full.text
    assert "event: message.completed" in full.text
    assert '"sequence_no":1' in full.text and '"sequence_no":2' in full.text
    assert resumed.status_code == 200
    assert f"id: {first.event_id}" not in resumed.text
    assert f"id: {completed_event.event_id}" in resumed.text
    assert acknowledged.status_code == 200
    assert "event: message.snapshot" in acknowledged.text
    assert '"status":"completed"' in acknowledged.text
    assert "id:" not in acknowledged.text


def test_p1e06_lists_and_cancels_only_owned_active_run(
    assistant_database: AssistantHarness,
) -> None:
    owner = register(assistant_database, "interaction-cancel-owner")
    outsider = register(assistant_database, "interaction-cancel-outsider")
    owner_context = context(owner)
    publish_runtime_config(assistant_database, owner.account_id, version=5)
    conversation = assistant_database.assistant.create_conversation(
        owner_context,
        title="合成取消会话",
    )
    submission = assistant_database.assistant.create_user_message(
        owner_context,
        conversation_id=conversation.conversation_id,
        texts=("取消这次合成问答",),
        idempotency_key="synthetic-cancel-message-0001",
    )

    listed = assistant_database.assistant.list_runs(
        owner_context,
        conversation_id=conversation.conversation_id,
        limit=100,
    )
    cancelled = assistant_database.assistant.cancel_run(
        owner_context,
        conversation_id=conversation.conversation_id,
        run_id=submission.run.run_id,
    )
    repeated = assistant_database.assistant.cancel_run(
        owner_context,
        conversation_id=conversation.conversation_id,
        run_id=submission.run.run_id,
    )
    messages = assistant_database.assistant.list_messages(
        owner_context,
        conversation_id=conversation.conversation_id,
        limit=100,
    )
    assistant_message = next(message for message in messages if message.role == "assistant")

    assert [run.run_id for run in listed] == [submission.run.run_id]
    assert cancelled.status == "cancelled"
    assert cancelled.error_code == "RUN_CANCELLED"
    assert repeated == cancelled
    assert assistant_message.status == "failed"
    assert assistant_message.parts == ()
    with pytest.raises(AssistantDeniedError):
        assistant_database.assistant.list_runs(
            context(outsider, workspace_id=owner.workspace_id),
            conversation_id=conversation.conversation_id,
            limit=100,
        )


def test_p1e06_revises_feedback_without_copying_comment_to_events(
    assistant_database: AssistantHarness,
) -> None:
    owner = register(assistant_database, "interaction-feedback-owner")
    owner_context = context(owner)
    publish_runtime_config(assistant_database, owner.account_id, version=6)
    conversation = assistant_database.assistant.create_conversation(
        owner_context,
        title="合成反馈会话",
    )
    submission = assistant_database.assistant.create_user_message(
        owner_context,
        conversation_id=conversation.conversation_id,
        texts=("生成可反馈的合成答案",),
        idempotency_key="synthetic-feedback-message-0001",
    )
    claimed = assistant_database.assistant.claim_run(
        owner_context,
        run_id=submission.run.run_id,
    )
    assert claimed is not None and claimed.assistant_message_id is not None
    assistant_database.assistant.complete_run(
        owner_context,
        run_id=claimed.run_id,
        text="仅依据合成证据生成的回答",
    )

    first = assistant_database.assistant.submit_feedback(
        owner_context,
        conversation_id=conversation.conversation_id,
        message_id=claimed.assistant_message_id,
        rating="helpful",
        issue_codes=(),
        comment=None,
    )
    revised = assistant_database.assistant.submit_feedback(
        owner_context,
        conversation_id=conversation.conversation_id,
        message_id=claimed.assistant_message_id,
        rating="unhelpful",
        issue_codes=("incorrect", "missing_source", "incorrect"),
        comment="  这段合成说明只应保存在反馈事实中  ",
    )
    current = assistant_database.assistant.get_feedback(
        owner_context,
        conversation_id=conversation.conversation_id,
        message_id=claimed.assistant_message_id,
    )

    assert first.version == 1
    assert revised.version == 2
    assert revised.issue_codes == ("incorrect", "missing_source")
    assert revised.comment == "这段合成说明只应保存在反馈事实中"
    assert current == revised
    with pytest.raises(AssistantValidationError):
        assistant_database.assistant.submit_feedback(
            owner_context,
            conversation_id=conversation.conversation_id,
            message_id=claimed.assistant_message_id,
            rating="unhelpful",
            issue_codes=(),
            comment=None,
        )

    with assistant_database.sessions() as session:
        stored_comment = session.scalar(
            select(message_feedbacks.c.comment).where(
                message_feedbacks.c.feedback_id == revised.feedback_id
            )
        )
        audit_payloads = session.scalars(
            select(audit_records.c.attributes).where(
                audit_records.c.resource_id == revised.feedback_id
            )
        ).all()
        outbox_payloads = session.scalars(
            select(outbox_events.c.payload).where(
                outbox_events.c.aggregate_id == revised.feedback_id
            )
        ).all()
    assert stored_comment == revised.comment
    assert len(audit_payloads) == 2
    assert len(outbox_payloads) == 2
    assert all("comment" not in payload for payload in (*audit_payloads, *outbox_payloads))
