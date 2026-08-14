"""建立 P1F-02 工作流步骤、尝试、预算和受限执行状态。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260815_0032"
down_revision: str | None = "20260815_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _extend_runs(schema)
    _create_steps(schema)
    _create_attempts(schema)


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "ix_workflow_node_attempts_run",
        table_name="workflow_node_attempts",
        schema=schema,
    )
    op.drop_table("workflow_node_attempts", schema=schema)
    op.drop_index(
        "ix_workflow_run_steps_run_sequence",
        table_name="workflow_run_steps",
        schema=schema,
    )
    op.drop_table("workflow_run_steps", schema=schema)
    op.drop_constraint("ck_workflow_runs_usage", "workflow_runs", schema=schema, type_="check")
    op.drop_constraint("ck_workflow_runs_completion", "workflow_runs", schema=schema, type_="check")
    op.drop_constraint("ck_workflow_runs_status", "workflow_runs", schema=schema, type_="check")
    for column in (
        "output_bytes",
        "retrieval_calls",
        "model_calls",
        "steps_executed",
        "execution_budget",
        "executor_version",
        "output_payload",
    ):
        op.drop_column("workflow_runs", column, schema=schema)
    op.create_check_constraint(
        "ck_workflow_runs_status",
        "workflow_runs",
        "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_workflow_runs_completion",
        "workflow_runs",
        "(status IN ('queued', 'running') AND completed_at IS NULL) OR "
        "(status IN ('succeeded', 'failed', 'cancelled') AND completed_at IS NOT NULL)",
        schema=schema,
    )


def _extend_runs(schema: str) -> None:
    op.drop_constraint("ck_workflow_runs_completion", "workflow_runs", schema=schema, type_="check")
    op.drop_constraint("ck_workflow_runs_status", "workflow_runs", schema=schema, type_="check")
    op.add_column(
        "workflow_runs",
        sa.Column("output_payload", postgresql.JSONB(), nullable=True),
        schema=schema,
    )
    op.add_column(
        "workflow_runs",
        sa.Column("executor_version", sa.String(64), nullable=True),
        schema=schema,
    )
    op.add_column(
        "workflow_runs",
        sa.Column("execution_budget", postgresql.JSONB(), nullable=True),
        schema=schema,
    )
    for column in ("steps_executed", "model_calls", "retrieval_calls", "output_bytes"):
        op.add_column(
            "workflow_runs",
            sa.Column(column, sa.Integer(), nullable=False, server_default="0"),
            schema=schema,
        )
    op.create_check_constraint(
        "ck_workflow_runs_status",
        "workflow_runs",
        "status IN ('queued', 'running', 'waiting_approval', 'succeeded', 'failed', 'cancelled')",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_workflow_runs_usage",
        "workflow_runs",
        "steps_executed >= 0 AND model_calls >= 0 AND retrieval_calls >= 0 AND output_bytes >= 0",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_workflow_runs_completion",
        "workflow_runs",
        "(status IN ('queued', 'running', 'waiting_approval') AND completed_at IS NULL) OR "
        "(status IN ('succeeded', 'failed', 'cancelled') AND completed_at IS NOT NULL)",
        schema=schema,
    )


def _create_steps(schema: str) -> None:
    op.create_table(
        "workflow_run_steps",
        sa.Column("workflow_step_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("node_type", sa.String(32), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_payload", postgresql.JSONB(), nullable=True),
        sa.Column("output_payload", postgresql.JSONB(), nullable=True),
        sa.Column("branch_key", sa.String(64), nullable=True),
        sa.Column("policy_decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.UniqueConstraint(
            "workflow_run_id",
            "node_id",
            name="uq_workflow_run_steps_node",
        ),
        sa.UniqueConstraint(
            "workflow_step_id",
            "workflow_run_id",
            "workspace_id",
            name="uq_workflow_run_steps_identity",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id", "workspace_id"],
            [f"{schema}.workflow_runs.workflow_run_id", f"{schema}.workflow_runs.workspace_id"],
            name="fk_workflow_run_steps_run",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("sequence_no >= 1", name="ck_workflow_run_steps_sequence"),
        sa.CheckConstraint(
            "node_type IN "
            "('trigger', 'condition', 'knowledge_retrieval', 'model', 'approval', 'result')",
            name="ck_workflow_run_steps_node_type",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'waiting_approval', 'succeeded', 'skipped', 'failed')",
            name="ck_workflow_run_steps_status",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status = 'waiting_approval' AND started_at IS NOT NULL "
            "AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'failed') AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL) OR "
            "(status = 'skipped' AND started_at IS NULL AND completed_at IS NOT NULL)",
            name="ck_workflow_run_steps_timestamps",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_workflow_run_steps_run_sequence",
        "workflow_run_steps",
        ["workflow_run_id", "sequence_no"],
        schema=schema,
    )


def _create_attempts(schema: str) -> None:
    op.create_table(
        "workflow_node_attempts",
        sa.Column("workflow_attempt_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_step_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("executor_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_hash", sa.String(64), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.UniqueConstraint(
            "workflow_step_id",
            "attempt_no",
            name="uq_workflow_node_attempts_number",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_step_id", "workflow_run_id", "workspace_id"],
            [
                f"{schema}.workflow_run_steps.workflow_step_id",
                f"{schema}.workflow_run_steps.workflow_run_id",
                f"{schema}.workflow_run_steps.workspace_id",
            ],
            name="fk_workflow_node_attempts_step",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("attempt_no >= 1", name="ck_workflow_node_attempts_number"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_workflow_node_attempts_status",
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_workflow_node_attempts_input_hash",
        ),
        sa.CheckConstraint(
            "output_hash IS NULL OR output_hash ~ '^[0-9a-f]{64}$'",
            name="ck_workflow_node_attempts_output_hash",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'failed') AND completed_at IS NOT NULL)",
            name="ck_workflow_node_attempts_completion",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_workflow_node_attempts_run",
        "workflow_node_attempts",
        ["workflow_run_id", "started_at"],
        schema=schema,
    )
