"""建立 P4-05 工具计划预算与当前策略决策事实。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260816_0055"
down_revision: str | None = "20260816_0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """冻结每步预算，并要求 Step 进入 ready 前存在精确匹配的允许证据。"""

    schema = _schema()
    _add_step_budgets(schema)
    op.create_unique_constraint(
        "uq_agent_tool_definitions_policy_binding",
        "agent_tool_definitions",
        ["tool_id", "tool_version", "permission_code"],
        schema=schema,
    )
    _create_policy_decisions(schema)
    op.execute(sa.text(_step_transition_sql(schema, require_policy=True)))
    op.execute(sa.text(_attempt_insert_sql(schema, use_step_budget=True)))
    op.execute(sa.text(_policy_immutable_sql(schema)))
    op.execute(
        sa.text(
            f"CREATE TRIGGER trg_tool_policy_decisions_immutable BEFORE UPDATE "
            f'ON "{schema}".tool_policy_decisions FOR EACH ROW '
            f'EXECUTE FUNCTION "{schema}".reject_tool_policy_decision_update()'
        )
    )


def downgrade() -> None:
    """仅在没有 P4-05 策略事实时移除新表，并恢复 P4-03 状态约束。"""

    schema = _schema()
    count = op.get_bind().scalar(sa.text(f'SELECT count(*) FROM "{schema}".tool_policy_decisions'))
    if int(count or 0) > 0:
        raise RuntimeError("存在工具计划策略事实, 拒绝破坏性降级")
    op.execute(
        sa.text(
            f'DROP TRIGGER trg_tool_policy_decisions_immutable ON "{schema}".tool_policy_decisions'
        )
    )
    op.execute(sa.text(f'DROP FUNCTION "{schema}".reject_tool_policy_decision_update()'))
    op.execute(sa.text(_step_transition_sql(schema, require_policy=False)))
    op.execute(sa.text(_attempt_insert_sql(schema, use_step_budget=False)))
    op.drop_index(
        "ix_tool_policy_decisions_workspace_time",
        table_name="tool_policy_decisions",
        schema=schema,
    )
    op.drop_table("tool_policy_decisions", schema=schema)
    op.drop_constraint(
        "uq_agent_tool_definitions_policy_binding",
        "agent_tool_definitions",
        schema=schema,
        type_="unique",
    )
    op.drop_constraint("ck_tool_steps_budget", "tool_steps", schema=schema, type_="check")
    for column_name in (
        "max_cost_microunits",
        "max_result_bytes",
        "max_attempts",
        "timeout_seconds",
    ):
        op.drop_column("tool_steps", column_name, schema=schema)


def _add_step_budgets(schema: str) -> None:
    """使用保守默认值兼容 P4-03 既有事实，随后移除数据库默认写入口。"""

    columns = (
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("max_result_bytes", sa.Integer(), nullable=False, server_default="262144"),
        sa.Column("max_cost_microunits", sa.BigInteger(), nullable=False, server_default="0"),
    )
    for column in columns:
        op.add_column("tool_steps", column, schema=schema)
    op.create_check_constraint(
        "ck_tool_steps_budget",
        "tool_steps",
        "timeout_seconds BETWEEN 1 AND 1800 AND max_attempts BETWEEN 1 AND 5 "
        "AND max_result_bytes BETWEEN 1 AND 262144 AND max_cost_microunits >= 0",
        schema=schema,
    )
    for column in columns:
        op.alter_column(
            "tool_steps",
            column.name,
            server_default=None,
            schema=schema,
        )


def _create_policy_decisions(schema: str) -> None:
    op.create_table(
        "tool_policy_decisions",
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_version", sa.Integer(), nullable=False),
        sa.Column("canonical_arguments_hash", sa.String(64), nullable=False),
        sa.Column("permission_code", sa.String(160), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("resource_scope_hash", sa.String(64), nullable=False),
        sa.Column("field_mask_hash", sa.String(64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("step_id", name="uq_tool_policy_decisions_step"),
        sa.ForeignKeyConstraint(
            [
                "step_id",
                "run_id",
                "workspace_id",
                "tool_id",
                "tool_version",
                "canonical_arguments_hash",
            ],
            [
                f"{schema}.tool_steps.step_id",
                f"{schema}.tool_steps.run_id",
                f"{schema}.tool_steps.workspace_id",
                f"{schema}.tool_steps.tool_id",
                f"{schema}.tool_steps.tool_version",
                f"{schema}.tool_steps.canonical_arguments_hash",
            ],
            name="fk_tool_policy_decisions_step",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tool_id", "tool_version", "permission_code"],
            [
                f"{schema}.agent_tool_definitions.tool_id",
                f"{schema}.agent_tool_definitions.tool_version",
                f"{schema}.agent_tool_definitions.permission_code",
            ],
            name="fk_tool_policy_decisions_definition",
        ),
        sa.CheckConstraint(
            "tool_version >= 1",
            name="ck_tool_policy_decisions_tool_version",
        ),
        sa.CheckConstraint(
            "canonical_arguments_hash ~ '^[0-9a-f]{64}$' "
            "AND resource_scope_hash ~ '^[0-9a-f]{64}$' "
            "AND field_mask_hash ~ '^[0-9a-f]{64}$'",
            name="ck_tool_policy_decisions_hashes",
        ),
        sa.CheckConstraint(
            "permission_code ~ '^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*){2,}$'",
            name="ck_tool_policy_decisions_permission",
        ),
        sa.CheckConstraint(
            "policy_version >= 1",
            name="ck_tool_policy_decisions_version",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_tool_policy_decisions_workspace_time",
        "tool_policy_decisions",
        ["workspace_id", "evaluated_at"],
        schema=schema,
    )


def _step_transition_sql(schema: str, *, require_policy: bool) -> str:
    budget_identity = (
        ", NEW.timeout_seconds, NEW.max_attempts, NEW.max_result_bytes, NEW.max_cost_microunits"
        if require_policy
        else ""
    )
    old_budget_identity = (
        ", OLD.timeout_seconds, OLD.max_attempts, OLD.max_result_bytes, OLD.max_cost_microunits"
        if require_policy
        else ""
    )
    policy_guard = (
        f"""
      IF NEW.state = 'ready' AND NOT EXISTS (
          SELECT 1 FROM "{schema}".tool_policy_decisions AS decisions
          WHERE decisions.step_id = OLD.step_id
            AND decisions.run_id = OLD.run_id
            AND decisions.workspace_id = OLD.workspace_id
            AND decisions.tool_id = OLD.tool_id
            AND decisions.tool_version = OLD.tool_version
            AND decisions.canonical_arguments_hash = OLD.canonical_arguments_hash
            AND decisions.evaluated_at = NEW.updated_at) THEN
        RAISE EXCEPTION 'tool step ready requires current allow decision';
      END IF;"""
        if require_policy
        else ""
    )
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_step_transition() RETURNS trigger AS $$
    DECLARE parent_state varchar(32);
    BEGIN
      IF ROW(NEW.run_id, NEW.workspace_id, NEW.sequence_no, NEW.tool_id,
             NEW.tool_version, NEW.canonical_arguments_hash, NEW.created_at{budget_identity})
         IS DISTINCT FROM
         ROW(OLD.run_id, OLD.workspace_id, OLD.sequence_no, OLD.tool_id,
             OLD.tool_version, OLD.canonical_arguments_hash,
             OLD.created_at{old_budget_identity}) THEN
        RAISE EXCEPTION 'tool step identity is immutable';
      END IF;
      IF NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'tool step version must advance exactly once';
      END IF;
      IF OLD.state IN ('completed', 'failed', 'cancelled', 'timed_out') THEN
        RAISE EXCEPTION 'tool step terminal state is immutable';
      END IF;
      SELECT state INTO parent_state FROM "{schema}".tool_runs WHERE run_id = OLD.run_id;
      IF NEW.state = 'cancelled' AND parent_state IN ('cancellation_requested', 'cancelled') THEN
        NULL;
      ELSIF NOT ((OLD.state = 'planned' AND NEW.state IN ('policy_checking', 'cancelled'))
          OR (OLD.state = 'policy_checking' AND NEW.state IN
              ('ready', 'waiting_confirmation', 'waiting_approval'))
          OR (OLD.state = 'waiting_confirmation' AND NEW.state = 'ready')
          OR (OLD.state = 'waiting_approval' AND NEW.state = 'ready')
          OR (OLD.state = 'ready' AND NEW.state = 'running')
          OR (OLD.state = 'running' AND NEW.state IN
              ('completed', 'failed', 'timed_out'))) THEN
        RAISE EXCEPTION 'illegal tool step transition: % -> %', OLD.state, NEW.state;
      END IF;
      IF OLD.current_attempt_no IS DISTINCT FROM NEW.current_attempt_no
         AND NOT (OLD.state = 'ready' AND NEW.state = 'running'
                  AND NEW.current_attempt_no = COALESCE(OLD.current_attempt_no, 0) + 1) THEN
        RAISE EXCEPTION 'tool step current attempt can only advance during claim';
      END IF;{policy_guard}
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _attempt_insert_sql(schema: str, *, use_step_budget: bool) -> str:
    attempt_limit = "steps.max_attempts" if use_step_budget else "runs.max_attempts_per_step"
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_attempt_insert() RETURNS trigger AS $$
    BEGIN
      IF NOT EXISTS (
          SELECT 1 FROM "{schema}".tool_steps AS steps
          JOIN "{schema}".tool_runs AS runs ON runs.run_id = steps.run_id
          WHERE steps.step_id = NEW.step_id AND steps.run_id = NEW.run_id
            AND steps.workspace_id = NEW.workspace_id AND steps.state = 'running'
            AND steps.current_attempt_no = NEW.attempt_no AND runs.state = 'running'
            AND runs.cancel_requested_at IS NULL
            AND NEW.attempt_no <= {attempt_limit}) THEN
        RAISE EXCEPTION 'tool attempt is not the current claimed generation';
      END IF;
      IF NEW.lease_generation <> NEW.attempt_no THEN
        RAISE EXCEPTION 'tool attempt lease generation mismatch';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _policy_immutable_sql(schema: str) -> str:
    return f"""
    CREATE FUNCTION "{schema}".reject_tool_policy_decision_update() RETURNS trigger AS $$
    BEGIN
      RAISE EXCEPTION 'tool policy decision is immutable';
    END;
    $$ LANGUAGE plpgsql;
    """
