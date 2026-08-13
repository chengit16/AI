"""建立 P0-10 SSE 事件事实、回放和同会话并发基线。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0003"
down_revision: str | None = "20260813_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "stream_runs",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_sequence_no", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("final_payload", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'completed', 'failed', 'cancelled')",
            name="ck_stream_runs_status",
        ),
        sa.CheckConstraint("last_sequence_no >= 0", name="ck_stream_runs_sequence_no"),
        schema=schema,
    )
    op.create_index(
        "ix_stream_runs_workspace_conversation",
        "stream_runs",
        ["workspace_id", "conversation_id"],
        schema=schema,
    )
    op.create_index(
        "uq_stream_runs_active_conversation",
        "stream_runs",
        ["conversation_id"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "stream_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.Column("traceparent", sa.String(length=55), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("run_id", "sequence_no", name="uq_stream_events_run_sequence"),
        sa.CheckConstraint("sequence_no >= 1", name="ck_stream_events_sequence_no"),
        schema=schema,
    )
    op.create_index(
        "ix_stream_events_workspace_run_sequence",
        "stream_events",
        ["workspace_id", "run_id", "sequence_no"],
        schema=schema,
    )
    op.create_index(
        "ix_stream_events_expires_at",
        "stream_events",
        ["expires_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("ix_stream_events_expires_at", table_name="stream_events", schema=schema)
    op.drop_index(
        "ix_stream_events_workspace_run_sequence",
        table_name="stream_events",
        schema=schema,
    )
    op.drop_table("stream_events", schema=schema)
    op.drop_index(
        "uq_stream_runs_active_conversation",
        table_name="stream_runs",
        schema=schema,
    )
    op.drop_index(
        "ix_stream_runs_workspace_conversation",
        table_name="stream_runs",
        schema=schema,
    )
    op.drop_table("stream_runs", schema=schema)
