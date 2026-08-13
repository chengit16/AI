"""建立 P0-06 工作空间隔离与 Outbox 基线。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0001"
down_revision: str | None = None
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
        "workspace_resources",
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("sensitive_value", sa.String(length=1024), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_workspace_resources_version"),
        schema=schema,
    )
    op.create_index(
        "ix_workspace_resources_workspace_resource",
        "workspace_resources",
        ["workspace_id", "resource_id"],
        schema=schema,
    )
    op.create_table(
        "outbox_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(length=255), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.Column("traceparent", sa.String(length=55), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("schema_version >= 1", name="ck_outbox_events_schema_version"),
        sa.CheckConstraint("aggregate_version >= 1", name="ck_outbox_events_aggregate_version"),
        schema=schema,
    )
    op.create_index(
        "ix_outbox_events_unpublished",
        "outbox_events",
        ["published_at", "occurred_at"],
        schema=schema,
    )
    op.create_table(
        "consumer_receipts",
        sa.Column("consumer_name", sa.String(length=255), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "consumer_name",
            "event_id",
            name="uq_consumer_receipts_consumer_event",
        ),
        schema=schema,
    )
    op.create_table(
        "resource_projections",
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("applied_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("apply_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("apply_count >= 1", name="ck_resource_projections_apply_count"),
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_table("resource_projections", schema=schema)
    op.drop_table("consumer_receipts", schema=schema)
    op.drop_index("ix_outbox_events_unpublished", table_name="outbox_events", schema=schema)
    op.drop_table("outbox_events", schema=schema)
    op.drop_index(
        "ix_workspace_resources_workspace_resource",
        table_name="workspace_resources",
        schema=schema,
    )
    op.drop_table("workspace_resources", schema=schema)
