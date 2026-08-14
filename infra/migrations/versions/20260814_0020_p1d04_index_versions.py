"""建立 P1D-04 可追溯 Chunk、索引版本和原子发布指针。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0020"
down_revision: str | None = "20260814_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _extend_retrieval_chunks(schema)
    _create_index_versions(schema)
    _create_document_index_publications(schema)
    _backfill_succeeded_ingestion_jobs(schema)


def downgrade() -> None:
    schema = _schema()
    op.drop_table("document_index_publications", schema=schema)
    op.drop_table("index_versions", schema=schema)
    op.drop_constraint(
        "ck_retrieval_chunks_traceability",
        "retrieval_chunks",
        type_="check",
        schema=schema,
    )
    for column in (
        "created_at",
        "ocr_used",
        "parser_name",
        "parsed_content_hash",
        "permission_labels",
        "source_id",
        "ingestion_job_id",
    ):
        op.drop_column("retrieval_chunks", column, schema=schema)


def _extend_retrieval_chunks(schema: str) -> None:
    op.add_column(
        "retrieval_chunks",
        sa.Column("ingestion_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "retrieval_chunks",
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "retrieval_chunks",
        sa.Column(
            "permission_labels",
            postgresql.ARRAY(sa.String(80)),
            nullable=False,
            server_default="{}",
        ),
        schema=schema,
    )
    op.add_column(
        "retrieval_chunks",
        sa.Column("parsed_content_hash", sa.String(64), nullable=True),
        schema=schema,
    )
    op.add_column(
        "retrieval_chunks",
        sa.Column("parser_name", sa.String(255), nullable=True),
        schema=schema,
    )
    op.add_column(
        "retrieval_chunks",
        sa.Column("ocr_used", sa.Boolean(), nullable=True),
        schema=schema,
    )
    op.add_column(
        "retrieval_chunks",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        schema=schema,
    )
    # P0 合成 Chunk 允许整组追溯字段为空；正式 P1D-04 Chunk 必须整组完整。
    op.create_check_constraint(
        "ck_retrieval_chunks_traceability",
        "retrieval_chunks",
        "(ingestion_job_id IS NULL AND source_id IS NULL AND parsed_content_hash IS NULL "
        "AND parser_name IS NULL AND ocr_used IS NULL) OR "
        "(ingestion_job_id IS NOT NULL AND source_id IS NOT NULL "
        "AND parsed_content_hash ~ '^[0-9a-f]{64}$' "
        "AND parser_name IS NOT NULL AND ocr_used IS NOT NULL)",
        schema=schema,
    )


def _create_index_versions(schema: str) -> None:
    op.create_table(
        "index_versions",
        sa.Column("index_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("build_no", sa.Integer(), nullable=False),
        sa.Column("artifact_object_key", sa.String(1024), nullable=False),
        sa.Column("source_content_hash", sa.String(64), nullable=False),
        sa.Column("parsed_content_hash", sa.String(64), nullable=False),
        sa.Column("chunker_version", sa.String(128), nullable=False),
        sa.Column("embedding_model_version", sa.String(255), nullable=False),
        sa.Column("tokenizer_version", sa.String(128), nullable=False),
        sa.Column(
            "department_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column("visibility", sa.String(32), nullable=False),
        sa.Column("security_level", sa.String(32), nullable=False),
        sa.Column(
            "permission_labels",
            postgresql.ARRAY(sa.String(80)),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by", sa.String(255), nullable=True),
        sa.Column("claim_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("failure_stage", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "ingestion_job_id",
            "build_no",
            name="uq_index_versions_ingestion_build",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "document_id",
            "index_version_id",
            name="uq_index_versions_workspace_document_version",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "document_id",
            "document_version_id",
            "index_version_id",
            name="uq_index_versions_document_source_version",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_job_id"],
            [f"{schema}.ingestion_jobs.ingestion_job_id"],
            name="fk_index_versions_ingestion_job",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "knowledge_base_id"],
            [
                f"{schema}.knowledge_bases.workspace_id",
                f"{schema}.knowledge_bases.knowledge_base_id",
            ],
            name="fk_index_versions_knowledge_base",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id", "document_version_id"],
            [
                f"{schema}.document_versions.workspace_id",
                f"{schema}.document_versions.document_id",
                f"{schema}.document_versions.document_version_id",
            ],
            name="fk_index_versions_document_version",
        ),
        sa.CheckConstraint("build_no >= 1", name="ck_index_versions_build_no"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'ready', 'active', 'retired', 'failed')",
            name="ck_index_versions_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
            name="ck_index_versions_attempts",
        ),
        sa.CheckConstraint(
            "source_content_hash ~ '^[0-9a-f]{64}$' AND parsed_content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_index_versions_hashes",
        ),
        sa.CheckConstraint(
            "visibility IN ('private', 'workspace', 'departments')",
            name="ck_index_versions_visibility",
        ),
        sa.CheckConstraint(
            "security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
            name="ck_index_versions_security_level",
        ),
        sa.CheckConstraint(
            "(visibility = 'departments' AND cardinality(department_ids) > 0) OR "
            "(visibility <> 'departments' AND cardinality(department_ids) = 0)",
            name="ck_index_versions_department_scope",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND claimed_by IS NOT NULL AND claim_until IS NOT NULL "
            "AND started_at IS NOT NULL) OR "
            "(status <> 'running' AND claimed_by IS NULL AND claim_until IS NULL)",
            name="ck_index_versions_claim",
        ),
        sa.CheckConstraint(
            "(status IN ('ready', 'active', 'retired') AND completed_at IS NOT NULL "
            "AND chunk_count >= 1) OR "
            "(status NOT IN ('ready', 'active', 'retired') AND chunk_count IS NULL)",
            name="ck_index_versions_result",
        ),
        sa.CheckConstraint(
            "(status IN ('retry_wait', 'failed') AND failure_stage IS NOT NULL "
            "AND error_code IS NOT NULL AND error_message IS NOT NULL) OR "
            "(status NOT IN ('retry_wait', 'failed') AND failure_stage IS NULL "
            "AND error_code IS NULL AND error_message IS NULL)",
            name="ck_index_versions_failure",
        ),
        sa.CheckConstraint(
            "failure_stage IS NULL OR "
            "failure_stage IN ('artifact', 'chunk', 'embedding', 'index', 'worker')",
            name="ck_index_versions_failure_stage",
        ),
        sa.CheckConstraint(
            "(status IN ('ready', 'active', 'retired', 'failed') "
            "AND completed_at IS NOT NULL) OR "
            "(status IN ('queued', 'running', 'retry_wait') AND completed_at IS NULL)",
            name="ck_index_versions_completed_at",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND activated_at IS NOT NULL) OR (status <> 'active')",
            name="ck_index_versions_activated_at",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_index_versions_claim",
        "index_versions",
        ["status", "available_at", "created_at"],
        schema=schema,
    )
    op.create_index(
        "ix_index_versions_lease",
        "index_versions",
        ["status", "claim_until"],
        schema=schema,
    )
    op.create_index(
        "ix_index_versions_workspace_document",
        "index_versions",
        ["workspace_id", "document_id", "document_version_id", "build_no"],
        schema=schema,
    )
    op.create_index(
        "uq_index_versions_active_document",
        "index_versions",
        ["workspace_id", "document_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        schema=schema,
    )


def _create_document_index_publications(schema: str) -> None:
    op.create_table(
        "document_index_publications",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("index_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            [f"{schema}.documents.workspace_id", f"{schema}.documents.document_id"],
            name="fk_document_index_publications_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id", "document_version_id", "index_version_id"],
            [
                f"{schema}.index_versions.workspace_id",
                f"{schema}.index_versions.document_id",
                f"{schema}.index_versions.document_version_id",
                f"{schema}.index_versions.index_version_id",
            ],
            name="fk_document_index_publications_index_version",
        ),
        schema=schema,
    )


def _backfill_succeeded_ingestion_jobs(schema: str) -> None:
    # 使用 ingestion_job_id 作为首个索引版本 ID，保证历史回填幂等且可追溯。
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".index_versions (
                index_version_id, workspace_id, knowledge_base_id, document_id,
                document_version_id, ingestion_job_id, source_id, build_no,
                artifact_object_key, source_content_hash, parsed_content_hash,
                chunker_version, embedding_model_version, tokenizer_version,
                department_ids, visibility, security_level, permission_labels,
                status, attempt_count, max_attempts, available_at, created_at, updated_at
            )
            SELECT
                job.ingestion_job_id, job.workspace_id, job.knowledge_base_id,
                job.document_id, job.document_version_id, job.ingestion_job_id,
                job.source_id, 1, job.artifact_object_key, job.source_content_hash,
                job.parsed_content_hash, 'structural-char-v1',
                'deterministic-hash-1024-v1', 'cjk-bigram-v1',
                document.department_ids, document.visibility, document.security_level,
                document.permission_labels, 'queued', 0, 3, CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM "{schema}".ingestion_jobs AS job
            JOIN "{schema}".documents AS document
              ON document.workspace_id = job.workspace_id
             AND document.document_id = job.document_id
            WHERE job.status = 'succeeded'
              AND document.status = 'active'
            ON CONFLICT (ingestion_job_id, build_no) DO NOTHING
            """
        )
    )
