"""建立 P4-08 合成副作用、执行前幂等预留和原子结果提交协议。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260816_0058"
down_revision: str | None = "20260816_0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """创建幂等与合成副作用事实，并把预留提升为写调用数据库前置条件。"""

    schema = _schema()
    _create_idempotency_records(schema)
    _create_synthetic_side_effects(schema)
    _create_functions(schema)
    _create_triggers(schema)
    op.execute(sa.text(_call_transition_sql(schema, require_idempotency=True)))


def downgrade() -> None:
    """只在从未产生幂等或副作用事实时恢复 P4-07 结构。"""

    schema = _schema()
    idempotency_count = op.get_bind().scalar(
        sa.text(f'SELECT count(*) FROM "{schema}".tool_idempotency_records')
    )
    side_effect_count = op.get_bind().scalar(
        sa.text(f'SELECT count(*) FROM "{schema}".synthetic_tool_side_effects')
    )
    if int(idempotency_count or 0) > 0 or int(side_effect_count or 0) > 0:
        raise RuntimeError("存在工具幂等或合成副作用事实, 拒绝破坏性降级")

    op.execute(sa.text(_call_transition_sql(schema, require_idempotency=False)))
    _drop_triggers(schema)
    _drop_functions(schema)
    op.drop_index(
        "ix_synthetic_tool_side_effects_workspace_time",
        table_name="synthetic_tool_side_effects",
        schema=schema,
    )
    op.drop_table("synthetic_tool_side_effects", schema=schema)
    op.drop_index(
        "ix_tool_idempotency_records_workspace_time",
        table_name="tool_idempotency_records",
        schema=schema,
    )
    op.drop_table("tool_idempotency_records", schema=schema)


def _create_idempotency_records(schema: str) -> None:
    op.create_table(
        "tool_idempotency_records",
        sa.Column("idempotency_record_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_version", sa.Integer(), nullable=False),
        sa.Column("confirmation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confirmation_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=True),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.UniqueConstraint("tool_call_id", name="uq_tool_idempotency_records_call"),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key_hash",
            name="uq_tool_idempotency_records_key",
        ),
        sa.UniqueConstraint(
            "idempotency_record_id",
            "workspace_id",
            "idempotency_key_hash",
            "request_hash",
            name="uq_tool_idempotency_records_effect_binding",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_tool_idempotency_records_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["tool_call_id"],
            [f"{schema}.tool_calls.tool_call_id"],
            name="fk_tool_idempotency_records_call",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["confirmation_id"],
            [f"{schema}.tool_confirmations.confirmation_id"],
            name="fk_tool_idempotency_records_confirmation",
        ),
        sa.CheckConstraint("tool_version >= 1", name="ck_tool_idempotency_records_version"),
        sa.CheckConstraint(
            "confirmation_hash ~ '^[0-9a-f]{64}$' "
            "AND idempotency_key_hash ~ '^[0-9a-f]{64}$' "
            "AND request_hash ~ '^[0-9a-f]{64}$' "
            "AND (result_hash IS NULL OR result_hash ~ '^[0-9a-f]{64}$')",
            name="ck_tool_idempotency_records_hashes",
        ),
        sa.CheckConstraint(
            "state IN ('reserved', 'succeeded', 'failed', 'outcome_unknown')",
            name="ck_tool_idempotency_records_state",
        ),
        sa.CheckConstraint(
            "(state = 'reserved' AND result_hash IS NULL AND completed_at IS NULL "
            "AND error_code IS NULL) OR "
            "(state = 'succeeded' AND result_hash IS NOT NULL AND completed_at IS NOT NULL "
            "AND error_code IS NULL) OR "
            "(state = 'failed' AND result_hash IS NULL AND completed_at IS NOT NULL "
            "AND error_code IS NOT NULL) OR "
            "(state = 'outcome_unknown' AND result_hash IS NULL "
            "AND completed_at IS NOT NULL AND error_code = 'TOOL_OUTCOME_UNKNOWN')",
            name="ck_tool_idempotency_records_outcome",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= reserved_at",
            name="ck_tool_idempotency_records_time",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_tool_idempotency_records_workspace_time",
        "tool_idempotency_records",
        ["workspace_id", "reserved_at"],
        schema=schema,
    )


def _create_synthetic_side_effects(schema: str) -> None:
    op.create_table(
        "synthetic_tool_side_effects",
        sa.Column("side_effect_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("canonical_arguments_hash", sa.String(64), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key_hash",
            name="uq_synthetic_tool_side_effects_key",
        ),
        sa.UniqueConstraint(
            "idempotency_record_id",
            name="uq_synthetic_tool_side_effects_record",
        ),
        sa.ForeignKeyConstraint(
            [
                "idempotency_record_id",
                "workspace_id",
                "idempotency_key_hash",
                "request_hash",
            ],
            [
                f"{schema}.tool_idempotency_records.idempotency_record_id",
                f"{schema}.tool_idempotency_records.workspace_id",
                f"{schema}.tool_idempotency_records.idempotency_key_hash",
                f"{schema}.tool_idempotency_records.request_hash",
            ],
            name="fk_synthetic_tool_side_effects_record",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$' "
            "AND request_hash ~ '^[0-9a-f]{64}$' "
            "AND canonical_arguments_hash ~ '^[0-9a-f]{64}$' "
            "AND result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_synthetic_tool_side_effects_hashes",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_synthetic_tool_side_effects_workspace_time",
        "synthetic_tool_side_effects",
        ["workspace_id", "committed_at"],
        schema=schema,
    )


def _create_functions(schema: str) -> None:
    op.execute(sa.text(_idempotency_insert_sql(schema)))
    op.execute(sa.text(_idempotency_mutation_sql(schema)))
    op.execute(sa.text(_side_effect_insert_sql(schema)))
    op.execute(sa.text(_side_effect_mutation_sql(schema)))


def _create_triggers(schema: str) -> None:
    statements = (
        f'CREATE TRIGGER trg_tool_idempotency_records_insert BEFORE INSERT ON "{schema}".'
        f'tool_idempotency_records FOR EACH ROW EXECUTE FUNCTION "{schema}".'
        "validate_tool_idempotency_insert()",
        "CREATE TRIGGER trg_tool_idempotency_records_mutation BEFORE UPDATE OR DELETE "
        f'ON "{schema}".'
        f'tool_idempotency_records FOR EACH ROW EXECUTE FUNCTION "{schema}".'
        "validate_tool_idempotency_mutation()",
        f'CREATE TRIGGER trg_synthetic_tool_side_effects_insert BEFORE INSERT ON "{schema}".'
        f'synthetic_tool_side_effects FOR EACH ROW EXECUTE FUNCTION "{schema}".'
        "validate_synthetic_tool_side_effect_insert()",
        "CREATE TRIGGER trg_synthetic_tool_side_effects_mutation BEFORE UPDATE OR DELETE "
        f'ON "{schema}".'
        f'synthetic_tool_side_effects FOR EACH ROW EXECUTE FUNCTION "{schema}".'
        "validate_synthetic_tool_side_effect_mutation()",
    )
    for statement in statements:
        op.execute(sa.text(statement))


def _drop_triggers(schema: str) -> None:
    op.execute(
        sa.text(
            f'DROP TRIGGER trg_synthetic_tool_side_effects_mutation ON "{schema}".'
            "synthetic_tool_side_effects"
        )
    )
    op.execute(
        sa.text(
            f'DROP TRIGGER trg_synthetic_tool_side_effects_insert ON "{schema}".'
            "synthetic_tool_side_effects"
        )
    )
    op.execute(
        sa.text(
            f'DROP TRIGGER trg_tool_idempotency_records_mutation ON "{schema}".'
            "tool_idempotency_records"
        )
    )
    op.execute(
        sa.text(
            f'DROP TRIGGER trg_tool_idempotency_records_insert ON "{schema}".'
            "tool_idempotency_records"
        )
    )


def _drop_functions(schema: str) -> None:
    for function_name in (
        "validate_synthetic_tool_side_effect_mutation",
        "validate_synthetic_tool_side_effect_insert",
        "validate_tool_idempotency_mutation",
        "validate_tool_idempotency_insert",
    ):
        op.execute(sa.text(f'DROP FUNCTION "{schema}".{function_name}()'))


def _idempotency_insert_sql(schema: str) -> str:
    return f"""
    CREATE FUNCTION "{schema}".validate_tool_idempotency_insert() RETURNS trigger AS $$
    BEGIN
      IF NEW.state <> 'reserved' THEN
        RAISE EXCEPTION 'tool idempotency must start reserved';
      END IF;
      IF NOT EXISTS (
          SELECT 1 FROM "{schema}".tool_calls call
          JOIN "{schema}".tool_attempts attempt ON attempt.attempt_id = call.attempt_id
          JOIN "{schema}".tool_steps step ON step.step_id = call.step_id
          JOIN "{schema}".tool_runs run ON run.run_id = call.run_id
          JOIN "{schema}".agent_tool_definitions definition
            ON definition.tool_id = call.tool_id
           AND definition.tool_version = call.tool_version
          JOIN "{schema}".tool_confirmations confirmation
            ON confirmation.confirmation_id = NEW.confirmation_id
          JOIN "{schema}".tool_policy_decisions policy
            ON policy.decision_id = (
              SELECT latest.decision_id
                FROM "{schema}".tool_policy_decisions latest
               WHERE latest.step_id = NEW.step_id
               ORDER BY latest.evaluated_at DESC
               LIMIT 1)
          WHERE call.tool_call_id = NEW.tool_call_id
            AND call.workspace_id = NEW.workspace_id AND call.run_id = NEW.run_id
            AND call.step_id = NEW.step_id AND call.tool_id = NEW.tool_id
            AND call.tool_version = NEW.tool_version
            AND call.state = 'confirmed' AND call.access_mode = 'write'
            AND attempt.state = 'executing' AND attempt.lease_expires_at > NEW.reserved_at
            AND step.state = 'running' AND run.state = 'running'
            AND run.cancel_requested_at IS NULL
            AND definition.status = 'active' AND definition.access_mode = 'write'
            AND definition.adapter_kind = 'synthetic_internal_write'
            AND definition.retry_mode = 'idempotent_write' AND definition.synthetic = true
            AND confirmation.workspace_id = NEW.workspace_id
            AND confirmation.run_id = NEW.run_id AND confirmation.step_id = NEW.step_id
            AND confirmation.tool_id = NEW.tool_id
            AND confirmation.tool_version = NEW.tool_version
            AND confirmation.canonical_arguments_hash = call.canonical_arguments_hash
            AND confirmation.confirmation_hash = NEW.confirmation_hash
            AND confirmation.state = 'approved' AND confirmation.expires_at > NEW.reserved_at
            AND policy.decision_id <> confirmation.policy_decision_id
            AND policy.permission_code = confirmation.permission_code
            AND policy.policy_version = confirmation.policy_version
            AND policy.resource_scope_hash = confirmation.resource_scope_hash
            AND policy.field_mask_hash = confirmation.field_mask_hash
            AND policy.evaluated_at >= confirmation.resolved_at
            AND NOT EXISTS (
                SELECT 1 FROM "{schema}".tool_confirmation_invalidations invalidation
                 WHERE invalidation.confirmation_id = confirmation.confirmation_id)) THEN
        RAISE EXCEPTION 'tool idempotency reservation is not authorized';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _idempotency_mutation_sql(schema: str) -> str:
    return f"""
    CREATE FUNCTION "{schema}".validate_tool_idempotency_mutation() RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN
        IF current_setting('ai_platform.lifecycle_purge', true) = 'on' THEN
          RETURN OLD;
        END IF;
        RAISE EXCEPTION 'tool idempotency history is immutable';
      END IF;
      IF ROW(NEW.idempotency_record_id, NEW.workspace_id, NEW.run_id, NEW.step_id,
             NEW.tool_call_id, NEW.tool_id, NEW.tool_version, NEW.confirmation_id,
             NEW.confirmation_hash, NEW.idempotency_key_hash, NEW.request_hash,
             NEW.reserved_at)
         IS DISTINCT FROM
         ROW(OLD.idempotency_record_id, OLD.workspace_id, OLD.run_id, OLD.step_id,
             OLD.tool_call_id, OLD.tool_id, OLD.tool_version, OLD.confirmation_id,
             OLD.confirmation_hash, OLD.idempotency_key_hash, OLD.request_hash,
             OLD.reserved_at) THEN
        RAISE EXCEPTION 'tool idempotency identity is immutable';
      END IF;
      IF NOT ((OLD.state = 'reserved'
               AND NEW.state IN ('succeeded', 'failed', 'outcome_unknown'))
          OR (OLD.state = 'outcome_unknown' AND NEW.state = 'succeeded')) THEN
        RAISE EXCEPTION 'illegal tool idempotency transition: % -> %', OLD.state, NEW.state;
      END IF;
      IF NEW.state = 'succeeded' AND NOT EXISTS (
          SELECT 1 FROM "{schema}".synthetic_tool_side_effects effect
           WHERE effect.idempotency_record_id = NEW.idempotency_record_id
             AND effect.workspace_id = NEW.workspace_id
             AND effect.idempotency_key_hash = NEW.idempotency_key_hash
             AND effect.request_hash = NEW.request_hash
             AND effect.result_hash = NEW.result_hash) THEN
        RAISE EXCEPTION 'successful idempotency record requires exact side effect';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _side_effect_insert_sql(schema: str) -> str:
    return f"""
    CREATE FUNCTION "{schema}".validate_synthetic_tool_side_effect_insert()
    RETURNS trigger AS $$
    BEGIN
      IF NOT EXISTS (
          SELECT 1 FROM "{schema}".tool_idempotency_records record
          JOIN "{schema}".tool_calls call ON call.tool_call_id = record.tool_call_id
          JOIN "{schema}".agent_tool_definitions definition
            ON definition.tool_id = record.tool_id
           AND definition.tool_version = record.tool_version
          WHERE record.idempotency_record_id = NEW.idempotency_record_id
            AND record.workspace_id = NEW.workspace_id
            AND record.idempotency_key_hash = NEW.idempotency_key_hash
            AND record.request_hash = NEW.request_hash AND record.state = 'reserved'
            AND call.canonical_arguments_hash = NEW.canonical_arguments_hash
            AND call.state = 'executing'
            AND definition.adapter_kind = 'synthetic_internal_write'
            AND definition.access_mode = 'write' AND definition.synthetic = true
            AND NEW.committed_at >= record.reserved_at) THEN
        RAISE EXCEPTION 'synthetic side effect is not backed by active reservation';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """


def _side_effect_mutation_sql(schema: str) -> str:
    return f"""
    CREATE FUNCTION "{schema}".validate_synthetic_tool_side_effect_mutation()
    RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'DELETE'
         AND current_setting('ai_platform.lifecycle_purge', true) = 'on' THEN
        RETURN OLD;
      END IF;
      RAISE EXCEPTION 'synthetic tool side effect is immutable';
    END;
    $$ LANGUAGE plpgsql;
    """


def _call_transition_sql(schema: str, *, require_idempotency: bool) -> str:
    start_guard = ""
    terminal_guard = ""
    if require_idempotency:
        start_guard = f"""
      IF OLD.state = 'confirmed' AND NEW.state = 'executing' AND NEW.access_mode = 'write'
         AND NOT EXISTS (
            SELECT 1 FROM "{schema}".tool_idempotency_records record
             WHERE record.tool_call_id = NEW.tool_call_id
               AND record.workspace_id = NEW.workspace_id
               AND record.run_id = NEW.run_id AND record.step_id = NEW.step_id
               AND record.tool_id = NEW.tool_id AND record.tool_version = NEW.tool_version
               AND record.state = 'reserved') THEN
        RAISE EXCEPTION 'write tool call requires reserved idempotency identity';
      END IF;
        """
        terminal_guard = f"""
      IF OLD.state = 'executing' AND NEW.access_mode = 'write'
         AND ((NEW.state = 'succeeded' AND NOT EXISTS (
                 SELECT 1 FROM "{schema}".tool_idempotency_records record
                  WHERE record.tool_call_id = NEW.tool_call_id
                    AND record.state = 'succeeded'))
           OR (NEW.state = 'failed' AND NOT EXISTS (
                 SELECT 1 FROM "{schema}".tool_idempotency_records record
                  WHERE record.tool_call_id = NEW.tool_call_id
                    AND record.state = 'failed'))) THEN
        RAISE EXCEPTION 'write tool call terminal state lacks matching idempotency outcome';
      END IF;
        """
    return f"""
    CREATE OR REPLACE FUNCTION "{schema}".validate_tool_call_transition()
    RETURNS trigger AS $$
    DECLARE requirement varchar(32);
    BEGIN
      IF ROW(NEW.run_id, NEW.step_id, NEW.attempt_id, NEW.workspace_id, NEW.tool_id,
             NEW.tool_version, NEW.canonical_arguments_hash, NEW.access_mode,
             NEW.risk_level, NEW.created_at)
         IS DISTINCT FROM
         ROW(OLD.run_id, OLD.step_id, OLD.attempt_id, OLD.workspace_id, OLD.tool_id,
             OLD.tool_version, OLD.canonical_arguments_hash, OLD.access_mode,
             OLD.risk_level, OLD.created_at) THEN
        RAISE EXCEPTION 'tool call binding is immutable';
      END IF;
      SELECT credential_requirement INTO requirement
        FROM "{schema}".agent_tool_definitions
       WHERE tool_id = NEW.tool_id AND tool_version = NEW.tool_version;
      IF requirement IS NULL THEN
        RAISE EXCEPTION 'tool call definition is unavailable';
      END IF;
      IF OLD.state = 'proposed' AND NEW.state = 'authorized' THEN
        IF requirement = 'credential_ref' THEN
          IF OLD.credential_ref IS NOT NULL OR NEW.credential_ref IS NULL
             OR NOT EXISTS (
                SELECT 1 FROM "{schema}".tool_credentials credential
                 WHERE credential.credential_ref = NEW.credential_ref
                   AND credential.workspace_id = NEW.workspace_id
                   AND credential.tool_id = NEW.tool_id
                   AND credential.tool_version = NEW.tool_version
                   AND credential.status = 'active') THEN
            RAISE EXCEPTION 'tool call credential binding is unavailable';
          END IF;
        ELSIF OLD.credential_ref IS NOT NULL OR NEW.credential_ref IS NOT NULL THEN
          RAISE EXCEPTION 'credential-free tool call cannot bind credential';
        END IF;
      ELSIF NEW.credential_ref IS DISTINCT FROM OLD.credential_ref THEN
        RAISE EXCEPTION 'tool call credential binding is immutable';
      END IF;
      IF requirement = 'credential_ref'
         AND NEW.state IN ('authorized', 'confirmed', 'executing')
         AND NOT EXISTS (
            SELECT 1 FROM "{schema}".tool_credentials credential
             WHERE credential.credential_ref = NEW.credential_ref
               AND credential.workspace_id = NEW.workspace_id
               AND credential.tool_id = NEW.tool_id
               AND credential.tool_version = NEW.tool_version
               AND credential.status = 'active') THEN
        RAISE EXCEPTION 'tool call credential was revoked or rotated';
      END IF;
      {start_guard}
      {terminal_guard}
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
