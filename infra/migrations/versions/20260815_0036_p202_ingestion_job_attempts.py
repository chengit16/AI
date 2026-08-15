"""建立 P2-02 入库阶段、不可变 Attempt 和稳定取消/超时终态。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260815_0036"
down_revision: str | None = "20260815_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _extend_ingestion_jobs(schema)
    _create_job_stages(schema)
    _create_job_attempts(schema)
    _backfill_operational_facts(schema)
    _protect_attempt_history(schema)


def downgrade() -> None:
    schema = _schema()
    op.execute(
        sa.text(f'DROP TRIGGER protect_ingestion_job_attempts ON "{schema}".ingestion_job_attempts')
    )
    op.execute(sa.text(f'DROP FUNCTION "{schema}".protect_ingestion_attempt_history()'))
    op.drop_table("ingestion_job_attempts", schema=schema)
    op.drop_table("ingestion_job_stages", schema=schema)
    _restore_stage_one_ingestion_jobs(schema)


def _extend_ingestion_jobs(schema: str) -> None:
    for constraint in (
        "ck_ingestion_jobs_status",
        "ck_ingestion_jobs_failure",
        "ck_ingestion_jobs_completed_at",
        "ck_ingestion_jobs_manual_retry",
        "ck_ingestion_jobs_claim",
    ):
        op.drop_constraint(constraint, "ingestion_jobs", type_="check", schema=schema)
    op.add_column(
        "ingestion_jobs",
        sa.Column("cancelled_by_actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("active_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.execute(
        sa.text(
            f"""
            UPDATE "{schema}".ingestion_jobs
            SET active_attempt_id =
                md5(ingestion_job_id::text || ':legacy:' || attempt_count::text)::uuid
            WHERE status = 'running'
            """
        )
    )
    op.create_unique_constraint(
        "uq_ingestion_jobs_workspace_job",
        "ingestion_jobs",
        ["workspace_id", "ingestion_job_id"],
        schema=schema,
    )
    op.create_check_constraint(
        "ck_ingestion_jobs_claim",
        "ingestion_jobs",
        "(status = 'running' AND claimed_by IS NOT NULL AND claim_until IS NOT NULL "
        "AND active_attempt_id IS NOT NULL AND started_at IS NOT NULL) OR "
        "(status <> 'running' AND claimed_by IS NULL AND claim_until IS NULL "
        "AND active_attempt_id IS NULL)",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_ingestion_jobs_status",
        "ingestion_jobs",
        "status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', "
        "'cancelled', 'timed_out')",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_ingestion_jobs_failure",
        "ingestion_jobs",
        "(status IN ('retry_wait', 'failed', 'timed_out') AND failure_stage IS NOT NULL "
        "AND error_code IS NOT NULL AND error_message IS NOT NULL) OR "
        "(status NOT IN ('retry_wait', 'failed', 'timed_out') AND failure_stage IS NULL "
        "AND error_code IS NULL AND error_message IS NULL)",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_ingestion_jobs_completed_at",
        "ingestion_jobs",
        "(status IN ('succeeded', 'failed', 'cancelled', 'timed_out') "
        "AND completed_at IS NOT NULL) OR "
        "(status NOT IN ('succeeded', 'failed', 'cancelled', 'timed_out') "
        "AND completed_at IS NULL)",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_ingestion_jobs_manual_retry",
        "ingestion_jobs",
        "manual_retry_count BETWEEN 0 AND 3 AND "
        "((manual_retry_count = 0 AND last_retried_by_actor_id IS NULL "
        "AND last_retried_at IS NULL) OR "
        "(manual_retry_count > 0 AND last_retried_by_actor_id IS NOT NULL "
        "AND last_retried_at IS NOT NULL))",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_ingestion_jobs_cancellation",
        "ingestion_jobs",
        "(status = 'cancelled' AND cancelled_by_actor_id IS NOT NULL "
        "AND cancelled_at IS NOT NULL AND cancelled_at = completed_at) OR "
        "(status <> 'cancelled' AND cancelled_by_actor_id IS NULL AND cancelled_at IS NULL)",
        schema=schema,
    )


def _create_job_stages(schema: str) -> None:
    op.create_table(
        "ingestion_job_stages",
        sa.Column("job_stage_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage_key", sa.String(64), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_stage", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "ingestion_job_id",
            "stage_key",
            name="uq_ingestion_job_stages_job_key",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "job_stage_id",
            name="uq_ingestion_job_stages_workspace_stage",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "ingestion_job_id"],
            [
                f"{schema}.ingestion_jobs.workspace_id",
                f"{schema}.ingestion_jobs.ingestion_job_id",
            ],
            name="fk_ingestion_job_stages_job",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("stage_key = 'ingestion'", name="ck_ingestion_job_stages_key"),
        sa.CheckConstraint("sequence_no = 1", name="ck_ingestion_job_stages_sequence"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_ingestion_job_stages_attempt_count"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', "
            "'cancelled', 'timed_out')",
            name="ck_ingestion_job_stages_status",
        ),
        sa.CheckConstraint(
            "(status IN ('retry_wait', 'failed', 'timed_out') AND failure_stage IS NOT NULL "
            "AND error_code IS NOT NULL AND error_message IS NOT NULL) OR "
            "(status NOT IN ('retry_wait', 'failed', 'timed_out') AND failure_stage IS NULL "
            "AND error_code IS NULL AND error_message IS NULL)",
            name="ck_ingestion_job_stages_failure",
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed', 'cancelled', 'timed_out') "
            "AND completed_at IS NOT NULL) OR "
            "(status NOT IN ('succeeded', 'failed', 'cancelled', 'timed_out') "
            "AND completed_at IS NULL)",
            name="ck_ingestion_job_stages_completed_at",
        ),
        schema=schema,
    )


def _create_job_attempts(schema: str) -> None:
    op.create_table(
        "ingestion_job_attempts",
        sa.Column("job_attempt_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_stage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("worker_id", sa.String(255), nullable=False),
        sa.Column("initiated_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("traceparent", sa.String(55), nullable=False),
        sa.Column("lease_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_stage", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "job_stage_id",
            "generation",
            "attempt_no",
            name="uq_ingestion_job_attempts_stage_generation_no",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "job_stage_id"],
            [
                f"{schema}.ingestion_job_stages.workspace_id",
                f"{schema}.ingestion_job_stages.job_stage_id",
            ],
            name="fk_ingestion_job_attempts_stage",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "ingestion_job_id"],
            [
                f"{schema}.ingestion_jobs.workspace_id",
                f"{schema}.ingestion_jobs.ingestion_job_id",
            ],
            name="fk_ingestion_job_attempts_job",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("generation >= 0", name="ck_ingestion_job_attempts_generation"),
        sa.CheckConstraint("attempt_no >= 1", name="ck_ingestion_job_attempts_number"),
        sa.CheckConstraint(
            "trigger IN ('automatic', 'automatic_retry', 'lease_recovery', "
            "'manual_recovery', 'legacy_backfill')",
            name="ck_ingestion_job_attempts_trigger",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'retry_wait', 'failed', 'cancelled', 'timed_out')",
            name="ck_ingestion_job_attempts_status",
        ),
        sa.CheckConstraint(
            "lease_expires_at > lease_started_at AND started_at >= lease_started_at",
            name="ck_ingestion_job_attempts_lease",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND completed_at IS NULL) OR "
            "(status <> 'running' AND completed_at IS NOT NULL)",
            name="ck_ingestion_job_attempts_completed_at",
        ),
        sa.CheckConstraint(
            "(status IN ('retry_wait', 'failed', 'timed_out') AND failure_stage IS NOT NULL "
            "AND error_code IS NOT NULL AND error_message IS NOT NULL) OR "
            "(status NOT IN ('retry_wait', 'failed', 'timed_out') AND failure_stage IS NULL "
            "AND error_code IS NULL AND error_message IS NULL)",
            name="ck_ingestion_job_attempts_failure",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_ingestion_job_attempts_job_history",
        "ingestion_job_attempts",
        ["workspace_id", "ingestion_job_id", "generation", "attempt_no"],
        schema=schema,
    )
    op.create_index(
        "uq_ingestion_job_attempts_active_stage",
        "ingestion_job_attempts",
        ["job_stage_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
        schema=schema,
    )


def _backfill_operational_facts(schema: str) -> None:
    # 阶段 1 只有当前尝试计数，无法伪造完整历史；迁移只生成一条明确标记的汇总 Attempt。
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".ingestion_job_stages (
                job_stage_id, workspace_id, ingestion_job_id, stage_key, sequence_no,
                status, attempt_count, started_at, completed_at, failure_stage,
                error_code, error_message, created_at, updated_at
            )
            SELECT
                ingestion_job_id, workspace_id, ingestion_job_id, 'ingestion', 1,
                status, attempt_count, started_at, completed_at, failure_stage,
                error_code, error_message, created_at, updated_at
            FROM "{schema}".ingestion_jobs
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".ingestion_job_attempts (
                job_attempt_id, workspace_id, job_stage_id, ingestion_job_id,
                generation, attempt_no, trigger, status, worker_id,
                initiated_by_actor_id, trace_id, traceparent, lease_started_at,
                lease_expires_at, started_at, completed_at, failure_stage,
                error_code, error_message, created_at
            )
            SELECT
                md5(ingestion_job_id::text || ':legacy:' || attempt_count::text)::uuid,
                workspace_id,
                ingestion_job_id,
                ingestion_job_id,
                manual_retry_count,
                attempt_count,
                'legacy_backfill',
                status,
                COALESCE(claimed_by, 'legacy-stage-1'),
                COALESCE(last_retried_by_actor_id, requested_by_actor_id),
                trace_id,
                traceparent,
                COALESCE(started_at, created_at),
                CASE
                    WHEN claim_until > COALESCE(started_at, created_at) THEN claim_until
                    ELSE COALESCE(started_at, created_at) + INTERVAL '1 second'
                END,
                COALESCE(started_at, created_at),
                CASE WHEN status = 'running' THEN NULL ELSE updated_at END,
                failure_stage,
                error_code,
                error_message,
                COALESCE(started_at, created_at)
            FROM "{schema}".ingestion_jobs
            WHERE attempt_count > 0
            """
        )
    )


def _protect_attempt_history(schema: str) -> None:
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".protect_ingestion_attempt_history()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'ingestion job attempts are immutable';
                END IF;
                IF OLD.status <> 'running' THEN
                    RAISE EXCEPTION 'completed ingestion job attempts are immutable';
                END IF;
                IF NEW.status = 'running' OR NEW.completed_at IS NULL THEN
                    RAISE EXCEPTION
                        'ingestion job attempt must transition directly to a terminal status';
                END IF;
                IF ROW(
                    NEW.job_attempt_id, NEW.workspace_id, NEW.job_stage_id,
                    NEW.ingestion_job_id, NEW.generation, NEW.attempt_no,
                    NEW.trigger, NEW.worker_id, NEW.initiated_by_actor_id,
                    NEW.trace_id, NEW.traceparent, NEW.lease_started_at,
                    NEW.lease_expires_at, NEW.started_at, NEW.created_at
                ) IS DISTINCT FROM ROW(
                    OLD.job_attempt_id, OLD.workspace_id, OLD.job_stage_id,
                    OLD.ingestion_job_id, OLD.generation, OLD.attempt_no,
                    OLD.trigger, OLD.worker_id, OLD.initiated_by_actor_id,
                    OLD.trace_id, OLD.traceparent, OLD.lease_started_at,
                    OLD.lease_expires_at, OLD.started_at, OLD.created_at
                ) THEN
                    RAISE EXCEPTION 'ingestion job attempt identity is immutable';
                END IF;
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER protect_ingestion_job_attempts
            BEFORE UPDATE OR DELETE ON "{schema}".ingestion_job_attempts
            FOR EACH ROW EXECUTE FUNCTION "{schema}".protect_ingestion_attempt_history();
            """
        )
    )


def _restore_stage_one_ingestion_jobs(schema: str) -> None:
    op.execute(
        sa.text(
            f"""
            UPDATE "{schema}".ingestion_jobs
            SET status = 'failed',
                failure_stage = 'worker',
                error_code = CASE
                    WHEN status = 'cancelled' THEN 'INGESTION_JOB_CANCELLED'
                    ELSE COALESCE(error_code, 'INGESTION_WORKER_LEASE_EXPIRED')
                END,
                error_message = CASE
                    WHEN status = 'cancelled' THEN '任务在降级前已取消'
                    ELSE COALESCE(error_message, '任务在降级前已超时')
                END,
                completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                claimed_by = NULL,
                claim_until = NULL,
                active_attempt_id = NULL,
                cancelled_by_actor_id = NULL,
                cancelled_at = NULL
            WHERE status IN ('cancelled', 'timed_out')
            """
        )
    )
    for constraint in (
        "ck_ingestion_jobs_cancellation",
        "ck_ingestion_jobs_status",
        "ck_ingestion_jobs_failure",
        "ck_ingestion_jobs_completed_at",
        "ck_ingestion_jobs_manual_retry",
        "ck_ingestion_jobs_claim",
    ):
        op.drop_constraint(constraint, "ingestion_jobs", type_="check", schema=schema)
    op.drop_constraint(
        "uq_ingestion_jobs_workspace_job",
        "ingestion_jobs",
        type_="unique",
        schema=schema,
    )
    op.drop_column("ingestion_jobs", "cancelled_at", schema=schema)
    op.drop_column("ingestion_jobs", "cancelled_by_actor_id", schema=schema)
    op.drop_column("ingestion_jobs", "active_attempt_id", schema=schema)
    op.create_check_constraint(
        "ck_ingestion_jobs_claim",
        "ingestion_jobs",
        "(status = 'running' AND claimed_by IS NOT NULL AND claim_until IS NOT NULL "
        "AND started_at IS NOT NULL) OR "
        "(status <> 'running' AND claimed_by IS NULL AND claim_until IS NULL)",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_ingestion_jobs_status",
        "ingestion_jobs",
        "status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed')",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_ingestion_jobs_failure",
        "ingestion_jobs",
        "(status IN ('retry_wait', 'failed') AND failure_stage IS NOT NULL "
        "AND error_code IS NOT NULL AND error_message IS NOT NULL) OR "
        "(status NOT IN ('retry_wait', 'failed') AND failure_stage IS NULL "
        "AND error_code IS NULL AND error_message IS NULL)",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_ingestion_jobs_completed_at",
        "ingestion_jobs",
        "(status IN ('succeeded', 'failed') AND completed_at IS NOT NULL) OR "
        "(status NOT IN ('succeeded', 'failed') AND completed_at IS NULL)",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_ingestion_jobs_manual_retry",
        "ingestion_jobs",
        "manual_retry_count >= 0 AND "
        "((manual_retry_count = 0 AND last_retried_by_actor_id IS NULL "
        "AND last_retried_at IS NULL) OR "
        "(manual_retry_count > 0 AND last_retried_by_actor_id IS NOT NULL "
        "AND last_retried_at IS NOT NULL))",
        schema=schema,
    )
