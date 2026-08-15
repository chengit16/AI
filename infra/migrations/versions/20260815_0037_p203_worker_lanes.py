"""建立 P2-03 Worker Lane、索引两阶段租约和死信恢复事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260815_0037"
down_revision: str | None = "20260815_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _add_ingestion_lanes(schema)
    _add_index_lanes(schema)


def downgrade() -> None:
    schema = _schema()
    _restore_stage_two_index(schema)
    _remove_ingestion_lanes(schema)


def _add_ingestion_lanes(schema: str) -> None:
    op.add_column(
        "ingestion_jobs",
        sa.Column("processing_lane", sa.String(32), nullable=True),
        schema=schema,
    )
    # PDF 和图片始终交给 OCR Lane，避免 Tika/Tesseract 资源占满普通解析进程。
    op.execute(
        sa.text(
            f"""
            UPDATE "{schema}".ingestion_jobs
               SET processing_lane = CASE
                   WHEN lower(source_name) ~ '\\.(pdf|png|jpe?g|tiff?)$' THEN 'ocr'
                   ELSE 'parsing'
               END
            """
        )
    )
    op.alter_column("ingestion_jobs", "processing_lane", nullable=False, schema=schema)
    op.create_check_constraint(
        "ck_ingestion_jobs_processing_lane",
        "ingestion_jobs",
        "processing_lane IN ('parsing', 'ocr')",
        schema=schema,
    )
    op.drop_constraint(
        "ck_ingestion_job_stages_key",
        "ingestion_job_stages",
        type_="check",
        schema=schema,
    )
    op.execute(
        sa.text(
            f"""
            UPDATE "{schema}".ingestion_job_stages AS stage
               SET stage_key = job.processing_lane
              FROM "{schema}".ingestion_jobs AS job
             WHERE job.ingestion_job_id = stage.ingestion_job_id
            """
        )
    )
    op.create_check_constraint(
        "ck_ingestion_job_stages_key",
        "ingestion_job_stages",
        "stage_key IN ('parsing', 'ocr')",
        schema=schema,
    )


def _add_index_lanes(schema: str) -> None:
    for constraint in (
        "ck_index_versions_status",
        "ck_index_versions_attempts",
        "ck_index_versions_claim",
        "ck_index_versions_failure",
        "ck_index_versions_completed_at",
    ):
        op.drop_constraint(constraint, "index_versions", type_="check", schema=schema)

    columns = (
        sa.Column("processing_lane", sa.String(32), nullable=True),
        sa.Column("embedding_attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("indexing_attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("staged_chunk_count", sa.Integer(), nullable=True),
        sa.Column("manual_recovery_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_recovered_by_actor_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("last_recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in columns:
        op.add_column("index_versions", column, schema=schema)

    # 旧构建只有单阶段事实；迁移将完成态视作索引阶段，活动态视作 Embedding 阶段。
    op.execute(
        sa.text(
            f"""
            UPDATE "{schema}".index_versions
               SET processing_lane = CASE
                       WHEN status IN ('ready', 'active', 'retired') THEN 'indexing'
                       ELSE 'embedding'
                   END,
                   embedding_attempt_count = CASE
                       WHEN status IN ('ready', 'active', 'retired') THEN 0
                       ELSE attempt_count
                   END,
                   indexing_attempt_count = CASE
                       WHEN status IN ('ready', 'active', 'retired') THEN attempt_count
                       ELSE 0
                   END,
                   active_attempt_id = CASE
                       WHEN status = 'running'
                       THEN md5(index_version_id::text || ':legacy:' || attempt_count::text)::uuid
                       ELSE NULL
                   END,
                   staged_chunk_count = CASE
                       WHEN status IN ('ready', 'active', 'retired') THEN chunk_count
                       ELSE NULL
                   END,
                   status = CASE status
                       WHEN 'running' THEN 'embedding_running'
                       WHEN 'retry_wait' THEN 'embedding_retry_wait'
                       ELSE status
                   END
            """
        )
    )
    op.alter_column("index_versions", "processing_lane", nullable=False, schema=schema)
    for column_name in (
        "embedding_attempt_count",
        "indexing_attempt_count",
        "manual_recovery_count",
    ):
        op.alter_column(
            "index_versions",
            column_name,
            server_default=None,
            schema=schema,
        )
    _create_index_lane_constraints(schema)


def _create_index_lane_constraints(schema: str) -> None:
    checks = {
        "ck_index_versions_status": (
            "status IN ('queued', 'embedding_running', 'embedding_retry_wait', "
            "'index_queued', 'index_running', 'index_retry_wait', 'ready', 'active', "
            "'retired', 'failed', 'dead_letter')"
        ),
        "ck_index_versions_processing_lane": "processing_lane IN ('embedding', 'indexing')",
        "ck_index_versions_attempts": (
            "attempt_count >= 0 AND embedding_attempt_count >= 0 "
            "AND indexing_attempt_count >= 0 AND max_attempts >= 1 "
            "AND attempt_count <= max_attempts "
            "AND embedding_attempt_count <= max_attempts "
            "AND indexing_attempt_count <= max_attempts"
        ),
        "ck_index_versions_claim": (
            "(status IN ('embedding_running', 'index_running') "
            "AND claimed_by IS NOT NULL AND claim_until IS NOT NULL "
            "AND active_attempt_id IS NOT NULL AND started_at IS NOT NULL) OR "
            "(status NOT IN ('embedding_running', 'index_running') "
            "AND claimed_by IS NULL AND claim_until IS NULL AND active_attempt_id IS NULL)"
        ),
        "ck_index_versions_failure": (
            "(status IN ('embedding_retry_wait', 'index_retry_wait', 'failed', 'dead_letter') "
            "AND failure_stage IS NOT NULL AND error_code IS NOT NULL "
            "AND error_message IS NOT NULL) OR "
            "(status NOT IN ('embedding_retry_wait', 'index_retry_wait', 'failed', "
            "'dead_letter') AND failure_stage IS NULL AND error_code IS NULL "
            "AND error_message IS NULL)"
        ),
        "ck_index_versions_completed_at": (
            "(status IN ('ready', 'active', 'retired', 'failed', 'dead_letter') "
            "AND completed_at IS NOT NULL) OR "
            "(status IN ('queued', 'embedding_running', 'embedding_retry_wait', "
            "'index_queued', 'index_running', 'index_retry_wait') AND completed_at IS NULL)"
        ),
        "ck_index_versions_staged_result": (
            "(staged_chunk_count >= 1 AND (status IN ('index_queued', 'index_running', "
            "'index_retry_wait', 'ready', 'active', 'retired') OR "
            "(status = 'dead_letter' AND processing_lane = 'indexing'))) OR "
            "(staged_chunk_count IS NULL AND (status NOT IN ('index_queued', 'index_running', "
            "'index_retry_wait', 'ready', 'active', 'retired', 'dead_letter') OR "
            "(status = 'dead_letter' AND processing_lane = 'embedding')))"
        ),
        "ck_index_versions_manual_recovery": (
            "manual_recovery_count BETWEEN 0 AND 3 AND "
            "((manual_recovery_count = 0 AND last_recovered_by_actor_id IS NULL "
            "AND last_recovered_at IS NULL) OR "
            "(manual_recovery_count > 0 AND last_recovered_by_actor_id IS NOT NULL "
            "AND last_recovered_at IS NOT NULL))"
        ),
        "ck_index_versions_dead_letter": (
            "(status = 'dead_letter' AND dead_lettered_at IS NOT NULL "
            "AND dead_lettered_at = completed_at) OR "
            "(status <> 'dead_letter' AND dead_lettered_at IS NULL)"
        ),
    }
    for name, condition in checks.items():
        op.create_check_constraint(name, "index_versions", condition, schema=schema)


def _restore_stage_two_index(schema: str) -> None:
    for constraint in (
        "ck_index_versions_dead_letter",
        "ck_index_versions_manual_recovery",
        "ck_index_versions_staged_result",
        "ck_index_versions_completed_at",
        "ck_index_versions_failure",
        "ck_index_versions_claim",
        "ck_index_versions_attempts",
        "ck_index_versions_processing_lane",
        "ck_index_versions_status",
    ):
        op.drop_constraint(constraint, "index_versions", type_="check", schema=schema)
    op.execute(
        sa.text(
            f"""
            UPDATE "{schema}".index_versions
               SET status = CASE status
                       WHEN 'embedding_running' THEN 'running'
                       WHEN 'index_running' THEN 'running'
                       WHEN 'embedding_retry_wait' THEN 'retry_wait'
                       WHEN 'index_retry_wait' THEN 'retry_wait'
                       WHEN 'index_queued' THEN 'queued'
                       WHEN 'dead_letter' THEN 'failed'
                       ELSE status
                   END,
                   claimed_by = CASE
                       WHEN status IN ('embedding_running', 'index_running') THEN claimed_by
                       ELSE NULL
                   END,
                   claim_until = CASE
                       WHEN status IN ('embedding_running', 'index_running') THEN claim_until
                       ELSE NULL
                   END,
                   completed_at = CASE
                       WHEN status = 'index_queued' THEN NULL
                       ELSE completed_at
                   END,
                   failure_stage = CASE
                       WHEN status = 'index_queued' THEN NULL
                       ELSE failure_stage
                   END,
                   error_code = CASE WHEN status = 'index_queued' THEN NULL ELSE error_code END,
                   error_message = CASE
                       WHEN status = 'index_queued' THEN NULL
                       ELSE error_message
                   END
            """
        )
    )
    for column in (
        "dead_lettered_at",
        "last_recovered_at",
        "last_recovered_by_actor_id",
        "manual_recovery_count",
        "staged_chunk_count",
        "active_attempt_id",
        "indexing_attempt_count",
        "embedding_attempt_count",
        "processing_lane",
    ):
        op.drop_column("index_versions", column, schema=schema)
    _create_legacy_index_constraints(schema)


def _create_legacy_index_constraints(schema: str) -> None:
    checks = {
        "ck_index_versions_status": (
            "status IN ('queued', 'running', 'retry_wait', 'ready', 'active', 'retired', 'failed')"
        ),
        "ck_index_versions_attempts": (
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts"
        ),
        "ck_index_versions_claim": (
            "(status = 'running' AND claimed_by IS NOT NULL AND claim_until IS NOT NULL "
            "AND started_at IS NOT NULL) OR "
            "(status <> 'running' AND claimed_by IS NULL AND claim_until IS NULL)"
        ),
        "ck_index_versions_failure": (
            "(status IN ('retry_wait', 'failed') AND failure_stage IS NOT NULL "
            "AND error_code IS NOT NULL AND error_message IS NOT NULL) OR "
            "(status NOT IN ('retry_wait', 'failed') AND failure_stage IS NULL "
            "AND error_code IS NULL AND error_message IS NULL)"
        ),
        "ck_index_versions_completed_at": (
            "(status IN ('ready', 'active', 'retired', 'failed') AND completed_at IS NOT NULL) OR "
            "(status IN ('queued', 'running', 'retry_wait') AND completed_at IS NULL)"
        ),
    }
    for name, condition in checks.items():
        op.create_check_constraint(name, "index_versions", condition, schema=schema)


def _remove_ingestion_lanes(schema: str) -> None:
    op.drop_constraint(
        "ck_ingestion_job_stages_key",
        "ingestion_job_stages",
        type_="check",
        schema=schema,
    )
    op.execute(sa.text(f"UPDATE \"{schema}\".ingestion_job_stages SET stage_key = 'ingestion'"))
    op.create_check_constraint(
        "ck_ingestion_job_stages_key",
        "ingestion_job_stages",
        "stage_key = 'ingestion'",
        schema=schema,
    )
    op.drop_constraint(
        "ck_ingestion_jobs_processing_lane",
        "ingestion_jobs",
        type_="check",
        schema=schema,
    )
    op.drop_column("ingestion_jobs", "processing_lane", schema=schema)
