from ai_platform_backend.integration import persistence as integration_tables
from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
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
    Column("menu_version", Integer, nullable=False, server_default="1"),
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
    CheckConstraint("menu_version >= 1", name="ck_workspaces_menu_version"),
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

role_permission_grants = Table(
    "role_permission_grants",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), primary_key=True),
    Column("role_id", UUID(as_uuid=True), primary_key=True),
    Column("permission_code", String(160), primary_key=True),
    Column("scope_type", String(32), nullable=False),
    Column("department_ids", ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"),
    Column("resource_ids", ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"),
    Column("maximum_security_level", String(32), nullable=False),
    Column("field_mask", ARRAY(String(160)), nullable=False, server_default="{}"),
    ForeignKeyConstraint(
        ["workspace_id", "role_id"],
        [f"{SCHEMA_TOKEN}.roles.workspace_id", f"{SCHEMA_TOKEN}.roles.role_id"],
        name="fk_role_permission_grants_role",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "permission_code ~ '^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*){2,}$'",
        name="ck_role_permission_grants_code",
    ),
    CheckConstraint(
        "scope_type IN ('workspace', 'department_tree', 'self', 'resource')",
        name="ck_role_permission_grants_scope",
    ),
    CheckConstraint(
        "maximum_security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
        name="ck_role_permission_grants_security_level",
    ),
    CheckConstraint(
        "(scope_type IN ('workspace', 'self') AND cardinality(department_ids) = 0 "
        "AND cardinality(resource_ids) = 0) OR "
        "(scope_type = 'department_tree' AND cardinality(department_ids) > 0 "
        "AND cardinality(resource_ids) = 0) OR "
        "(scope_type = 'resource' AND cardinality(resource_ids) > 0 "
        "AND cardinality(department_ids) = 0)",
        name="ck_role_permission_grants_targets",
    ),
)

registered_menu_api_bindings = Table(
    "registered_menu_api_bindings",
    metadata,
    Column("menu_id", UUID(as_uuid=True), primary_key=True),
    Column("api_resource_id", UUID(as_uuid=True), primary_key=True),
    Column("action_type", String(32), nullable=False),
    CheckConstraint(
        "action_type IN ('query', 'mutation', 'publish', 'approve')",
        name="ck_registered_menu_api_bindings_action",
    ),
)

workspace_menu_overrides = Table(
    "workspace_menu_overrides",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), primary_key=True),
    Column("menu_id", UUID(as_uuid=True), primary_key=True),
    Column("parent_menu_id", UUID(as_uuid=True), nullable=True),
    Column("name", String(80), nullable=False),
    Column("icon_key", String(80), nullable=True),
    Column("sort_order", Integer, nullable=False),
    Column("visible", Boolean, nullable=False),
    Column("version", Integer, nullable=False),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_workspace_menu_overrides_workspace",
        ondelete="CASCADE",
    ),
    CheckConstraint("sort_order >= 0", name="ck_workspace_menu_overrides_sort"),
    CheckConstraint("version >= 1", name="ck_workspace_menu_overrides_version"),
)

role_menus = Table(
    "role_menus",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), primary_key=True),
    Column("role_id", UUID(as_uuid=True), primary_key=True),
    Column("menu_id", UUID(as_uuid=True), primary_key=True),
    Column("visible", Boolean, nullable=False),
    ForeignKeyConstraint(
        ["workspace_id", "role_id"],
        [f"{SCHEMA_TOKEN}.roles.workspace_id", f"{SCHEMA_TOKEN}.roles.role_id"],
        name="fk_role_menus_role",
        ondelete="CASCADE",
    ),
)

Index(
    "ix_role_menus_lookup",
    role_menus.c.workspace_id,
    role_menus.c.role_id,
    role_menus.c.visible,
)

menu_releases = Table(
    "menu_releases",
    metadata,
    Column("release_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("release_number", Integer, nullable=False),
    Column("release_kind", String(32), nullable=False),
    Column("source_release_id", UUID(as_uuid=True), nullable=True),
    Column("status", String(32), nullable=False),
    Column("snapshot", JSONB, nullable=False),
    Column("snapshot_digest", String(64), nullable=False),
    Column("validation_errors", ARRAY(String(500)), nullable=False, server_default="{}"),
    Column("rejection_reason", String(500), nullable=True),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("decided_by_account_id", UUID(as_uuid=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("validated_at", DateTime(timezone=True), nullable=True),
    Column("decided_at", DateTime(timezone=True), nullable=True),
    Column("published_at", DateTime(timezone=True), nullable=True),
    Column("version", Integer, nullable=False),
    UniqueConstraint(
        "workspace_id",
        "release_number",
        name="uq_menu_releases_workspace_number",
    ),
    UniqueConstraint(
        "workspace_id",
        "release_id",
        name="uq_menu_releases_workspace_release",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_menu_releases_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "source_release_id"],
        [
            f"{SCHEMA_TOKEN}.menu_releases.workspace_id",
            f"{SCHEMA_TOKEN}.menu_releases.release_id",
        ],
        name="fk_menu_releases_source",
    ),
    CheckConstraint(
        "release_kind IN ('standard', 'rollback')",
        name="ck_menu_releases_kind",
    ),
    CheckConstraint(
        "status IN ('draft', 'validated', 'approved', 'rejected', 'published')",
        name="ck_menu_releases_status",
    ),
    CheckConstraint("release_number >= 1", name="ck_menu_releases_number"),
    CheckConstraint("char_length(snapshot_digest) = 64", name="ck_menu_releases_digest"),
    CheckConstraint("version >= 1", name="ck_menu_releases_version"),
)

Index(
    "ix_menu_releases_workspace_status",
    menu_releases.c.workspace_id,
    menu_releases.c.status,
    menu_releases.c.release_number,
)

workspace_menu_publications = Table(
    "workspace_menu_publications",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), primary_key=True),
    Column("current_release_id", UUID(as_uuid=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_workspace_menu_publications_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "current_release_id"],
        [
            f"{SCHEMA_TOKEN}.menu_releases.workspace_id",
            f"{SCHEMA_TOKEN}.menu_releases.release_id",
        ],
        name="fk_workspace_menu_publications_release",
    ),
)

knowledge_bases = Table(
    "knowledge_bases",
    metadata,
    Column("knowledge_base_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("name", String(120), nullable=False),
    Column("description", String(1000), nullable=True),
    Column("default_visibility", String(32), nullable=False),
    Column("department_ids", ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"),
    Column("default_security_level", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True), nullable=True),
    Column("version", Integer, nullable=False),
    UniqueConstraint(
        "workspace_id",
        "knowledge_base_id",
        name="uq_knowledge_bases_workspace_base",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_knowledge_bases_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_knowledge_bases_creator",
    ),
    CheckConstraint(
        "default_visibility IN ('private', 'workspace', 'departments')",
        name="ck_knowledge_bases_visibility",
    ),
    CheckConstraint(
        "(default_visibility = 'departments' AND cardinality(department_ids) > 0) "
        "OR (default_visibility <> 'departments' AND cardinality(department_ids) = 0)",
        name="ck_knowledge_bases_department_scope",
    ),
    CheckConstraint(
        "default_security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
        name="ck_knowledge_bases_security_level",
    ),
    CheckConstraint("status IN ('active', 'deleted')", name="ck_knowledge_bases_status"),
    CheckConstraint(
        "(status = 'deleted' AND deleted_at IS NOT NULL) "
        "OR (status = 'active' AND deleted_at IS NULL)",
        name="ck_knowledge_bases_deleted_at",
    ),
    CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 120", name="ck_knowledge_bases_name"),
    CheckConstraint("version >= 1", name="ck_knowledge_bases_version"),
)
Index(
    "uq_knowledge_bases_active_name",
    knowledge_bases.c.workspace_id,
    func.lower(knowledge_bases.c.name),
    unique=True,
    postgresql_where=knowledge_bases.c.status == "active",
)

documents = Table(
    "documents",
    metadata,
    Column("document_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("knowledge_base_id", UUID(as_uuid=True), nullable=False),
    Column("title", String(255), nullable=False),
    Column("visibility", String(32), nullable=False),
    Column("department_ids", ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"),
    Column("security_level", String(32), nullable=False),
    Column("permission_labels", ARRAY(String(80)), nullable=False, server_default="{}"),
    Column("status", String(32), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True), nullable=True),
    Column("version", Integer, nullable=False),
    UniqueConstraint("workspace_id", "document_id", name="uq_documents_workspace_document"),
    ForeignKeyConstraint(
        ["workspace_id", "knowledge_base_id"],
        [
            f"{SCHEMA_TOKEN}.knowledge_bases.workspace_id",
            f"{SCHEMA_TOKEN}.knowledge_bases.knowledge_base_id",
        ],
        name="fk_documents_knowledge_base",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_documents_creator",
    ),
    CheckConstraint(
        "visibility IN ('private', 'workspace', 'departments')",
        name="ck_documents_visibility",
    ),
    CheckConstraint(
        "(visibility = 'departments' AND cardinality(department_ids) > 0) "
        "OR (visibility <> 'departments' AND cardinality(department_ids) = 0)",
        name="ck_documents_department_scope",
    ),
    CheckConstraint(
        "security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
        name="ck_documents_security_level",
    ),
    CheckConstraint("status IN ('active', 'deleted')", name="ck_documents_status"),
    CheckConstraint(
        "(status = 'deleted' AND deleted_at IS NOT NULL) "
        "OR (status = 'active' AND deleted_at IS NULL)",
        name="ck_documents_deleted_at",
    ),
    CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 255", name="ck_documents_title"),
    CheckConstraint("version >= 1", name="ck_documents_version"),
)
Index(
    "ix_documents_workspace_base_status",
    documents.c.workspace_id,
    documents.c.knowledge_base_id,
    documents.c.status,
)

document_versions = Table(
    "document_versions",
    metadata,
    Column("document_version_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("document_id", UUID(as_uuid=True), nullable=False),
    Column("version_number", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("content_hash", String(64), nullable=True),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=True),
    Column("record_version", Integer, nullable=False),
    UniqueConstraint(
        "workspace_id",
        "document_id",
        "version_number",
        name="uq_document_versions_number",
    ),
    UniqueConstraint(
        "workspace_id",
        "document_version_id",
        name="uq_document_versions_workspace_version",
    ),
    UniqueConstraint(
        "workspace_id",
        "document_id",
        "document_version_id",
        name="uq_document_versions_document_version",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "document_id"],
        [f"{SCHEMA_TOKEN}.documents.workspace_id", f"{SCHEMA_TOKEN}.documents.document_id"],
        name="fk_document_versions_document",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_document_versions_creator",
    ),
    CheckConstraint(
        "status IN ('draft', 'ready', 'published', 'superseded')",
        name="ck_document_versions_status",
    ),
    CheckConstraint(
        "(status IN ('published', 'superseded') AND published_at IS NOT NULL) "
        "OR (status IN ('draft', 'ready') AND published_at IS NULL)",
        name="ck_document_versions_published_at",
    ),
    CheckConstraint(
        "(status = 'draft' AND content_hash IS NULL) "
        "OR (status <> 'draft' AND content_hash ~ '^[0-9a-f]{64}$')",
        name="ck_document_versions_content_hash",
    ),
    CheckConstraint("version_number >= 1", name="ck_document_versions_number"),
    CheckConstraint("record_version >= 1", name="ck_document_versions_record_version"),
)
Index(
    "ix_document_versions_workspace_document_status",
    document_versions.c.workspace_id,
    document_versions.c.document_id,
    document_versions.c.status,
)

document_sources = Table(
    "document_sources",
    metadata,
    Column("source_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("document_version_id", UUID(as_uuid=True), nullable=False),
    Column("source_kind", String(32), nullable=False),
    Column("source_name", String(255), nullable=False),
    Column("original_object_key", String(1024), nullable=True),
    Column("source_path", String(2048), nullable=True),
    Column("source_url", String(2048), nullable=True),
    Column("external_source_id", String(512), nullable=True),
    Column("captured_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("media_type", String(255), nullable=True),
    Column("size_bytes", BigInteger, nullable=True),
    Column("content_hash", String(64), nullable=True),
    Column("scan_status", String(32), nullable=True),
    Column("scanner_version", String(255), nullable=True),
    Column("scanned_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint(
        "workspace_id",
        "document_version_id",
        name="uq_document_sources_version",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "document_version_id"],
        [
            f"{SCHEMA_TOKEN}.document_versions.workspace_id",
            f"{SCHEMA_TOKEN}.document_versions.document_version_id",
        ],
        name="fk_document_sources_version",
    ),
    CheckConstraint(
        "source_kind IN ('manual', 'upload', 'web', 'data_source')",
        name="ck_document_sources_kind",
    ),
    CheckConstraint(
        "(source_kind = 'manual' AND original_object_key IS NULL AND source_path IS NULL "
        "AND source_url IS NULL AND external_source_id IS NULL) OR "
        "(source_kind = 'upload' AND original_object_key IS NOT NULL AND source_url IS NULL) OR "
        "(source_kind = 'web' AND source_url IS NOT NULL AND original_object_key IS NULL) OR "
        "(source_kind = 'data_source' AND external_source_id IS NOT NULL)",
        name="ck_document_sources_locator",
    ),
    CheckConstraint(
        "char_length(btrim(source_name)) BETWEEN 1 AND 255",
        name="ck_document_sources_name",
    ),
    CheckConstraint(
        "(media_type IS NULL AND size_bytes IS NULL AND content_hash IS NULL "
        "AND scan_status IS NULL AND scanner_version IS NULL AND scanned_at IS NULL) OR "
        "(source_kind = 'upload' AND char_length(btrim(media_type)) > 0 "
        "AND size_bytes > 0 AND content_hash ~ '^[0-9a-f]{64}$' "
        "AND scan_status = 'clean' AND char_length(btrim(scanner_version)) > 0 "
        "AND scanned_at IS NOT NULL)",
        name="ck_document_sources_upload_security",
    ),
)

document_publications = Table(
    "document_publications",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), primary_key=True),
    Column("document_id", UUID(as_uuid=True), primary_key=True),
    Column("current_document_version_id", UUID(as_uuid=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["workspace_id", "document_id"],
        [f"{SCHEMA_TOKEN}.documents.workspace_id", f"{SCHEMA_TOKEN}.documents.document_id"],
        name="fk_document_publications_document",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "document_id", "current_document_version_id"],
        [
            f"{SCHEMA_TOKEN}.document_versions.workspace_id",
            f"{SCHEMA_TOKEN}.document_versions.document_id",
            f"{SCHEMA_TOKEN}.document_versions.document_version_id",
        ],
        name="fk_document_publications_version",
    ),
)
Index(
    "ix_role_permission_grants_lookup",
    role_permission_grants.c.workspace_id,
    role_permission_grants.c.permission_code,
    role_permission_grants.c.role_id,
)

workspace_entitlements = Table(
    "workspace_entitlements",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), primary_key=True),
    Column("plan_code", String(64), nullable=False),
    Column("max_storage_bytes", BigInteger, nullable=False),
    Column("max_members", Integer, nullable=False),
    Column("max_knowledge_bases", Integer, nullable=False),
    Column("max_published_agents", Integer, nullable=False),
    Column("max_monthly_questions", Integer, nullable=False),
    Column("open_api_allowed", Boolean, nullable=False),
    Column("public_publish_allowed", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_workspace_entitlements_workspace",
        ondelete="CASCADE",
    ),
    CheckConstraint("plan_code ~ '^[a-z][a-z0-9_]{2,63}$'", name="ck_entitlements_plan_code"),
    CheckConstraint("max_storage_bytes >= 0", name="ck_entitlements_storage"),
    CheckConstraint("max_members >= 1", name="ck_entitlements_members"),
    CheckConstraint("max_knowledge_bases >= 0", name="ck_entitlements_knowledge_bases"),
    CheckConstraint("max_published_agents >= 0", name="ck_entitlements_agents"),
    CheckConstraint("max_monthly_questions >= 0", name="ck_entitlements_questions"),
    CheckConstraint("version >= 1", name="ck_entitlements_version"),
)

workspace_feature_settings = Table(
    "workspace_feature_settings",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), primary_key=True),
    Column("open_api_enabled", Boolean, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_workspace_feature_settings_workspace",
        ondelete="CASCADE",
    ),
    CheckConstraint("version >= 1", name="ck_workspace_feature_settings_version"),
)

workspace_usage_counters = Table(
    "workspace_usage_counters",
    metadata,
    Column("workspace_id", UUID(as_uuid=True), primary_key=True),
    Column("metric", String(64), primary_key=True),
    Column("period_key", String(16), primary_key=True),
    Column("used_value", BigInteger, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_workspace_usage_counters_workspace",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "metric IN ('storage_bytes', 'knowledge_bases', 'published_agents', 'questions_monthly')",
        name="ck_workspace_usage_counters_metric",
    ),
    CheckConstraint("used_value >= 0", name="ck_workspace_usage_counters_value"),
    CheckConstraint("version >= 1", name="ck_workspace_usage_counters_version"),
)

workspace_usage_records = Table(
    "workspace_usage_records",
    metadata,
    Column("usage_record_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("metric", String(64), nullable=False),
    Column("period_key", String(16), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("delta_value", BigInteger, nullable=False),
    Column("resulting_value", BigInteger, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "workspace_id",
        "idempotency_key",
        name="uq_workspace_usage_records_idempotency",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_workspace_usage_records_workspace",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "metric IN ('storage_bytes', 'knowledge_bases', 'published_agents', 'questions_monthly')",
        name="ck_workspace_usage_records_metric",
    ),
    CheckConstraint("delta_value <> 0", name="ck_workspace_usage_records_delta"),
    CheckConstraint("resulting_value >= 0", name="ck_workspace_usage_records_result"),
)
Index(
    "ix_workspace_usage_records_period",
    workspace_usage_records.c.workspace_id,
    workspace_usage_records.c.metric,
    workspace_usage_records.c.period_key,
    workspace_usage_records.c.occurred_at,
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
