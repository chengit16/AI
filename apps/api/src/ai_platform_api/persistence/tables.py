from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID

from ai_platform_api.persistence.database import SCHEMA_TOKEN

metadata = MetaData(schema=SCHEMA_TOKEN)

workspace_resources = Table(
    "workspace_resources",
    metadata,
    Column("resource_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("title", String(255), nullable=False),
    Column("sensitive_value", String(1024), nullable=True),
    Column("version", Integer, nullable=False),
    CheckConstraint("version >= 1", name="ck_workspace_resources_version"),
)
Index(
    "ix_workspace_resources_workspace_resource",
    workspace_resources.c.workspace_id,
    workspace_resources.c.resource_id,
)

outbox_events = Table(
    "outbox_events",
    metadata,
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("event_type", String(255), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("aggregate_id", UUID(as_uuid=True), nullable=False),
    Column("aggregate_version", Integer, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("traceparent", String(55), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=True),
    CheckConstraint("schema_version >= 1", name="ck_outbox_events_schema_version"),
    CheckConstraint("aggregate_version >= 1", name="ck_outbox_events_aggregate_version"),
)
Index("ix_outbox_events_unpublished", outbox_events.c.published_at, outbox_events.c.occurred_at)

consumer_receipts = Table(
    "consumer_receipts",
    metadata,
    Column("consumer_name", String(255), primary_key=True),
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("processed_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("consumer_name", "event_id", name="uq_consumer_receipts_consumer_event"),
)

resource_projections = Table(
    "resource_projections",
    metadata,
    Column("aggregate_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("applied_event_id", UUID(as_uuid=True), nullable=False),
    Column("apply_count", Integer, nullable=False),
    CheckConstraint("apply_count >= 1", name="ck_resource_projections_apply_count"),
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
    Column("source_position", JSONB, nullable=False),
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
