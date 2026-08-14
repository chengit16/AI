"""验证 P0-10 PostgreSQL 流式事件并发与断点回放。"""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.modules.streaming.domain.errors import (
    ConversationBusyError,
    StreamCursorNotFoundError,
    StreamEventExpiredError,
    StreamReplayLimitExceededError,
    StreamRunClosedError,
    StreamRunNotFoundError,
)
from ai_platform_api.modules.streaming.domain.models import StreamPolicy
from ai_platform_api.modules.streaming.infrastructure.sqlalchemy import SqlAlchemyStreamStore
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000001")
OTHER_WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000002")
CONVERSATION_ID = UUID("20000000-0000-4000-8000-000000000001")
MESSAGE_ID = UUID("30000000-0000-4000-8000-000000000001")
RUN_ID = UUID("40000000-0000-4000-8000-000000000001")
TRACE_ID = "0123456789abcdef0123456789abcdef"
TRACEPARENT = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class DatabaseHarness:
    schema: str
    engine: Engine
    sessions: sessionmaker[Session]


@pytest.fixture(scope="module")
def database() -> Iterator[DatabaseHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p010_test_{uuid4().hex}"
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


def policy(**overrides: int) -> StreamPolicy:
    values = {
        "retention_seconds": 24 * 60 * 60,
        "replay_limit_events": 5_000,
        "replay_limit_bytes": 10 * 1024 * 1024,
    }
    values.update(overrides)
    return StreamPolicy(**values)


def test_migration_creates_stream_tables_and_active_conversation_index(
    database: DatabaseHarness,
) -> None:
    with database.engine.connect() as connection:
        tables = set(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = :schema"
                ),
                {"schema": database.schema},
            ).scalars()
        )
        indexes = set(
            connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = :schema"),
                {"schema": database.schema},
            ).scalars()
        )

    assert {"stream_runs", "stream_events"}.issubset(tables)
    assert {
        "uq_stream_runs_active_conversation",
        "uq_stream_events_run_sequence",
        "ix_stream_events_workspace_run_sequence",
        "ix_stream_events_expires_at",
    }.issubset(indexes)


def test_event_sequence_replay_and_snapshot_recovery(database: DatabaseHarness) -> None:
    with database.sessions.begin() as session:
        store = SqlAlchemyStreamStore(session)
        run = store.start_run(
            WORKSPACE_ID,
            CONVERSATION_ID,
            MESSAGE_ID,
            RUN_ID,
            NOW,
            policy(),
        )
        first = store.append_event(
            RUN_ID,
            UUID(int=5_001),
            "message.delta",
            TRACE_ID,
            TRACEPARENT,
            {"delta": "第一段"},
            NOW,
        )
        second = store.append_event(
            RUN_ID,
            UUID(int=5_002),
            "message.completed",
            TRACE_ID,
            TRACEPARENT,
            {"text": "最终结果"},
            NOW,
        )
        finished = store.finish_run(RUN_ID, "completed", {"text": "最终结果"}, NOW)

        replay = store.replay(WORKSPACE_ID, RUN_ID, first.event_id, NOW, policy())
        snapshot = store.replay(WORKSPACE_ID, RUN_ID, second.event_id, NOW, policy())

    assert run.last_sequence_no == 0
    assert [event.sequence_no for event in replay.events] == [2]
    assert replay.events[0].event_id == second.event_id
    assert finished.status == "completed"
    assert snapshot.events == ()
    assert snapshot.snapshot_required is True
    assert snapshot.final_run.final_payload == {"text": "最终结果"}


def test_active_conversation_unique_and_finished_run_can_be_replaced(
    database: DatabaseHarness,
) -> None:
    conversation_id = UUID(int=6_001)
    first_run_id = UUID(int=6_002)
    second_run_id = UUID(int=6_003)
    with database.sessions.begin() as session:
        store = SqlAlchemyStreamStore(session)
        store.start_run(WORKSPACE_ID, conversation_id, MESSAGE_ID, first_run_id, NOW, policy())
        with pytest.raises(ConversationBusyError):
            store.start_run(WORKSPACE_ID, conversation_id, MESSAGE_ID, second_run_id, NOW, policy())
        store.finish_run(first_run_id, "cancelled", None, NOW)
        replacement = store.start_run(
            WORKSPACE_ID,
            conversation_id,
            MESSAGE_ID,
            second_run_id,
            NOW,
            policy(),
        )

    assert replacement.run_id == second_run_id


def test_workspace_cursor_and_expired_events_fail_closed(database: DatabaseHarness) -> None:
    run_id = UUID(int=7_001)
    conversation_id = UUID(int=7_002)
    with database.sessions.begin() as session:
        store = SqlAlchemyStreamStore(session)
        store.start_run(
            WORKSPACE_ID,
            conversation_id,
            MESSAGE_ID,
            run_id,
            NOW,
            policy(retention_seconds=10),
        )
        event = store.append_event(
            run_id,
            UUID(int=7_003),
            "message.delta",
            TRACE_ID,
            TRACEPARENT,
            {},
            NOW,
        )
        with pytest.raises(StreamRunNotFoundError):
            store.replay(OTHER_WORKSPACE_ID, run_id, None, NOW, policy())
        with pytest.raises(StreamCursorNotFoundError):
            store.replay(WORKSPACE_ID, run_id, uuid4(), NOW, policy())
        with pytest.raises(StreamEventExpiredError):
            store.replay(
                WORKSPACE_ID, run_id, event.event_id, NOW + timedelta(seconds=11), policy()
            )


def test_replay_event_and_byte_limits_are_enforced(database: DatabaseHarness) -> None:
    run_id = UUID(int=8_001)
    conversation_id = UUID(int=8_002)
    with database.sessions.begin() as session:
        store = SqlAlchemyStreamStore(session)
        store.start_run(WORKSPACE_ID, conversation_id, MESSAGE_ID, run_id, NOW, policy())
        store.append_event(
            run_id,
            UUID(int=8_003),
            "message.delta",
            TRACE_ID,
            TRACEPARENT,
            {"delta": "合成大段内容"},
            NOW,
        )
        store.append_event(
            run_id,
            UUID(int=8_004),
            "message.delta",
            TRACE_ID,
            TRACEPARENT,
            {"delta": "第二段"},
            NOW,
        )
        with pytest.raises(StreamReplayLimitExceededError):
            store.replay(
                WORKSPACE_ID,
                run_id,
                None,
                NOW,
                policy(replay_limit_events=1),
            )
        with pytest.raises(StreamReplayLimitExceededError):
            store.replay(
                WORKSPACE_ID,
                run_id,
                None,
                NOW,
                policy(replay_limit_bytes=1),
            )


def test_closed_run_rejects_append_and_missing_run_is_not_leaked(database: DatabaseHarness) -> None:
    run_id = UUID(int=9_001)
    conversation_id = UUID(int=9_002)
    with database.sessions.begin() as session:
        store = SqlAlchemyStreamStore(session)
        store.start_run(WORKSPACE_ID, conversation_id, MESSAGE_ID, run_id, NOW, policy())
        store.finish_run(run_id, "failed", {"error": "合成失败"}, NOW)
        with pytest.raises(StreamRunClosedError):
            store.append_event(
                run_id, UUID(int=9_003), "message.delta", TRACE_ID, TRACEPARENT, {}, NOW
            )
        with pytest.raises(StreamRunNotFoundError):
            store.append_event(
                uuid4(), UUID(int=9_004), "message.delta", TRACE_ID, TRACEPARENT, {}, NOW
            )
