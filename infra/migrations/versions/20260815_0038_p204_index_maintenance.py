"""建立 P2-04 索引巡检、差异修复和全量重建证据表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260815_0038"
down_revision: str | None = "20260815_0037"
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
        "index_maintenance_runs",
        sa.Column("maintenance_run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_kind", sa.String(32), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requested_by_actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scanned_document_count", sa.Integer(), nullable=False),
        sa.Column("inconsistency_count", sa.Integer(), nullable=False),
        sa.Column("repaired_count", sa.Integer(), nullable=False),
        sa.Column("rebuild_queued_count", sa.Integer(), nullable=False),
        sa.Column("cleaned_chunk_count", sa.Integer(), nullable=False),
        sa.Column("result_digest", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "run_kind IN ('inspection', 'full_rebuild', 'cleanup')",
            name="ck_index_maintenance_runs_kind",
        ),
        sa.CheckConstraint("status = 'completed'", name="ck_index_maintenance_runs_status"),
        sa.CheckConstraint(
            "scanned_document_count >= 0 AND inconsistency_count >= 0 "
            "AND repaired_count >= 0 AND rebuild_queued_count >= 0 "
            "AND cleaned_chunk_count >= 0",
            name="ck_index_maintenance_runs_counts",
        ),
        sa.CheckConstraint(
            "completed_at >= started_at",
            name="ck_index_maintenance_runs_time",
        ),
        sa.CheckConstraint(
            "result_digest ~ '^[0-9a-f]{64}$'",
            name="ck_index_maintenance_runs_digest",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_index_maintenance_runs_kind_time",
        "index_maintenance_runs",
        ["run_kind", "completed_at"],
        schema=schema,
    )
    op.create_table(
        "index_inspection_findings",
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("maintenance_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("finding_code", sa.String(64), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("index_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolution", sa.String(32), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["maintenance_run_id"],
            [f"{schema}.index_maintenance_runs.maintenance_run_id"],
            name="fk_index_inspection_findings_run",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "finding_code IN ('INDEX_PUBLICATION_MISSING', "
            "'INDEX_PUBLICATION_VERSION_MISMATCH', 'INDEX_VERSION_STATE_MISMATCH', "
            "'INDEX_SOURCE_FACT_MISMATCH', 'INDEX_CHUNK_COUNT_MISMATCH', "
            "'INDEX_CHUNK_REFERENCE_MISMATCH', 'INDEX_UNEXPECTED_ACTIVE_CHUNK', "
            "'INDEX_ORPHAN_ACTIVE_VERSION')",
            name="ck_index_inspection_findings_code",
        ),
        sa.CheckConstraint(
            "resolution IN ('unresolved', 'repaired', 'rebuild_queued')",
            name="ck_index_inspection_findings_resolution",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_index_inspection_findings_run",
        "index_inspection_findings",
        ["maintenance_run_id", "finding_code"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "ix_index_inspection_findings_run",
        table_name="index_inspection_findings",
        schema=schema,
    )
    op.drop_table("index_inspection_findings", schema=schema)
    op.drop_index(
        "ix_index_maintenance_runs_kind_time",
        table_name="index_maintenance_runs",
        schema=schema,
    )
    op.drop_table("index_maintenance_runs", schema=schema)
