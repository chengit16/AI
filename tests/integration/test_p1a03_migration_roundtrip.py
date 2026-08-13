import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, text

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)


@pytest.fixture
def migration_database() -> Iterator[tuple[Config, Connection, str]]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1a03_test_{uuid4().hex}"
    engine = create_engine(database_url)
    with engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.commit()
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "infra/migrations"))
        config.set_main_option("prepend_sys_path", str(ROOT / "apps/api/src"))
        config.set_main_option("sqlalchemy.url", database_url)
        config.set_main_option("ai_platform_schema", schema)
        try:
            yield config, connection, schema
        finally:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            connection.commit()
    engine.dispose()


def schema_snapshot(connection: Connection, schema: str) -> tuple[tuple[object, ...], ...]:
    rows = connection.execute(
        text(
            """
            SELECT 'column', table_name, column_name, ordinal_position::text,
                   data_type, udt_name, is_nullable,
                   COALESCE(column_default, ''), COALESCE(generation_expression, '')
              FROM information_schema.columns
             WHERE table_schema = :schema
            UNION ALL
            SELECT 'constraint', conrelid::regclass::text, conname, contype::text,
                   pg_get_constraintdef(oid), '', '', '', ''
              FROM pg_constraint
             WHERE connamespace = CAST(:schema AS regnamespace)
            UNION ALL
            SELECT 'index', tablename, indexname, '',
                   REPLACE(indexdef, quote_ident(:schema) || '.', ''), '', '', '', ''
              FROM pg_indexes
             WHERE schemaname = :schema
             ORDER BY 1, 2, 3, 4, 5
            """
        ),
        {"schema": schema},
    )
    return tuple(tuple(row) for row in rows)


def business_tables(connection: Connection, schema: str) -> set[str]:
    return set(
        connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name <> 'alembic_version'"
            ),
            {"schema": schema},
        ).scalars()
    )


def current_revision(connection: Connection, schema: str) -> str | None:
    revision = connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version'))
    return revision if isinstance(revision, str) else None


def test_empty_schema_can_upgrade_downgrade_and_reupgrade_identically(
    migration_database: tuple[Config, Connection, str],
) -> None:
    config, connection, schema = migration_database

    command.upgrade(config, "head")
    connection.commit()
    first_head = schema_snapshot(connection, schema)

    assert current_revision(connection, schema) == "20260813_0004"
    assert business_tables(connection, schema) == {
        "accounts",
        "consumer_receipts",
        "open_api_keys",
        "outbox_events",
        "resource_projections",
        "retrieval_chunks",
        "stream_events",
        "stream_runs",
        "workspace_memberships",
        "workspace_resources",
        "workspaces",
    }

    command.downgrade(config, "base")
    connection.commit()

    assert business_tables(connection, schema) == set()
    assert current_revision(connection, schema) is None

    command.upgrade(config, "head")
    connection.commit()

    assert current_revision(connection, schema) == "20260813_0004"
    assert schema_snapshot(connection, schema) == first_head
