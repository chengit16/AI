from ai_platform_backend.integration import persistence as integration_tables
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
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID

from ai_platform_api.persistence.database import SCHEMA_TOKEN

metadata = MetaData(schema=SCHEMA_TOKEN)
audit_records = integration_tables.audit_records
consumer_receipts = integration_tables.consumer_receipts
outbox_events = integration_tables.outbox_events
resource_projections = integration_tables.resource_projections

accounts = Table(
    "accounts",
    metadata,
    Column("account_id", UUID(as_uuid=True), primary_key=True),
    Column("login_name", String(255), nullable=False, unique=True),
    Column("display_name", String(120), nullable=False),
    Column("password_hash", String(512), nullable=False),
    Column("status", String(32), nullable=False),
    Column("auth_version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("updated_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("version", Integer, nullable=False),
    CheckConstraint("status IN ('active', 'disabled')", name="ck_accounts_status"),
    CheckConstraint("auth_version >= 1", name="ck_accounts_auth_version"),
    CheckConstraint("version >= 1", name="ck_accounts_version"),
)

workspaces = Table(
    "workspaces",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_type", String(32), nullable=False),
    Column("name", String(120), nullable=False),
    Column("owner_account_id", UUID(as_uuid=True), nullable=True),
    Column("entitlement_version", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("updated_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("version", Integer, nullable=False),
    CheckConstraint("workspace_type IN ('personal', 'enterprise')", name="ck_workspaces_type"),
    CheckConstraint(
        "status IN ('active', 'suspended', 'archived')",
        name="ck_workspaces_status",
    ),
    CheckConstraint("entitlement_version >= 1", name="ck_workspaces_entitlement_version"),
    CheckConstraint("version >= 1", name="ck_workspaces_version"),
    ForeignKeyConstraint(
        ["owner_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_workspaces_owner",
    ),
)

workspace_memberships = Table(
    "workspace_memberships",
    metadata,
    Column("membership_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("account_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("workspace_id", "account_id", name="uq_workspace_memberships_member"),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_workspace_memberships_workspace",
    ),
    ForeignKeyConstraint(
        ["account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_workspace_memberships_account",
    ),
    CheckConstraint(
        "status IN ('active', 'disabled', 'left')",
        name="ck_workspace_memberships_status",
    ),
)
Index(
    "ix_workspace_memberships_account_workspace",
    workspace_memberships.c.account_id,
    workspace_memberships.c.workspace_id,
)

open_api_keys = Table(
    "open_api_keys",
    metadata,
    Column("key_id", UUID(as_uuid=True), primary_key=True),
    Column("actor_id", UUID(as_uuid=True), nullable=False, unique=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("name", String(255), nullable=False),
    Column("secret_digest", String(64), nullable=False),
    Column("last_four", String(4), nullable=False),
    Column("scopes", ARRAY(String(128)), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    CheckConstraint("char_length(last_four) = 4", name="ck_open_api_keys_last_four"),
    CheckConstraint("char_length(secret_digest) = 64", name="ck_open_api_keys_digest"),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_open_api_keys_workspace",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_open_api_keys_creator",
    ),
)
Index(
    "ix_open_api_keys_workspace_status",
    open_api_keys.c.workspace_id,
    open_api_keys.c.revoked_at,
    open_api_keys.c.expires_at,
)

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

stream_runs = Table(
    "stream_runs",
    metadata,
    Column("run_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("conversation_id", UUID(as_uuid=True), nullable=False),
    Column("message_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(32), nullable=False),
    Column("last_sequence_no", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("final_payload", JSONB, nullable=True),
    CheckConstraint(
        "status IN ('active', 'completed', 'failed', 'cancelled')",
        name="ck_stream_runs_status",
    ),
    CheckConstraint("last_sequence_no >= 0", name="ck_stream_runs_sequence_no"),
)
Index(
    "ix_stream_runs_workspace_conversation",
    stream_runs.c.workspace_id,
    stream_runs.c.conversation_id,
)
Index(
    "uq_stream_runs_active_conversation",
    stream_runs.c.conversation_id,
    unique=True,
    postgresql_where=stream_runs.c.status == "active",
)

stream_events = Table(
    "stream_events",
    metadata,
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("conversation_id", UUID(as_uuid=True), nullable=False),
    Column("message_id", UUID(as_uuid=True), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("event_type", String(64), nullable=False),
    Column("sequence_no", Integer, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("traceparent", String(55), nullable=False),
    Column("payload", JSONB, nullable=False),
    UniqueConstraint("run_id", "sequence_no", name="uq_stream_events_run_sequence"),
    CheckConstraint("sequence_no >= 1", name="ck_stream_events_sequence_no"),
)
Index(
    "ix_stream_events_workspace_run_sequence",
    stream_events.c.workspace_id,
    stream_events.c.run_id,
    stream_events.c.sequence_no,
)
Index(
    "ix_stream_events_expires_at",
    stream_events.c.expires_at,
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
