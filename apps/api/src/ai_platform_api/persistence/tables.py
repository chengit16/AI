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
    func,
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
    CheckConstraint(
        "login_name = lower(btrim(login_name)) AND char_length(login_name) >= 3",
        name="ck_accounts_normalized_login",
    ),
)

workspaces = Table(
    "workspaces",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_type", String(32), nullable=False),
    Column("name", String(120), nullable=False),
    Column("owner_account_id", UUID(as_uuid=True), nullable=True),
    Column("entitlement_version", Integer, nullable=False),
    Column("role_version", Integer, nullable=False, server_default="1"),
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
    CheckConstraint("role_version >= 1", name="ck_workspaces_role_version"),
    CheckConstraint("version >= 1", name="ck_workspaces_version"),
    ForeignKeyConstraint(
        ["owner_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_workspaces_owner",
    ),
    CheckConstraint(
        "(workspace_type = 'personal' AND owner_account_id IS NOT NULL) "
        "OR (workspace_type = 'enterprise' AND owner_account_id IS NULL)",
        name="ck_workspaces_owner_by_type",
    ),
)
Index(
    "uq_workspaces_personal_owner",
    workspaces.c.owner_account_id,
    unique=True,
    postgresql_where=workspaces.c.workspace_type == "personal",
)

workspace_memberships = Table(
    "workspace_memberships",
    metadata,
    Column("membership_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("account_id", UUID(as_uuid=True), nullable=False),
    Column("membership_type", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
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
    CheckConstraint(
        "membership_type IN ('owner', 'member')",
        name="ck_workspace_memberships_type",
    ),
    CheckConstraint("version >= 1", name="ck_workspace_memberships_version"),
    UniqueConstraint(
        "workspace_id",
        "membership_id",
        name="uq_workspace_memberships_workspace_membership",
    ),
)
Index(
    "ix_workspace_memberships_account_workspace",
    workspace_memberships.c.account_id,
    workspace_memberships.c.workspace_id,
)
Index(
    "uq_workspace_memberships_owner",
    workspace_memberships.c.workspace_id,
    unique=True,
    postgresql_where=workspace_memberships.c.membership_type == "owner",
)

workspace_invitations = Table(
    "workspace_invitations",
    metadata,
    Column("invitation_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("invited_account_id", UUID(as_uuid=True), nullable=False),
    Column("invited_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("accepted_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "status IN ('pending', 'accepted', 'cancelled', 'expired')",
        name="ck_workspace_invitations_status",
    ),
    CheckConstraint("expires_at > created_at", name="ck_workspace_invitations_expiry"),
    CheckConstraint(
        "(status = 'accepted' AND accepted_at IS NOT NULL) "
        "OR (status <> 'accepted' AND accepted_at IS NULL)",
        name="ck_workspace_invitations_accepted_at",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_workspace_invitations_workspace",
    ),
    ForeignKeyConstraint(
        ["invited_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_workspace_invitations_account",
    ),
    ForeignKeyConstraint(
        ["invited_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_workspace_invitations_inviter",
    ),
)
Index(
    "uq_workspace_invitations_pending",
    workspace_invitations.c.workspace_id,
    workspace_invitations.c.invited_account_id,
    unique=True,
    postgresql_where=workspace_invitations.c.status == "pending",
)

departments = Table(
    "departments",
    metadata,
    Column("department_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("parent_department_id", UUID(as_uuid=True), nullable=True),
    Column("name", String(120), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint(
        "workspace_id",
        "department_id",
        name="uq_departments_workspace_department",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_departments_workspace",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "parent_department_id"],
        [f"{SCHEMA_TOKEN}.departments.workspace_id", f"{SCHEMA_TOKEN}.departments.department_id"],
        name="fk_departments_parent",
    ),
    CheckConstraint("parent_department_id <> department_id", name="ck_departments_not_self_parent"),
    CheckConstraint("status IN ('active', 'disabled')", name="ck_departments_status"),
    CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 120", name="ck_departments_name"),
    CheckConstraint("version >= 1", name="ck_departments_version"),
)
Index(
    "uq_departments_sibling_name",
    departments.c.workspace_id,
    departments.c.parent_department_id,
    func.lower(departments.c.name),
    unique=True,
    postgresql_nulls_not_distinct=True,
)
Index(
    "ix_departments_workspace_parent",
    departments.c.workspace_id,
    departments.c.parent_department_id,
)

department_closure = Table(
    "department_closure",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), primary_key=True),
    Column("ancestor_department_id", UUID(as_uuid=True), primary_key=True),
    Column("descendant_department_id", UUID(as_uuid=True), primary_key=True),
    Column("depth", Integer, nullable=False),
    ForeignKeyConstraint(
        ["workspace_id", "ancestor_department_id"],
        [f"{SCHEMA_TOKEN}.departments.workspace_id", f"{SCHEMA_TOKEN}.departments.department_id"],
        name="fk_department_closure_ancestor",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "descendant_department_id"],
        [f"{SCHEMA_TOKEN}.departments.workspace_id", f"{SCHEMA_TOKEN}.departments.department_id"],
        name="fk_department_closure_descendant",
        ondelete="CASCADE",
    ),
    CheckConstraint("depth >= 0", name="ck_department_closure_depth"),
)
Index(
    "ix_department_closure_descendant",
    department_closure.c.workspace_id,
    department_closure.c.descendant_department_id,
    department_closure.c.depth,
)

positions = Table(
    "positions",
    metadata,
    Column("position_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("department_id", UUID(as_uuid=True), nullable=False),
    Column("name", String(120), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint(
        "workspace_id",
        "department_id",
        "position_id",
        name="uq_positions_workspace_department_position",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "department_id"],
        [f"{SCHEMA_TOKEN}.departments.workspace_id", f"{SCHEMA_TOKEN}.departments.department_id"],
        name="fk_positions_department",
    ),
    CheckConstraint("status IN ('active', 'disabled')", name="ck_positions_status"),
    CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 120", name="ck_positions_name"),
    CheckConstraint("version >= 1", name="ck_positions_version"),
)
Index("ix_positions_workspace_department", positions.c.workspace_id, positions.c.department_id)
Index(
    "uq_positions_department_name",
    positions.c.workspace_id,
    positions.c.department_id,
    func.lower(positions.c.name),
    unique=True,
)

membership_departments = Table(
    "membership_departments",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), primary_key=True),
    Column("membership_id", UUID(as_uuid=True), primary_key=True),
    Column("department_id", UUID(as_uuid=True), primary_key=True),
    Column("is_primary", Boolean, nullable=False),
    Column("assigned_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["workspace_id", "membership_id"],
        [
            f"{SCHEMA_TOKEN}.workspace_memberships.workspace_id",
            f"{SCHEMA_TOKEN}.workspace_memberships.membership_id",
        ],
        name="fk_membership_departments_membership",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "department_id"],
        [f"{SCHEMA_TOKEN}.departments.workspace_id", f"{SCHEMA_TOKEN}.departments.department_id"],
        name="fk_membership_departments_department",
    ),
)
Index(
    "uq_membership_departments_primary",
    membership_departments.c.workspace_id,
    membership_departments.c.membership_id,
    unique=True,
    postgresql_where=membership_departments.c.is_primary.is_(True),
)
Index(
    "ix_membership_departments_scope",
    membership_departments.c.workspace_id,
    membership_departments.c.department_id,
    membership_departments.c.membership_id,
)

membership_positions = Table(
    "membership_positions",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), primary_key=True),
    Column("membership_id", UUID(as_uuid=True), primary_key=True),
    Column("position_id", UUID(as_uuid=True), primary_key=True),
    Column("department_id", UUID(as_uuid=True), nullable=False),
    Column("assigned_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["workspace_id", "membership_id"],
        [
            f"{SCHEMA_TOKEN}.workspace_memberships.workspace_id",
            f"{SCHEMA_TOKEN}.workspace_memberships.membership_id",
        ],
        name="fk_membership_positions_membership",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "membership_id", "department_id"],
        [
            f"{SCHEMA_TOKEN}.membership_departments.workspace_id",
            f"{SCHEMA_TOKEN}.membership_departments.membership_id",
            f"{SCHEMA_TOKEN}.membership_departments.department_id",
        ],
        name="fk_membership_positions_department_assignment",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "department_id", "position_id"],
        [
            f"{SCHEMA_TOKEN}.positions.workspace_id",
            f"{SCHEMA_TOKEN}.positions.department_id",
            f"{SCHEMA_TOKEN}.positions.position_id",
        ],
        name="fk_membership_positions_position",
    ),
)
Index(
    "ix_membership_positions_scope",
    membership_positions.c.workspace_id,
    membership_positions.c.department_id,
    membership_positions.c.position_id,
    membership_positions.c.membership_id,
)

roles = Table(
    "roles",
    metadata,
    Column("role_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("role_key", String(64), nullable=False),
    Column("name", String(120), nullable=False),
    Column("status", String(32), nullable=False),
    Column("system_managed", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint("workspace_id", "role_id", name="uq_roles_workspace_role"),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_roles_workspace",
    ),
    CheckConstraint("role_key ~ '^[a-z][a-z0-9_]{2,63}$'", name="ck_roles_key"),
    CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 120", name="ck_roles_name"),
    CheckConstraint("status IN ('active', 'disabled')", name="ck_roles_status"),
    CheckConstraint("version >= 1", name="ck_roles_version"),
)
Index("uq_roles_workspace_key", roles.c.workspace_id, roles.c.role_key, unique=True)
Index(
    "uq_roles_workspace_name",
    roles.c.workspace_id,
    func.lower(roles.c.name),
    unique=True,
)

role_bindings = Table(
    "role_bindings",
    metadata,
    Column("binding_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("role_id", UUID(as_uuid=True), nullable=False),
    Column("scope_type", String(32), nullable=False),
    Column("department_id", UUID(as_uuid=True), nullable=True),
    Column("membership_id", UUID(as_uuid=True), nullable=True),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("version", Integer, nullable=False),
    ForeignKeyConstraint(
        ["workspace_id", "role_id"],
        [f"{SCHEMA_TOKEN}.roles.workspace_id", f"{SCHEMA_TOKEN}.roles.role_id"],
        name="fk_role_bindings_role",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "department_id"],
        [f"{SCHEMA_TOKEN}.departments.workspace_id", f"{SCHEMA_TOKEN}.departments.department_id"],
        name="fk_role_bindings_department",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "membership_id"],
        [
            f"{SCHEMA_TOKEN}.workspace_memberships.workspace_id",
            f"{SCHEMA_TOKEN}.workspace_memberships.membership_id",
        ],
        name="fk_role_bindings_membership",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "scope_type IN ('workspace', 'department', 'member')",
        name="ck_role_bindings_scope",
    ),
    CheckConstraint("status IN ('active', 'revoked')", name="ck_role_bindings_status"),
    CheckConstraint(
        "(scope_type = 'workspace' AND department_id IS NULL AND membership_id IS NULL) "
        "OR (scope_type = 'department' AND department_id IS NOT NULL AND membership_id IS NULL) "
        "OR (scope_type = 'member' AND department_id IS NULL AND membership_id IS NOT NULL)",
        name="ck_role_bindings_target",
    ),
    CheckConstraint(
        "(status = 'active' AND revoked_at IS NULL) "
        "OR (status = 'revoked' AND revoked_at IS NOT NULL)",
        name="ck_role_bindings_revoked_at",
    ),
    CheckConstraint("version >= 1", name="ck_role_bindings_version"),
)
Index(
    "uq_role_bindings_active_scope",
    role_bindings.c.workspace_id,
    role_bindings.c.role_id,
    role_bindings.c.scope_type,
    role_bindings.c.department_id,
    role_bindings.c.membership_id,
    unique=True,
    postgresql_nulls_not_distinct=True,
    postgresql_where=role_bindings.c.status == "active",
)
Index(
    "ix_role_bindings_member",
    role_bindings.c.workspace_id,
    role_bindings.c.membership_id,
    role_bindings.c.status,
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
