"""建立 P4-09 Worker 租约续期、安全重试、取消观察和人工恢复代际。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260816_0059"
down_revision: str | None = "20260816_0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """扩展既有状态事实，并用数据库 Trigger 约束续租和恢复代际。"""

    schema = _schema()
    _add_columns(schema)
    _replace_constraints(schema)
    _replace_transition_functions(schema, recovery_enabled=True)


def downgrade() -> None:
    """只有从未产生重试、续租、取消观察或恢复事实时才允许回退。"""

    schema = _schema()
    _reject_unsafe_downgrade(schema)
    _replace_transition_functions(schema, recovery_enabled=False)
    _restore_constraints(schema)
    _drop_columns(schema)


def _add_columns(schema: str) -> None:
    """使用可回填的 Expand 顺序兼容非空 P4-08 数据库。"""

    run_columns = (
        sa.Column("recovery_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recovery_reason_code", sa.String(128), nullable=True),
        sa.Column("recovery_required_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_recovered_by_actor_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("last_recovered_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in run_columns:
        op.add_column("tool_runs", column, schema=schema)

    step_columns = (
        sa.Column("recovery_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "next_attempt_trigger",
            sa.String(32),
            nullable=False,
            server_default="automatic",
        ),
    )
    for column in step_columns:
        op.add_column("tool_steps", column, schema=schema)
    op.execute(sa.text(f'UPDATE "{schema}".tool_steps SET available_at = updated_at'))
    op.alter_column("tool_steps", "available_at", nullable=False, schema=schema)

    attempt_columns = (
        sa.Column("recovery_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trigger", sa.String(32), nullable=False, server_default="automatic"),
        sa.Column("cancel_observed_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in attempt_columns:
        op.add_column("tool_attempts", column, schema=schema)
    for table_name, column_name in (
        ("tool_runs", "recovery_generation"),
        ("tool_steps", "recovery_generation"),
        ("tool_steps", "next_attempt_trigger"),
        ("tool_attempts", "recovery_generation"),
        ("tool_attempts", "trigger"),
    ):
        op.alter_column(table_name, column_name, server_default=None, schema=schema)


def _replace_constraints(schema: str) -> None:
    op.drop_constraint("ck_tool_runs_state", "tool_runs", type_="check", schema=schema)
    op.create_check_constraint(
        "ck_tool_runs_state",
        "tool_runs",
        "state IN ('pending', 'planning', 'running', 'waiting_confirmation', "
        "'waiting_approval', 'cancellation_requested', 'manual_recovery', "
        "'completed', 'failed', 'cancelled', 'timed_out')",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_tool_runs_recovery",
        "tool_runs",
        "recovery_generation BETWEEN 0 AND 3 AND "
        "((state = 'manual_recovery' AND recovery_reason_code IS NOT NULL "
        "AND recovery_required_at IS NOT NULL) OR "
        "(state <> 'manual_recovery' AND recovery_reason_code IS NULL "
        "AND recovery_required_at IS NULL)) AND "
        "((recovery_generation = 0 AND last_recovered_by_actor_id IS NULL "
        "AND last_recovered_at IS NULL) OR "
        "(recovery_generation > 0 AND last_recovered_by_actor_id IS NOT NULL "
        "AND last_recovered_at IS NOT NULL))",
        schema=schema,
    )

    op.drop_constraint("ck_tool_steps_state", "tool_steps", type_="check", schema=schema)
    op.create_check_constraint(
        "ck_tool_steps_state",
        "tool_steps",
        "state IN ('planned', 'policy_checking', 'waiting_confirmation', "
        "'waiting_approval', 'ready', 'retry_wait', 'running', 'manual_recovery', "
        "'completed', 'failed', 'cancelled', 'timed_out')",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_tool_steps_recovery_generation",
        "tool_steps",
        "recovery_generation BETWEEN 0 AND 3",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_tool_steps_attempt_trigger",
        "tool_steps",
        "next_attempt_trigger IN "
        "('automatic', 'automatic_retry', 'lease_recovery', 'manual_recovery')",
        schema=schema,
    )

    op.drop_constraint(
        "uq_tool_attempts_step_attempt",
        "tool_attempts",
        type_="unique",
        schema=schema,
    )
    op.create_unique_constraint(
        "uq_tool_attempts_step_generation_attempt",
        "tool_attempts",
        ["step_id", "recovery_generation", "attempt_no"],
        schema=schema,
    )
    op.drop_constraint("ck_tool_attempts_generation", "tool_attempts", type_="check", schema=schema)
    op.create_check_constraint(
        "ck_tool_attempts_generation",
        "tool_attempts",
        "recovery_generation BETWEEN 0 AND 3 AND "
        "lease_generation = recovery_generation * 10 + attempt_no",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_tool_attempts_trigger",
        "tool_attempts",
        "trigger IN ('automatic', 'automatic_retry', 'lease_recovery', 'manual_recovery')",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_tool_attempts_cancel_observed",
        "tool_attempts",
        "cancel_observed_at IS NULL OR cancel_observed_at >= lease_started_at",
        schema=schema,
    )


def _restore_constraints(schema: str) -> None:
    op.drop_constraint(
        "ck_tool_attempts_cancel_observed", "tool_attempts", type_="check", schema=schema
    )
    op.drop_constraint("ck_tool_attempts_trigger", "tool_attempts", type_="check", schema=schema)
    op.drop_constraint("ck_tool_attempts_generation", "tool_attempts", type_="check", schema=schema)
    op.create_check_constraint(
        "ck_tool_attempts_generation",
        "tool_attempts",
        "lease_generation >= 1",
        schema=schema,
    )
    op.drop_constraint(
        "uq_tool_attempts_step_generation_attempt",
        "tool_attempts",
        type_="unique",
        schema=schema,
    )
    op.create_unique_constraint(
        "uq_tool_attempts_step_attempt",
        "tool_attempts",
        ["step_id", "attempt_no"],
        schema=schema,
    )
    op.drop_constraint("ck_tool_steps_attempt_trigger", "tool_steps", type_="check", schema=schema)
    op.drop_constraint(
        "ck_tool_steps_recovery_generation", "tool_steps", type_="check", schema=schema
    )
    op.drop_constraint("ck_tool_steps_state", "tool_steps", type_="check", schema=schema)
    op.create_check_constraint(
        "ck_tool_steps_state",
        "tool_steps",
        "state IN ('planned', 'policy_checking', 'waiting_confirmation', "
        "'waiting_approval', 'ready', 'running', 'completed', 'failed', "
        "'cancelled', 'timed_out')",
        schema=schema,
    )
    op.drop_constraint("ck_tool_runs_recovery", "tool_runs", type_="check", schema=schema)
    op.drop_constraint("ck_tool_runs_state", "tool_runs", type_="check", schema=schema)
    op.create_check_constraint(
        "ck_tool_runs_state",
        "tool_runs",
        "state IN ('pending', 'planning', 'running', 'waiting_confirmation', "
        "'waiting_approval', 'cancellation_requested', 'completed', 'failed', "
        "'cancelled', 'timed_out')",
        schema=schema,
    )


def _drop_columns(schema: str) -> None:
    for column_name in ("cancel_observed_at", "trigger", "recovery_generation"):
        op.drop_column("tool_attempts", column_name, schema=schema)
    for column_name in ("next_attempt_trigger", "available_at", "recovery_generation"):
        op.drop_column("tool_steps", column_name, schema=schema)
    for column_name in (
        "last_recovered_at",
        "last_recovered_by_actor_id",
        "recovery_required_at",
        "recovery_reason_code",
        "recovery_generation",
    ):
        op.drop_column("tool_runs", column_name, schema=schema)


def _reject_unsafe_downgrade(schema: str) -> None:
    count = op.get_bind().scalar(
        sa.text(
            f"""
            SELECT
              (SELECT count(*) FROM "{schema}".tool_runs
                WHERE recovery_generation > 0 OR state = 'manual_recovery'
                   OR recovery_reason_code IS NOT NULL OR recovery_required_at IS NOT NULL)
              + (SELECT count(*) FROM "{schema}".tool_steps
                WHERE recovery_generation > 0 OR state IN ('retry_wait', 'manual_recovery')
                   OR next_attempt_trigger <> 'automatic')
              + (SELECT count(*) FROM "{schema}".tool_attempts
                WHERE recovery_generation > 0 OR trigger <> 'automatic'
                   OR cancel_observed_at IS NOT NULL)
            """
        )
    )
    if int(count or 0) > 0:
        raise RuntimeError("存在工具重试、续租观察或恢复事实, 拒绝破坏性降级")


def _replace_transition_functions(schema: str, *, recovery_enabled: bool) -> None:
    if recovery_enabled:
        statements = (
            _run_transition_sql(schema),
            _step_transition_sql(schema),
            _step_current_attempt_sql(schema),
            _attempt_insert_sql(schema),
            _attempt_transition_sql(schema),
        )
    else:
        statements = (
            _legacy_run_transition_sql(schema),
            _legacy_step_transition_sql(schema),
            _legacy_step_current_attempt_sql(schema),
            _legacy_attempt_insert_sql(schema),
            _legacy_attempt_transition_sql(schema),
        )
    for statement in statements:
        op.execute(sa.text(statement))


def _run_transition_sql(schema: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_run_transition() RETURNS trigger AS $$
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
      IF NEW.recovery_generation <> OLD.recovery_generation THEN
        IF current_setting('ai_platform.tool_recovery_write', true) IS DISTINCT FROM 'on'
           OR OLD.state <> 'manual_recovery' OR NEW.state <> 'running'
           OR NEW.recovery_generation <> OLD.recovery_generation + 1
           OR NEW.recovery_generation > 3
           OR NEW.last_recovered_by_actor_id IS NULL OR NEW.last_recovered_at IS NULL THEN
          RAISE EXCEPTION 'tool run recovery generation is controlled';
        END IF;
      ELSIF ROW(NEW.last_recovered_by_actor_id, NEW.last_recovered_at)
         IS DISTINCT FROM ROW(OLD.last_recovered_by_actor_id, OLD.last_recovered_at) THEN
        RAISE EXCEPTION 'tool run recovery actor is immutable outside recovery';
      END IF;
      IF NOT ((OLD.state = 'pending' AND NEW.state IN ('planning', 'cancellation_requested'))
          OR (OLD.state = 'planning' AND NEW.state = 'running')
          OR (OLD.state = 'running' AND NEW.state IN
              ('waiting_confirmation', 'waiting_approval', 'completed', 'failed',
               'cancellation_requested', 'manual_recovery', 'timed_out'))
          OR (OLD.state = 'waiting_confirmation' AND NEW.state = 'running')
          OR (OLD.state = 'waiting_approval' AND NEW.state = 'running')
          OR (OLD.state = 'manual_recovery' AND NEW.state IN
              ('running', 'completed', 'cancellation_requested', 'timed_out'))
          OR (OLD.state = 'cancellation_requested' AND NEW.state = 'cancelled')) THEN
        RAISE EXCEPTION 'illegal tool run transition: % -> %', OLD.state, NEW.state;
      END IF;
      IF NEW.state IN ('running', 'manual_recovery') AND NEW.updated_at >= NEW.deadline_at THEN
        RAISE EXCEPTION 'tool run cannot remain active after deadline';
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
      IF NEW.state IN ('waiting_confirmation', 'waiting_approval') AND NOT EXISTS (
          SELECT 1 FROM "{schema}".tool_steps step
          WHERE step.run_id = OLD.run_id AND step.state = NEW.state) THEN
        RAISE EXCEPTION 'tool run waiting state requires matching step';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _step_transition_sql(schema: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_step_transition() RETURNS trigger AS $$
    DECLARE parent_state varchar(32);
    BEGIN
      IF ROW(NEW.run_id, NEW.workspace_id, NEW.sequence_no, NEW.tool_id,
             NEW.tool_version, NEW.canonical_arguments_hash, NEW.created_at,
             NEW.timeout_seconds, NEW.max_attempts, NEW.max_result_bytes, NEW.max_cost_microunits)
         IS DISTINCT FROM
         ROW(OLD.run_id, OLD.workspace_id, OLD.sequence_no, OLD.tool_id,
             OLD.tool_version, OLD.canonical_arguments_hash, OLD.created_at,
             OLD.timeout_seconds, OLD.max_attempts, OLD.max_result_bytes, OLD.max_cost_microunits)
      THEN RAISE EXCEPTION 'tool step identity is immutable'; END IF;
      IF NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'tool step version must advance exactly once';
      END IF;
      IF OLD.state IN ('completed', 'failed', 'cancelled', 'timed_out') THEN
        RAISE EXCEPTION 'tool step terminal state is immutable';
      END IF;
      SELECT state INTO parent_state FROM "{schema}".tool_runs WHERE run_id = OLD.run_id;
      IF NEW.state = 'cancelled' AND parent_state IN ('cancellation_requested', 'cancelled') THEN
        NULL;
      ELSIF NOT ((OLD.state = 'planned' AND NEW.state IN
              ('policy_checking', 'cancelled', 'timed_out'))
          OR (OLD.state = 'policy_checking' AND NEW.state IN
              ('ready', 'waiting_confirmation', 'waiting_approval', 'cancelled', 'timed_out'))
          OR (OLD.state = 'waiting_confirmation' AND NEW.state IN
              ('ready', 'cancelled', 'timed_out'))
          OR (OLD.state = 'waiting_approval' AND NEW.state IN ('ready', 'cancelled', 'timed_out'))
          OR (OLD.state IN ('ready', 'retry_wait') AND NEW.state IN
              ('running', 'cancelled', 'timed_out'))
          OR (OLD.state = 'running' AND NEW.state IN
              ('completed', 'failed', 'retry_wait', 'manual_recovery', 'cancelled', 'timed_out'))
          OR (OLD.state = 'manual_recovery' AND NEW.state IN
              ('ready', 'completed', 'cancelled', 'timed_out'))) THEN
        RAISE EXCEPTION 'illegal tool step transition: % -> %', OLD.state, NEW.state;
      END IF;
      IF NEW.recovery_generation <> OLD.recovery_generation THEN
        IF current_setting('ai_platform.tool_recovery_write', true) IS DISTINCT FROM 'on'
           OR OLD.state <> 'manual_recovery' OR NEW.state <> 'ready'
           OR NEW.recovery_generation <> OLD.recovery_generation + 1
           OR NEW.current_attempt_no IS NOT NULL
           OR NEW.next_attempt_trigger <> 'manual_recovery' THEN
          RAISE EXCEPTION 'tool step recovery generation is controlled';
        END IF;
      ELSIF OLD.current_attempt_no IS DISTINCT FROM NEW.current_attempt_no
         AND NOT (OLD.state IN ('ready', 'retry_wait') AND NEW.state = 'running'
                  AND NEW.current_attempt_no = COALESCE(OLD.current_attempt_no, 0) + 1) THEN
        RAISE EXCEPTION 'tool step current attempt can only advance during claim';
      END IF;
      IF NEW.state = 'retry_wait'
         AND (NEW.available_at <= NEW.updated_at
              OR NEW.next_attempt_trigger NOT IN ('automatic_retry', 'lease_recovery')) THEN
        RAISE EXCEPTION 'tool step retry requires future availability and retry trigger';
      END IF;
      IF NEW.state = 'ready' AND OLD.state <> 'manual_recovery' AND NOT EXISTS (
          SELECT 1 FROM "{schema}".tool_policy_decisions decision
          WHERE decision.step_id = OLD.step_id AND decision.run_id = OLD.run_id
            AND decision.workspace_id = OLD.workspace_id AND decision.tool_id = OLD.tool_id
            AND decision.tool_version = OLD.tool_version
            AND decision.canonical_arguments_hash = OLD.canonical_arguments_hash
            AND decision.evaluated_at = NEW.updated_at) THEN
        RAISE EXCEPTION 'tool step ready requires current allow decision';
      END IF;
      IF NEW.state = 'ready' AND OLD.state <> 'manual_recovery' AND EXISTS (
          SELECT 1 FROM "{schema}".agent_tool_definitions definition
          WHERE definition.tool_id = OLD.tool_id AND definition.tool_version = OLD.tool_version
            AND definition.access_mode = 'write') AND NOT EXISTS (
          SELECT 1 FROM "{schema}".tool_confirmations confirmation
          JOIN "{schema}".tool_policy_decisions decision
            ON decision.step_id = confirmation.step_id AND decision.evaluated_at = NEW.updated_at
          LEFT JOIN "{schema}".tool_confirmation_invalidations invalidation
            ON invalidation.confirmation_id = confirmation.confirmation_id
          WHERE confirmation.step_id = OLD.step_id AND confirmation.run_id = OLD.run_id
            AND confirmation.workspace_id = OLD.workspace_id
            AND confirmation.tool_id = OLD.tool_id AND confirmation.tool_version = OLD.tool_version
            AND confirmation.canonical_arguments_hash = OLD.canonical_arguments_hash
            AND confirmation.state = 'approved' AND invalidation.confirmation_id IS NULL
            AND confirmation.expires_at > NEW.updated_at
            AND decision.decision_id <> confirmation.policy_decision_id
            AND decision.evaluated_at >= confirmation.resolved_at
            AND confirmation.permission_code = decision.permission_code
            AND confirmation.policy_version = decision.policy_version
            AND confirmation.resource_scope_hash = decision.resource_scope_hash
            AND confirmation.field_mask_hash = decision.field_mask_hash) THEN
        RAISE EXCEPTION 'write tool step ready requires current approved confirmation';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _step_current_attempt_sql(schema: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_step_current_attempt() RETURNS trigger AS $$
    BEGIN
      IF NEW.current_attempt_no IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM "{schema}".tool_attempts
          WHERE step_id = NEW.step_id AND recovery_generation = NEW.recovery_generation
            AND attempt_no = NEW.current_attempt_no) THEN
        RAISE EXCEPTION 'tool step current attempt does not exist';
      END IF;
      RETURN NULL;
    END;
    $$ LANGUAGE plpgsql;
    """


def _attempt_insert_sql(schema: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_attempt_insert() RETURNS trigger AS $$
    BEGIN
      IF NOT EXISTS (
          SELECT 1 FROM "{schema}".tool_steps AS steps
          JOIN "{schema}".tool_runs AS runs ON runs.run_id = steps.run_id
          WHERE steps.step_id = NEW.step_id AND steps.run_id = NEW.run_id
            AND steps.workspace_id = NEW.workspace_id AND steps.state = 'running'
            AND steps.recovery_generation = NEW.recovery_generation
            AND runs.recovery_generation = NEW.recovery_generation
            AND steps.current_attempt_no = NEW.attempt_no AND runs.state = 'running'
            AND runs.cancel_requested_at IS NULL AND runs.deadline_at > NEW.lease_started_at
            AND NEW.lease_expires_at <= runs.deadline_at
            AND NEW.lease_expires_at <= NEW.lease_started_at
                + make_interval(secs => steps.timeout_seconds)
            AND NEW.attempt_no <= steps.max_attempts
            AND ((NEW.attempt_no = 1 AND NEW.trigger IN ('automatic', 'manual_recovery'))
              OR (NEW.attempt_no > 1
                  AND NEW.trigger IN ('automatic_retry', 'lease_recovery')))) THEN
        RAISE EXCEPTION 'tool attempt is not the current claimed generation';
      END IF;
      IF NEW.lease_generation <> NEW.recovery_generation * 10 + NEW.attempt_no THEN
        RAISE EXCEPTION 'tool attempt lease generation mismatch';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _attempt_transition_sql(schema: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_attempt_transition() RETURNS trigger AS $$
    DECLARE step_timeout integer; run_deadline timestamptz; run_state varchar(32);
    BEGIN
      IF ROW(NEW.run_id, NEW.step_id, NEW.workspace_id, NEW.recovery_generation,
             NEW.attempt_no, NEW.lease_generation, NEW.trigger, NEW.worker_id,
             NEW.lease_started_at)
         IS DISTINCT FROM
         ROW(OLD.run_id, OLD.step_id, OLD.workspace_id, OLD.recovery_generation,
             OLD.attempt_no, OLD.lease_generation, OLD.trigger, OLD.worker_id,
             OLD.lease_started_at) THEN
        RAISE EXCEPTION 'tool attempt lease identity is immutable';
      END IF;
      SELECT step.timeout_seconds, run.deadline_at, run.state
        INTO step_timeout, run_deadline, run_state
        FROM "{schema}".tool_steps step
        JOIN "{schema}".tool_runs run ON run.run_id = step.run_id
       WHERE step.step_id = OLD.step_id;
      IF NEW.lease_expires_at IS DISTINCT FROM OLD.lease_expires_at THEN
        IF current_setting('ai_platform.tool_lease_write', true) IS DISTINCT FROM 'on'
           OR OLD.state NOT IN ('leased', 'executing') OR NEW.state <> OLD.state
           OR NEW.lease_expires_at <= OLD.lease_expires_at
           OR NEW.lease_expires_at > OLD.lease_started_at + make_interval(secs => step_timeout)
           OR NEW.lease_expires_at > run_deadline
           OR run_state <> 'running' THEN
          RAISE EXCEPTION 'tool attempt lease renewal is controlled';
        END IF;
      END IF;
      IF NEW.cancel_observed_at IS DISTINCT FROM OLD.cancel_observed_at
         AND (current_setting('ai_platform.tool_lease_write', true) IS DISTINCT FROM 'on'
              OR OLD.cancel_observed_at IS NOT NULL OR NEW.cancel_observed_at IS NULL
              OR run_state <> 'cancellation_requested') THEN
        RAISE EXCEPTION 'tool attempt cancellation observation is controlled';
      END IF;
      IF OLD.state IN ('succeeded', 'failed', 'cancelled', 'timed_out',
                       'ignored_late_result') THEN
        RAISE EXCEPTION 'tool attempt terminal state is immutable';
      END IF;
      IF NEW.state = OLD.state THEN
        IF NEW.lease_expires_at IS NOT DISTINCT FROM OLD.lease_expires_at
           AND NEW.cancel_observed_at IS NOT DISTINCT FROM OLD.cancel_observed_at THEN
          RAISE EXCEPTION 'tool attempt update has no controlled change';
        END IF;
      ELSIF NOT ((OLD.state = 'leased' AND NEW.state IN ('executing', 'cancelled', 'timed_out'))
          OR (OLD.state = 'executing' AND NEW.state IN
              ('succeeded', 'failed', 'cancelled', 'timed_out', 'ignored_late_result'))) THEN
        RAISE EXCEPTION 'illegal tool attempt transition: % -> %', OLD.state, NEW.state;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _legacy_run_transition_sql(schema: str) -> str:
    """恢复 P4-08 的 Run 状态图，供受支持的单版本回退继续运行。"""

    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_run_transition() RETURNS trigger AS $$
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
      IF NEW.state IN ('waiting_confirmation', 'waiting_approval') AND NOT EXISTS (
          SELECT 1 FROM "{schema}".tool_steps step
          WHERE step.run_id = OLD.run_id AND step.state = NEW.state) THEN
        RAISE EXCEPTION 'tool run waiting state requires matching step';
      END IF;
      IF OLD.state IN ('waiting_confirmation', 'waiting_approval') AND NEW.state = 'running'
         AND EXISTS (SELECT 1 FROM "{schema}".tool_steps step
                     WHERE step.run_id = OLD.run_id
                       AND step.state IN ('waiting_confirmation', 'waiting_approval')) THEN
        RAISE EXCEPTION 'tool run cannot resume with waiting step';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _legacy_step_transition_sql(schema: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_step_transition() RETURNS trigger AS $$
    DECLARE parent_state varchar(32);
    BEGIN
      IF ROW(NEW.run_id, NEW.workspace_id, NEW.sequence_no, NEW.tool_id,
             NEW.tool_version, NEW.canonical_arguments_hash, NEW.created_at,
             NEW.timeout_seconds, NEW.max_attempts, NEW.max_result_bytes, NEW.max_cost_microunits)
         IS DISTINCT FROM
         ROW(OLD.run_id, OLD.workspace_id, OLD.sequence_no, OLD.tool_id,
             OLD.tool_version, OLD.canonical_arguments_hash, OLD.created_at,
             OLD.timeout_seconds, OLD.max_attempts, OLD.max_result_bytes, OLD.max_cost_microunits)
      THEN RAISE EXCEPTION 'tool step identity is immutable'; END IF;
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
          OR (OLD.state = 'running' AND NEW.state IN ('completed', 'failed', 'timed_out'))) THEN
        RAISE EXCEPTION 'illegal tool step transition: % -> %', OLD.state, NEW.state;
      END IF;
      IF OLD.current_attempt_no IS DISTINCT FROM NEW.current_attempt_no
         AND NOT (OLD.state = 'ready' AND NEW.state = 'running'
                  AND NEW.current_attempt_no = COALESCE(OLD.current_attempt_no, 0) + 1) THEN
        RAISE EXCEPTION 'tool step current attempt can only advance during claim';
      END IF;
      IF NEW.state = 'ready' AND NOT EXISTS (
          SELECT 1 FROM "{schema}".tool_policy_decisions decision
          WHERE decision.step_id = OLD.step_id AND decision.run_id = OLD.run_id
            AND decision.workspace_id = OLD.workspace_id AND decision.tool_id = OLD.tool_id
            AND decision.tool_version = OLD.tool_version
            AND decision.canonical_arguments_hash = OLD.canonical_arguments_hash
            AND decision.evaluated_at = NEW.updated_at) THEN
        RAISE EXCEPTION 'tool step ready requires current allow decision';
      END IF;
      IF NEW.state = 'ready' AND EXISTS (
          SELECT 1 FROM "{schema}".agent_tool_definitions definition
          WHERE definition.tool_id = OLD.tool_id AND definition.tool_version = OLD.tool_version
            AND definition.access_mode = 'write') AND NOT EXISTS (
          SELECT 1 FROM "{schema}".tool_confirmations confirmation
          JOIN "{schema}".tool_policy_decisions decision
            ON decision.step_id = confirmation.step_id AND decision.evaluated_at = NEW.updated_at
          LEFT JOIN "{schema}".tool_confirmation_invalidations invalidation
            ON invalidation.confirmation_id = confirmation.confirmation_id
          WHERE confirmation.step_id = OLD.step_id AND confirmation.run_id = OLD.run_id
            AND confirmation.workspace_id = OLD.workspace_id
            AND confirmation.tool_id = OLD.tool_id AND confirmation.tool_version = OLD.tool_version
            AND confirmation.canonical_arguments_hash = OLD.canonical_arguments_hash
            AND confirmation.state = 'approved' AND invalidation.confirmation_id IS NULL
            AND confirmation.expires_at > NEW.updated_at
            AND decision.decision_id <> confirmation.policy_decision_id
            AND decision.evaluated_at >= confirmation.resolved_at
            AND confirmation.permission_code = decision.permission_code
            AND confirmation.policy_version = decision.policy_version
            AND confirmation.resource_scope_hash = decision.resource_scope_hash
            AND confirmation.field_mask_hash = decision.field_mask_hash) THEN
        RAISE EXCEPTION 'write tool step ready requires current approved confirmation';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _legacy_step_current_attempt_sql(schema: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_step_current_attempt() RETURNS trigger AS $$
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


def _legacy_attempt_insert_sql(schema: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_attempt_insert() RETURNS trigger AS $$
    BEGIN
      IF NOT EXISTS (
          SELECT 1 FROM "{schema}".tool_steps AS steps
          JOIN "{schema}".tool_runs AS runs ON runs.run_id = steps.run_id
          WHERE steps.step_id = NEW.step_id AND steps.run_id = NEW.run_id
            AND steps.workspace_id = NEW.workspace_id AND steps.state = 'running'
            AND steps.current_attempt_no = NEW.attempt_no AND runs.state = 'running'
            AND runs.cancel_requested_at IS NULL AND NEW.attempt_no <= steps.max_attempts) THEN
        RAISE EXCEPTION 'tool attempt is not the current claimed generation';
      END IF;
      IF NEW.lease_generation <> NEW.attempt_no THEN
        RAISE EXCEPTION 'tool attempt lease generation mismatch';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _legacy_attempt_transition_sql(schema: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_attempt_transition() RETURNS trigger AS $$
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
              ('succeeded', 'failed', 'cancelled', 'timed_out', 'ignored_late_result'))) THEN
        RAISE EXCEPTION 'illegal tool attempt transition: % -> %', OLD.state, NEW.state;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """
