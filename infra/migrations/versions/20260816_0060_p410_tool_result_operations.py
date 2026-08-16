"""建立 P4-10 工具安全结果、可恢复进度与完整用量成本事实。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260816_0060"
down_revision: str | None = "20260816_0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """创建三类最小运营事实，并在数据库层复核绑定、预算与不可变性。"""

    schema = _schema()
    op.create_unique_constraint(
        "uq_tool_calls_fact_binding",
        "tool_calls",
        ["tool_call_id", "workspace_id", "run_id", "step_id", "attempt_id"],
        schema=schema,
    )
    _create_safe_results(schema)
    _create_usage_records(schema)
    _create_progress_events(schema)
    _create_functions(schema)
    _create_triggers(schema)


def downgrade() -> None:
    """存在任何 P4-10 运营事实时拒绝破坏性回退。"""

    schema = _schema()
    count = op.get_bind().scalar(
        sa.text(
            f"""
            SELECT
              (SELECT count(*) FROM "{schema}".tool_safe_results)
              + (SELECT count(*) FROM "{schema}".tool_usage_records)
              + (SELECT count(*) FROM "{schema}".tool_progress_events)
            """
        )
    )
    if int(count or 0) > 0:
        raise RuntimeError("存在工具结果、用量或进度事实, 拒绝破坏性降级")
    _drop_triggers(schema)
    _drop_functions(schema)
    op.drop_index(
        "ix_tool_progress_events_workspace_run_cursor",
        table_name="tool_progress_events",
        schema=schema,
    )
    op.drop_table("tool_progress_events", schema=schema)
    op.drop_index(
        "ix_tool_usage_records_run",
        table_name="tool_usage_records",
        schema=schema,
    )
    op.drop_index(
        "ix_tool_usage_records_workspace_time",
        table_name="tool_usage_records",
        schema=schema,
    )
    op.drop_table("tool_usage_records", schema=schema)
    op.drop_index(
        "ix_tool_safe_results_workspace_time",
        table_name="tool_safe_results",
        schema=schema,
    )
    op.drop_table("tool_safe_results", schema=schema)
    op.drop_constraint(
        "uq_tool_calls_fact_binding",
        "tool_calls",
        type_="unique",
        schema=schema,
    )


def _create_safe_results(schema: str) -> None:
    op.create_table(
        "tool_safe_results",
        sa.Column("result_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("output_schema_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("result_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("schema_check", sa.String(8), nullable=False),
        sa.Column("size_check", sa.String(8), nullable=False),
        sa.Column("sensitive_fields_check", sa.String(8), nullable=False),
        sa.Column("prompt_injection_check", sa.String(8), nullable=False),
        sa.Column("eligible_for_model_context", sa.Boolean(), nullable=False),
        sa.Column("credential_exposure_detected", sa.Boolean(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tool_call_id", name="uq_tool_safe_results_call"),
        sa.ForeignKeyConstraint(
            ["tool_call_id", "workspace_id", "run_id", "step_id", "attempt_id"],
            [
                f"{schema}.tool_calls.tool_call_id",
                f"{schema}.tool_calls.workspace_id",
                f"{schema}.tool_calls.run_id",
                f"{schema}.tool_calls.step_id",
                f"{schema}.tool_calls.attempt_id",
            ],
            name="fk_tool_safe_results_call",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "output_schema_hash ~ '^[0-9a-f]{64}$' AND content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_tool_safe_results_hashes",
        ),
        sa.CheckConstraint("result_size_bytes >= 0", name="ck_tool_safe_results_size"),
        sa.CheckConstraint(
            "status IN ('accepted', 'rejected')",
            name="ck_tool_safe_results_status",
        ),
        sa.CheckConstraint(
            "schema_check IN ('passed', 'failed') AND size_check IN ('passed', 'failed') "
            "AND sensitive_fields_check IN ('passed', 'failed') "
            "AND prompt_injection_check IN ('passed', 'failed')",
            name="ck_tool_safe_results_checks",
        ),
        sa.CheckConstraint(
            "credential_exposure_detected = false AND "
            "((status = 'accepted' AND schema_check = 'passed' AND size_check = 'passed' "
            "AND sensitive_fields_check = 'passed' AND prompt_injection_check = 'passed' "
            "AND eligible_for_model_context = true AND result_size_bytes <= 262144) OR "
            "(status = 'rejected' AND eligible_for_model_context = false "
            "AND (schema_check = 'failed' OR size_check = 'failed' "
            "OR sensitive_fields_check = 'failed' OR prompt_injection_check = 'failed')))",
            name="ck_tool_safe_results_eligibility",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_tool_safe_results_workspace_time",
        "tool_safe_results",
        ["workspace_id", "recorded_at"],
        schema=schema,
    )


def _create_usage_records(schema: str) -> None:
    op.create_table(
        "tool_usage_records",
        sa.Column("usage_record_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_version", sa.Integer(), nullable=False),
        sa.Column("access_mode", sa.String(16), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("result_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("attempt_id", name="uq_tool_usage_records_attempt"),
        sa.ForeignKeyConstraint(
            ["tool_call_id", "workspace_id", "run_id", "step_id", "attempt_id"],
            [
                f"{schema}.tool_calls.tool_call_id",
                f"{schema}.tool_calls.workspace_id",
                f"{schema}.tool_calls.run_id",
                f"{schema}.tool_calls.step_id",
                f"{schema}.tool_calls.attempt_id",
            ],
            name="fk_tool_usage_records_call",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tool_id", "tool_version"],
            [
                f"{schema}.agent_tool_definitions.tool_id",
                f"{schema}.agent_tool_definitions.tool_version",
            ],
            name="fk_tool_usage_records_definition",
        ),
        sa.CheckConstraint("tool_version >= 1", name="ck_tool_usage_records_tool_version"),
        sa.CheckConstraint(
            "access_mode IN ('read', 'write')",
            name="ck_tool_usage_records_access_mode",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_tool_usage_records_risk",
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'failed', 'cancelled', 'timed_out', "
            "'ignored_late_result', 'manual_recovery')",
            name="ck_tool_usage_records_outcome",
        ),
        sa.CheckConstraint(
            "duration_ms >= 0 AND result_size_bytes >= 0 AND cost_microunits >= 0",
            name="ck_tool_usage_records_values",
        ),
        sa.CheckConstraint(
            "(outcome = 'succeeded' AND error_code IS NULL) OR "
            "(outcome <> 'succeeded' AND error_code IS NOT NULL)",
            name="ck_tool_usage_records_error",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_tool_usage_records_workspace_time",
        "tool_usage_records",
        ["workspace_id", "recorded_at"],
        schema=schema,
    )
    op.create_index(
        "ix_tool_usage_records_run",
        "tool_usage_records",
        ["run_id", "recorded_at"],
        schema=schema,
    )


def _create_progress_events(schema: str) -> None:
    op.create_table(
        "tool_progress_events",
        sa.Column("progress_event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cursor", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("run_state", sa.String(32), nullable=False),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("step_state", sa.String(32), nullable=True),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "cursor", name="uq_tool_progress_events_run_cursor"),
        sa.ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            [f"{schema}.tool_runs.run_id", f"{schema}.tool_runs.workspace_id"],
            name="fk_tool_progress_events_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id", "run_id", "workspace_id"],
            [
                f"{schema}.tool_steps.step_id",
                f"{schema}.tool_steps.run_id",
                f"{schema}.tool_steps.workspace_id",
            ],
            name="fk_tool_progress_events_step",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "step_id", "run_id", "workspace_id"],
            [
                f"{schema}.tool_attempts.attempt_id",
                f"{schema}.tool_attempts.step_id",
                f"{schema}.tool_attempts.run_id",
                f"{schema}.tool_attempts.workspace_id",
            ],
            name="fk_tool_progress_events_attempt",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tool_call_id", "workspace_id", "run_id", "step_id", "attempt_id"],
            [
                f"{schema}.tool_calls.tool_call_id",
                f"{schema}.tool_calls.workspace_id",
                f"{schema}.tool_calls.run_id",
                f"{schema}.tool_calls.step_id",
                f"{schema}.tool_calls.attempt_id",
            ],
            name="fk_tool_progress_events_call",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("cursor >= 1", name="ck_tool_progress_events_cursor"),
        sa.CheckConstraint(
            "event_type IN ('tool.run.created', 'tool.run.state_changed', "
            "'tool.step.state_changed', 'tool.call.confirmation_requested', "
            "'tool.call.confirmation_resolved', 'tool.call.started', "
            "'tool.call.completed', 'tool.call.failed', "
            "'tool.run.cancellation_requested', 'tool.run.cancelled')",
            name="ck_tool_progress_events_type",
        ),
        sa.CheckConstraint(
            "run_state IN ('pending', 'planning', 'running', 'waiting_confirmation', "
            "'waiting_approval', 'cancellation_requested', 'manual_recovery', "
            "'completed', 'failed', 'cancelled', 'timed_out')",
            name="ck_tool_progress_events_run_state",
        ),
        sa.CheckConstraint(
            "step_state IS NULL OR step_state IN ('planned', 'policy_checking', "
            "'waiting_confirmation', 'waiting_approval', 'ready', 'retry_wait', "
            "'running', 'manual_recovery', 'completed', 'failed', 'cancelled', 'timed_out')",
            name="ck_tool_progress_events_step_state",
        ),
        sa.CheckConstraint(
            "(step_id IS NULL AND step_state IS NULL AND attempt_id IS NULL "
            "AND tool_call_id IS NULL) OR "
            "(step_id IS NOT NULL AND step_state IS NOT NULL "
            "AND ((attempt_id IS NULL AND tool_call_id IS NULL) "
            "OR (attempt_id IS NOT NULL AND tool_call_id IS NOT NULL)))",
            name="ck_tool_progress_events_references",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_tool_progress_events_workspace_run_cursor",
        "tool_progress_events",
        ["workspace_id", "run_id", "cursor"],
        schema=schema,
    )


def _create_functions(schema: str) -> None:
    op.execute(sa.text(_safe_result_validation_sql(schema)))
    op.execute(sa.text(_usage_validation_sql(schema)))
    op.execute(sa.text(_progress_validation_sql(schema)))
    op.execute(sa.text(_terminal_fact_validation_sql(schema)))
    op.execute(sa.text(_immutable_fact_sql(schema)))


def _create_triggers(schema: str) -> None:
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER validate_tool_safe_result_insert
            BEFORE INSERT ON "{schema}".tool_safe_results
            FOR EACH ROW EXECUTE FUNCTION "{schema}".validate_tool_safe_result_insert();
            CREATE TRIGGER validate_tool_usage_record_insert
            BEFORE INSERT ON "{schema}".tool_usage_records
            FOR EACH ROW EXECUTE FUNCTION "{schema}".validate_tool_usage_record_insert();
            CREATE TRIGGER validate_tool_progress_event_insert
            BEFORE INSERT ON "{schema}".tool_progress_events
            FOR EACH ROW EXECUTE FUNCTION "{schema}".validate_tool_progress_event_insert();
            CREATE TRIGGER validate_tool_attempt_terminal_facts
            BEFORE UPDATE ON "{schema}".tool_attempts
            FOR EACH ROW EXECUTE FUNCTION "{schema}".validate_tool_terminal_facts();
            CREATE TRIGGER validate_tool_call_terminal_facts
            BEFORE UPDATE ON "{schema}".tool_calls
            FOR EACH ROW EXECUTE FUNCTION "{schema}".validate_tool_terminal_facts();
            """
        )
    )
    for table_name in ("tool_safe_results", "tool_usage_records", "tool_progress_events"):
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER protect_{table_name}
                BEFORE UPDATE OR DELETE ON "{schema}".{table_name}
                FOR EACH ROW EXECUTE FUNCTION "{schema}".protect_tool_operation_fact();
                """
            )
        )


def _drop_triggers(schema: str) -> None:
    for table_name in ("tool_safe_results", "tool_usage_records", "tool_progress_events"):
        op.execute(
            sa.text(f'DROP TRIGGER IF EXISTS protect_{table_name} ON "{schema}".{table_name}')
        )
    op.execute(
        sa.text(
            f"""
            DROP TRIGGER IF EXISTS validate_tool_safe_result_insert
              ON "{schema}".tool_safe_results;
            DROP TRIGGER IF EXISTS validate_tool_usage_record_insert
              ON "{schema}".tool_usage_records;
            DROP TRIGGER IF EXISTS validate_tool_progress_event_insert
              ON "{schema}".tool_progress_events;
            DROP TRIGGER IF EXISTS validate_tool_attempt_terminal_facts
              ON "{schema}".tool_attempts;
            DROP TRIGGER IF EXISTS validate_tool_call_terminal_facts
              ON "{schema}".tool_calls;
            """
        )
    )


def _drop_functions(schema: str) -> None:
    for function_name in (
        "validate_tool_safe_result_insert",
        "validate_tool_usage_record_insert",
        "validate_tool_progress_event_insert",
        "validate_tool_terminal_facts",
        "protect_tool_operation_fact",
    ):
        op.execute(sa.text(f'DROP FUNCTION IF EXISTS "{schema}".{function_name}()'))


def _safe_result_validation_sql(schema: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_safe_result_insert()
    RETURNS trigger AS $$
    DECLARE
      expected_schema_hash text;
      step_result_limit bigint;
      current_call_state text;
    BEGIN
      SELECT d.output_schema_hash, s.max_result_bytes, c.state
        INTO expected_schema_hash, step_result_limit, current_call_state
        FROM "{schema}".tool_calls c
        JOIN "{schema}".tool_steps s
          ON s.step_id = c.step_id AND s.run_id = c.run_id AND s.workspace_id = c.workspace_id
        JOIN "{schema}".agent_tool_definitions d
          ON d.tool_id = c.tool_id AND d.tool_version = c.tool_version
       WHERE c.tool_call_id = NEW.tool_call_id
         AND c.workspace_id = NEW.workspace_id
         AND c.run_id = NEW.run_id
         AND c.step_id = NEW.step_id
         AND c.attempt_id = NEW.attempt_id
       FOR UPDATE OF c, s;
      IF NOT FOUND OR current_call_state <> 'executing' THEN
        RAISE EXCEPTION 'tool safe result requires current executing call';
      END IF;
      IF NEW.output_schema_hash <> expected_schema_hash THEN
        RAISE EXCEPTION 'tool safe result schema hash mismatch';
      END IF;
      IF NEW.status = 'accepted' AND NEW.result_size_bytes > step_result_limit THEN
        RAISE EXCEPTION 'tool safe result exceeds frozen step budget';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _usage_validation_sql(schema: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_usage_record_insert()
    RETURNS trigger AS $$
    DECLARE
      current_call record;
      step_spent bigint;
      run_spent bigint;
    BEGIN
      SELECT c.tool_id, c.tool_version, c.access_mode, c.risk_level,
             s.max_cost_microunits AS step_budget,
             r.max_cost_microunits AS run_budget
        INTO current_call
        FROM "{schema}".tool_calls c
        JOIN "{schema}".tool_steps s
          ON s.step_id = c.step_id AND s.run_id = c.run_id AND s.workspace_id = c.workspace_id
        JOIN "{schema}".tool_runs r
          ON r.run_id = c.run_id AND r.workspace_id = c.workspace_id
       WHERE c.tool_call_id = NEW.tool_call_id
         AND c.workspace_id = NEW.workspace_id
         AND c.run_id = NEW.run_id
         AND c.step_id = NEW.step_id
         AND c.attempt_id = NEW.attempt_id
       FOR UPDATE OF c, s, r;
      IF NOT FOUND OR ROW(NEW.tool_id, NEW.tool_version, NEW.access_mode, NEW.risk_level)
         IS DISTINCT FROM ROW(current_call.tool_id, current_call.tool_version,
                              current_call.access_mode, current_call.risk_level) THEN
        RAISE EXCEPTION 'tool usage identity mismatch';
      END IF;
      SELECT COALESCE(sum(cost_microunits), 0) INTO step_spent
        FROM "{schema}".tool_usage_records WHERE step_id = NEW.step_id;
      SELECT COALESCE(sum(cost_microunits), 0) INTO run_spent
        FROM "{schema}".tool_usage_records WHERE run_id = NEW.run_id;
      IF step_spent + NEW.cost_microunits > current_call.step_budget
         OR run_spent + NEW.cost_microunits > current_call.run_budget THEN
        RAISE EXCEPTION 'tool usage exceeds frozen cost budget';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _progress_validation_sql(schema: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_progress_event_insert()
    RETURNS trigger AS $$
    DECLARE
      expected_cursor bigint;
    BEGIN
      PERFORM 1 FROM "{schema}".tool_runs
       WHERE run_id = NEW.run_id AND workspace_id = NEW.workspace_id
       FOR UPDATE;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'tool progress run does not exist';
      END IF;
      SELECT COALESCE(max(cursor), 0) + 1 INTO expected_cursor
        FROM "{schema}".tool_progress_events WHERE run_id = NEW.run_id;
      IF NEW.cursor <> expected_cursor THEN
        RAISE EXCEPTION 'tool progress cursor must advance exactly once';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _terminal_fact_validation_sql(schema: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_terminal_facts()
    RETURNS trigger AS $$
    DECLARE
      usage_count bigint;
      accepted_result_count bigint;
    BEGIN
      IF TG_TABLE_NAME = 'tool_attempts'
         AND OLD.state NOT IN ('succeeded', 'failed', 'cancelled', 'timed_out',
                               'ignored_late_result')
         AND NEW.state IN ('succeeded', 'failed', 'cancelled', 'timed_out',
                           'ignored_late_result') THEN
        SELECT count(*) INTO usage_count
          FROM "{schema}".tool_usage_records WHERE attempt_id = NEW.attempt_id;
        IF usage_count <> 1 THEN
          RAISE EXCEPTION 'terminal tool attempt requires exactly one usage record';
        END IF;
      ELSIF TG_TABLE_NAME = 'tool_calls'
         AND OLD.state NOT IN ('succeeded', 'failed', 'cancelled', 'timed_out')
         AND NEW.state IN ('succeeded', 'failed', 'cancelled', 'timed_out') THEN
        SELECT count(*) INTO usage_count
          FROM "{schema}".tool_usage_records WHERE tool_call_id = NEW.tool_call_id;
        IF usage_count <> 1 THEN
          RAISE EXCEPTION 'terminal tool call requires exactly one usage record';
        END IF;
        IF NEW.state = 'succeeded' THEN
          SELECT count(*) INTO accepted_result_count
            FROM "{schema}".tool_safe_results
           WHERE tool_call_id = NEW.tool_call_id
             AND status = 'accepted'
             AND eligible_for_model_context = true;
          IF accepted_result_count <> 1 THEN
            RAISE EXCEPTION 'successful tool call requires accepted safe result';
          END IF;
        END IF;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _immutable_fact_sql(schema: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".protect_tool_operation_fact()
    RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'DELETE'
         AND current_setting('ai_platform.lifecycle_purge', true) = 'on' THEN
        RETURN OLD;
      END IF;
      RAISE EXCEPTION 'tool operation facts are immutable';
    END;
    $$ LANGUAGE plpgsql;
    """
