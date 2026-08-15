"""验证 P3-08 Runtime 发布读取、Run 绑定和服务暂停隔离闭环。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from ai_platform_api.modules.assistant.application.service import AssistantConversationService
from ai_platform_api.modules.assistant.infrastructure.sqlalchemy import (
    SqlAlchemyAssistantUnitOfWork,
)
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.modules.service_governance.application.errors import (
    ServiceRouteUnavailableError,
)
from ai_platform_api.modules.service_governance.application.service import (
    ServiceGovernanceService,
)
from ai_platform_api.modules.service_governance.infrastructure.sqlalchemy import (
    SqlAlchemyServiceGovernanceUnitOfWork,
    SqlAlchemyServiceRepository,
)
from ai_platform_api.modules.service_runtime.infrastructure.sqlalchemy import (
    SqlAlchemyRuntimeSnapshotSource,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    assistant_runs,
    audit_records,
    outbox_events,
    services,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, insert, select, text, update
from sqlalchemy.exc import DBAPIError

from tests.integration.test_p1e01_assistant_postgres import (
    AssistantHarness,
    context,
    publish_runtime_config,
    register,
)

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)


@pytest.fixture(scope="module")
def runtime_database() -> Iterator[AssistantHarness]:
    """创建只属于 P3-08 的临时 Schema，避免复用其他节点的运行事实。"""

    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p308_test_{uuid4().hex}"
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
            AssistantConversationService(
                SqlAlchemyAssistantUnitOfWork(sessions, SqlAlchemyServiceRepository)
            ),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def test_runtime_source_and_database_preserve_exact_run_binding(
    runtime_database: AssistantHarness,
) -> None:
    """当前和历史读取都只接受已发布事实，数据库拒绝改绑和跨空间伪造。"""

    # 长函数保留原因: 同一组事实需要连续证明应用落库、Runtime 读取和数据库最终防线。
    # 1. 通过真实助手用例创建首个 Run，并核对数据库、审计和 Outbox 的四元绑定。
    owner = register(runtime_database, "runtime-binding-owner")
    owner_context = context(owner)
    first_config_id = publish_runtime_config(runtime_database, owner.account_id, version=1)
    first_conversation = runtime_database.assistant.create_conversation(
        owner_context,
        title="合成 Runtime 绑定会话",
    )
    first = runtime_database.assistant.create_user_message(
        owner_context,
        conversation_id=first_conversation.conversation_id,
        texts=("验证合成发布绑定",),
        idempotency_key="synthetic-runtime-binding-first",
    )
    assert first.run.service_id is not None
    assert first.run.service_route_id is not None
    assert first.run.service_route_version == 1
    assert first.run.runtime_config_version_id == first_config_id

    source = SqlAlchemyRuntimeSnapshotSource(runtime_database.sessions)
    current = source.get_current(owner.workspace_id, first.run.service_id)
    bound = source.get_bound(
        owner.workspace_id,
        first.run.service_id,
        first.run.service_route_id,
        first.run.service_route_version,
        first.run.agent_release_id,
    )
    assert current is not None and bound is not None
    assert current == bound
    assert current.agent_release_id == first.run.agent_release_id

    with runtime_database.sessions() as session:
        stored = (
            session.execute(
                select(assistant_runs).where(assistant_runs.c.run_id == first.run.run_id)
            )
            .mappings()
            .one()
        )
        queued_audit = session.scalar(
            select(audit_records.c.attributes).where(
                audit_records.c.resource_id == first.run.run_id,
                audit_records.c.action == "assistant.message.create",
            )
        )
        queued_event = session.scalar(
            select(outbox_events.c.payload).where(
                outbox_events.c.aggregate_id == first.run.run_id,
                outbox_events.c.event_type == "assistant.run.queued",
            )
        )
    assert stored["service_id"] == first.run.service_id
    assert stored["service_route_id"] == first.run.service_route_id
    assert stored["service_route_version"] == first.run.service_route_version
    assert stored["agent_release_id"] == first.run.agent_release_id
    assert queued_audit is not None and queued_event is not None
    for document in (queued_audit, queued_event):
        assert document["service_id"] == str(first.run.service_id)
        assert document["service_route_id"] == str(first.run.service_route_id)
        assert document["service_route_version"] == first.run.service_route_version
        assert document["agent_release_id"] == str(first.run.agent_release_id)

    # 2. 结束首个 Run 后发布第二版 Route；历史精确读取不受当前指针变化影响。
    assert runtime_database.assistant.claim_run(owner_context, run_id=first.run.run_id) is not None
    runtime_database.assistant.complete_run(
        owner_context,
        run_id=first.run.run_id,
        text="合成绑定验证完成",
    )
    publish_runtime_config(runtime_database, owner.account_id, version=2)
    second_conversation = runtime_database.assistant.create_conversation(
        owner_context,
        title="合成 Runtime 第二版会话",
    )
    second = runtime_database.assistant.create_user_message(
        owner_context,
        conversation_id=second_conversation.conversation_id,
        texts=("验证第二版合成发布绑定",),
        idempotency_key="synthetic-runtime-binding-second",
    )
    assert second.run.service_id == first.run.service_id
    assert second.run.service_route_id != first.run.service_route_id
    assert second.run.service_route_version == 2
    historical = source.get_bound(
        owner.workspace_id,
        first.run.service_id,
        first.run.service_route_id,
        first.run.service_route_version,
        first.run.agent_release_id,
    )
    latest = source.get_current(owner.workspace_id, first.run.service_id)
    assert historical is not None and latest is not None
    assert historical.agent_release_id == first.run.agent_release_id
    assert latest.agent_release_id == second.run.agent_release_id

    # 3. Trigger 拒绝已落库 Run 改绑，也拒绝 Route 与 Release 不一致的新记录。
    with pytest.raises(DBAPIError), runtime_database.sessions.begin() as session:
        session.execute(
            update(assistant_runs)
            .where(assistant_runs.c.run_id == first.run.run_id)
            .values(service_route_version=99)
        )
    forged = dict(stored)
    forged.update(
        run_id=uuid4(),
        idempotency_key="synthetic-runtime-forged-release",
        service_route_id=second.run.service_route_id,
        service_route_version=second.run.service_route_version,
    )
    with pytest.raises(DBAPIError), runtime_database.sessions.begin() as session:
        session.execute(insert(assistant_runs).values(**forged))

    historical_route = dict(stored)
    historical_route.update(
        run_id=uuid4(),
        idempotency_key="synthetic-runtime-forged-historical-route",
    )
    with pytest.raises(DBAPIError), runtime_database.sessions.begin() as session:
        session.execute(insert(assistant_runs).values(**historical_route))

    # 4. 另一空间的发布身份既不能被 Runtime Source 读取，也不能写入本空间 Run。
    outsider = register(runtime_database, "runtime-binding-outsider")
    outsider_context = context(outsider)
    outsider_conversation = runtime_database.assistant.create_conversation(
        outsider_context,
        title="合成跨空间绑定会话",
    )
    outsider_run = runtime_database.assistant.create_user_message(
        outsider_context,
        conversation_id=outsider_conversation.conversation_id,
        texts=("合成跨空间问题",),
        idempotency_key="synthetic-runtime-outsider",
    ).run
    assert outsider_run.service_id is not None
    assert outsider_run.service_route_id is not None
    assert (
        source.get_bound(
            owner.workspace_id,
            outsider_run.service_id,
            outsider_run.service_route_id,
            outsider_run.service_route_version or 0,
            outsider_run.agent_release_id,
        )
        is None
    )
    cross_workspace = dict(stored)
    cross_workspace.update(
        run_id=uuid4(),
        idempotency_key="synthetic-runtime-cross-workspace",
        service_id=outsider_run.service_id,
        service_route_id=outsider_run.service_route_id,
        service_route_version=outsider_run.service_route_version,
        agent_release_id=outsider_run.agent_release_id,
        runtime_config_version_id=outsider_run.runtime_config_version_id,
    )
    with pytest.raises(DBAPIError), runtime_database.sessions.begin() as session:
        session.execute(insert(assistant_runs).values(**cross_workspace))


def test_suspended_service_rejects_new_run_but_inflight_run_can_finish(
    runtime_database: AssistantHarness,
) -> None:
    """暂停只阻止新的执行绑定，已经冻结的 Run 仍可按原发布事实收敛。"""

    owner = register(runtime_database, "runtime-suspension-owner")
    owner_context = context(owner)
    publish_runtime_config(runtime_database, owner.account_id, version=3)
    inflight_conversation = runtime_database.assistant.create_conversation(
        owner_context,
        title="合成暂停前在途会话",
    )
    blocked_conversation = runtime_database.assistant.create_conversation(
        owner_context,
        title="合成暂停后新请求会话",
    )
    inflight = runtime_database.assistant.create_user_message(
        owner_context,
        conversation_id=inflight_conversation.conversation_id,
        texts=("合成暂停前问题",),
        idempotency_key="synthetic-runtime-before-suspend",
    ).run
    assert inflight.service_id is not None

    with runtime_database.sessions() as session:
        service_version = session.scalar(
            select(services.c.version).where(services.c.service_id == inflight.service_id)
        )
    assert service_version is not None
    governance = ServiceGovernanceService(
        SqlAlchemyServiceGovernanceUnitOfWork(runtime_database.sessions)
    )
    governance_context = replace(owner_context, authorized_workspace=True)
    suspended = governance.update_service(
        governance_context,
        service_id=inflight.service_id,
        expected_version=service_version,
        target_status="suspended",
        idempotency_key="synthetic-runtime-suspend-service",
    )
    assert suspended.service.status == "suspended"

    with pytest.raises(ServiceRouteUnavailableError):
        runtime_database.assistant.create_user_message(
            owner_context,
            conversation_id=blocked_conversation.conversation_id,
            texts=("合成暂停后问题",),
            idempotency_key="synthetic-runtime-after-suspend",
        )

    claimed = runtime_database.assistant.claim_run(owner_context, run_id=inflight.run_id)
    assert claimed is not None and claimed.status == "running"
    completed = runtime_database.assistant.complete_run(
        owner_context,
        run_id=inflight.run_id,
        text="合成在途运行正常完成",
    )
    assert completed.status == "completed"

    with runtime_database.sessions() as session:
        assert (
            session.scalar(
                select(services.c.status).where(services.c.service_id == inflight.service_id)
            )
            == "suspended"
        )
        assert (
            session.scalar(
                select(assistant_runs.c.status).where(assistant_runs.c.run_id == inflight.run_id)
            )
            == "completed"
        )
        assert (
            session.scalar(
                select(assistant_runs.c.run_id).where(
                    assistant_runs.c.idempotency_key == "synthetic-runtime-after-suspend"
                )
            )
            is None
        )
