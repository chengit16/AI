"""建立 P1D-03 入库任务状态、租约和失败定位事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0018"
down_revision: str | None = "20260814_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    op.create_unique_constraint(
        "uq_document_sources_workspace_version_source",
        "document_sources",
        ["workspace_id", "document_version_id", "source_id"],
        schema=schema,
    )
    op.create_table(
        "ingestion_jobs",
        sa.Column("ingestion_job_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("source_object_key", sa.String(1024), nullable=False),
        sa.Column("source_media_type", sa.String(255), nullable=False),
        sa.Column("source_content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by", sa.String(255), nullable=True),
        sa.Column("claim_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("traceparent", sa.String(55), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_stage", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.String(1000), nullable=True),
        sa.Column("artifact_object_key", sa.String(1024), nullable=True),
        sa.Column("parsed_content_hash", sa.String(64), nullable=True),
        sa.Column("parser_name", sa.String(255), nullable=True),
        sa.Column("ocr_used", sa.Boolean(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("block_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "document_version_id",
            name="uq_ingestion_jobs_document_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "knowledge_base_id"],
            [
                f"{schema}.knowledge_bases.workspace_id",
                f"{schema}.knowledge_bases.knowledge_base_id",
            ],
            name="fk_ingestion_jobs_knowledge_base",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id", "document_version_id"],
            [
                f"{schema}.document_versions.workspace_id",
                f"{schema}.document_versions.document_id",
                f"{schema}.document_versions.document_version_id",
            ],
            name="fk_ingestion_jobs_document_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_version_id", "source_id"],
            [
                f"{schema}.document_sources.workspace_id",
                f"{schema}.document_sources.document_version_id",
                f"{schema}.document_sources.source_id",
            ],
            name="fk_ingestion_jobs_source",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed')",
            name="ck_ingestion_jobs_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
            name="ck_ingestion_jobs_attempts",
        ),
        sa.CheckConstraint(
            "source_content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_ingestion_jobs_source_hash",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND claimed_by IS NOT NULL AND claim_until IS NOT NULL "
            "AND started_at IS NOT NULL) OR "
            "(status <> 'running' AND claimed_by IS NULL AND claim_until IS NULL)",
            name="ck_ingestion_jobs_claim",
        ),
        sa.CheckConstraint(
            "(status = 'succeeded' AND completed_at IS NOT NULL "
            "AND artifact_object_key IS NOT NULL "
            "AND parsed_content_hash ~ '^[0-9a-f]{64}$' AND parser_name IS NOT NULL "
            "AND ocr_used IS NOT NULL AND page_count >= 1 AND block_count >= 1) OR "
            "(status <> 'succeeded' AND artifact_object_key IS NULL "
            "AND parsed_content_hash IS NULL AND parser_name IS NULL AND ocr_used IS NULL "
            "AND page_count IS NULL AND block_count IS NULL)",
            name="ck_ingestion_jobs_result",
        ),
        sa.CheckConstraint(
            "(status IN ('retry_wait', 'failed') AND failure_stage IS NOT NULL "
            "AND error_code IS NOT NULL AND error_message IS NOT NULL) OR "
            "(status NOT IN ('retry_wait', 'failed') AND failure_stage IS NULL "
            "AND error_code IS NULL "
            "AND error_message IS NULL)",
            name="ck_ingestion_jobs_failure",
        ),
        sa.CheckConstraint(
            "failure_stage IS NULL OR "
            "failure_stage IN ('source', 'parse', 'ocr', 'artifact', 'worker')",
            name="ck_ingestion_jobs_failure_stage",
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed') AND completed_at IS NOT NULL) OR "
            "(status NOT IN ('succeeded', 'failed') AND completed_at IS NULL)",
            name="ck_ingestion_jobs_completed_at",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_ingestion_jobs_claim",
        "ingestion_jobs",
        ["status", "available_at", "created_at"],
        schema=schema,
    )
    op.create_index(
        "ix_ingestion_jobs_lease",
        "ingestion_jobs",
        ["status", "claim_until"],
        schema=schema,
    )
    op.create_index(
        "ix_ingestion_jobs_workspace_document",
        "ingestion_jobs",
        ["workspace_id", "document_id", "created_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_table("ingestion_jobs", schema=schema)
    op.drop_constraint(
        "uq_document_sources_workspace_version_source",
        "document_sources",
        type_="unique",
        schema=schema,
    )
