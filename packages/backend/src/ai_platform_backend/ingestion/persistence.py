from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from ai_platform_backend.database import SCHEMA_TOKEN

metadata = MetaData(schema=SCHEMA_TOKEN)

ingestion_jobs = Table(
    "ingestion_jobs",
    metadata,
    Column("ingestion_job_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("knowledge_base_id", UUID(as_uuid=True), nullable=False),
    Column("document_id", UUID(as_uuid=True), nullable=False),
    Column("document_version_id", UUID(as_uuid=True), nullable=False),
    Column("source_id", UUID(as_uuid=True), nullable=False),
    Column("source_name", String(255), nullable=False),
    Column("source_object_key", String(1024), nullable=False),
    Column("source_media_type", String(255), nullable=False),
    Column("source_content_hash", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("claimed_by", String(255), nullable=True),
    Column("claim_until", DateTime(timezone=True), nullable=True),
    Column("requested_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("traceparent", String(55), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("failure_stage", String(32), nullable=True),
    Column("error_code", String(128), nullable=True),
    Column("error_message", String(1000), nullable=True),
    Column("artifact_object_key", String(1024), nullable=True),
    Column("parsed_content_hash", String(64), nullable=True),
    Column("parser_name", String(255), nullable=True),
    Column("ocr_used", Boolean, nullable=True),
    Column("page_count", Integer, nullable=True),
    Column("block_count", Integer, nullable=True),
    Column("manual_retry_count", Integer, nullable=False, server_default="0"),
    Column("last_retried_by_actor_id", UUID(as_uuid=True), nullable=True),
    Column("last_retried_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "workspace_id",
        "document_version_id",
        name="uq_ingestion_jobs_document_version",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "knowledge_base_id"],
        [
            f"{SCHEMA_TOKEN}.knowledge_bases.workspace_id",
            f"{SCHEMA_TOKEN}.knowledge_bases.knowledge_base_id",
        ],
        name="fk_ingestion_jobs_knowledge_base",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "document_id", "document_version_id"],
        [
            f"{SCHEMA_TOKEN}.document_versions.workspace_id",
            f"{SCHEMA_TOKEN}.document_versions.document_id",
            f"{SCHEMA_TOKEN}.document_versions.document_version_id",
        ],
        name="fk_ingestion_jobs_document_version",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "document_version_id", "source_id"],
        [
            f"{SCHEMA_TOKEN}.document_sources.workspace_id",
            f"{SCHEMA_TOKEN}.document_sources.document_version_id",
            f"{SCHEMA_TOKEN}.document_sources.source_id",
        ],
        name="fk_ingestion_jobs_source",
    ),
    CheckConstraint(
        "status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed')",
        name="ck_ingestion_jobs_status",
    ),
    CheckConstraint(
        "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
        name="ck_ingestion_jobs_attempts",
    ),
    CheckConstraint(
        "source_content_hash ~ '^[0-9a-f]{64}$'",
        name="ck_ingestion_jobs_source_hash",
    ),
    CheckConstraint(
        "(status = 'running' AND claimed_by IS NOT NULL AND claim_until IS NOT NULL "
        "AND started_at IS NOT NULL) OR "
        "(status <> 'running' AND claimed_by IS NULL AND claim_until IS NULL)",
        name="ck_ingestion_jobs_claim",
    ),
    CheckConstraint(
        "(status = 'succeeded' AND completed_at IS NOT NULL "
        "AND artifact_object_key IS NOT NULL "
        "AND parsed_content_hash ~ '^[0-9a-f]{64}$' AND parser_name IS NOT NULL "
        "AND ocr_used IS NOT NULL AND page_count >= 1 AND block_count >= 1) OR "
        "(status <> 'succeeded' AND artifact_object_key IS NULL "
        "AND parsed_content_hash IS NULL AND parser_name IS NULL AND ocr_used IS NULL "
        "AND page_count IS NULL AND block_count IS NULL)",
        name="ck_ingestion_jobs_result",
    ),
    CheckConstraint(
        "(status IN ('retry_wait', 'failed') AND failure_stage IS NOT NULL "
        "AND error_code IS NOT NULL AND error_message IS NOT NULL) OR "
        "(status NOT IN ('retry_wait', 'failed') AND failure_stage IS NULL AND error_code IS NULL "
        "AND error_message IS NULL)",
        name="ck_ingestion_jobs_failure",
    ),
    CheckConstraint(
        "failure_stage IS NULL OR "
        "failure_stage IN ('source', 'parse', 'ocr', 'artifact', 'worker')",
        name="ck_ingestion_jobs_failure_stage",
    ),
    CheckConstraint(
        "(status IN ('succeeded', 'failed') AND completed_at IS NOT NULL) OR "
        "(status NOT IN ('succeeded', 'failed') AND completed_at IS NULL)",
        name="ck_ingestion_jobs_completed_at",
    ),
    CheckConstraint(
        "manual_retry_count >= 0 AND "
        "((manual_retry_count = 0 AND last_retried_by_actor_id IS NULL "
        "AND last_retried_at IS NULL) OR "
        "(manual_retry_count > 0 AND last_retried_by_actor_id IS NOT NULL "
        "AND last_retried_at IS NOT NULL))",
        name="ck_ingestion_jobs_manual_retry",
    ),
)

Index(
    "ix_ingestion_jobs_claim",
    ingestion_jobs.c.status,
    ingestion_jobs.c.available_at,
    ingestion_jobs.c.created_at,
)
Index(
    "ix_ingestion_jobs_lease",
    ingestion_jobs.c.status,
    ingestion_jobs.c.claim_until,
)
Index(
    "ix_ingestion_jobs_workspace_document",
    ingestion_jobs.c.workspace_id,
    ingestion_jobs.c.document_id,
    ingestion_jobs.c.created_at,
)
