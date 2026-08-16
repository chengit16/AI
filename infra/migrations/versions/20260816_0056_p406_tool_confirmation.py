"""建立 P4-06 工具确认、审批绑定和重新授权门禁。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260816_0056"
down_revision: str | None = "20260816_0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """允许追加 PDP 证据，并为写步骤建立确认与当前策略双重数据库门禁。"""

    schema = _schema()
    op.drop_constraint(
        "uq_tool_policy_decisions_step",
        "tool_policy_decisions",
        schema=schema,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_tool_policy_decisions_step_time",
        "tool_policy_decisions",
        ["step_id", "evaluated_at"],
        schema=schema,
    )
    _create_confirmations(schema)
    _create_invalidations(schema)
    _create_confirmation_functions(schema)
    _create_confirmation_triggers(schema)
    op.execute(sa.text(_step_transition_sql(schema, require_confirmation=True)))
    op.execute(sa.text(_run_transition_sql(schema, require_waiting_step=True)))


def downgrade() -> None:
    """只在没有确认和追加 PDP 证据时恢复 P4-05 单证据结构。"""

    schema = _schema()
    confirmation_count = op.get_bind().scalar(
        sa.text(f'SELECT count(*) FROM "{schema}".tool_confirmations')
    )
    repeated_policy_count = op.get_bind().scalar(
        sa.text(
            f'SELECT count(*) FROM (SELECT step_id FROM "{schema}".tool_policy_decisions '
            "GROUP BY step_id HAVING count(*) > 1) repeated"
        )
    )
    if int(confirmation_count or 0) > 0 or int(repeated_policy_count or 0) > 0:
        raise RuntimeError("存在工具确认或追加策略事实, 拒绝破坏性降级")

    op.execute(sa.text(_step_transition_sql(schema, require_confirmation=False)))
    op.execute(sa.text(_run_transition_sql(schema, require_waiting_step=False)))
    _drop_confirmation_triggers(schema)
    _drop_confirmation_functions(schema)
    op.drop_index(
        "ix_tool_confirmation_invalidations_workspace_time",
        table_name="tool_confirmation_invalidations",
        schema=schema,
    )
    op.drop_table("tool_confirmation_invalidations", schema=schema)
    op.drop_index(
        "ix_tool_confirmations_workspace_time",
        table_name="tool_confirmations",
        schema=schema,
    )
    op.drop_table("tool_confirmations", schema=schema)
    op.drop_constraint(
        "uq_tool_policy_decisions_step_time",
        "tool_policy_decisions",
        schema=schema,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_tool_policy_decisions_step",
        "tool_policy_decisions",
        ["step_id"],
        schema=schema,
    )


def _create_confirmations(schema: str) -> None:
    op.create_table(
        "tool_confirmations",
        sa.Column("confirmation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("approval_instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_version", sa.Integer(), nullable=False),
        sa.Column("canonical_arguments_hash", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("policy_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_code", sa.String(160), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("resource_scope_hash", sa.String(64), nullable=False),
        sa.Column("field_mask_hash", sa.String(64), nullable=False),
        sa.Column("policy_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("confirmation_hash", sa.String(64), nullable=False),
        sa.Column("subject_digest", sa.String(64), nullable=False),
        sa.Column("chain_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("confirmed_by_actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("approval_instance_id", name="uq_tool_confirmations_approval"),
        sa.UniqueConstraint("step_id", name="uq_tool_confirmations_step"),
        sa.UniqueConstraint(
            "confirmation_id",
            "workspace_id",
            "run_id",
            "step_id",
            name="uq_tool_confirmations_identity",
        ),
        sa.ForeignKeyConstraint(
            ["approval_instance_id", "workspace_id"],
            [
                f"{schema}.approval_instances.approval_instance_id",
                f"{schema}.approval_instances.workspace_id",
            ],
            name="fk_tool_confirmations_approval",
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
            name="fk_tool_confirmations_step",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["policy_decision_id"],
            [f"{schema}.tool_policy_decisions.decision_id"],
            name="fk_tool_confirmations_policy",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_actor_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_tool_confirmations_actor",
        ),
        sa.CheckConstraint(
            "mode IN ('personal_owner', 'enterprise_approval')",
            name="ck_tool_confirmations_mode",
        ),
        sa.CheckConstraint(
            "risk_level IN ('high', 'critical')",
            name="ck_tool_confirmations_risk",
        ),
        sa.CheckConstraint(
            "canonical_arguments_hash ~ '^[0-9a-f]{64}$' "
            "AND resource_scope_hash ~ '^[0-9a-f]{64}$' "
            "AND field_mask_hash ~ '^[0-9a-f]{64}$' "
            "AND confirmation_hash ~ '^[0-9a-f]{64}$' "
            "AND subject_digest ~ '^[0-9a-f]{64}$' "
            "AND chain_digest ~ '^[0-9a-f]{64}$'",
            name="ck_tool_confirmations_hashes",
        ),
        sa.CheckConstraint(
            "permission_code ~ '^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*){2,}$'",
            name="ck_tool_confirmations_permission",
        ),
        sa.CheckConstraint(
            "tool_version >= 1 AND policy_version >= 1",
            name="ck_tool_confirmations_versions",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'approved', 'rejected', 'expired', 'withdrawn')",
            name="ck_tool_confirmations_state",
        ),
        sa.CheckConstraint(
            "(state = 'pending' AND resolved_at IS NULL AND confirmed_by_actor_id IS NULL) OR "
            "(state <> 'pending' AND resolved_at IS NOT NULL)",
            name="ck_tool_confirmations_resolution",
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND updated_at >= created_at",
            name="ck_tool_confirmations_time",
        ),
        sa.CheckConstraint("version >= 1", name="ck_tool_confirmations_version"),
        schema=schema,
    )
    op.create_index(
        "ix_tool_confirmations_workspace_time",
        "tool_confirmations",
        ["workspace_id", "created_at"],
        schema=schema,
    )


def _create_invalidations(schema: str) -> None:
    op.create_table(
        "tool_confirmation_invalidations",
        sa.Column("invalidation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("confirmation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "confirmation_id",
            name="uq_tool_confirmation_invalidations_confirmation",
        ),
        sa.ForeignKeyConstraint(
            ["confirmation_id", "workspace_id", "run_id", "step_id"],
            [
                f"{schema}.tool_confirmations.confirmation_id",
                f"{schema}.tool_confirmations.workspace_id",
                f"{schema}.tool_confirmations.run_id",
                f"{schema}.tool_confirmations.step_id",
            ],
            name="fk_tool_confirmation_invalidations_confirmation",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "state IN ('expired', 'withdrawn')",
            name="ck_tool_confirmation_invalidations_state",
        ),
        sa.CheckConstraint(
            "reason_code IN ('arguments_changed', 'tool_changed', 'policy_changed', "
            "'permission_revoked', 'expired')",
            name="ck_tool_confirmation_invalidations_reason",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_tool_confirmation_invalidations_workspace_time",
        "tool_confirmation_invalidations",
        ["workspace_id", "occurred_at"],
        schema=schema,
    )


def _create_confirmation_functions(schema: str) -> None:
    op.execute(sa.text(_confirmation_insert_sql(schema)))
    op.execute(sa.text(_confirmation_mutation_sql(schema)))
    op.execute(sa.text(_invalidation_insert_sql(schema)))
    op.execute(sa.text(_invalidation_mutation_sql(schema)))


def _create_confirmation_triggers(schema: str) -> None:
    statements = (
        (
            "CREATE TRIGGER trg_tool_confirmations_validate BEFORE INSERT ON "
            f'"{schema}".tool_confirmations FOR EACH ROW EXECUTE FUNCTION '
            f'"{schema}".validate_tool_confirmation_insert()'
        ),
        (
            "CREATE TRIGGER trg_tool_confirmations_transition BEFORE UPDATE OR DELETE ON "
            f'"{schema}".tool_confirmations FOR EACH ROW EXECUTE FUNCTION '
            f'"{schema}".validate_tool_confirmation_mutation()'
        ),
        (
            "CREATE TRIGGER trg_tool_confirmation_invalidations_validate BEFORE INSERT ON "
            f'"{schema}".tool_confirmation_invalidations FOR EACH ROW EXECUTE FUNCTION '
            f'"{schema}".validate_tool_confirmation_invalidation()'
        ),
        (
            "CREATE TRIGGER trg_tool_confirmation_invalidations_immutable "
            f'BEFORE UPDATE OR DELETE ON "{schema}".tool_confirmation_invalidations '
            f"FOR EACH ROW EXECUTE FUNCTION "
            f'"{schema}".reject_tool_confirmation_invalidation_mutation()'
        ),
    )
    for statement in statements:
        op.execute(sa.text(statement))


def _drop_confirmation_triggers(schema: str) -> None:
    statements = (
        (
            "DROP TRIGGER trg_tool_confirmation_invalidations_immutable ON "
            f'"{schema}".tool_confirmation_invalidations'
        ),
        (
            "DROP TRIGGER trg_tool_confirmation_invalidations_validate ON "
            f'"{schema}".tool_confirmation_invalidations'
        ),
        f'DROP TRIGGER trg_tool_confirmations_transition ON "{schema}".tool_confirmations',
        f'DROP TRIGGER trg_tool_confirmations_validate ON "{schema}".tool_confirmations',
    )
    for statement in statements:
        op.execute(sa.text(statement))


def _drop_confirmation_functions(schema: str) -> None:
    for function_name in (
        "reject_tool_confirmation_invalidation_mutation",
        "validate_tool_confirmation_invalidation",
        "validate_tool_confirmation_mutation",
        "validate_tool_confirmation_insert",
    ):
        op.execute(sa.text(f'DROP FUNCTION "{schema}".{function_name}()'))


def _confirmation_insert_sql(schema: str) -> str:
    return f"""
    CREATE FUNCTION "{schema}".validate_tool_confirmation_insert() RETURNS trigger AS $$
    BEGIN
      IF NOT EXISTS (
          SELECT 1 FROM "{schema}".approval_instances approval
          JOIN "{schema}".tool_steps step
            ON step.step_id = NEW.step_id AND step.run_id = NEW.run_id
           AND step.workspace_id = NEW.workspace_id AND step.tool_id = NEW.tool_id
           AND step.tool_version = NEW.tool_version
           AND step.canonical_arguments_hash = NEW.canonical_arguments_hash
          JOIN "{schema}".tool_runs run
            ON run.run_id = step.run_id AND run.workspace_id = step.workspace_id
          JOIN "{schema}".agent_tool_definitions definition
            ON definition.tool_id = step.tool_id AND definition.tool_version = step.tool_version
          JOIN "{schema}".tool_policy_decisions decision
            ON decision.decision_id = NEW.policy_decision_id
           AND decision.step_id = step.step_id AND decision.run_id = step.run_id
           AND decision.workspace_id = step.workspace_id AND decision.tool_id = step.tool_id
           AND decision.tool_version = step.tool_version
           AND decision.canonical_arguments_hash = step.canonical_arguments_hash
          WHERE approval.approval_instance_id = NEW.approval_instance_id
            AND approval.workspace_id = NEW.workspace_id
            AND approval.resource_type = 'tool.call' AND approval.operation = 'execute'
            AND approval.resource_id = NEW.step_id AND approval.status = 'pending'
            AND approval.subject_digest = NEW.subject_digest
            AND approval.chain_digest = NEW.chain_digest
            AND approval.requester_account_id = run.requested_by_account_id
            AND ((approval.personal_owner_confirmation = true
                  AND NEW.mode = 'personal_owner')
              OR (approval.personal_owner_confirmation = false
                  AND NEW.mode = 'enterprise_approval'))
            AND step.state = 'policy_checking' AND run.state = 'running'
            AND run.deadline_at = NEW.expires_at AND run.deadline_at > NEW.created_at
            AND definition.status = 'active' AND definition.access_mode = 'write'
            AND definition.adapter_kind = 'synthetic_internal_write' AND definition.synthetic = true
            AND definition.risk_level = NEW.risk_level
            AND definition.permission_code = NEW.permission_code
            AND decision.permission_code = NEW.permission_code
            AND decision.policy_version = NEW.policy_version
            AND decision.resource_scope_hash = NEW.resource_scope_hash
            AND decision.field_mask_hash = NEW.field_mask_hash
            AND decision.evaluated_at = NEW.policy_evaluated_at
      ) THEN RAISE EXCEPTION 'invalid tool confirmation binding'; END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _confirmation_mutation_sql(schema: str) -> str:
    return f"""
    CREATE FUNCTION "{schema}".validate_tool_confirmation_mutation() RETURNS trigger AS $$
    DECLARE approval_state varchar(32);
    BEGIN
      IF TG_OP = 'DELETE' THEN
        IF current_setting('ai_platform.lifecycle_purge', true) = 'on' THEN RETURN OLD; END IF;
        RAISE EXCEPTION 'tool confirmation is immutable';
      END IF;
      IF ROW(NEW.approval_instance_id, NEW.workspace_id, NEW.run_id, NEW.step_id,
             NEW.tool_id, NEW.tool_version, NEW.canonical_arguments_hash, NEW.mode,
             NEW.policy_decision_id, NEW.permission_code, NEW.policy_version,
             NEW.resource_scope_hash, NEW.field_mask_hash, NEW.policy_evaluated_at,
             NEW.risk_level, NEW.confirmation_hash, NEW.subject_digest, NEW.chain_digest,
             NEW.expires_at, NEW.created_at)
         IS DISTINCT FROM
         ROW(OLD.approval_instance_id, OLD.workspace_id, OLD.run_id, OLD.step_id,
             OLD.tool_id, OLD.tool_version, OLD.canonical_arguments_hash, OLD.mode,
             OLD.policy_decision_id, OLD.permission_code, OLD.policy_version,
             OLD.resource_scope_hash, OLD.field_mask_hash, OLD.policy_evaluated_at,
             OLD.risk_level, OLD.confirmation_hash, OLD.subject_digest, OLD.chain_digest,
             OLD.expires_at, OLD.created_at)
      THEN RAISE EXCEPTION 'tool confirmation identity is immutable'; END IF;
      IF NEW.version <> OLD.version + 1 OR OLD.state <> 'pending'
         OR NEW.state NOT IN ('approved', 'rejected', 'expired', 'withdrawn')
      THEN RAISE EXCEPTION 'invalid tool confirmation transition'; END IF;
      SELECT status INTO approval_state FROM "{schema}".approval_instances
       WHERE approval_instance_id = OLD.approval_instance_id;
      IF NEW.state = 'approved' AND approval_state <> 'approved' THEN
        RAISE EXCEPTION 'approved confirmation requires approved instance';
      ELSIF NEW.state = 'rejected' AND approval_state <> 'rejected' THEN
        RAISE EXCEPTION 'rejected confirmation requires rejected instance';
      ELSIF NEW.state = 'withdrawn' AND approval_state <> 'withdrawn' THEN
        RAISE EXCEPTION 'withdrawn confirmation requires withdrawn instance';
      ELSIF NEW.state = 'expired' AND NEW.resolved_at < OLD.expires_at THEN
        RAISE EXCEPTION 'confirmation cannot expire before deadline';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _invalidation_insert_sql(schema: str) -> str:
    return f"""
    CREATE FUNCTION "{schema}".validate_tool_confirmation_invalidation() RETURNS trigger AS $$
    DECLARE confirmation_state varchar(16); confirmation_expiry timestamptz;
            confirmation_resolved timestamptz;
    BEGIN
      SELECT state, expires_at, resolved_at
        INTO confirmation_state, confirmation_expiry, confirmation_resolved
        FROM "{schema}".tool_confirmations
       WHERE confirmation_id = NEW.confirmation_id AND workspace_id = NEW.workspace_id
         AND run_id = NEW.run_id AND step_id = NEW.step_id;
      IF confirmation_state <> 'approved' THEN
        RAISE EXCEPTION 'only approved confirmation can be invalidated';
      END IF;
      IF NEW.occurred_at < confirmation_resolved THEN
        RAISE EXCEPTION 'confirmation cannot be invalidated before approval';
      END IF;
      IF (NEW.state = 'expired') <> (NEW.reason_code = 'expired') THEN
        RAISE EXCEPTION 'confirmation invalidation state and reason mismatch';
      END IF;
      IF NEW.state = 'expired' AND NEW.occurred_at < confirmation_expiry THEN
        RAISE EXCEPTION 'confirmation cannot expire before deadline';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _invalidation_mutation_sql(schema: str) -> str:
    return f"""
    CREATE FUNCTION "{schema}".reject_tool_confirmation_invalidation_mutation()
    RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'DELETE' AND current_setting('ai_platform.lifecycle_purge', true) = 'on'
      THEN RETURN OLD; END IF;
      RAISE EXCEPTION 'tool confirmation invalidation is immutable';
    END;
    $$ LANGUAGE plpgsql;
    """


def _step_transition_sql(schema: str, *, require_confirmation: bool) -> str:
    confirmation_guard = (
        f"""
      IF NEW.state = 'ready' AND EXISTS (
          SELECT 1 FROM "{schema}".agent_tool_definitions definition
          WHERE definition.tool_id = OLD.tool_id AND definition.tool_version = OLD.tool_version
            AND definition.access_mode = 'write') AND NOT EXISTS (
          SELECT 1 FROM "{schema}".tool_confirmations confirmation
          JOIN "{schema}".tool_policy_decisions decision
            ON decision.step_id = confirmation.step_id
           AND decision.evaluated_at = NEW.updated_at
          LEFT JOIN "{schema}".tool_confirmation_invalidations invalidation
            ON invalidation.confirmation_id = confirmation.confirmation_id
          WHERE confirmation.step_id = OLD.step_id AND confirmation.run_id = OLD.run_id
            AND confirmation.workspace_id = OLD.workspace_id
            AND confirmation.tool_id = OLD.tool_id
            AND confirmation.tool_version = OLD.tool_version
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
      END IF;"""
        if require_confirmation
        else ""
    )
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
      END IF;{confirmation_guard}
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _run_transition_sql(schema: str, *, require_waiting_step: bool) -> str:
    waiting_guard = (
        f"""
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
      END IF;"""
        if require_waiting_step
        else ""
    )
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
      END IF;{waiting_guard}
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """
