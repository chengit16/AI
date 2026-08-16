"""建立 P4-03 工具 Run、Step、Attempt、ToolCall 与租约状态事实。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260816_0054"
down_revision: str | None = "20260816_0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """创建四类状态表，并在数据库层阻断越序、失租和终态覆盖。"""

    schema = _schema()
    _create_runs(schema)
    _create_steps(schema)
    _create_attempts(schema)
    _create_calls(schema)
    _create_transition_functions(schema)
    _create_transition_triggers(schema)


def downgrade() -> None:
    """仅允许空任务事实降级，避免静默删除运行和历史 Attempt。"""

    schema = _schema()
    count = op.get_bind().scalar(sa.text(f'SELECT count(*) FROM "{schema}".tool_runs'))
    if int(count or 0) > 0:
        raise RuntimeError("存在工具任务事实, 拒绝破坏性降级")
    _drop_transition_triggers(schema)
    for table_name in ("tool_calls", "tool_attempts", "tool_steps", "tool_runs"):
        op.drop_table(table_name, schema=schema)
    for function_name in (
        "validate_tool_call_transition",
        "validate_tool_attempt_transition",
        "validate_tool_attempt_insert",
        "validate_tool_step_current_attempt",
        "validate_tool_step_transition",
        "validate_tool_run_transition",
    ):
        op.execute(sa.text(f'DROP FUNCTION "{schema}".{function_name}()'))


def _create_runs(schema: str) -> None:
    op.create_table(
        "tool_runs",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column("max_attempts_per_step", sa.Integer(), nullable=False),
        sa.Column("max_execution_seconds", sa.Integer(), nullable=False),
        sa.Column("max_cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("traceparent", sa.String(55), nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("run_id", "workspace_id", name="uq_tool_runs_id_workspace"),
        sa.UniqueConstraint(
            "workspace_id",
            "requested_by_actor_id",
            "idempotency_key",
            name="uq_tool_runs_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_tool_runs_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_tool_runs_requester",
        ),
        sa.ForeignKeyConstraint(
            ["service_id", "workspace_id"],
            [f"{schema}.services.service_id", f"{schema}.services.workspace_id"],
            name="fk_tool_runs_service",
        ),
        sa.ForeignKeyConstraint(
            ["agent_release_id", "workspace_id"],
            [
                f"{schema}.agent_releases.release_id",
                f"{schema}.agent_releases.workspace_id",
            ],
            name="fk_tool_runs_agent_release",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'planning', 'running', 'waiting_confirmation', "
            "'waiting_approval', 'cancellation_requested', 'completed', 'failed', "
            "'cancelled', 'timed_out')",
            name="ck_tool_runs_state",
        ),
        sa.CheckConstraint(
            "max_steps BETWEEN 1 AND 50 AND max_attempts_per_step BETWEEN 1 AND 5 "
            "AND max_execution_seconds BETWEEN 1 AND 1800 AND max_cost_microunits >= 0",
            name="ck_tool_runs_budget",
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$' "
            "AND request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_tool_runs_idempotency",
        ),
        sa.CheckConstraint("trace_id ~ '^[0-9a-f]{32}$'", name="ck_tool_runs_trace_id"),
        sa.CheckConstraint("deadline_at > created_at", name="ck_tool_runs_deadline"),
        sa.CheckConstraint(
            "(state IN ('completed', 'failed', 'cancelled', 'timed_out') "
            "AND completed_at IS NOT NULL) OR "
            "(state NOT IN ('completed', 'failed', 'cancelled', 'timed_out') "
            "AND completed_at IS NULL)",
            name="ck_tool_runs_completion",
        ),
        sa.CheckConstraint(
            "(state IN ('cancellation_requested', 'cancelled') "
            "AND cancel_requested_at IS NOT NULL) OR "
            "(state NOT IN ('cancellation_requested', 'cancelled'))",
            name="ck_tool_runs_cancellation",
        ),
        sa.CheckConstraint("version >= 1", name="ck_tool_runs_version"),
        schema=schema,
    )
    op.create_index(
        "ix_tool_runs_workspace_time",
        "tool_runs",
        ["workspace_id", "created_at"],
        schema=schema,
    )
    op.create_index(
        "ix_tool_runs_claim",
        "tool_runs",
        ["state", "deadline_at"],
        schema=schema,
    )


def _create_steps(schema: str) -> None:
    op.create_table(
        "tool_steps",
        sa.Column("step_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_version", sa.Integer(), nullable=False),
        sa.Column("canonical_arguments_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("current_attempt_no", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("run_id", "sequence_no", name="uq_tool_steps_run_sequence"),
        sa.UniqueConstraint(
            "step_id",
            "run_id",
            "workspace_id",
            name="uq_tool_steps_run_identity",
        ),
        sa.UniqueConstraint(
            "step_id",
            "run_id",
            "workspace_id",
            "tool_id",
            "tool_version",
            "canonical_arguments_hash",
            name="uq_tool_steps_call_binding",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            [f"{schema}.tool_runs.run_id", f"{schema}.tool_runs.workspace_id"],
            name="fk_tool_steps_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tool_id", "tool_version"],
            [
                f"{schema}.agent_tool_definitions.tool_id",
                f"{schema}.agent_tool_definitions.tool_version",
            ],
            name="fk_tool_steps_definition",
        ),
        sa.CheckConstraint("sequence_no >= 1", name="ck_tool_steps_sequence"),
        sa.CheckConstraint("tool_version >= 1", name="ck_tool_steps_tool_version"),
        sa.CheckConstraint(
            "canonical_arguments_hash ~ '^[0-9a-f]{64}$'",
            name="ck_tool_steps_arguments_hash",
        ),
        sa.CheckConstraint(
            "state IN ('planned', 'policy_checking', 'waiting_confirmation', "
            "'waiting_approval', 'ready', 'running', 'completed', 'failed', "
            "'cancelled', 'timed_out')",
            name="ck_tool_steps_state",
        ),
        sa.CheckConstraint(
            "current_attempt_no IS NULL OR current_attempt_no BETWEEN 1 AND 5",
            name="ck_tool_steps_current_attempt",
        ),
        sa.CheckConstraint(
            "state <> 'running' OR current_attempt_no IS NOT NULL",
            name="ck_tool_steps_running_attempt",
        ),
        sa.CheckConstraint("version >= 1", name="ck_tool_steps_version"),
        schema=schema,
    )
    op.create_index(
        "ix_tool_steps_claim",
        "tool_steps",
        ["state", "created_at"],
        schema=schema,
    )


def _create_attempts(schema: str) -> None:
    op.create_table(
        "tool_attempts",
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("lease_generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("worker_id", sa.String(120), nullable=False),
        sa.Column("lease_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.UniqueConstraint("step_id", "attempt_no", name="uq_tool_attempts_step_attempt"),
        sa.UniqueConstraint(
            "attempt_id",
            "step_id",
            "run_id",
            "workspace_id",
            name="uq_tool_attempts_identity",
        ),
        sa.ForeignKeyConstraint(
            ["step_id", "run_id", "workspace_id"],
            [
                f"{schema}.tool_steps.step_id",
                f"{schema}.tool_steps.run_id",
                f"{schema}.tool_steps.workspace_id",
            ],
            name="fk_tool_attempts_step",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("attempt_no BETWEEN 1 AND 5", name="ck_tool_attempts_number"),
        sa.CheckConstraint("lease_generation >= 1", name="ck_tool_attempts_generation"),
        sa.CheckConstraint(
            "char_length(btrim(worker_id)) BETWEEN 1 AND 120",
            name="ck_tool_attempts_worker",
        ),
        sa.CheckConstraint(
            "lease_expires_at > lease_started_at AND started_at >= lease_started_at",
            name="ck_tool_attempts_lease",
        ),
        sa.CheckConstraint(
            "state IN ('leased', 'executing', 'succeeded', 'failed', 'cancelled', "
            "'timed_out', 'ignored_late_result')",
            name="ck_tool_attempts_state",
        ),
        sa.CheckConstraint(
            "(state IN ('leased', 'executing') AND completed_at IS NULL) OR "
            "(state NOT IN ('leased', 'executing') AND completed_at IS NOT NULL)",
            name="ck_tool_attempts_completion",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_tool_attempts_lease",
        "tool_attempts",
        ["state", "lease_expires_at"],
        schema=schema,
    )


def _create_calls(schema: str) -> None:
    op.create_table(
        "tool_calls",
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_version", sa.Integer(), nullable=False),
        sa.Column("canonical_arguments_hash", sa.String(64), nullable=False),
        sa.Column("access_mode", sa.String(16), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("credential_ref", sa.String(69), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.UniqueConstraint("attempt_id", name="uq_tool_calls_attempt"),
        sa.ForeignKeyConstraint(
            ["attempt_id", "step_id", "run_id", "workspace_id"],
            [
                f"{schema}.tool_attempts.attempt_id",
                f"{schema}.tool_attempts.step_id",
                f"{schema}.tool_attempts.run_id",
                f"{schema}.tool_attempts.workspace_id",
            ],
            name="fk_tool_calls_attempt",
            ondelete="CASCADE",
        ),
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
            name="fk_tool_calls_step_binding",
        ),
        sa.CheckConstraint("tool_version >= 1", name="ck_tool_calls_tool_version"),
        sa.CheckConstraint(
            "canonical_arguments_hash ~ '^[0-9a-f]{64}$'",
            name="ck_tool_calls_arguments_hash",
        ),
        sa.CheckConstraint(
            "access_mode IN ('read', 'write')",
            name="ck_tool_calls_access_mode",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_tool_calls_risk",
        ),
        sa.CheckConstraint(
            "credential_ref IS NULL OR credential_ref ~ '^cred_[a-z0-9]{16,64}$'",
            name="ck_tool_calls_credential_ref",
        ),
        sa.CheckConstraint(
            "state IN ('proposed', 'authorized', 'confirmed', 'executing', "
            "'succeeded', 'failed', 'cancelled', 'timed_out')",
            name="ck_tool_calls_state",
        ),
        sa.CheckConstraint(
            "(state IN ('succeeded', 'failed', 'cancelled', 'timed_out') "
            "AND completed_at IS NOT NULL) OR "
            "(state NOT IN ('succeeded', 'failed', 'cancelled', 'timed_out') "
            "AND completed_at IS NULL)",
            name="ck_tool_calls_completion",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_tool_calls_workspace_time",
        "tool_calls",
        ["workspace_id", "created_at"],
        schema=schema,
    )


def _create_transition_functions(schema: str) -> None:
    """触发器重复执行状态图和身份约束，阻断绕过应用层的原始 SQL。"""

    op.execute(sa.text(_run_transition_sql(schema)))
    op.execute(sa.text(_step_transition_sql(schema)))
    op.execute(sa.text(_step_current_attempt_sql(schema)))
    op.execute(sa.text(_attempt_insert_sql(schema)))
    op.execute(sa.text(_attempt_transition_sql(schema)))
    op.execute(sa.text(_call_transition_sql(schema)))


def _run_transition_sql(schema: str) -> str:
    return f"""
    CREATE FUNCTION "{schema}".validate_tool_run_transition() RETURNS trigger AS $$
    BEGIN
      IF ROW(NEW.workspace_id, NEW.requested_by_actor_id, NEW.requested_by_account_id,
             NEW.service_id, NEW.agent_release_id, NEW.max_steps, NEW.max_attempts_per_step,
             NEW.max_execution_seconds, NEW.max_cost_microunits, NEW.idempotency_key,
             NEW.request_hash, NEW.trace_id, NEW.traceparent, NEW.deadline_at, NEW.created_at)
         IS DISTINCT FROM
         ROW(OLD.workspace_id, OLD.requested_by_actor_id, OLD.requested_by_account_id,
             OLD.service_id, OLD.agent_release_id, OLD.max_steps, OLD.max_attempts_per_step,
             OLD.max_execution_seconds, OLD.max_cost_microunits, OLD.idempotency_key,
             OLD.request_hash, OLD.trace_id, OLD.traceparent, OLD.deadline_at, OLD.created_at)
      THEN RAISE EXCEPTION 'tool run identity is immutable'; END IF;
      IF NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'tool run version must advance exactly once';
      END IF;
      IF OLD.state IN ('completed', 'failed', 'cancelled', 'timed_out') THEN
        RAISE EXCEPTION 'tool run terminal state is immutable';
      END IF;
      IF NOT ((OLD.state = 'pending' AND NEW.state IN ('planning', 'cancellation_requested'))
          OR (OLD.state = 'planning' AND NEW.state = 'running')
          OR (OLD.state = 'running' AND NEW.state IN
              ('waiting_confirmation', 'waiting_approval', 'completed', 'failed',
               'cancellation_requested', 'timed_out'))
          OR (OLD.state = 'waiting_confirmation' AND NEW.state = 'running')
          OR (OLD.state = 'waiting_approval' AND NEW.state = 'running')
          OR (OLD.state = 'cancellation_requested' AND NEW.state = 'cancelled')) THEN
        RAISE EXCEPTION 'illegal tool run transition: % -> %', OLD.state, NEW.state;
      END IF;
      IF OLD.state = 'planning' AND NEW.state = 'running'
         AND NOT EXISTS (SELECT 1 FROM "{schema}".tool_steps WHERE run_id = OLD.run_id) THEN
        RAISE EXCEPTION 'tool run requires at least one step';
      END IF;
      IF NEW.state = 'completed'
         AND (NOT EXISTS (SELECT 1 FROM "{schema}".tool_steps WHERE run_id = OLD.run_id)
              OR EXISTS (SELECT 1 FROM "{schema}".tool_steps
                         WHERE run_id = OLD.run_id AND state <> 'completed')) THEN
        RAISE EXCEPTION 'tool run cannot complete with unfinished steps';
      END IF;
      IF NEW.state = 'cancelled' AND EXISTS (
          SELECT 1 FROM "{schema}".tool_attempts
          WHERE run_id = OLD.run_id AND state IN ('leased', 'executing')) THEN
        RAISE EXCEPTION 'tool run cancellation still has active attempt';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _step_transition_sql(schema: str) -> str:
    return f"""
    CREATE FUNCTION "{schema}".validate_tool_step_transition() RETURNS trigger AS $$
    DECLARE parent_state varchar(32);
    BEGIN
      IF ROW(NEW.run_id, NEW.workspace_id, NEW.sequence_no, NEW.tool_id,
             NEW.tool_version, NEW.canonical_arguments_hash, NEW.created_at)
         IS DISTINCT FROM
         ROW(OLD.run_id, OLD.workspace_id, OLD.sequence_no, OLD.tool_id,
             OLD.tool_version, OLD.canonical_arguments_hash, OLD.created_at) THEN
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
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _step_current_attempt_sql(schema: str) -> str:
    return f"""
    CREATE FUNCTION "{schema}".validate_tool_step_current_attempt() RETURNS trigger AS $$
    BEGIN
      IF NEW.current_attempt_no IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM "{schema}".tool_attempts
          WHERE step_id = NEW.step_id AND attempt_no = NEW.current_attempt_no) THEN
        RAISE EXCEPTION 'tool step current attempt does not exist';
      END IF;
      RETURN NULL;
    END;
    $$ LANGUAGE plpgsql;
    """


def _attempt_insert_sql(schema: str) -> str:
    return f"""
    CREATE FUNCTION "{schema}".validate_tool_attempt_insert() RETURNS trigger AS $$
    BEGIN
      IF NOT EXISTS (
          SELECT 1 FROM "{schema}".tool_steps AS steps
          JOIN "{schema}".tool_runs AS runs ON runs.run_id = steps.run_id
          WHERE steps.step_id = NEW.step_id AND steps.run_id = NEW.run_id
            AND steps.workspace_id = NEW.workspace_id AND steps.state = 'running'
            AND steps.current_attempt_no = NEW.attempt_no AND runs.state = 'running'
            AND runs.cancel_requested_at IS NULL
            AND NEW.attempt_no <= runs.max_attempts_per_step) THEN
        RAISE EXCEPTION 'tool attempt is not the current claimed generation';
      END IF;
      IF NEW.lease_generation <> NEW.attempt_no THEN
        RAISE EXCEPTION 'tool attempt lease generation mismatch';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _attempt_transition_sql(schema: str) -> str:
    return f"""
    CREATE FUNCTION "{schema}".validate_tool_attempt_transition() RETURNS trigger AS $$
    BEGIN
      IF ROW(NEW.run_id, NEW.step_id, NEW.workspace_id, NEW.attempt_no,
             NEW.lease_generation, NEW.worker_id, NEW.lease_started_at,
             NEW.lease_expires_at)
         IS DISTINCT FROM
         ROW(OLD.run_id, OLD.step_id, OLD.workspace_id, OLD.attempt_no,
             OLD.lease_generation, OLD.worker_id, OLD.lease_started_at,
             OLD.lease_expires_at) THEN
        RAISE EXCEPTION 'tool attempt lease identity is immutable';
      END IF;
      IF OLD.state IN ('succeeded', 'failed', 'cancelled', 'timed_out',
                       'ignored_late_result') THEN
        RAISE EXCEPTION 'tool attempt terminal state is immutable';
      END IF;
      IF NOT ((OLD.state = 'leased' AND NEW.state IN ('executing', 'cancelled', 'timed_out'))
          OR (OLD.state = 'executing' AND NEW.state IN
              ('succeeded', 'failed', 'cancelled', 'timed_out',
               'ignored_late_result'))) THEN
        RAISE EXCEPTION 'illegal tool attempt transition: % -> %', OLD.state, NEW.state;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _call_transition_sql(schema: str) -> str:
    return f"""
    CREATE FUNCTION "{schema}".validate_tool_call_transition() RETURNS trigger AS $$
    BEGIN
      IF ROW(NEW.run_id, NEW.step_id, NEW.attempt_id, NEW.workspace_id, NEW.tool_id,
             NEW.tool_version, NEW.canonical_arguments_hash, NEW.access_mode,
             NEW.risk_level, NEW.credential_ref, NEW.created_at)
         IS DISTINCT FROM
         ROW(OLD.run_id, OLD.step_id, OLD.attempt_id, OLD.workspace_id, OLD.tool_id,
             OLD.tool_version, OLD.canonical_arguments_hash, OLD.access_mode,
             OLD.risk_level, OLD.credential_ref, OLD.created_at) THEN
        RAISE EXCEPTION 'tool call binding is immutable';
      END IF;
      IF OLD.state IN ('succeeded', 'failed', 'cancelled', 'timed_out') THEN
        RAISE EXCEPTION 'tool call terminal state is immutable';
      END IF;
      IF NOT ((OLD.state = 'proposed' AND NEW.state IN ('authorized', 'cancelled', 'timed_out'))
          OR (OLD.state = 'authorized' AND NEW.state IN ('confirmed', 'cancelled', 'timed_out'))
          OR (OLD.state = 'confirmed' AND NEW.state IN ('executing', 'cancelled', 'timed_out'))
          OR (OLD.state = 'executing' AND NEW.state IN
              ('succeeded', 'failed', 'cancelled', 'timed_out'))) THEN
        RAISE EXCEPTION 'illegal tool call transition: % -> %', OLD.state, NEW.state;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _create_transition_triggers(schema: str) -> None:
    statements = (
        f'CREATE TRIGGER trg_tool_runs_transition BEFORE UPDATE ON "{schema}".tool_runs '
        f'FOR EACH ROW EXECUTE FUNCTION "{schema}".validate_tool_run_transition()',
        f'CREATE TRIGGER trg_tool_steps_transition BEFORE UPDATE ON "{schema}".tool_steps '
        f'FOR EACH ROW EXECUTE FUNCTION "{schema}".validate_tool_step_transition()',
        f"CREATE CONSTRAINT TRIGGER trg_tool_steps_current_attempt AFTER INSERT OR UPDATE "
        f'ON "{schema}".tool_steps DEFERRABLE INITIALLY DEFERRED FOR EACH ROW '
        f'EXECUTE FUNCTION "{schema}".validate_tool_step_current_attempt()',
        f'CREATE TRIGGER trg_tool_attempts_insert BEFORE INSERT ON "{schema}".tool_attempts '
        f'FOR EACH ROW EXECUTE FUNCTION "{schema}".validate_tool_attempt_insert()',
        f'CREATE TRIGGER trg_tool_attempts_transition BEFORE UPDATE ON "{schema}".tool_attempts '
        f'FOR EACH ROW EXECUTE FUNCTION "{schema}".validate_tool_attempt_transition()',
        f'CREATE TRIGGER trg_tool_calls_transition BEFORE UPDATE ON "{schema}".tool_calls '
        f'FOR EACH ROW EXECUTE FUNCTION "{schema}".validate_tool_call_transition()',
    )
    for statement in statements:
        op.execute(sa.text(statement))


def _drop_transition_triggers(schema: str) -> None:
    statements = (
        f'DROP TRIGGER trg_tool_calls_transition ON "{schema}".tool_calls',
        f'DROP TRIGGER trg_tool_attempts_transition ON "{schema}".tool_attempts',
        f'DROP TRIGGER trg_tool_attempts_insert ON "{schema}".tool_attempts',
        f'DROP TRIGGER trg_tool_steps_current_attempt ON "{schema}".tool_steps',
        f'DROP TRIGGER trg_tool_steps_transition ON "{schema}".tool_steps',
        f'DROP TRIGGER trg_tool_runs_transition ON "{schema}".tool_runs',
    )
    for statement in statements:
        op.execute(sa.text(statement))
