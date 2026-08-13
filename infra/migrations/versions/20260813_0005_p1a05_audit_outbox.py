"""建立 P1A-05 审计事实与 Outbox 发布调度状态。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0005"
down_revision: str | None = "20260813_0004"
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
        "audit_records",
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("resource_type", sa.String(length=128), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.Column("traceparent", sa.String(length=55), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'denied', 'failed')",
            name="ck_audit_outcome",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_audit_records_workspace_occurred",
        "audit_records",
        ["workspace_id", "occurred_at", "audit_id"],
        schema=schema,
    )
    op.create_index(
        "ix_audit_records_resource",
        "audit_records",
        ["workspace_id", "resource_type", "resource_id"],
        schema=schema,
    )
    # 审计事实一旦写入即不可由普通业务事务更新或删除，修正通过追加记录表达。
    op.execute(
        sa.text(
            f'CREATE FUNCTION "{schema}".reject_audit_record_mutation() '
            "RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'audit records are immutable'; END; $$"
        )
    )
    op.execute(
        sa.text(
            f"CREATE TRIGGER reject_audit_record_mutation BEFORE UPDATE OR DELETE ON "
            f'"{schema}".audit_records FOR EACH ROW EXECUTE FUNCTION '
            f'"{schema}".reject_audit_record_mutation()'
        )
    )

    op.add_column(
        "outbox_events",
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "outbox_events",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "outbox_events",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "outbox_events",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        schema=schema,
    )
    op.add_column(
        "outbox_events",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        schema=schema,
    )
    op.add_column(
        "outbox_events",
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "outbox_events",
        sa.Column("claimed_by", sa.String(length=255), nullable=True),
        schema=schema,
    )
    op.add_column(
        "outbox_events",
        sa.Column("claim_until", sa.DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "outbox_events",
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        schema=schema,
    )
    # 阶段 0 的合成事件以 occurred_at 作为首次可发布时间，不改变原有事件顺序。
    op.execute(
        sa.text(
            f'UPDATE "{schema}".outbox_events SET available_at = occurred_at '
            "WHERE available_at IS NULL"
        )
    )
    op.alter_column("outbox_events", "available_at", nullable=False, schema=schema)
    op.alter_column("outbox_events", "status", server_default=None, schema=schema)
    op.alter_column("outbox_events", "attempt_count", server_default=None, schema=schema)
    op.create_check_constraint(
        "ck_outbox_events_attempt_count",
        "outbox_events",
        "attempt_count >= 0",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_outbox_events_status",
        "outbox_events",
        "status IN ('pending', 'publishing', 'published', 'dead_letter')",
        schema=schema,
    )
    op.drop_index("ix_outbox_events_unpublished", table_name="outbox_events", schema=schema)
    op.create_index(
        "ix_outbox_events_dispatch",
        "outbox_events",
        ["status", "available_at", "occurred_at"],
        schema=schema,
    )
    op.create_index(
        "ix_outbox_events_claim",
        "outbox_events",
        ["status", "claim_until"],
        schema=schema,
    )

    op.add_column(
        "consumer_receipts",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "consumer_receipts",
        sa.Column("trace_id", sa.String(length=32), nullable=True),
        schema=schema,
    )
    op.add_column(
        "consumer_receipts",
        sa.Column("traceparent", sa.String(length=55), nullable=True),
        schema=schema,
    )
    op.execute(
        sa.text(
            f'UPDATE "{schema}".consumer_receipts SET '
            "task_id = event_id, "
            "trace_id = '00000000000000000000000000000001', "
            "traceparent = '00-00000000000000000000000000000001-0000000000000001-00' "
            "WHERE task_id IS NULL"
        )
    )
    op.alter_column("consumer_receipts", "task_id", nullable=False, schema=schema)
    op.alter_column("consumer_receipts", "trace_id", nullable=False, schema=schema)
    op.alter_column("consumer_receipts", "traceparent", nullable=False, schema=schema)

    op.add_column(
        "resource_projections",
        sa.Column("last_traceparent", sa.Text(), nullable=True),
        schema=schema,
    )
    op.execute(
        sa.text(
            f'UPDATE "{schema}".resource_projections SET '
            "last_traceparent = '00-00000000000000000000000000000001-0000000000000001-00' "
            "WHERE last_traceparent IS NULL"
        )
    )
    op.alter_column("resource_projections", "last_traceparent", nullable=False, schema=schema)


def downgrade() -> None:
    schema = _schema()
    op.drop_column("resource_projections", "last_traceparent", schema=schema)
    op.drop_column("consumer_receipts", "traceparent", schema=schema)
    op.drop_column("consumer_receipts", "trace_id", schema=schema)
    op.drop_column("consumer_receipts", "task_id", schema=schema)
    op.drop_index("ix_outbox_events_claim", table_name="outbox_events", schema=schema)
    op.drop_index("ix_outbox_events_dispatch", table_name="outbox_events", schema=schema)
    op.create_index(
        "ix_outbox_events_unpublished",
        "outbox_events",
        ["published_at", "occurred_at"],
        schema=schema,
    )
    op.drop_constraint(
        "ck_outbox_events_status",
        "outbox_events",
        type_="check",
        schema=schema,
    )
    op.drop_constraint(
        "ck_outbox_events_attempt_count",
        "outbox_events",
        type_="check",
        schema=schema,
    )
    op.drop_column("outbox_events", "last_error_code", schema=schema)
    op.drop_column("outbox_events", "claim_until", schema=schema)
    op.drop_column("outbox_events", "claimed_by", schema=schema)
    op.drop_column("outbox_events", "available_at", schema=schema)
    op.drop_column("outbox_events", "attempt_count", schema=schema)
    op.drop_column("outbox_events", "status", schema=schema)
    op.drop_column("outbox_events", "request_id", schema=schema)
    op.drop_column("outbox_events", "user_id", schema=schema)
    op.drop_column("outbox_events", "actor_id", schema=schema)
    op.drop_index("ix_audit_records_resource", table_name="audit_records", schema=schema)
    op.drop_index(
        "ix_audit_records_workspace_occurred",
        table_name="audit_records",
        schema=schema,
    )
    op.drop_table("audit_records", schema=schema)
    op.execute(sa.text(f'DROP FUNCTION "{schema}".reject_audit_record_mutation()'))
