"""验证 P2-05 跨 API 实例唤醒、断点回放和 Valkey 故障降级。"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.assistant.api import routes as assistant_routes
from ai_platform_api.modules.assistant.application.service import AssistantRun
from ai_platform_api.modules.streaming.application.service import TransactionalStreamService
from ai_platform_api.modules.streaming.domain.errors import (
    ConversationBusyError,
    StreamRunNotFoundError,
)
from ai_platform_api.modules.streaming.domain.models import StreamPolicy, StreamWakeupSubscription
from ai_platform_api.modules.streaming.infrastructure.sqlalchemy import SqlAlchemyStreamUnitOfWork
from ai_platform_api.modules.streaming.infrastructure.valkey import ValkeyStreamNotifier
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
DEFAULT_VALKEY_URL = "redis://127.0.0.1:6379/12"
TRACE_ID = "0123456789abcdef0123456789abcdef"
TRACEPARENT = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"


@dataclass(frozen=True)
class DatabaseHarness:
    """为跨实例测试提供独立 Schema，避免触碰本地平台业务数据。"""

    schema: str
    engine: Engine
    sessions: sessionmaker[Session]


@pytest.fixture(scope="module")
def database() -> Iterator[DatabaseHarness]:
    """从空 Schema 升级到当前 Revision，并在模块结束后完整移除合成事实。"""

    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p205_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "infra/migrations"))
    config.set_main_option("prepend_sys_path", str(ROOT / "apps/api/src"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("ai_platform_schema", schema)
    command.upgrade(config, "head")

    engine = create_platform_engine(database_url, schema)
    try:
        yield DatabaseHarness(schema, engine, create_session_factory(engine))
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _service(database: DatabaseHarness, valkey_url: str | None) -> TransactionalStreamService:
    """构造共享事实库但进程资源相互独立的流服务，模拟不同 API 实例。"""

    notifier = ValkeyStreamNotifier(valkey_url) if valkey_url is not None else None
    return TransactionalStreamService(
        SqlAlchemyStreamUnitOfWork(database.sessions),
        StreamPolicy(),
        notifier,
    )


def _wait_for_wakeup(subscription: StreamWakeupSubscription, timeout_seconds: float = 2) -> bool:
    """允许先消费订阅确认帧，再等待固定的无正文消息。"""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if subscription.wait(min(0.2, deadline - time.monotonic())):
            return True
    return False


def test_two_instances_share_wakeup_and_resume_same_postgres_run(
    database: DatabaseHarness,
) -> None:
    """实例 A 写入后唤醒实例 B；A 中断后新实例仍从同一游标继续且保持空间隔离。"""

    valkey_url = os.environ.get("AI_PLATFORM_TEST_VALKEY_URL", DEFAULT_VALKEY_URL)
    writer = _service(database, valkey_url)
    reader = _service(database, valkey_url)
    workspace_id = uuid4()
    other_workspace_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    run_id = uuid4()
    now = datetime.now(UTC)
    subscription = reader.subscribe(run_id)
    assert subscription is not None

    try:
        writer.start_run(workspace_id, conversation_id, message_id, run_id, now=now)
        assert _wait_for_wakeup(subscription)
        first = writer.append(
            run_id,
            "message.delta",
            TRACE_ID,
            TRACEPARENT,
            {"delta": "合成跨实例正文"},
            now=now,
        )
        assert _wait_for_wakeup(subscription)
        assert reader.replay(workspace_id, run_id, None, now=now).events == (first,)

        with pytest.raises(StreamRunNotFoundError):
            reader.replay(other_workspace_id, run_id, None, now=now)
        with pytest.raises(ConversationBusyError):
            reader.start_run(workspace_id, conversation_id, message_id, uuid4(), now=now)

        # 关闭写实例模拟进程中断；替代实例只回放原 Run，不触发任何生成或新建事实。
        writer.close()
        replacement = _service(database, valkey_url)
        try:
            resumed = replacement.replay(workspace_id, run_id, first.event_id, now=now)
            assert resumed.events == ()
            assert resumed.final_run.run_id == run_id
            assert resumed.final_run.last_sequence_no == 1
        finally:
            replacement.close()
    finally:
        subscription.close()
        reader.close()
        writer.close()


def test_lost_notification_falls_back_to_bounded_postgres_polling(
    database: DatabaseHarness,
) -> None:
    """完全没有通知客户端时，另一实例仍在五秒恢复预算内读取严格递增事件。"""

    producer = _service(database, None)
    consumer = _service(database, None)
    workspace_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    run_id = uuid4()
    now = datetime.now(UTC)
    stream_run = producer.start_run(workspace_id, conversation_id, message_id, run_id, now=now)
    initial = consumer.replay(workspace_id, run_id, None, now=now)
    context = RequestContext.trusted(
        actor_id=uuid4(),
        user_id=uuid4(),
        workspace_id=workspace_id,
        trace=TraceContext(TRACE_ID, TRACEPARENT.split("-")[2]),
        authentication_method="browser_session",
    )
    assistant_run = cast(
        AssistantRun,
        SimpleNamespace(
            run_id=run_id,
            conversation_id=conversation_id,
            trace_id=TRACE_ID,
            traceparent=TRACEPARENT,
        ),
    )

    def append_after_connection() -> None:
        time.sleep(0.1)
        producer.append(
            run_id,
            "message.delta",
            TRACE_ID,
            TRACEPARENT,
            {"delta": "轮询恢复"},
            now=datetime.now(UTC),
        )

    writer = threading.Thread(target=append_after_connection, daemon=True)
    frames = assistant_routes.stream_run_frames(
        consumer,
        context,
        assistant_run,
        lambda: assistant_run,
        None,
        initial,
        heartbeat_seconds=15,
        poll_interval_ms=50,
    )
    started = time.monotonic()
    writer.start()
    try:
        frame = next(frames)
    finally:
        frames.close()
        writer.join(timeout=2)
        producer.close()
        consumer.close()

    assert stream_run.last_sequence_no == 0
    assert "event: message.delta" in frame
    assert '"sequence_no":1' in frame
    assert time.monotonic() - started < 5


def test_twenty_cross_instance_recovery_samples_meet_local_p99_budget(
    database: DatabaseHarness,
) -> None:
    """按 P2-01 nearest-rank 口径采集 20 个样本，本地最大值即 p99 且不得超过五秒。"""

    valkey_url = os.environ.get("AI_PLATFORM_TEST_VALKEY_URL", DEFAULT_VALKEY_URL)
    writer = _service(database, valkey_url)
    reader = _service(database, valkey_url)
    workspace_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    run_id = uuid4()
    now = datetime.now(UTC)
    subscription = reader.subscribe(run_id)
    assert subscription is not None
    writer.start_run(workspace_id, conversation_id, message_id, run_id, now=now)
    assert _wait_for_wakeup(subscription)

    cursor = None
    recovery_seconds: list[float] = []
    try:
        for sequence_no in range(1, 21):
            started = time.monotonic()
            event = writer.append(
                run_id,
                "message.delta",
                TRACE_ID,
                TRACEPARENT,
                {"delta": f"合成片段-{sequence_no}"},
                now=datetime.now(UTC),
            )
            assert _wait_for_wakeup(subscription)
            replay = reader.replay(
                workspace_id,
                run_id,
                cursor,
                now=datetime.now(UTC),
            )
            recovery_seconds.append(time.monotonic() - started)
            assert replay.events == (event,)
            assert event.sequence_no == sequence_no
            cursor = event.event_id
    finally:
        subscription.close()
        writer.close()
        reader.close()

    assert len(recovery_seconds) == 20
    assert max(recovery_seconds) <= 5


def test_valkey_outage_does_not_block_stream_fact_commit(database: DatabaseHarness) -> None:
    """通知端口不可达时，事务仍成功提交并能由任意无通知实例完成回放。"""

    unavailable = _service(database, "redis://127.0.0.1:1/0")
    fallback = _service(database, None)
    workspace_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    run_id = uuid4()
    now = datetime.now(UTC)
    try:
        unavailable.start_run(workspace_id, conversation_id, message_id, run_id, now=now)
        event = unavailable.append(
            run_id,
            "message.delta",
            TRACE_ID,
            TRACEPARENT,
            {"delta": "通知故障后仍可恢复"},
            now=now,
        )
        replay = fallback.replay(workspace_id, run_id, None, now=now)

        subscription = unavailable.subscribe(run_id)
        assert subscription is None or subscription.wait(0.05) is False
        assert [item.event_id for item in replay.events] == [event.event_id]
    finally:
        unavailable.close()
        fallback.close()
