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
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    agent_publications,
    agent_releases,
    agents,
    ai_runtime_config_publication,
    ai_runtime_config_versions,
    assistant_runs,
    audit_records,
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
