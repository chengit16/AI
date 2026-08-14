"""定义索引版本、关键词、向量、全文和构建租约 PostgreSQL 表。"""

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID

from ai_platform_backend.database import SCHEMA_TOKEN

metadata = MetaData(schema=SCHEMA_TOKEN)

index_versions = Table(
    "index_versions",
    metadata,
    Column("index_version_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("knowledge_base_id", UUID(as_uuid=True), nullable=False),
    Column("document_id", UUID(as_uuid=True), nullable=False),
    Column("document_version_id", UUID(as_uuid=True), nullable=False),
    Column("ingestion_job_id", UUID(as_uuid=True), nullable=False),
    Column("source_id", UUID(as_uuid=True), nullable=False),
    Column("build_no", Integer, nullable=False),
    Column("artifact_object_key", String(1024), nullable=False),
    Column("source_content_hash", String(64), nullable=False),
    Column("parsed_content_hash", String(64), nullable=False),
    Column("chunker_version", String(128), nullable=False),
    Column("embedding_model_version", String(255), nullable=False),
    Column("tokenizer_version", String(128), nullable=False),
    Column("department_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
    Column("visibility", String(32), nullable=False),
    Column("security_level", String(32), nullable=False),
    Column("permission_labels", ARRAY(String(80)), nullable=False),
    Column("status", String(32), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("claimed_by", String(255), nullable=True),
    Column("claim_until", DateTime(timezone=True), nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("activated_at", DateTime(timezone=True), nullable=True),
    Column("chunk_count", Integer, nullable=True),
    Column("failure_stage", String(32), nullable=True),
    Column("error_code", String(128), nullable=True),
    Column("error_message", String(1000), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "ingestion_job_id",
        "build_no",
        name="uq_index_versions_ingestion_build",
    ),
    UniqueConstraint(
        "workspace_id",
        "document_id",
        "index_version_id",
        name="uq_index_versions_workspace_document_version",
    ),
    UniqueConstraint(
        "workspace_id",
        "document_id",
        "document_version_id",
        "index_version_id",
        name="uq_index_versions_document_source_version",
    ),
    ForeignKeyConstraint(
        ["ingestion_job_id"],
        [f"{SCHEMA_TOKEN}.ingestion_jobs.ingestion_job_id"],
        name="fk_index_versions_ingestion_job",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "knowledge_base_id"],
        [
            f"{SCHEMA_TOKEN}.knowledge_bases.workspace_id",
            f"{SCHEMA_TOKEN}.knowledge_bases.knowledge_base_id",
        ],
        name="fk_index_versions_knowledge_base",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "document_id", "document_version_id"],
        [
            f"{SCHEMA_TOKEN}.document_versions.workspace_id",
            f"{SCHEMA_TOKEN}.document_versions.document_id",
            f"{SCHEMA_TOKEN}.document_versions.document_version_id",
        ],
        name="fk_index_versions_document_version",
    ),
    CheckConstraint("build_no >= 1", name="ck_index_versions_build_no"),
    CheckConstraint(
        "status IN ('queued', 'running', 'retry_wait', 'ready', 'active', 'retired', 'failed')",
        name="ck_index_versions_status",
    ),
    CheckConstraint(
        "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
        name="ck_index_versions_attempts",
    ),
    CheckConstraint(
        "source_content_hash ~ '^[0-9a-f]{64}$' AND parsed_content_hash ~ '^[0-9a-f]{64}$'",
        name="ck_index_versions_hashes",
    ),
    CheckConstraint(
        "visibility IN ('private', 'workspace', 'departments')",
        name="ck_index_versions_visibility",
    ),
    CheckConstraint(
        "security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
        name="ck_index_versions_security_level",
    ),
    CheckConstraint(
        "(visibility = 'departments' AND cardinality(department_ids) > 0) OR "
        "(visibility <> 'departments' AND cardinality(department_ids) = 0)",
        name="ck_index_versions_department_scope",
    ),
    CheckConstraint(
        "(status = 'running' AND claimed_by IS NOT NULL AND claim_until IS NOT NULL "
        "AND started_at IS NOT NULL) OR "
        "(status <> 'running' AND claimed_by IS NULL AND claim_until IS NULL)",
        name="ck_index_versions_claim",
    ),
    CheckConstraint(
        "(status IN ('ready', 'active', 'retired') AND completed_at IS NOT NULL "
        "AND chunk_count >= 1) OR "
        "(status NOT IN ('ready', 'active', 'retired') AND chunk_count IS NULL)",
        name="ck_index_versions_result",
    ),
    CheckConstraint(
        "(status IN ('retry_wait', 'failed') AND failure_stage IS NOT NULL "
        "AND error_code IS NOT NULL AND error_message IS NOT NULL) OR "
        "(status NOT IN ('retry_wait', 'failed') AND failure_stage IS NULL "
        "AND error_code IS NULL AND error_message IS NULL)",
        name="ck_index_versions_failure",
    ),
    CheckConstraint(
        "failure_stage IS NULL OR "
        "failure_stage IN ('artifact', 'chunk', 'embedding', 'index', 'worker')",
        name="ck_index_versions_failure_stage",
    ),
    CheckConstraint(
        "(status IN ('ready', 'active', 'retired', 'failed') AND completed_at IS NOT NULL) OR "
        "(status IN ('queued', 'running', 'retry_wait') AND completed_at IS NULL)",
        name="ck_index_versions_completed_at",
    ),
    CheckConstraint(
        "(status = 'active' AND activated_at IS NOT NULL) OR (status <> 'active')",
        name="ck_index_versions_activated_at",
    ),
)
Index(
    "ix_index_versions_claim",
    index_versions.c.status,
    index_versions.c.available_at,
    index_versions.c.created_at,
)
Index(
    "ix_index_versions_lease",
    index_versions.c.status,
    index_versions.c.claim_until,
)
Index(
    "ix_index_versions_workspace_document",
    index_versions.c.workspace_id,
    index_versions.c.document_id,
    index_versions.c.document_version_id,
    index_versions.c.build_no,
)
Index(
    "uq_index_versions_active_document",
    index_versions.c.workspace_id,
    index_versions.c.document_id,
    unique=True,
    postgresql_where=index_versions.c.status == "active",
)

document_index_publications = Table(
    "document_index_publications",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), primary_key=True),
    Column("document_id", UUID(as_uuid=True), primary_key=True),
    Column("document_version_id", UUID(as_uuid=True), nullable=False),
    Column("index_version_id", UUID(as_uuid=True), nullable=False),
    Column("activated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["workspace_id", "document_id"],
        [f"{SCHEMA_TOKEN}.documents.workspace_id", f"{SCHEMA_TOKEN}.documents.document_id"],
        name="fk_document_index_publications_document",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "document_id", "document_version_id", "index_version_id"],
        [
            f"{SCHEMA_TOKEN}.index_versions.workspace_id",
            f"{SCHEMA_TOKEN}.index_versions.document_id",
            f"{SCHEMA_TOKEN}.index_versions.document_version_id",
            f"{SCHEMA_TOKEN}.index_versions.index_version_id",
        ],
        name="fk_document_index_publications_index_version",
    ),
)

retrieval_chunks = Table(
    "retrieval_chunks",
    metadata,
    Column("index_version_id", UUID(as_uuid=True), primary_key=True),
    Column("chunk_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("knowledge_base_id", UUID(as_uuid=True), nullable=False),
    Column("document_id", UUID(as_uuid=True), nullable=False),
    Column("document_version_id", UUID(as_uuid=True), nullable=False),
    Column("ingestion_job_id", UUID(as_uuid=True), nullable=True),
    Column("source_id", UUID(as_uuid=True), nullable=True),
    Column("sequence_no", Integer, nullable=False),
    Column("content", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("embedding", VECTOR(1024), nullable=False),
    Column("keyword_text", Text, nullable=False),
    Column(
        "keyword_vector",
        TSVECTOR,
        Computed("to_tsvector('simple', keyword_text)", persisted=True),
    ),
    Column("department_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
    Column("visibility", String(32), nullable=False),
    Column("security_level", String(32), nullable=False),
    Column("permission_labels", ARRAY(String(80)), nullable=False, server_default="{}"),
    Column("source_position", JSONB, nullable=False),
    Column("parsed_content_hash", String(64), nullable=True),
    Column("parser_name", String(255), nullable=True),
    Column("ocr_used", Boolean, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("active", Boolean, nullable=False),
    CheckConstraint("sequence_no >= 1", name="ck_retrieval_chunks_sequence_no"),
    CheckConstraint(
        "visibility IN ('private', 'workspace', 'departments', 'public')",
        name="ck_retrieval_chunks_visibility",
    ),
    CheckConstraint(
        "security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
        name="ck_retrieval_chunks_security_level",
    ),
    CheckConstraint(
        "(ingestion_job_id IS NULL AND source_id IS NULL AND parsed_content_hash IS NULL "
        "AND parser_name IS NULL AND ocr_used IS NULL) OR "
        "(ingestion_job_id IS NOT NULL AND source_id IS NOT NULL "
        "AND parsed_content_hash ~ '^[0-9a-f]{64}$' "
        "AND parser_name IS NOT NULL AND ocr_used IS NOT NULL)",
        name="ck_retrieval_chunks_traceability",
    ),
)
Index(
    "ix_retrieval_chunks_scope",
    retrieval_chunks.c.workspace_id,
    retrieval_chunks.c.index_version_id,
    retrieval_chunks.c.knowledge_base_id,
    retrieval_chunks.c.document_id,
)
Index(
    "ix_retrieval_chunks_document_sequence",
    retrieval_chunks.c.workspace_id,
    retrieval_chunks.c.document_version_id,
    retrieval_chunks.c.sequence_no,
)
Index(
    "ix_retrieval_chunks_keyword",
    retrieval_chunks.c.keyword_vector,
    postgresql_using="gin",
)
Index(
    "ix_retrieval_chunks_embedding_hnsw",
    retrieval_chunks.c.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
)
