"""聚合 API、共享索引和集成事件表元数据，作为 Migration 事实来源。"""

from ai_platform_backend.indexing import persistence as indexing_tables
from ai_platform_backend.ingestion import persistence as ingestion_tables
from ai_platform_backend.integration import persistence as integration_tables
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

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

platform_administrators = Table(
    "platform_administrators",
    metadata,
    Column("account_id", UUID(as_uuid=True), primary_key=True),
    Column("status", String(32), nullable=False),
    Column("granted_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("granted_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    ForeignKeyConstraint(
        ["account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_platform_administrators_account",
    ),
    ForeignKeyConstraint(
        ["granted_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_platform_administrators_granter",
    ),
    CheckConstraint(
        "status IN ('active', 'revoked')",
        name="ck_platform_administrators_status",
    ),
    CheckConstraint(
        "(status = 'active' AND revoked_at IS NULL) OR "
        "(status = 'revoked' AND revoked_at IS NOT NULL)",
        name="ck_platform_administrators_revocation",
    ),
)

model_provider_configurations = Table(
    "model_provider_configurations",
    metadata,
    Column("provider_id", UUID(as_uuid=True), primary_key=True),
    Column("provider_key", String(64), nullable=False, unique=True),
    Column("display_name", String(120), nullable=False),
    Column("adapter_kind", String(32), nullable=False),
    Column("base_url", String(2048), nullable=False),
    Column("probe_model_id", String(255), nullable=False),
    Column("location", String(32), nullable=False),
    Column("declared_capabilities", ARRAY(String(32)), nullable=False),
    Column("policy_review_status", String(32), nullable=False),
    Column("max_security_level", String(32), nullable=False),
    Column("retention_days", Integer, nullable=True),
    Column("training_usage_allowed", Boolean, nullable=False),
    Column("policy_url", String(2048), nullable=True),
    Column("policy_version", String(128), nullable=True),
    Column("policy_reviewed_by_account_id", UUID(as_uuid=True), nullable=True),
    Column("policy_reviewed_at", DateTime(timezone=True), nullable=True),
    Column("probe_status", String(32), nullable=False),
    Column("probed_capabilities", ARRAY(String(32)), nullable=False),
    Column("last_probe_error_code", String(128), nullable=True),
    Column("last_probed_at", DateTime(timezone=True), nullable=True),
    Column("status", String(32), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    ForeignKeyConstraint(
        ["policy_reviewed_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_model_provider_configurations_policy_reviewer",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_model_provider_configurations_creator",
    ),
    ForeignKeyConstraint(
        ["updated_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_model_provider_configurations_updater",
    ),
    CheckConstraint("adapter_kind = 'openai_compatible'", name="ck_model_providers_adapter"),
    CheckConstraint("location IN ('external', 'private')", name="ck_model_providers_location"),
    CheckConstraint(
        "policy_review_status IN ('pending', 'approved', 'rejected')",
        name="ck_model_providers_policy_status",
    ),
    CheckConstraint(
        "max_security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
        name="ck_model_providers_security_level",
    ),
    CheckConstraint(
        "retention_days IS NULL OR retention_days BETWEEN 0 AND 3650",
        name="ck_model_providers_retention",
    ),
    CheckConstraint(
        "probe_status IN ('not_run', 'passed', 'failed')",
        name="ck_model_providers_probe_status",
    ),
    CheckConstraint(
        "status IN ('draft', 'active', 'disabled')",
        name="ck_model_providers_status",
    ),
    CheckConstraint("version >= 1", name="ck_model_providers_version"),
    CheckConstraint(
        "(policy_review_status = 'pending' AND policy_reviewed_by_account_id IS NULL "
        "AND policy_reviewed_at IS NULL) OR "
        "(policy_review_status <> 'pending' AND policy_reviewed_by_account_id IS NOT NULL "
        "AND policy_reviewed_at IS NOT NULL)",
        name="ck_model_providers_policy_review",
    ),
    CheckConstraint(
        "(probe_status = 'not_run' AND last_probed_at IS NULL) OR "
        "(probe_status <> 'not_run' AND last_probed_at IS NOT NULL)",
        name="ck_model_providers_probe_time",
    ),
)

model_provider_credentials = Table(
    "model_provider_credentials",
    metadata,
    Column("credential_id", UUID(as_uuid=True), primary_key=True),
    Column("provider_id", UUID(as_uuid=True), nullable=False),
    Column("credential_version", Integer, nullable=False),
    Column("master_key_version", Integer, nullable=False),
    Column("encrypted_data_key", LargeBinary, nullable=False),
    Column("data_key_nonce", LargeBinary, nullable=False),
    Column("ciphertext", LargeBinary, nullable=False),
    Column("data_nonce", LargeBinary, nullable=False),
    Column("last_four", String(4), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint(
        "provider_id",
        "credential_version",
        name="uq_model_provider_credentials_version",
    ),
    ForeignKeyConstraint(
        ["provider_id"],
        [f"{SCHEMA_TOKEN}.model_provider_configurations.provider_id"],
        name="fk_model_provider_credentials_provider",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_model_provider_credentials_creator",
    ),
    CheckConstraint("credential_version >= 1", name="ck_model_credentials_version"),
    CheckConstraint("master_key_version >= 1", name="ck_model_credentials_master_key"),
    CheckConstraint("char_length(last_four) = 4", name="ck_model_credentials_last_four"),
    CheckConstraint("status IN ('active', 'revoked')", name="ck_model_credentials_status"),
    CheckConstraint(
        "(status = 'active' AND revoked_at IS NULL) OR "
        "(status = 'revoked' AND revoked_at IS NOT NULL)",
        name="ck_model_credentials_revocation",
    ),
)
Index(
    "uq_model_provider_credentials_active",
    model_provider_credentials.c.provider_id,
    unique=True,
    postgresql_where=model_provider_credentials.c.status == "active",
)

ai_runtime_config_versions = Table(
    "ai_runtime_config_versions",
    metadata,
    Column("runtime_config_version_id", UUID(as_uuid=True), primary_key=True),
    Column("version_number", Integer, nullable=False, unique=True),
    Column("display_name", String(120), nullable=False),
    Column("content_hash", String(64), nullable=False, unique=True),
    Column("system_prompt_template", Text, nullable=False),
    Column("system_prompt_hash", String(64), nullable=False),
    Column("component_versions", JSONB, nullable=False),
    Column("attempt_timeout_ms", Integer, nullable=False),
    Column("total_timeout_ms", Integer, nullable=False),
    Column("max_attempts_per_route", Integer, nullable=False),
    Column("max_prompt_characters", Integer, nullable=False),
    Column("max_output_tokens", Integer, nullable=False),
    Column("max_response_characters", Integer, nullable=False),
    Column("circuit_failure_threshold", Integer, nullable=False),
    Column("circuit_recovery_ms", Integer, nullable=False),
    Column("rule_degradation_message", Text, nullable=True),
    Column("max_estimated_cost_microunits", BigInteger, nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_ai_runtime_configs_creator",
    ),
    CheckConstraint("version_number >= 1", name="ck_ai_runtime_configs_version"),
    CheckConstraint(
        "content_hash ~ '^[0-9a-f]{64}$' AND system_prompt_hash ~ '^[0-9a-f]{64}$'",
        name="ck_ai_runtime_configs_hashes",
    ),
    CheckConstraint(
        "jsonb_typeof(component_versions) = 'object'",
        name="ck_ai_runtime_configs_components",
    ),
    CheckConstraint(
        "attempt_timeout_ms BETWEEN 1 AND 120000 "
        "AND total_timeout_ms BETWEEN attempt_timeout_ms AND 300000 "
        "AND max_attempts_per_route BETWEEN 1 AND 5 "
        "AND max_prompt_characters BETWEEN 1 AND 2000000 "
        "AND max_output_tokens BETWEEN 1 AND 65536 "
        "AND max_response_characters BETWEEN 1 AND 4000000 "
        "AND circuit_failure_threshold BETWEEN 1 AND 20 "
        "AND circuit_recovery_ms BETWEEN 1 AND 3600000 "
        "AND max_estimated_cost_microunits BETWEEN 1 AND 1000000000000",
        name="ck_ai_runtime_configs_budgets",
    ),
)

ai_runtime_model_routes = Table(
    "ai_runtime_model_routes",
    metadata,
    Column("route_id", UUID(as_uuid=True), primary_key=True),
    Column("runtime_config_version_id", UUID(as_uuid=True), nullable=False),
    Column("provider_id", UUID(as_uuid=True), nullable=False),
    Column("provider_configuration_version", Integer, nullable=False),
    Column("priority", Integer, nullable=False),
    Column("model_id", String(255), nullable=False),
    Column("location", String(32), nullable=False),
    Column("capabilities", ARRAY(String(32)), nullable=False),
    Column("input_price_microunits_per_million_tokens", BigInteger, nullable=False),
    Column("output_price_microunits_per_million_tokens", BigInteger, nullable=False),
    Column("currency", String(3), nullable=False),
    UniqueConstraint(
        "runtime_config_version_id",
        "priority",
        name="uq_ai_runtime_routes_priority",
    ),
    UniqueConstraint(
        "runtime_config_version_id",
        "provider_id",
        "model_id",
        name="uq_ai_runtime_routes_provider_model",
    ),
    ForeignKeyConstraint(
        ["runtime_config_version_id"],
        [f"{SCHEMA_TOKEN}.ai_runtime_config_versions.runtime_config_version_id"],
        name="fk_ai_runtime_routes_config",
    ),
    ForeignKeyConstraint(
        ["provider_id"],
        [f"{SCHEMA_TOKEN}.model_provider_configurations.provider_id"],
        name="fk_ai_runtime_routes_provider",
    ),
    CheckConstraint(
        "provider_configuration_version >= 1", name="ck_ai_runtime_routes_provider_version"
    ),
    CheckConstraint("priority BETWEEN 1 AND 8", name="ck_ai_runtime_routes_priority"),
    CheckConstraint("location IN ('external', 'private')", name="ck_ai_runtime_routes_location"),
    CheckConstraint("currency = 'CNY'", name="ck_ai_runtime_routes_currency"),
    CheckConstraint(
        "input_price_microunits_per_million_tokens BETWEEN 0 AND 1000000000000 "
        "AND output_price_microunits_per_million_tokens BETWEEN 0 AND 1000000000000",
        name="ck_ai_runtime_routes_prices",
    ),
)

ai_runtime_config_publication = Table(
    "ai_runtime_config_publication",
    metadata,
    Column("publication_key", String(32), primary_key=True),
    Column("runtime_config_version_id", UUID(as_uuid=True), nullable=False),
    Column("generation", Integer, nullable=False),
    Column("published_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["runtime_config_version_id"],
        [f"{SCHEMA_TOKEN}.ai_runtime_config_versions.runtime_config_version_id"],
        name="fk_ai_runtime_publication_config",
    ),
    ForeignKeyConstraint(
        ["published_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_ai_runtime_publication_publisher",
    ),
    CheckConstraint("publication_key = 'current'", name="ck_ai_runtime_publication_key"),
    CheckConstraint("generation >= 1", name="ck_ai_runtime_publication_generation"),
)

platform_audit_records = Table(
    "platform_audit_records",
    metadata,
    Column("audit_id", UUID(as_uuid=True), primary_key=True),
    Column("account_id", UUID(as_uuid=True), nullable=False),
    Column("provider_id", UUID(as_uuid=True), nullable=True),
    Column("runtime_config_version_id", UUID(as_uuid=True), nullable=True),
    Column("action", String(128), nullable=False),
    Column("request_id", UUID(as_uuid=True), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("details", JSONB, nullable=False),
    ForeignKeyConstraint(
        ["account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_platform_audit_records_account",
    ),
    ForeignKeyConstraint(
        ["provider_id"],
        [f"{SCHEMA_TOKEN}.model_provider_configurations.provider_id"],
        name="fk_platform_audit_records_provider",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["runtime_config_version_id"],
        [f"{SCHEMA_TOKEN}.ai_runtime_config_versions.runtime_config_version_id"],
        name="fk_platform_audit_records_runtime_config",
    ),
    CheckConstraint(
        "(provider_id IS NOT NULL)::integer + (runtime_config_version_id IS NOT NULL)::integer = 1",
        name="ck_platform_audit_subject",
    ),
    CheckConstraint("trace_id ~ '^[0-9a-f]{32}$'", name="ck_platform_audit_trace_id"),
)
Index(
    "ix_platform_audit_provider_time",
    platform_audit_records.c.provider_id,
    platform_audit_records.c.occurred_at,
)
Index(
    "ix_platform_audit_runtime_config_time",
    platform_audit_records.c.runtime_config_version_id,
    platform_audit_records.c.occurred_at,
)

model_invocations = Table(
    "model_invocations",
    metadata,
    Column("invocation_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("runtime_config_version_id", UUID(as_uuid=True), nullable=False),
    Column("task_type", String(128), nullable=False),
    Column("security_level", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("traceparent", String(55), nullable=False),
    Column("external_data_allowed", Boolean, nullable=False),
    Column("requested_max_output_tokens", Integer, nullable=False),
    Column("selected_route_id", UUID(as_uuid=True), nullable=True),
    Column("selected_provider_id", UUID(as_uuid=True), nullable=True),
    Column("selected_model_id", String(255), nullable=True),
    Column("input_tokens", BigInteger, nullable=False),
    Column("output_tokens", BigInteger, nullable=False),
    Column("estimated_cost_microunits", BigInteger, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("finish_reason", String(128), nullable=True),
    Column("degradation_reason", String(128), nullable=True),
    Column("error_code", String(128), nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_model_invocations_workspace",
    ),
    ForeignKeyConstraint(
        ["runtime_config_version_id"],
        [f"{SCHEMA_TOKEN}.ai_runtime_config_versions.runtime_config_version_id"],
        name="fk_model_invocations_runtime_config",
    ),
    ForeignKeyConstraint(
        ["selected_route_id"],
        [f"{SCHEMA_TOKEN}.ai_runtime_model_routes.route_id"],
        name="fk_model_invocations_selected_route",
    ),
    ForeignKeyConstraint(
        ["selected_provider_id"],
        [f"{SCHEMA_TOKEN}.model_provider_configurations.provider_id"],
        name="fk_model_invocations_selected_provider",
    ),
    CheckConstraint(
        "security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
        name="ck_model_invocations_security_level",
    ),
    CheckConstraint(
        "status IN ('running', 'succeeded', 'degraded', 'failed', 'rejected')",
        name="ck_model_invocations_status",
    ),
    CheckConstraint(
        "(status = 'running' AND completed_at IS NULL) OR "
        "(status <> 'running' AND completed_at IS NOT NULL)",
        name="ck_model_invocations_completion",
    ),
    CheckConstraint(
        "input_tokens >= 0 AND output_tokens >= 0 AND estimated_cost_microunits >= 0",
        name="ck_model_invocations_usage",
    ),
    CheckConstraint("currency = 'CNY'", name="ck_model_invocations_currency"),
    CheckConstraint("trace_id ~ '^[0-9a-f]{32}$'", name="ck_model_invocations_trace_id"),
)
Index(
    "ix_model_invocations_workspace_time",
    model_invocations.c.workspace_id,
    model_invocations.c.started_at,
)
Index(
    "ix_model_invocations_workspace_trace",
    model_invocations.c.workspace_id,
    model_invocations.c.trace_id,
)
Index(
    "ix_model_invocations_runtime_config",
    model_invocations.c.runtime_config_version_id,
    model_invocations.c.started_at,
)

model_invocation_attempts = Table(
    "model_invocation_attempts",
    metadata,
    Column("invocation_id", UUID(as_uuid=True), primary_key=True),
    Column("attempt_index", Integer, primary_key=True),
    Column("route_id", UUID(as_uuid=True), nullable=False),
    Column("provider_id", UUID(as_uuid=True), nullable=False),
    Column("model_id", String(255), nullable=False),
    Column("credential_version", Integer, nullable=True),
    Column("attempt_no", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("duration_ms", Integer, nullable=False),
    Column("failure_kind", String(64), nullable=True),
    Column("provider_request_id", String(255), nullable=True),
    Column("input_tokens", BigInteger, nullable=True),
    Column("output_tokens", BigInteger, nullable=True),
    Column("estimated_cost_microunits", BigInteger, nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("traceparent", String(55), nullable=False),
    ForeignKeyConstraint(
        ["invocation_id"],
        [f"{SCHEMA_TOKEN}.model_invocations.invocation_id"],
        name="fk_model_invocation_attempts_invocation",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["route_id"],
        [f"{SCHEMA_TOKEN}.ai_runtime_model_routes.route_id"],
        name="fk_model_invocation_attempts_route",
    ),
    ForeignKeyConstraint(
        ["provider_id"],
        [f"{SCHEMA_TOKEN}.model_provider_configurations.provider_id"],
        name="fk_model_invocation_attempts_provider",
    ),
    ForeignKeyConstraint(
        ["provider_id", "credential_version"],
        [
            f"{SCHEMA_TOKEN}.model_provider_credentials.provider_id",
            f"{SCHEMA_TOKEN}.model_provider_credentials.credential_version",
        ],
        name="fk_model_invocation_attempts_credential",
    ),
    CheckConstraint("attempt_index >= 1", name="ck_model_attempts_index"),
    CheckConstraint("attempt_no >= 0", name="ck_model_attempts_number"),
    CheckConstraint(
        "status IN ('succeeded', 'failed', 'circuit_open')",
        name="ck_model_attempts_status",
    ),
    CheckConstraint(
        "duration_ms >= 0 AND estimated_cost_microunits >= 0",
        name="ck_model_attempts_usage",
    ),
    CheckConstraint(
        "(input_tokens IS NULL AND output_tokens IS NULL) OR "
        "(input_tokens >= 0 AND output_tokens >= 0)",
        name="ck_model_attempts_tokens",
    ),
    CheckConstraint("trace_id ~ '^[0-9a-f]{32}$'", name="ck_model_attempts_trace_id"),
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
    UniqueConstraint(
        "workspace_id",
        "document_version_id",
        "source_id",
        name="uq_document_sources_workspace_version_source",
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

# API 与 Worker 共享任务、阶段和 Attempt Metadata，确保 Migration 能比较完整外键与约束。
ingestion_jobs = ingestion_tables.ingestion_jobs.to_metadata(metadata)
ingestion_job_stages = ingestion_tables.ingestion_job_stages.to_metadata(metadata)
ingestion_job_attempts = ingestion_tables.ingestion_job_attempts.to_metadata(metadata)

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

approval_policies = Table(
    "approval_policies",
    metadata,
    Column("approval_policy_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("name", String(120), nullable=False),
    Column("status", String(32), nullable=False),
    Column("current_version_id", UUID(as_uuid=True), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint(
        "approval_policy_id",
        "workspace_id",
        name="uq_approval_policies_id_workspace",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_approval_policies_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_approval_policies_creator",
    ),
    ForeignKeyConstraint(
        ["approval_policy_id", "workspace_id", "current_version_id"],
        [
            f"{SCHEMA_TOKEN}.approval_policy_versions.approval_policy_id",
            f"{SCHEMA_TOKEN}.approval_policy_versions.workspace_id",
            f"{SCHEMA_TOKEN}.approval_policy_versions.approval_policy_version_id",
        ],
        name="fk_approval_policies_current_version",
        deferrable=True,
        initially="DEFERRED",
    ),
    CheckConstraint("status IN ('active', 'disabled')", name="ck_approval_policies_status"),
    CheckConstraint("version >= 1", name="ck_approval_policies_version"),
    CheckConstraint(
        "char_length(btrim(name)) BETWEEN 1 AND 120",
        name="ck_approval_policies_name",
    ),
)
Index(
    "uq_approval_policies_workspace_name",
    approval_policies.c.workspace_id,
    func.lower(approval_policies.c.name),
    unique=True,
)
Index(
    "ix_approval_policies_workspace_time",
    approval_policies.c.workspace_id,
    approval_policies.c.updated_at,
)

approval_policy_versions = Table(
    "approval_policy_versions",
    metadata,
    Column("approval_policy_version_id", UUID(as_uuid=True), primary_key=True),
    Column("approval_policy_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("version_number", Integer, nullable=False),
    Column("definition", JSONB, nullable=False),
    Column("definition_digest", String(64), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "approval_policy_id",
        "workspace_id",
        "approval_policy_version_id",
        name="uq_approval_policy_versions_identity",
    ),
    UniqueConstraint(
        "approval_policy_id",
        "version_number",
        name="uq_approval_policy_versions_number",
    ),
    ForeignKeyConstraint(
        ["approval_policy_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.approval_policies.approval_policy_id",
            f"{SCHEMA_TOKEN}.approval_policies.workspace_id",
        ],
        name="fk_approval_policy_versions_policy",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_approval_policy_versions_creator",
    ),
    CheckConstraint(
        "version_number >= 1",
        name="ck_approval_policy_versions_number",
    ),
    CheckConstraint(
        "definition_digest ~ '^[0-9a-f]{64}$'",
        name="ck_approval_policy_versions_digest",
    ),
)
Index(
    "ix_approval_policy_versions_policy_time",
    approval_policy_versions.c.workspace_id,
    approval_policy_versions.c.approval_policy_id,
    approval_policy_versions.c.created_at,
)

workflows = Table(
    "workflows",
    metadata,
    Column("workflow_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("name", String(120), nullable=False),
    Column("description", String(1000), nullable=True),
    Column("status", String(32), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint("workflow_id", "workspace_id", name="uq_workflows_id_workspace"),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_workflows_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_workflows_creator",
    ),
    CheckConstraint("status IN ('active', 'archived')", name="ck_workflows_status"),
    CheckConstraint("version >= 1", name="ck_workflows_version"),
    CheckConstraint(
        "char_length(btrim(name)) BETWEEN 1 AND 120",
        name="ck_workflows_name",
    ),
)
Index(
    "ix_workflows_workspace_time",
    workflows.c.workspace_id,
    workflows.c.updated_at,
)

workflow_drafts = Table(
    "workflow_drafts",
    metadata,
    Column("workflow_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("graph", JSONB, nullable=False),
    Column("graph_digest", String(64), nullable=False),
    Column("validation_errors", JSONB, nullable=False),
    Column("updated_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["workflow_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.workflows.workflow_id", f"{SCHEMA_TOKEN}.workflows.workspace_id"],
        name="fk_workflow_drafts_workflow",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["updated_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_workflow_drafts_updater",
    ),
    CheckConstraint("revision >= 1", name="ck_workflow_drafts_revision"),
    CheckConstraint(
        "graph_digest ~ '^[0-9a-f]{64}$'",
        name="ck_workflow_drafts_digest",
    ),
)

workflow_versions = Table(
    "workflow_versions",
    metadata,
    Column("workflow_version_id", UUID(as_uuid=True), primary_key=True),
    Column("workflow_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("version_number", Integer, nullable=False),
    Column("source_draft_revision", Integer, nullable=False),
    Column("graph", JSONB, nullable=False),
    Column("graph_digest", String(64), nullable=False),
    Column("published_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "workflow_version_id",
        "workspace_id",
        name="uq_workflow_versions_id_workspace",
    ),
    UniqueConstraint(
        "workflow_id",
        "workspace_id",
        "workflow_version_id",
        name="uq_workflow_versions_workflow_version",
    ),
    UniqueConstraint(
        "workflow_id",
        "version_number",
        name="uq_workflow_versions_number",
    ),
    UniqueConstraint(
        "workflow_id",
        "source_draft_revision",
        name="uq_workflow_versions_draft_revision",
    ),
    ForeignKeyConstraint(
        ["workflow_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.workflows.workflow_id", f"{SCHEMA_TOKEN}.workflows.workspace_id"],
        name="fk_workflow_versions_workflow",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["published_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_workflow_versions_publisher",
    ),
    CheckConstraint("version_number >= 1", name="ck_workflow_versions_number"),
    CheckConstraint(
        "source_draft_revision >= 1",
        name="ck_workflow_versions_draft_revision",
    ),
    CheckConstraint(
        "graph_digest ~ '^[0-9a-f]{64}$'",
        name="ck_workflow_versions_digest",
    ),
)
Index(
    "ix_workflow_versions_workflow_time",
    workflow_versions.c.workspace_id,
    workflow_versions.c.workflow_id,
    workflow_versions.c.published_at,
)

workflow_publications = Table(
    "workflow_publications",
    metadata,
    Column("workflow_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("workflow_version_id", UUID(as_uuid=True), nullable=False),
    Column("generation", Integer, nullable=False),
    Column("published_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["workflow_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.workflows.workflow_id", f"{SCHEMA_TOKEN}.workflows.workspace_id"],
        name="fk_workflow_publications_workflow",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["workflow_id", "workspace_id", "workflow_version_id"],
        [
            f"{SCHEMA_TOKEN}.workflow_versions.workflow_id",
            f"{SCHEMA_TOKEN}.workflow_versions.workspace_id",
            f"{SCHEMA_TOKEN}.workflow_versions.workflow_version_id",
        ],
        name="fk_workflow_publications_version",
    ),
    ForeignKeyConstraint(
        ["published_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_workflow_publications_publisher",
    ),
    CheckConstraint("generation >= 1", name="ck_workflow_publications_generation"),
)

workflow_runs = Table(
    "workflow_runs",
    metadata,
    Column("workflow_run_id", UUID(as_uuid=True), primary_key=True),
    Column("workflow_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("workflow_version_id", UUID(as_uuid=True), nullable=False),
    Column("requested_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(32), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("input_payload", JSONB, nullable=False),
    Column("output_payload", JSONB, nullable=True),
    Column("executor_version", String(64), nullable=True),
    Column("execution_budget", JSONB, nullable=True),
    Column("steps_executed", Integer, nullable=False, server_default="0"),
    Column("model_calls", Integer, nullable=False, server_default="0"),
    Column("retrieval_calls", Integer, nullable=False, server_default="0"),
    Column("output_bytes", Integer, nullable=False, server_default="0"),
    Column("trace_id", String(32), nullable=False),
    Column("traceparent", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("error_code", String(128), nullable=True),
    Column("version", Integer, nullable=False),
    UniqueConstraint(
        "workflow_run_id",
        "workspace_id",
        name="uq_workflow_runs_id_workspace",
    ),
    UniqueConstraint(
        "workspace_id",
        "requested_by_account_id",
        "idempotency_key",
        name="uq_workflow_runs_idempotency",
    ),
    ForeignKeyConstraint(
        ["workflow_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.workflows.workflow_id", f"{SCHEMA_TOKEN}.workflows.workspace_id"],
        name="fk_workflow_runs_workflow",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["workflow_id", "workspace_id", "workflow_version_id"],
        [
            f"{SCHEMA_TOKEN}.workflow_versions.workflow_id",
            f"{SCHEMA_TOKEN}.workflow_versions.workspace_id",
            f"{SCHEMA_TOKEN}.workflow_versions.workflow_version_id",
        ],
        name="fk_workflow_runs_version",
    ),
    ForeignKeyConstraint(
        ["requested_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_workflow_runs_requester",
    ),
    CheckConstraint(
        "status IN ('queued', 'running', 'waiting_approval', 'succeeded', 'failed', 'cancelled')",
        name="ck_workflow_runs_status",
    ),
    CheckConstraint(
        "request_hash ~ '^[0-9a-f]{64}$'",
        name="ck_workflow_runs_request_hash",
    ),
    CheckConstraint("version >= 1", name="ck_workflow_runs_version"),
    CheckConstraint(
        "steps_executed >= 0 AND model_calls >= 0 AND retrieval_calls >= 0 AND output_bytes >= 0",
        name="ck_workflow_runs_usage",
    ),
    CheckConstraint(
        "(status IN ('queued', 'running', 'waiting_approval') AND completed_at IS NULL) OR "
        "(status IN ('succeeded', 'failed', 'cancelled') AND completed_at IS NOT NULL)",
        name="ck_workflow_runs_completion",
    ),
)
Index(
    "ix_workflow_runs_workflow_time",
    workflow_runs.c.workspace_id,
    workflow_runs.c.workflow_id,
    workflow_runs.c.created_at,
)

workflow_run_steps = Table(
    "workflow_run_steps",
    metadata,
    Column("workflow_step_id", UUID(as_uuid=True), primary_key=True),
    Column("workflow_run_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("node_id", String(64), nullable=False),
    Column("node_type", String(32), nullable=False),
    Column("sequence_no", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("input_payload", JSONB, nullable=True),
    Column("output_payload", JSONB, nullable=True),
    Column("branch_key", String(64), nullable=True),
    Column("policy_decision_id", UUID(as_uuid=True), nullable=True),
    Column("policy_version", Integer, nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("error_code", String(128), nullable=True),
    UniqueConstraint(
        "workflow_run_id",
        "node_id",
        name="uq_workflow_run_steps_node",
    ),
    UniqueConstraint(
        "workflow_step_id",
        "workflow_run_id",
        "workspace_id",
        name="uq_workflow_run_steps_identity",
    ),
    ForeignKeyConstraint(
        ["workflow_run_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.workflow_runs.workflow_run_id",
            f"{SCHEMA_TOKEN}.workflow_runs.workspace_id",
        ],
        name="fk_workflow_run_steps_run",
        ondelete="CASCADE",
    ),
    CheckConstraint("sequence_no >= 1", name="ck_workflow_run_steps_sequence"),
    CheckConstraint(
        "node_type IN "
        "('trigger', 'condition', 'knowledge_retrieval', 'model', 'approval', 'result')",
        name="ck_workflow_run_steps_node_type",
    ),
    CheckConstraint(
        "status IN ('running', 'waiting_approval', 'succeeded', 'skipped', 'failed')",
        name="ck_workflow_run_steps_status",
    ),
    CheckConstraint(
        "(status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL) OR "
        "(status = 'waiting_approval' AND started_at IS NOT NULL AND completed_at IS NULL) OR "
        "(status IN ('succeeded', 'failed') AND started_at IS NOT NULL "
        "AND completed_at IS NOT NULL) OR "
        "(status = 'skipped' AND started_at IS NULL AND completed_at IS NOT NULL)",
        name="ck_workflow_run_steps_timestamps",
    ),
)
Index(
    "ix_workflow_run_steps_run_sequence",
    workflow_run_steps.c.workflow_run_id,
    workflow_run_steps.c.sequence_no,
)

workflow_node_attempts = Table(
    "workflow_node_attempts",
    metadata,
    Column("workflow_attempt_id", UUID(as_uuid=True), primary_key=True),
    Column("workflow_step_id", UUID(as_uuid=True), nullable=False),
    Column("workflow_run_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("attempt_no", Integer, nullable=False),
    Column("executor_version", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("input_hash", String(64), nullable=False),
    Column("output_hash", String(64), nullable=True),
    Column("usage", JSONB, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("error_code", String(128), nullable=True),
    UniqueConstraint(
        "workflow_step_id",
        "attempt_no",
        name="uq_workflow_node_attempts_number",
    ),
    ForeignKeyConstraint(
        ["workflow_step_id", "workflow_run_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.workflow_run_steps.workflow_step_id",
            f"{SCHEMA_TOKEN}.workflow_run_steps.workflow_run_id",
            f"{SCHEMA_TOKEN}.workflow_run_steps.workspace_id",
        ],
        name="fk_workflow_node_attempts_step",
        ondelete="CASCADE",
    ),
    CheckConstraint("attempt_no >= 1", name="ck_workflow_node_attempts_number"),
    CheckConstraint(
        "status IN ('running', 'succeeded', 'failed')",
        name="ck_workflow_node_attempts_status",
    ),
    CheckConstraint(
        "input_hash ~ '^[0-9a-f]{64}$'",
        name="ck_workflow_node_attempts_input_hash",
    ),
    CheckConstraint(
        "output_hash IS NULL OR output_hash ~ '^[0-9a-f]{64}$'",
        name="ck_workflow_node_attempts_output_hash",
    ),
    CheckConstraint(
        "(status = 'running' AND completed_at IS NULL) OR "
        "(status IN ('succeeded', 'failed') AND completed_at IS NOT NULL)",
        name="ck_workflow_node_attempts_completion",
    ),
)
Index(
    "ix_workflow_node_attempts_run",
    workflow_node_attempts.c.workflow_run_id,
    workflow_node_attempts.c.started_at,
)

approval_instances = Table(
    "approval_instances",
    metadata,
    Column("approval_instance_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("approval_policy_id", UUID(as_uuid=True), nullable=True),
    Column("approval_policy_version_id", UUID(as_uuid=True), nullable=True),
    Column("requester_account_id", UUID(as_uuid=True), nullable=False),
    Column("resource_type", String(128), nullable=False),
    Column("operation", String(128), nullable=False),
    Column("resource_id", UUID(as_uuid=True), nullable=True),
    Column("subject_digest", String(64), nullable=False),
    Column("chain_digest", String(64), nullable=False),
    Column("personal_owner_confirmation", Boolean, nullable=False),
    Column("status", String(32), nullable=False),
    Column("current_sequence_no", Integer, nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("workflow_run_id", UUID(as_uuid=True), nullable=True),
    Column("workflow_step_id", UUID(as_uuid=True), nullable=True),
    Column("trace_id", String(32), nullable=False),
    Column("traceparent", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("version", Integer, nullable=False),
    UniqueConstraint(
        "approval_instance_id",
        "workspace_id",
        name="uq_approval_instances_id_workspace",
    ),
    UniqueConstraint(
        "workspace_id",
        "requester_account_id",
        "idempotency_key",
        name="uq_approval_instances_request_idempotency",
    ),
    UniqueConstraint(
        "workflow_step_id",
        name="uq_approval_instances_workflow_step",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_approval_instances_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["requester_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_approval_instances_requester",
    ),
    ForeignKeyConstraint(
        ["approval_policy_id", "workspace_id", "approval_policy_version_id"],
        [
            f"{SCHEMA_TOKEN}.approval_policy_versions.approval_policy_id",
            f"{SCHEMA_TOKEN}.approval_policy_versions.workspace_id",
            f"{SCHEMA_TOKEN}.approval_policy_versions.approval_policy_version_id",
        ],
        name="fk_approval_instances_policy_version",
    ),
    ForeignKeyConstraint(
        ["workflow_run_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.workflow_runs.workflow_run_id",
            f"{SCHEMA_TOKEN}.workflow_runs.workspace_id",
        ],
        name="fk_approval_instances_workflow_run",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["workflow_step_id", "workflow_run_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.workflow_run_steps.workflow_step_id",
            f"{SCHEMA_TOKEN}.workflow_run_steps.workflow_run_id",
            f"{SCHEMA_TOKEN}.workflow_run_steps.workspace_id",
        ],
        name="fk_approval_instances_workflow_step",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "status IN ('pending', 'approved', 'rejected', 'withdrawn')",
        name="ck_approval_instances_status",
    ),
    CheckConstraint("current_sequence_no BETWEEN 1 AND 5", name="ck_approval_instances_sequence"),
    CheckConstraint("version >= 1", name="ck_approval_instances_version"),
    CheckConstraint(
        "subject_digest ~ '^[0-9a-f]{64}$' AND chain_digest ~ '^[0-9a-f]{64}$' "
        "AND request_hash ~ '^[0-9a-f]{64}$'",
        name="ck_approval_instances_digests",
    ),
    CheckConstraint(
        "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
        name="ck_approval_instances_idempotency",
    ),
    CheckConstraint(
        "(approval_policy_id IS NULL AND approval_policy_version_id IS NULL) OR "
        "(approval_policy_id IS NOT NULL AND approval_policy_version_id IS NOT NULL)",
        name="ck_approval_instances_policy_pair",
    ),
    CheckConstraint(
        "(workflow_run_id IS NULL AND workflow_step_id IS NULL) OR "
        "(workflow_run_id IS NOT NULL AND workflow_step_id IS NOT NULL)",
        name="ck_approval_instances_workflow_pair",
    ),
    CheckConstraint(
        "(status = 'pending' AND completed_at IS NULL) OR "
        "(status IN ('approved', 'rejected', 'withdrawn') AND completed_at IS NOT NULL)",
        name="ck_approval_instances_completion",
    ),
)
Index(
    "ix_approval_instances_workspace_time",
    approval_instances.c.workspace_id,
    approval_instances.c.created_at,
)
Index(
    "ix_approval_instances_requester_status",
    approval_instances.c.workspace_id,
    approval_instances.c.requester_account_id,
    approval_instances.c.status,
)

approval_instance_levels = Table(
    "approval_instance_levels",
    metadata,
    Column("approval_level_id", UUID(as_uuid=True), primary_key=True),
    Column("approval_instance_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("sequence_no", Integer, nullable=False),
    Column("mode", String(16), nullable=False),
    Column("status", String(32), nullable=False),
    Column("reminder_after_minutes", Integer, nullable=False),
    Column("timeout_after_minutes", Integer, nullable=False),
    Column("timeout_action", String(32), nullable=False),
    Column("fallback_approver_account_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
    Column("fallback_activated", Boolean, nullable=False),
    Column("reminder_at", DateTime(timezone=True), nullable=True),
    Column("reminded_at", DateTime(timezone=True), nullable=True),
    Column("timeout_at", DateTime(timezone=True), nullable=True),
    Column("activated_at", DateTime(timezone=True), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("version", Integer, nullable=False),
    UniqueConstraint(
        "approval_instance_id",
        "sequence_no",
        name="uq_approval_instance_levels_sequence",
    ),
    UniqueConstraint(
        "approval_level_id",
        "approval_instance_id",
        "workspace_id",
        name="uq_approval_instance_levels_identity",
    ),
    ForeignKeyConstraint(
        ["approval_instance_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.approval_instances.approval_instance_id",
            f"{SCHEMA_TOKEN}.approval_instances.workspace_id",
        ],
        name="fk_approval_instance_levels_instance",
        ondelete="CASCADE",
    ),
    CheckConstraint("sequence_no BETWEEN 1 AND 5", name="ck_approval_instance_levels_sequence"),
    CheckConstraint("mode IN ('any', 'all')", name="ck_approval_instance_levels_mode"),
    CheckConstraint(
        "status IN ('waiting', 'active', 'approved', 'rejected', 'withdrawn')",
        name="ck_approval_instance_levels_status",
    ),
    CheckConstraint(
        "timeout_action IN ('escalate', 'transfer', 'reject', 'wait')",
        name="ck_approval_instance_levels_timeout_action",
    ),
    CheckConstraint(
        "reminder_after_minutes >= 1 AND timeout_after_minutes > reminder_after_minutes",
        name="ck_approval_instance_levels_timeouts",
    ),
    CheckConstraint("version >= 1", name="ck_approval_instance_levels_version"),
)
Index(
    "ix_approval_instance_levels_due",
    approval_instance_levels.c.workspace_id,
    approval_instance_levels.c.status,
    approval_instance_levels.c.timeout_at,
    approval_instance_levels.c.reminder_at,
)

approval_assignments = Table(
    "approval_assignments",
    metadata,
    Column("approval_assignment_id", UUID(as_uuid=True), primary_key=True),
    Column("approval_instance_id", UUID(as_uuid=True), nullable=False),
    Column("approval_level_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("approver_account_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(32), nullable=False),
    Column("transferred_to_account_id", UUID(as_uuid=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("decided_at", DateTime(timezone=True), nullable=True),
    Column("version", Integer, nullable=False),
    UniqueConstraint(
        "approval_level_id",
        "approver_account_id",
        name="uq_approval_assignments_level_approver",
    ),
    UniqueConstraint(
        "approval_assignment_id",
        "approval_instance_id",
        "workspace_id",
        name="uq_approval_assignments_identity",
    ),
    ForeignKeyConstraint(
        ["approval_level_id", "approval_instance_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.approval_instance_levels.approval_level_id",
            f"{SCHEMA_TOKEN}.approval_instance_levels.approval_instance_id",
            f"{SCHEMA_TOKEN}.approval_instance_levels.workspace_id",
        ],
        name="fk_approval_assignments_level",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["approver_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_approval_assignments_approver",
    ),
    ForeignKeyConstraint(
        ["transferred_to_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_approval_assignments_transfer_target",
    ),
    CheckConstraint(
        "status IN ('waiting', 'pending', 'approved', 'rejected', 'transferred', 'cancelled')",
        name="ck_approval_assignments_status",
    ),
    CheckConstraint(
        "(status = 'transferred' AND transferred_to_account_id IS NOT NULL) OR "
        "(status != 'transferred' AND transferred_to_account_id IS NULL)",
        name="ck_approval_assignments_transfer",
    ),
    CheckConstraint("version >= 1", name="ck_approval_assignments_version"),
)
Index(
    "ix_approval_assignments_account_status",
    approval_assignments.c.workspace_id,
    approval_assignments.c.approver_account_id,
    approval_assignments.c.status,
)

approval_actions = Table(
    "approval_actions",
    metadata,
    Column("approval_action_id", UUID(as_uuid=True), primary_key=True),
    Column("approval_instance_id", UUID(as_uuid=True), nullable=False),
    Column("approval_level_id", UUID(as_uuid=True), nullable=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("actor_account_id", UUID(as_uuid=True), nullable=False),
    Column("action", String(32), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("target_account_id", UUID(as_uuid=True), nullable=True),
    Column("reason_code", String(128), nullable=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "approval_instance_id",
        "actor_account_id",
        "idempotency_key",
        name="uq_approval_actions_idempotency",
    ),
    ForeignKeyConstraint(
        ["approval_instance_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.approval_instances.approval_instance_id",
            f"{SCHEMA_TOKEN}.approval_instances.workspace_id",
        ],
        name="fk_approval_actions_instance",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["approval_level_id", "approval_instance_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.approval_instance_levels.approval_level_id",
            f"{SCHEMA_TOKEN}.approval_instance_levels.approval_instance_id",
            f"{SCHEMA_TOKEN}.approval_instance_levels.workspace_id",
        ],
        name="fk_approval_actions_level",
    ),
    ForeignKeyConstraint(
        ["actor_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_approval_actions_actor",
    ),
    ForeignKeyConstraint(
        ["target_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_approval_actions_target",
    ),
    CheckConstraint(
        "action IN ('approve', 'reject', 'transfer', 'withdraw', 'remind', 'escalate', "
        "'timeout_transfer', 'timeout_reject', 'timeout_wait')",
        name="ck_approval_actions_action",
    ),
    CheckConstraint(
        "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
        name="ck_approval_actions_idempotency",
    ),
    CheckConstraint(
        "reason_code IS NULL OR reason_code ~ '^[a-z][a-z0-9_.-]{0,127}$'",
        name="ck_approval_actions_reason",
    ),
)
Index(
    "ix_approval_actions_instance_time",
    approval_actions.c.approval_instance_id,
    approval_actions.c.occurred_at,
)

index_versions = indexing_tables.index_versions.to_metadata(metadata)
document_index_publications = indexing_tables.document_index_publications.to_metadata(metadata)
index_maintenance_runs = indexing_tables.index_maintenance_runs.to_metadata(metadata)
index_maintenance_requests = indexing_tables.index_maintenance_requests.to_metadata(metadata)
index_inspection_findings = indexing_tables.index_inspection_findings.to_metadata(metadata)
retrieval_chunks = indexing_tables.retrieval_chunks.to_metadata(metadata)

agent_prompt_versions = Table(
    "agent_prompt_versions",
    metadata,
    Column("prompt_version_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("name", String(120), nullable=False),
    Column("template", Text, nullable=False),
    Column("prompt_hash", String(64), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "prompt_version_id",
        "workspace_id",
        name="uq_agent_prompt_versions_id_workspace",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_agent_prompt_versions_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_agent_prompt_versions_creator",
    ),
    CheckConstraint(
        "char_length(btrim(name)) BETWEEN 1 AND 120",
        name="ck_agent_prompt_versions_name",
    ),
    CheckConstraint(
        "char_length(btrim(template)) BETWEEN 1 AND 32000",
        name="ck_agent_prompt_versions_template",
    ),
    CheckConstraint(
        "prompt_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_prompt_versions_hash",
    ),
)

agent_knowledge_scope_versions = Table(
    "agent_knowledge_scope_versions",
    metadata,
    Column("knowledge_scope_version_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("name", String(120), nullable=False),
    Column("scope_hash", String(64), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "knowledge_scope_version_id",
        "workspace_id",
        name="uq_agent_knowledge_scopes_id_workspace",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_agent_knowledge_scopes_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_agent_knowledge_scopes_creator",
    ),
    CheckConstraint(
        "char_length(btrim(name)) BETWEEN 1 AND 120",
        name="ck_agent_knowledge_scopes_name",
    ),
    CheckConstraint(
        "scope_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_knowledge_scopes_hash",
    ),
)

agent_knowledge_scope_items = Table(
    "agent_knowledge_scope_items",
    metadata,
    Column("knowledge_scope_version_id", UUID(as_uuid=True), primary_key=True),
    Column("knowledge_base_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("position", Integer, nullable=False),
    UniqueConstraint(
        "knowledge_scope_version_id",
        "position",
        name="uq_agent_knowledge_scope_items_position",
    ),
    ForeignKeyConstraint(
        ["knowledge_scope_version_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_knowledge_scope_versions.knowledge_scope_version_id",
            f"{SCHEMA_TOKEN}.agent_knowledge_scope_versions.workspace_id",
        ],
        name="fk_agent_knowledge_scope_items_scope",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["knowledge_base_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.knowledge_bases.knowledge_base_id",
            f"{SCHEMA_TOKEN}.knowledge_bases.workspace_id",
        ],
        name="fk_agent_knowledge_scope_items_base",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "position BETWEEN 1 AND 50",
        name="ck_agent_knowledge_scope_items_position",
    ),
)

agent_output_schema_versions = Table(
    "agent_output_schema_versions",
    metadata,
    Column("output_schema_version_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("name", String(120), nullable=False),
    Column("schema_document", JSONB, nullable=False),
    Column("schema_hash", String(64), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "output_schema_version_id",
        "workspace_id",
        name="uq_agent_output_schemas_id_workspace",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_agent_output_schemas_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_agent_output_schemas_creator",
    ),
    CheckConstraint(
        "char_length(btrim(name)) BETWEEN 1 AND 120",
        name="ck_agent_output_schemas_name",
    ),
    CheckConstraint(
        "jsonb_typeof(schema_document) = 'object'",
        name="ck_agent_output_schemas_document",
    ),
    CheckConstraint(
        "schema_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_output_schemas_hash",
    ),
)

agent_safety_policy_versions = Table(
    "agent_safety_policy_versions",
    metadata,
    Column("safety_policy_version_id", UUID(as_uuid=True), primary_key=True),
    Column("policy_key", String(64), nullable=False),
    Column("version_number", Integer, nullable=False),
    Column("implementation_version", String(64), nullable=False),
    Column("policy_hash", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    UniqueConstraint(
        "policy_key",
        "version_number",
        name="uq_agent_safety_policies_key_version",
    ),
    CheckConstraint("version_number >= 1", name="ck_agent_safety_policies_version"),
    CheckConstraint(
        "policy_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_safety_policies_hash",
    ),
    CheckConstraint(
        "status IN ('active', 'retired')",
        name="ck_agent_safety_policies_status",
    ),
)

agent_tool_definitions = Table(
    "agent_tool_definitions",
    metadata,
    Column("tool_id", UUID(as_uuid=True), primary_key=True),
    Column("tool_version", Integer, primary_key=True),
    Column("tool_key", String(120), nullable=False),
    Column("display_name", String(120), nullable=False),
    Column("description", String(500), nullable=False),
    Column("access_mode", String(16), nullable=False),
    Column("risk_level", String(16), nullable=False),
    Column("adapter_kind", String(32), nullable=False),
    Column("input_schema_document", JSONB, nullable=False),
    Column("input_schema_hash", String(64), nullable=False),
    Column("output_schema_document", JSONB, nullable=False),
    Column("output_schema_hash", String(64), nullable=False),
    Column("permission_code", String(160), nullable=False),
    Column("credential_requirement", String(32), nullable=False),
    Column("timeout_seconds", Integer, nullable=False),
    Column("retry_mode", String(32), nullable=False),
    Column("status", String(16), nullable=False),
    Column("definition_hash", String(64), nullable=False),
    Column("synthetic", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "tool_key",
        "tool_version",
        name="uq_agent_tool_definitions_key_version",
    ),
    UniqueConstraint(
        "tool_id",
        "tool_version",
        "permission_code",
        name="uq_agent_tool_definitions_policy_binding",
    ),
    CheckConstraint("tool_version >= 1", name="ck_agent_tool_definitions_version"),
    CheckConstraint(
        "access_mode IN ('read', 'write')",
        name="ck_agent_tool_definitions_access_mode",
    ),
    CheckConstraint(
        "permission_code ~ '^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*){2,}$'",
        name="ck_agent_tool_definitions_permission",
    ),
    CheckConstraint(
        "char_length(btrim(display_name)) BETWEEN 1 AND 120",
        name="ck_agent_tool_definitions_display_name",
    ),
    CheckConstraint(
        "char_length(btrim(description)) BETWEEN 1 AND 500",
        name="ck_agent_tool_definitions_description",
    ),
    CheckConstraint(
        "risk_level IN ('low', 'medium', 'high', 'critical')",
        name="ck_agent_tool_definitions_risk",
    ),
    CheckConstraint(
        "adapter_kind IN ('internal_read', 'synthetic_internal_write')",
        name="ck_agent_tool_definitions_adapter",
    ),
    CheckConstraint(
        "jsonb_typeof(input_schema_document) = 'object' "
        "AND input_schema_document ->> '$schema' = "
        "'https://json-schema.org/draft/2020-12/schema' "
        "AND input_schema_document ->> 'type' = 'object' "
        "AND input_schema_document -> 'additionalProperties' = 'false'::jsonb "
        "AND pg_column_size(input_schema_document) <= 32768",
        name="ck_agent_tool_definitions_input_schema",
    ),
    CheckConstraint(
        "jsonb_typeof(output_schema_document) = 'object' "
        "AND output_schema_document ->> '$schema' = "
        "'https://json-schema.org/draft/2020-12/schema' "
        "AND output_schema_document ->> 'type' = 'object' "
        "AND output_schema_document -> 'additionalProperties' = 'false'::jsonb "
        "AND pg_column_size(output_schema_document) <= 32768",
        name="ck_agent_tool_definitions_output_schema",
    ),
    CheckConstraint(
        "input_schema_hash ~ '^[0-9a-f]{64}$' "
        "AND output_schema_hash ~ '^[0-9a-f]{64}$' "
        "AND definition_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_tool_definitions_hashes",
    ),
    CheckConstraint(
        "credential_requirement IN ('none', 'credential_ref')",
        name="ck_agent_tool_definitions_credential",
    ),
    CheckConstraint(
        "timeout_seconds BETWEEN 1 AND 120",
        name="ck_agent_tool_definitions_timeout",
    ),
    CheckConstraint(
        "retry_mode IN ('none', 'safe_read', 'idempotent_write')",
        name="ck_agent_tool_definitions_retry",
    ),
    CheckConstraint(
        "status IN ('active', 'retired')",
        name="ck_agent_tool_definitions_status",
    ),
    CheckConstraint(
        "(adapter_kind = 'internal_read' AND access_mode = 'read' "
        "AND synthetic = false AND retry_mode IN ('none', 'safe_read')) OR "
        "(adapter_kind = 'synthetic_internal_write' AND access_mode = 'write' "
        "AND synthetic = true AND risk_level IN ('high', 'critical') "
        "AND retry_mode IN ('none', 'idempotent_write'))",
        name="ck_agent_tool_definitions_governance",
    ),
)

tool_plan_availability = Table(
    "tool_plan_availability",
    metadata,
    Column("tool_id", UUID(as_uuid=True), primary_key=True),
    Column("tool_version", Integer, primary_key=True),
    Column("plan_code", String(64), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["tool_id", "tool_version"],
        [
            f"{SCHEMA_TOKEN}.agent_tool_definitions.tool_id",
            f"{SCHEMA_TOKEN}.agent_tool_definitions.tool_version",
        ],
        name="fk_tool_plan_availability_definition",
    ),
    CheckConstraint(
        "plan_code ~ '^[a-z][a-z0-9_]{2,63}$'",
        name="ck_tool_plan_availability_plan_code",
    ),
)

tool_runs = Table(
    "tool_runs",
    metadata,
    Column("run_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("requested_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("requested_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("service_id", UUID(as_uuid=True), nullable=False),
    Column("agent_release_id", UUID(as_uuid=True), nullable=False),
    Column("state", String(32), nullable=False),
    Column("max_steps", Integer, nullable=False),
    Column("max_attempts_per_step", Integer, nullable=False),
    Column("max_execution_seconds", Integer, nullable=False),
    Column("max_cost_microunits", BigInteger, nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("traceparent", String(55), nullable=False),
    Column("cancel_requested_at", DateTime(timezone=True), nullable=True),
    Column("deadline_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("version", Integer, nullable=False),
    UniqueConstraint("run_id", "workspace_id", name="uq_tool_runs_id_workspace"),
    UniqueConstraint(
        "workspace_id",
        "requested_by_actor_id",
        "idempotency_key",
        name="uq_tool_runs_idempotency",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_tool_runs_workspace",
    ),
    ForeignKeyConstraint(
        ["requested_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_tool_runs_requester",
    ),
    ForeignKeyConstraint(
        ["service_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.services.service_id", f"{SCHEMA_TOKEN}.services.workspace_id"],
        name="fk_tool_runs_service",
    ),
    ForeignKeyConstraint(
        ["agent_release_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_releases.release_id",
            f"{SCHEMA_TOKEN}.agent_releases.workspace_id",
        ],
        name="fk_tool_runs_agent_release",
    ),
    CheckConstraint(
        "state IN ('pending', 'planning', 'running', 'waiting_confirmation', "
        "'waiting_approval', 'cancellation_requested', 'completed', 'failed', "
        "'cancelled', 'timed_out')",
        name="ck_tool_runs_state",
    ),
    CheckConstraint(
        "max_steps BETWEEN 1 AND 50 AND max_attempts_per_step BETWEEN 1 AND 5 "
        "AND max_execution_seconds BETWEEN 1 AND 1800 AND max_cost_microunits >= 0",
        name="ck_tool_runs_budget",
    ),
    CheckConstraint(
        "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$' "
        "AND request_hash ~ '^[0-9a-f]{64}$'",
        name="ck_tool_runs_idempotency",
    ),
    CheckConstraint("trace_id ~ '^[0-9a-f]{32}$'", name="ck_tool_runs_trace_id"),
    CheckConstraint("deadline_at > created_at", name="ck_tool_runs_deadline"),
    CheckConstraint(
        "(state IN ('completed', 'failed', 'cancelled', 'timed_out') "
        "AND completed_at IS NOT NULL) OR "
        "(state NOT IN ('completed', 'failed', 'cancelled', 'timed_out') "
        "AND completed_at IS NULL)",
        name="ck_tool_runs_completion",
    ),
    CheckConstraint(
        "(state IN ('cancellation_requested', 'cancelled') "
        "AND cancel_requested_at IS NOT NULL) OR "
        "(state NOT IN ('cancellation_requested', 'cancelled'))",
        name="ck_tool_runs_cancellation",
    ),
    CheckConstraint("version >= 1", name="ck_tool_runs_version"),
)
Index("ix_tool_runs_workspace_time", tool_runs.c.workspace_id, tool_runs.c.created_at)
Index("ix_tool_runs_claim", tool_runs.c.state, tool_runs.c.deadline_at)

tool_steps = Table(
    "tool_steps",
    metadata,
    Column("step_id", UUID(as_uuid=True), primary_key=True),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("sequence_no", Integer, nullable=False),
    Column("tool_id", UUID(as_uuid=True), nullable=False),
    Column("tool_version", Integer, nullable=False),
    Column("canonical_arguments_hash", String(64), nullable=False),
    Column("timeout_seconds", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    Column("max_result_bytes", Integer, nullable=False),
    Column("max_cost_microunits", BigInteger, nullable=False),
    Column("state", String(32), nullable=False),
    Column("current_attempt_no", Integer, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint("run_id", "sequence_no", name="uq_tool_steps_run_sequence"),
    UniqueConstraint(
        "step_id",
        "run_id",
        "workspace_id",
        name="uq_tool_steps_run_identity",
    ),
    UniqueConstraint(
        "step_id",
        "run_id",
        "workspace_id",
        "tool_id",
        "tool_version",
        "canonical_arguments_hash",
        name="uq_tool_steps_call_binding",
    ),
    ForeignKeyConstraint(
        ["run_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.tool_runs.run_id", f"{SCHEMA_TOKEN}.tool_runs.workspace_id"],
        name="fk_tool_steps_run",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tool_id", "tool_version"],
        [
            f"{SCHEMA_TOKEN}.agent_tool_definitions.tool_id",
            f"{SCHEMA_TOKEN}.agent_tool_definitions.tool_version",
        ],
        name="fk_tool_steps_definition",
    ),
    CheckConstraint("sequence_no >= 1", name="ck_tool_steps_sequence"),
    CheckConstraint("tool_version >= 1", name="ck_tool_steps_tool_version"),
    CheckConstraint(
        "canonical_arguments_hash ~ '^[0-9a-f]{64}$'",
        name="ck_tool_steps_arguments_hash",
    ),
    CheckConstraint(
        "timeout_seconds BETWEEN 1 AND 1800 AND max_attempts BETWEEN 1 AND 5 "
        "AND max_result_bytes BETWEEN 1 AND 262144 AND max_cost_microunits >= 0",
        name="ck_tool_steps_budget",
    ),
    CheckConstraint(
        "state IN ('planned', 'policy_checking', 'waiting_confirmation', "
        "'waiting_approval', 'ready', 'running', 'completed', 'failed', "
        "'cancelled', 'timed_out')",
        name="ck_tool_steps_state",
    ),
    CheckConstraint(
        "current_attempt_no IS NULL OR current_attempt_no BETWEEN 1 AND 5",
        name="ck_tool_steps_current_attempt",
    ),
    CheckConstraint(
        "state <> 'running' OR current_attempt_no IS NOT NULL",
        name="ck_tool_steps_running_attempt",
    ),
    CheckConstraint("version >= 1", name="ck_tool_steps_version"),
)
Index("ix_tool_steps_claim", tool_steps.c.state, tool_steps.c.created_at)

tool_policy_decisions = Table(
    "tool_policy_decisions",
    metadata,
    Column("decision_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("step_id", UUID(as_uuid=True), nullable=False),
    Column("tool_id", UUID(as_uuid=True), nullable=False),
    Column("tool_version", Integer, nullable=False),
    Column("canonical_arguments_hash", String(64), nullable=False),
    Column("permission_code", String(160), nullable=False),
    Column("policy_version", Integer, nullable=False),
    Column("resource_scope_hash", String(64), nullable=False),
    Column("field_mask_hash", String(64), nullable=False),
    Column("evaluated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "step_id",
        "evaluated_at",
        name="uq_tool_policy_decisions_step_time",
    ),
    ForeignKeyConstraint(
        [
            "step_id",
            "run_id",
            "workspace_id",
            "tool_id",
            "tool_version",
            "canonical_arguments_hash",
        ],
        [
            f"{SCHEMA_TOKEN}.tool_steps.step_id",
            f"{SCHEMA_TOKEN}.tool_steps.run_id",
            f"{SCHEMA_TOKEN}.tool_steps.workspace_id",
            f"{SCHEMA_TOKEN}.tool_steps.tool_id",
            f"{SCHEMA_TOKEN}.tool_steps.tool_version",
            f"{SCHEMA_TOKEN}.tool_steps.canonical_arguments_hash",
        ],
        name="fk_tool_policy_decisions_step",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tool_id", "tool_version", "permission_code"],
        [
            f"{SCHEMA_TOKEN}.agent_tool_definitions.tool_id",
            f"{SCHEMA_TOKEN}.agent_tool_definitions.tool_version",
            f"{SCHEMA_TOKEN}.agent_tool_definitions.permission_code",
        ],
        name="fk_tool_policy_decisions_definition",
    ),
    CheckConstraint("tool_version >= 1", name="ck_tool_policy_decisions_tool_version"),
    CheckConstraint(
        "canonical_arguments_hash ~ '^[0-9a-f]{64}$' "
        "AND resource_scope_hash ~ '^[0-9a-f]{64}$' "
        "AND field_mask_hash ~ '^[0-9a-f]{64}$'",
        name="ck_tool_policy_decisions_hashes",
    ),
    CheckConstraint(
        "permission_code ~ '^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*){2,}$'",
        name="ck_tool_policy_decisions_permission",
    ),
    CheckConstraint("policy_version >= 1", name="ck_tool_policy_decisions_version"),
)
Index(
    "ix_tool_policy_decisions_workspace_time",
    tool_policy_decisions.c.workspace_id,
    tool_policy_decisions.c.evaluated_at,
)

tool_confirmations = Table(
    "tool_confirmations",
    metadata,
    Column("confirmation_id", UUID(as_uuid=True), primary_key=True),
    Column("approval_instance_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("step_id", UUID(as_uuid=True), nullable=False),
    Column("tool_id", UUID(as_uuid=True), nullable=False),
    Column("tool_version", Integer, nullable=False),
    Column("canonical_arguments_hash", String(64), nullable=False),
    Column("mode", String(32), nullable=False),
    Column("policy_decision_id", UUID(as_uuid=True), nullable=False),
    Column("permission_code", String(160), nullable=False),
    Column("policy_version", Integer, nullable=False),
    Column("resource_scope_hash", String(64), nullable=False),
    Column("field_mask_hash", String(64), nullable=False),
    Column("policy_evaluated_at", DateTime(timezone=True), nullable=False),
    Column("risk_level", String(16), nullable=False),
    Column("confirmation_hash", String(64), nullable=False),
    Column("subject_digest", String(64), nullable=False),
    Column("chain_digest", String(64), nullable=False),
    Column("state", String(16), nullable=False),
    Column("confirmed_by_actor_id", UUID(as_uuid=True), nullable=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint("approval_instance_id", name="uq_tool_confirmations_approval"),
    UniqueConstraint("step_id", name="uq_tool_confirmations_step"),
    UniqueConstraint(
        "confirmation_id",
        "workspace_id",
        "run_id",
        "step_id",
        name="uq_tool_confirmations_identity",
    ),
    ForeignKeyConstraint(
        ["approval_instance_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.approval_instances.approval_instance_id",
            f"{SCHEMA_TOKEN}.approval_instances.workspace_id",
        ],
        name="fk_tool_confirmations_approval",
    ),
    ForeignKeyConstraint(
        [
            "step_id",
            "run_id",
            "workspace_id",
            "tool_id",
            "tool_version",
            "canonical_arguments_hash",
        ],
        [
            f"{SCHEMA_TOKEN}.tool_steps.step_id",
            f"{SCHEMA_TOKEN}.tool_steps.run_id",
            f"{SCHEMA_TOKEN}.tool_steps.workspace_id",
            f"{SCHEMA_TOKEN}.tool_steps.tool_id",
            f"{SCHEMA_TOKEN}.tool_steps.tool_version",
            f"{SCHEMA_TOKEN}.tool_steps.canonical_arguments_hash",
        ],
        name="fk_tool_confirmations_step",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["policy_decision_id"],
        [f"{SCHEMA_TOKEN}.tool_policy_decisions.decision_id"],
        name="fk_tool_confirmations_policy",
    ),
    ForeignKeyConstraint(
        ["confirmed_by_actor_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_tool_confirmations_actor",
    ),
    CheckConstraint(
        "mode IN ('personal_owner', 'enterprise_approval')",
        name="ck_tool_confirmations_mode",
    ),
    CheckConstraint(
        "risk_level IN ('high', 'critical')",
        name="ck_tool_confirmations_risk",
    ),
    CheckConstraint(
        "canonical_arguments_hash ~ '^[0-9a-f]{64}$' "
        "AND resource_scope_hash ~ '^[0-9a-f]{64}$' "
        "AND field_mask_hash ~ '^[0-9a-f]{64}$' "
        "AND confirmation_hash ~ '^[0-9a-f]{64}$' "
        "AND subject_digest ~ '^[0-9a-f]{64}$' "
        "AND chain_digest ~ '^[0-9a-f]{64}$'",
        name="ck_tool_confirmations_hashes",
    ),
    CheckConstraint(
        "permission_code ~ '^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*){2,}$'",
        name="ck_tool_confirmations_permission",
    ),
    CheckConstraint(
        "tool_version >= 1 AND policy_version >= 1", name="ck_tool_confirmations_versions"
    ),
    CheckConstraint(
        "state IN ('pending', 'approved', 'rejected', 'expired', 'withdrawn')",
        name="ck_tool_confirmations_state",
    ),
    CheckConstraint(
        "(state = 'pending' AND resolved_at IS NULL AND confirmed_by_actor_id IS NULL) OR "
        "(state <> 'pending' AND resolved_at IS NOT NULL)",
        name="ck_tool_confirmations_resolution",
    ),
    CheckConstraint(
        "expires_at > created_at AND updated_at >= created_at",
        name="ck_tool_confirmations_time",
    ),
    CheckConstraint("version >= 1", name="ck_tool_confirmations_version"),
)
Index(
    "ix_tool_confirmations_workspace_time",
    tool_confirmations.c.workspace_id,
    tool_confirmations.c.created_at,
)

tool_confirmation_invalidations = Table(
    "tool_confirmation_invalidations",
    metadata,
    Column("invalidation_id", UUID(as_uuid=True), primary_key=True),
    Column("confirmation_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("step_id", UUID(as_uuid=True), nullable=False),
    Column("state", String(16), nullable=False),
    Column("reason_code", String(32), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("confirmation_id", name="uq_tool_confirmation_invalidations_confirmation"),
    ForeignKeyConstraint(
        ["confirmation_id", "workspace_id", "run_id", "step_id"],
        [
            f"{SCHEMA_TOKEN}.tool_confirmations.confirmation_id",
            f"{SCHEMA_TOKEN}.tool_confirmations.workspace_id",
            f"{SCHEMA_TOKEN}.tool_confirmations.run_id",
            f"{SCHEMA_TOKEN}.tool_confirmations.step_id",
        ],
        name="fk_tool_confirmation_invalidations_confirmation",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "state IN ('expired', 'withdrawn')",
        name="ck_tool_confirmation_invalidations_state",
    ),
    CheckConstraint(
        "reason_code IN ('arguments_changed', 'tool_changed', 'policy_changed', "
        "'permission_revoked', 'expired')",
        name="ck_tool_confirmation_invalidations_reason",
    ),
)
Index(
    "ix_tool_confirmation_invalidations_workspace_time",
    tool_confirmation_invalidations.c.workspace_id,
    tool_confirmation_invalidations.c.occurred_at,
)

tool_credentials = Table(
    "tool_credentials",
    metadata,
    Column("credential_id", UUID(as_uuid=True), primary_key=True),
    Column("credential_ref", String(69), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("tool_id", UUID(as_uuid=True), nullable=False),
    Column("tool_version", Integer, nullable=False),
    Column("credential_version", Integer, nullable=False),
    Column("master_key_version", Integer, nullable=False),
    Column("encrypted_data_key", LargeBinary, nullable=False),
    Column("data_key_nonce", LargeBinary, nullable=False),
    Column("ciphertext", LargeBinary, nullable=False),
    Column("data_nonce", LargeBinary, nullable=False),
    Column("last_four", String(4), nullable=False),
    Column("status", String(16), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("revoked_by_account_id", UUID(as_uuid=True), nullable=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("credential_ref", name="uq_tool_credentials_ref"),
    UniqueConstraint(
        "workspace_id",
        "tool_id",
        "tool_version",
        "credential_version",
        name="uq_tool_credentials_version",
    ),
    UniqueConstraint(
        "credential_ref",
        "workspace_id",
        "tool_id",
        "tool_version",
        name="uq_tool_credentials_binding",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_tool_credentials_workspace",
    ),
    ForeignKeyConstraint(
        ["tool_id", "tool_version"],
        [
            f"{SCHEMA_TOKEN}.agent_tool_definitions.tool_id",
            f"{SCHEMA_TOKEN}.agent_tool_definitions.tool_version",
        ],
        name="fk_tool_credentials_definition",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_tool_credentials_creator",
    ),
    ForeignKeyConstraint(
        ["revoked_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_tool_credentials_revoker",
    ),
    CheckConstraint(
        "credential_ref ~ '^cred_[a-z0-9]{16,64}$'",
        name="ck_tool_credentials_ref",
    ),
    CheckConstraint(
        "tool_version >= 1 AND credential_version >= 1 AND master_key_version >= 1",
        name="ck_tool_credentials_versions",
    ),
    CheckConstraint(
        "octet_length(encrypted_data_key) >= 32 AND octet_length(data_key_nonce) = 12 "
        "AND octet_length(ciphertext) BETWEEN 24 AND 4112 "
        "AND octet_length(data_nonce) = 12",
        name="ck_tool_credentials_envelope",
    ),
    CheckConstraint("char_length(last_four) = 4", name="ck_tool_credentials_last_four"),
    CheckConstraint("status IN ('active', 'revoked')", name="ck_tool_credentials_status"),
    CheckConstraint(
        "(status = 'active' AND revoked_by_account_id IS NULL AND revoked_at IS NULL) OR "
        "(status = 'revoked' AND revoked_by_account_id IS NOT NULL "
        "AND revoked_at IS NOT NULL AND revoked_at >= created_at)",
        name="ck_tool_credentials_revocation",
    ),
)
Index(
    "uq_tool_credentials_active",
    tool_credentials.c.workspace_id,
    tool_credentials.c.tool_id,
    tool_credentials.c.tool_version,
    unique=True,
    postgresql_where=tool_credentials.c.status == "active",
)
Index(
    "ix_tool_credentials_workspace_time",
    tool_credentials.c.workspace_id,
    tool_credentials.c.created_at,
)

tool_attempts = Table(
    "tool_attempts",
    metadata,
    Column("attempt_id", UUID(as_uuid=True), primary_key=True),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("step_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("attempt_no", Integer, nullable=False),
    Column("lease_generation", Integer, nullable=False),
    Column("state", String(32), nullable=False),
    Column("worker_id", String(120), nullable=False),
    Column("lease_started_at", DateTime(timezone=True), nullable=False),
    Column("lease_expires_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("error_code", String(128), nullable=True),
    UniqueConstraint("step_id", "attempt_no", name="uq_tool_attempts_step_attempt"),
    UniqueConstraint(
        "attempt_id",
        "step_id",
        "run_id",
        "workspace_id",
        name="uq_tool_attempts_identity",
    ),
    ForeignKeyConstraint(
        ["step_id", "run_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.tool_steps.step_id",
            f"{SCHEMA_TOKEN}.tool_steps.run_id",
            f"{SCHEMA_TOKEN}.tool_steps.workspace_id",
        ],
        name="fk_tool_attempts_step",
        ondelete="CASCADE",
    ),
    CheckConstraint("attempt_no BETWEEN 1 AND 5", name="ck_tool_attempts_number"),
    CheckConstraint("lease_generation >= 1", name="ck_tool_attempts_generation"),
    CheckConstraint(
        "char_length(btrim(worker_id)) BETWEEN 1 AND 120",
        name="ck_tool_attempts_worker",
    ),
    CheckConstraint(
        "lease_expires_at > lease_started_at AND started_at >= lease_started_at",
        name="ck_tool_attempts_lease",
    ),
    CheckConstraint(
        "state IN ('leased', 'executing', 'succeeded', 'failed', 'cancelled', "
        "'timed_out', 'ignored_late_result')",
        name="ck_tool_attempts_state",
    ),
    CheckConstraint(
        "(state IN ('leased', 'executing') AND completed_at IS NULL) OR "
        "(state NOT IN ('leased', 'executing') AND completed_at IS NOT NULL)",
        name="ck_tool_attempts_completion",
    ),
)
Index("ix_tool_attempts_lease", tool_attempts.c.state, tool_attempts.c.lease_expires_at)

tool_calls = Table(
    "tool_calls",
    metadata,
    Column("tool_call_id", UUID(as_uuid=True), primary_key=True),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("step_id", UUID(as_uuid=True), nullable=False),
    Column("attempt_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("tool_id", UUID(as_uuid=True), nullable=False),
    Column("tool_version", Integer, nullable=False),
    Column("canonical_arguments_hash", String(64), nullable=False),
    Column("access_mode", String(16), nullable=False),
    Column("risk_level", String(16), nullable=False),
    Column("credential_ref", String(69), nullable=True),
    Column("state", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("error_code", String(128), nullable=True),
    UniqueConstraint("attempt_id", name="uq_tool_calls_attempt"),
    ForeignKeyConstraint(
        ["attempt_id", "step_id", "run_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.tool_attempts.attempt_id",
            f"{SCHEMA_TOKEN}.tool_attempts.step_id",
            f"{SCHEMA_TOKEN}.tool_attempts.run_id",
            f"{SCHEMA_TOKEN}.tool_attempts.workspace_id",
        ],
        name="fk_tool_calls_attempt",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        [
            "step_id",
            "run_id",
            "workspace_id",
            "tool_id",
            "tool_version",
            "canonical_arguments_hash",
        ],
        [
            f"{SCHEMA_TOKEN}.tool_steps.step_id",
            f"{SCHEMA_TOKEN}.tool_steps.run_id",
            f"{SCHEMA_TOKEN}.tool_steps.workspace_id",
            f"{SCHEMA_TOKEN}.tool_steps.tool_id",
            f"{SCHEMA_TOKEN}.tool_steps.tool_version",
            f"{SCHEMA_TOKEN}.tool_steps.canonical_arguments_hash",
        ],
        name="fk_tool_calls_step_binding",
    ),
    ForeignKeyConstraint(
        ["credential_ref", "workspace_id", "tool_id", "tool_version"],
        [
            f"{SCHEMA_TOKEN}.tool_credentials.credential_ref",
            f"{SCHEMA_TOKEN}.tool_credentials.workspace_id",
            f"{SCHEMA_TOKEN}.tool_credentials.tool_id",
            f"{SCHEMA_TOKEN}.tool_credentials.tool_version",
        ],
        name="fk_tool_calls_credential_binding",
    ),
    CheckConstraint("tool_version >= 1", name="ck_tool_calls_tool_version"),
    CheckConstraint(
        "canonical_arguments_hash ~ '^[0-9a-f]{64}$'",
        name="ck_tool_calls_arguments_hash",
    ),
    CheckConstraint("access_mode IN ('read', 'write')", name="ck_tool_calls_access_mode"),
    CheckConstraint(
        "risk_level IN ('low', 'medium', 'high', 'critical')",
        name="ck_tool_calls_risk",
    ),
    CheckConstraint(
        "credential_ref IS NULL OR credential_ref ~ '^cred_[a-z0-9]{16,64}$'",
        name="ck_tool_calls_credential_ref",
    ),
    CheckConstraint(
        "state IN ('proposed', 'authorized', 'confirmed', 'executing', "
        "'succeeded', 'failed', 'cancelled', 'timed_out')",
        name="ck_tool_calls_state",
    ),
    CheckConstraint(
        "(state IN ('succeeded', 'failed', 'cancelled', 'timed_out') "
        "AND completed_at IS NOT NULL) OR "
        "(state NOT IN ('succeeded', 'failed', 'cancelled', 'timed_out') "
        "AND completed_at IS NULL)",
        name="ck_tool_calls_completion",
    ),
)
Index("ix_tool_calls_workspace_time", tool_calls.c.workspace_id, tool_calls.c.created_at)

tool_idempotency_records = Table(
    "tool_idempotency_records",
    metadata,
    Column("idempotency_record_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("step_id", UUID(as_uuid=True), nullable=False),
    Column("tool_call_id", UUID(as_uuid=True), nullable=False),
    Column("tool_id", UUID(as_uuid=True), nullable=False),
    Column("tool_version", Integer, nullable=False),
    Column("confirmation_id", UUID(as_uuid=True), nullable=False),
    Column("confirmation_hash", String(64), nullable=False),
    Column("idempotency_key_hash", String(64), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("state", String(24), nullable=False),
    Column("result_hash", String(64), nullable=True),
    Column("reserved_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("error_code", String(128), nullable=True),
    UniqueConstraint("tool_call_id", name="uq_tool_idempotency_records_call"),
    UniqueConstraint(
        "workspace_id",
        "idempotency_key_hash",
        name="uq_tool_idempotency_records_key",
    ),
    UniqueConstraint(
        "idempotency_record_id",
        "workspace_id",
        "idempotency_key_hash",
        "request_hash",
        name="uq_tool_idempotency_records_effect_binding",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_tool_idempotency_records_workspace",
    ),
    ForeignKeyConstraint(
        ["tool_call_id"],
        [f"{SCHEMA_TOKEN}.tool_calls.tool_call_id"],
        name="fk_tool_idempotency_records_call",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["confirmation_id"],
        [f"{SCHEMA_TOKEN}.tool_confirmations.confirmation_id"],
        name="fk_tool_idempotency_records_confirmation",
    ),
    CheckConstraint("tool_version >= 1", name="ck_tool_idempotency_records_version"),
    CheckConstraint(
        "confirmation_hash ~ '^[0-9a-f]{64}$' "
        "AND idempotency_key_hash ~ '^[0-9a-f]{64}$' "
        "AND request_hash ~ '^[0-9a-f]{64}$' "
        "AND (result_hash IS NULL OR result_hash ~ '^[0-9a-f]{64}$')",
        name="ck_tool_idempotency_records_hashes",
    ),
    CheckConstraint(
        "state IN ('reserved', 'succeeded', 'failed', 'outcome_unknown')",
        name="ck_tool_idempotency_records_state",
    ),
    CheckConstraint(
        "(state = 'reserved' AND result_hash IS NULL AND completed_at IS NULL "
        "AND error_code IS NULL) OR "
        "(state = 'succeeded' AND result_hash IS NOT NULL AND completed_at IS NOT NULL "
        "AND error_code IS NULL) OR "
        "(state = 'failed' AND result_hash IS NULL AND completed_at IS NOT NULL "
        "AND error_code IS NOT NULL) OR "
        "(state = 'outcome_unknown' AND result_hash IS NULL AND completed_at IS NOT NULL "
        "AND error_code = 'TOOL_OUTCOME_UNKNOWN')",
        name="ck_tool_idempotency_records_outcome",
    ),
    CheckConstraint(
        "completed_at IS NULL OR completed_at >= reserved_at",
        name="ck_tool_idempotency_records_time",
    ),
)
Index(
    "ix_tool_idempotency_records_workspace_time",
    tool_idempotency_records.c.workspace_id,
    tool_idempotency_records.c.reserved_at,
)

synthetic_tool_side_effects = Table(
    "synthetic_tool_side_effects",
    metadata,
    Column("side_effect_id", UUID(as_uuid=True), primary_key=True),
    Column("idempotency_record_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("idempotency_key_hash", String(64), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("canonical_arguments_hash", String(64), nullable=False),
    Column("result_hash", String(64), nullable=False),
    Column("committed_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "workspace_id",
        "idempotency_key_hash",
        name="uq_synthetic_tool_side_effects_key",
    ),
    UniqueConstraint(
        "idempotency_record_id",
        name="uq_synthetic_tool_side_effects_record",
    ),
    ForeignKeyConstraint(
        [
            "idempotency_record_id",
            "workspace_id",
            "idempotency_key_hash",
            "request_hash",
        ],
        [
            f"{SCHEMA_TOKEN}.tool_idempotency_records.idempotency_record_id",
            f"{SCHEMA_TOKEN}.tool_idempotency_records.workspace_id",
            f"{SCHEMA_TOKEN}.tool_idempotency_records.idempotency_key_hash",
            f"{SCHEMA_TOKEN}.tool_idempotency_records.request_hash",
        ],
        name="fk_synthetic_tool_side_effects_record",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "idempotency_key_hash ~ '^[0-9a-f]{64}$' "
        "AND request_hash ~ '^[0-9a-f]{64}$' "
        "AND canonical_arguments_hash ~ '^[0-9a-f]{64}$' "
        "AND result_hash ~ '^[0-9a-f]{64}$'",
        name="ck_synthetic_tool_side_effects_hashes",
    ),
)
Index(
    "ix_synthetic_tool_side_effects_workspace_time",
    synthetic_tool_side_effects.c.workspace_id,
    synthetic_tool_side_effects.c.committed_at,
)

agents = Table(
    "agents",
    metadata,
    Column("agent_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("agent_key", String(64), nullable=False),
    Column("agent_kind", String(16), nullable=False, server_default="system"),
    Column("name", String(120), nullable=False),
    Column("description", String(1000), nullable=True),
    Column("status", String(32), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint("workspace_id", "agent_key", name="uq_agents_workspace_key"),
    UniqueConstraint("agent_id", "workspace_id", name="uq_agents_id_workspace"),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_agents_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_agents_creator",
    ),
    CheckConstraint(
        "(agent_kind = 'system' AND status IN ('active', 'disabled')) OR "
        "(agent_kind = 'custom' AND status IN ('active', 'archived'))",
        name="ck_agents_kind_status",
    ),
    CheckConstraint("agent_kind IN ('system', 'custom')", name="ck_agents_kind"),
    CheckConstraint("version >= 1", name="ck_agents_version"),
)

agent_drafts = Table(
    "agent_drafts",
    metadata,
    Column("draft_id", UUID(as_uuid=True), primary_key=True),
    Column("agent_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("configuration", JSONB, nullable=False),
    Column("config_hash", String(64), nullable=False),
    Column("updated_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("draft_id", "workspace_id", name="uq_agent_drafts_id_workspace"),
    UniqueConstraint("agent_id", "workspace_id", name="uq_agent_drafts_agent_workspace"),
    ForeignKeyConstraint(
        ["agent_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.agents.agent_id", f"{SCHEMA_TOKEN}.agents.workspace_id"],
        name="fk_agent_drafts_agent",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["updated_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_agent_drafts_updater",
    ),
    CheckConstraint("revision >= 1", name="ck_agent_drafts_revision"),
    CheckConstraint(
        "status IN ('editing', 'testing', 'test_failed', 'ready_for_approval', "
        "'approval_pending', 'approved', 'rejected', 'superseded')",
        name="ck_agent_drafts_status",
    ),
    CheckConstraint(
        "jsonb_typeof(configuration) = 'object'",
        name="ck_agent_drafts_configuration",
    ),
    CheckConstraint(
        "config_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_drafts_config_hash",
    ),
)

agent_draft_revisions = Table(
    "agent_draft_revisions",
    metadata,
    Column("draft_id", UUID(as_uuid=True), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("agent_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(32), nullable=False),
    Column("configuration", JSONB, nullable=False),
    Column("config_hash", String(64), nullable=False),
    Column("updated_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "draft_id",
        "revision",
        "agent_id",
        "workspace_id",
        name="uq_agent_draft_revisions_source",
    ),
    ForeignKeyConstraint(
        ["draft_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.agent_drafts.draft_id", f"{SCHEMA_TOKEN}.agent_drafts.workspace_id"],
        name="fk_agent_draft_revisions_draft",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["agent_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.agents.agent_id", f"{SCHEMA_TOKEN}.agents.workspace_id"],
        name="fk_agent_draft_revisions_agent",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["updated_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_agent_draft_revisions_updater",
    ),
    CheckConstraint("revision >= 1", name="ck_agent_draft_revisions_revision"),
    CheckConstraint(
        "status IN ('editing', 'testing', 'test_failed', 'ready_for_approval', "
        "'approval_pending', 'approved', 'rejected', 'superseded')",
        name="ck_agent_draft_revisions_status",
    ),
    CheckConstraint(
        "jsonb_typeof(configuration) = 'object'",
        name="ck_agent_draft_revisions_configuration",
    ),
    CheckConstraint(
        "config_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_draft_revisions_config_hash",
    ),
)
Index(
    "ix_agent_draft_revisions_agent_revision",
    agent_draft_revisions.c.workspace_id,
    agent_draft_revisions.c.agent_id,
    agent_draft_revisions.c.revision,
)

agent_release_candidates = Table(
    "agent_release_candidates",
    metadata,
    Column("candidate_id", UUID(as_uuid=True), primary_key=True),
    Column("agent_id", UUID(as_uuid=True), nullable=False),
    Column("draft_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("draft_revision", Integer, nullable=False),
    Column("candidate_hash", String(64), nullable=False),
    Column("config_hash", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint(
        "candidate_id",
        "workspace_id",
        name="uq_agent_release_candidates_id_workspace",
    ),
    UniqueConstraint(
        "draft_id",
        "draft_revision",
        name="uq_agent_release_candidates_draft_revision",
    ),
    ForeignKeyConstraint(
        ["draft_id", "draft_revision", "agent_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_draft_revisions.draft_id",
            f"{SCHEMA_TOKEN}.agent_draft_revisions.revision",
            f"{SCHEMA_TOKEN}.agent_draft_revisions.agent_id",
            f"{SCHEMA_TOKEN}.agent_draft_revisions.workspace_id",
        ],
        name="fk_agent_release_candidates_revision",
    ),
    ForeignKeyConstraint(
        ["agent_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.agents.agent_id", f"{SCHEMA_TOKEN}.agents.workspace_id"],
        name="fk_agent_release_candidates_agent",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_agent_release_candidates_creator",
    ),
    CheckConstraint("draft_revision >= 1", name="ck_agent_release_candidates_revision"),
    CheckConstraint(
        "candidate_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_release_candidates_candidate_hash",
    ),
    CheckConstraint(
        "config_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_release_candidates_config_hash",
    ),
    CheckConstraint(
        "status IN ('created', 'testing', 'test_failed', 'ready_for_approval', "
        "'approval_pending', 'approved', 'rejected', 'released', 'superseded')",
        name="ck_agent_release_candidates_status",
    ),
    CheckConstraint("version >= 1", name="ck_agent_release_candidates_version"),
)
Index(
    "ix_agent_release_candidates_workspace_time",
    agent_release_candidates.c.workspace_id,
    agent_release_candidates.c.created_at,
)

agent_evaluation_policy_versions = Table(
    "agent_evaluation_policy_versions",
    metadata,
    Column("evaluation_policy_version_id", UUID(as_uuid=True), primary_key=True),
    Column("policy_key", String(80), nullable=False),
    Column("version_number", Integer, nullable=False),
    Column("required_check_codes", JSONB, nullable=False),
    Column("hard_gate_check_codes", JSONB, nullable=False),
    Column("minimum_check_scores", JSONB, nullable=False),
    Column("failure_handling", String(32), nullable=False),
    Column("timeout_handling", String(32), nullable=False),
    Column("skipped_handling", String(32), nullable=False),
    Column("evaluator_kind", String(32), nullable=False),
    Column("online_llm_grading", Boolean, nullable=False),
    Column("multimodal_image_qa", Boolean, nullable=False),
    Column("policy_hash", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    UniqueConstraint(
        "policy_key",
        "version_number",
        name="uq_agent_evaluation_policies_key_version",
    ),
    CheckConstraint("version_number >= 1", name="ck_agent_evaluation_policies_version"),
    CheckConstraint(
        "jsonb_typeof(required_check_codes) = 'array' "
        "AND jsonb_array_length(required_check_codes) = 5 "
        "AND jsonb_typeof(hard_gate_check_codes) = 'array' "
        "AND jsonb_array_length(hard_gate_check_codes) = 4 "
        "AND jsonb_typeof(minimum_check_scores) = 'object'",
        name="ck_agent_evaluation_policies_documents",
    ),
    CheckConstraint(
        "failure_handling = 'block_release' "
        "AND timeout_handling = 'count_as_failure' "
        "AND skipped_handling = 'count_as_failure'",
        name="ck_agent_evaluation_policies_failure_modes",
    ),
    CheckConstraint(
        "evaluator_kind = 'deterministic_rules' "
        "AND online_llm_grading = false AND multimodal_image_qa = false",
        name="ck_agent_evaluation_policies_deferred_capabilities",
    ),
    CheckConstraint(
        "policy_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_evaluation_policies_hash",
    ),
    CheckConstraint(
        "status IN ('active', 'retired')",
        name="ck_agent_evaluation_policies_status",
    ),
)

agent_evaluation_dataset_versions = Table(
    "agent_evaluation_dataset_versions",
    metadata,
    Column("dataset_version_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("name", String(120), nullable=False),
    Column("dataset_version", String(80), nullable=False),
    Column("dataset_hash", String(64), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "dataset_version_id",
        "workspace_id",
        name="uq_agent_evaluation_datasets_id_workspace",
    ),
    UniqueConstraint(
        "workspace_id",
        "dataset_version",
        name="uq_agent_evaluation_datasets_workspace_version",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_agent_evaluation_datasets_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_agent_evaluation_datasets_creator",
    ),
    CheckConstraint(
        "char_length(btrim(name)) BETWEEN 1 AND 120",
        name="ck_agent_evaluation_datasets_name",
    ),
    CheckConstraint(
        "dataset_version ~ '^p304-[a-z0-9][a-z0-9-]{1,60}-v[1-9][0-9]*$'",
        name="ck_agent_evaluation_datasets_version",
    ),
    CheckConstraint(
        "dataset_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_evaluation_datasets_hash",
    ),
)

agent_evaluation_test_cases = Table(
    "agent_evaluation_test_cases",
    metadata,
    Column("case_id", UUID(as_uuid=True), primary_key=True),
    Column("dataset_version_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("position", Integer, nullable=False),
    Column("case_key", String(80), nullable=False),
    Column("check_code", String(32), nullable=False),
    Column("input_fixture", JSONB, nullable=False),
    Column("expected_fixture", JSONB, nullable=False),
    Column("timeout_ms", Integer, nullable=False),
    Column("minimum_score_bps", Integer, nullable=False),
    Column("case_hash", String(64), nullable=False),
    UniqueConstraint(
        "case_id",
        "dataset_version_id",
        "workspace_id",
        name="uq_agent_evaluation_cases_identity",
    ),
    UniqueConstraint(
        "dataset_version_id",
        "case_key",
        name="uq_agent_evaluation_cases_key",
    ),
    UniqueConstraint(
        "dataset_version_id",
        "position",
        name="uq_agent_evaluation_cases_position",
    ),
    ForeignKeyConstraint(
        ["dataset_version_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_evaluation_dataset_versions.dataset_version_id",
            f"{SCHEMA_TOKEN}.agent_evaluation_dataset_versions.workspace_id",
        ],
        name="fk_agent_evaluation_cases_dataset",
        ondelete="CASCADE",
    ),
    CheckConstraint("position >= 1", name="ck_agent_evaluation_cases_position"),
    CheckConstraint(
        "case_key ~ '^[a-z][a-z0-9_.-]{2,79}$'",
        name="ck_agent_evaluation_cases_key",
    ),
    CheckConstraint(
        "check_code IN ('functional', 'authorization', 'prompt_injection', "
        "'citation', 'output_contract')",
        name="ck_agent_evaluation_cases_check",
    ),
    CheckConstraint(
        "jsonb_typeof(input_fixture) = 'object' AND jsonb_typeof(expected_fixture) = 'object'",
        name="ck_agent_evaluation_cases_fixtures",
    ),
    CheckConstraint(
        "timeout_ms BETWEEN 1 AND 120000",
        name="ck_agent_evaluation_cases_timeout",
    ),
    CheckConstraint(
        "minimum_score_bps BETWEEN 0 AND 10000",
        name="ck_agent_evaluation_cases_score",
    ),
    CheckConstraint(
        "case_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_evaluation_cases_hash",
    ),
)

agent_evaluation_runs = Table(
    "agent_evaluation_runs",
    metadata,
    Column("evaluation_run_id", UUID(as_uuid=True), primary_key=True),
    Column("candidate_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("dataset_version_id", UUID(as_uuid=True), nullable=False),
    Column("evaluation_policy_version_id", UUID(as_uuid=True), nullable=False),
    Column("candidate_hash", String(64), nullable=False),
    Column("config_hash", String(64), nullable=False),
    Column("evaluator_version", String(64), nullable=False),
    Column("evidence_level", String(32), nullable=False),
    Column("status", String(16), nullable=False),
    Column("total_cases", Integer, nullable=False),
    Column("passed_cases", Integer, nullable=False),
    Column("failed_cases", Integer, nullable=False),
    Column("timeout_cases", Integer, nullable=False),
    Column("skipped_cases", Integer, nullable=False),
    Column("result_hash", String(64), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "evaluation_run_id",
        "workspace_id",
        name="uq_agent_evaluation_runs_id_workspace",
    ),
    UniqueConstraint(
        "evaluation_run_id",
        "dataset_version_id",
        "workspace_id",
        name="uq_agent_evaluation_runs_dataset_identity",
    ),
    UniqueConstraint(
        "candidate_id",
        "dataset_version_id",
        "evaluation_policy_version_id",
        name="uq_agent_evaluation_runs_identity",
    ),
    ForeignKeyConstraint(
        ["candidate_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_release_candidates.candidate_id",
            f"{SCHEMA_TOKEN}.agent_release_candidates.workspace_id",
        ],
        name="fk_agent_evaluation_runs_candidate",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["dataset_version_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_evaluation_dataset_versions.dataset_version_id",
            f"{SCHEMA_TOKEN}.agent_evaluation_dataset_versions.workspace_id",
        ],
        name="fk_agent_evaluation_runs_dataset",
    ),
    ForeignKeyConstraint(
        ["evaluation_policy_version_id"],
        [f"{SCHEMA_TOKEN}.agent_evaluation_policy_versions.evaluation_policy_version_id"],
        name="fk_agent_evaluation_runs_policy",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_agent_evaluation_runs_creator",
    ),
    CheckConstraint(
        "candidate_hash ~ '^[0-9a-f]{64}$' AND config_hash ~ '^[0-9a-f]{64}$' "
        "AND result_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_evaluation_runs_hashes",
    ),
    CheckConstraint(
        "evaluator_version ~ '^[a-z][a-z0-9._-]{2,63}$'",
        name="ck_agent_evaluation_runs_evaluator",
    ),
    CheckConstraint(
        "evidence_level = 'core_functional'",
        name="ck_agent_evaluation_runs_evidence_level",
    ),
    CheckConstraint(
        "status IN ('passed', 'failed')",
        name="ck_agent_evaluation_runs_status",
    ),
    CheckConstraint(
        "total_cases >= 5 AND passed_cases >= 0 AND failed_cases >= 0 "
        "AND timeout_cases >= 0 AND skipped_cases >= 0 "
        "AND total_cases = passed_cases + failed_cases + timeout_cases + skipped_cases",
        name="ck_agent_evaluation_runs_counts",
    ),
    CheckConstraint(
        "status <> 'passed' OR passed_cases = total_cases",
        name="ck_agent_evaluation_runs_passed",
    ),
)
Index(
    "ix_agent_evaluation_runs_candidate_time",
    agent_evaluation_runs.c.workspace_id,
    agent_evaluation_runs.c.candidate_id,
    agent_evaluation_runs.c.completed_at,
)

agent_evaluation_check_results = Table(
    "agent_evaluation_check_results",
    metadata,
    Column("evaluation_run_id", UUID(as_uuid=True), primary_key=True),
    Column("check_code", String(32), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("position", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("case_count", Integer, nullable=False),
    Column("passed_count", Integer, nullable=False),
    Column("score_bps", Integer, nullable=False),
    Column("evidence_hash", String(64), nullable=False),
    UniqueConstraint(
        "evaluation_run_id",
        "position",
        name="uq_agent_evaluation_checks_position",
    ),
    ForeignKeyConstraint(
        ["evaluation_run_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_evaluation_runs.evaluation_run_id",
            f"{SCHEMA_TOKEN}.agent_evaluation_runs.workspace_id",
        ],
        name="fk_agent_evaluation_checks_run",
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "check_code IN ('functional', 'authorization', 'prompt_injection', "
        "'citation', 'output_contract')",
        name="ck_agent_evaluation_checks_code",
    ),
    CheckConstraint("position BETWEEN 1 AND 5", name="ck_agent_evaluation_checks_position"),
    CheckConstraint(
        "status IN ('passed', 'failed')",
        name="ck_agent_evaluation_checks_status",
    ),
    CheckConstraint(
        "case_count >= 1 AND passed_count BETWEEN 0 AND case_count",
        name="ck_agent_evaluation_checks_counts",
    ),
    CheckConstraint(
        "score_bps BETWEEN 0 AND 10000",
        name="ck_agent_evaluation_checks_score",
    ),
    CheckConstraint(
        "status <> 'passed' OR passed_count = case_count",
        name="ck_agent_evaluation_checks_passed",
    ),
    CheckConstraint(
        "evidence_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_evaluation_checks_hash",
    ),
)

agent_evaluation_case_results = Table(
    "agent_evaluation_case_results",
    metadata,
    Column("evaluation_run_id", UUID(as_uuid=True), primary_key=True),
    Column("case_id", UUID(as_uuid=True), primary_key=True),
    Column("dataset_version_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("check_code", String(32), nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("score_bps", Integer, nullable=False),
    Column("duration_ms", Integer, nullable=False),
    Column("evidence_hash", String(64), nullable=False),
    ForeignKeyConstraint(
        ["evaluation_run_id", "dataset_version_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_evaluation_runs.evaluation_run_id",
            f"{SCHEMA_TOKEN}.agent_evaluation_runs.dataset_version_id",
            f"{SCHEMA_TOKEN}.agent_evaluation_runs.workspace_id",
        ],
        name="fk_agent_evaluation_case_results_run",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["case_id", "dataset_version_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_evaluation_test_cases.case_id",
            f"{SCHEMA_TOKEN}.agent_evaluation_test_cases.dataset_version_id",
            f"{SCHEMA_TOKEN}.agent_evaluation_test_cases.workspace_id",
        ],
        name="fk_agent_evaluation_case_results_case",
    ),
    CheckConstraint(
        "check_code IN ('functional', 'authorization', 'prompt_injection', "
        "'citation', 'output_contract')",
        name="ck_agent_evaluation_case_results_check",
    ),
    CheckConstraint(
        "outcome IN ('passed', 'failed', 'timeout', 'skipped')",
        name="ck_agent_evaluation_case_results_outcome",
    ),
    CheckConstraint(
        "score_bps BETWEEN 0 AND 10000 AND duration_ms >= 0",
        name="ck_agent_evaluation_case_results_metrics",
    ),
    CheckConstraint(
        "outcome = 'passed' OR score_bps = 0",
        name="ck_agent_evaluation_case_results_failed_score",
    ),
    CheckConstraint(
        "evidence_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_evaluation_case_results_hash",
    ),
)

agent_approval_bindings = Table(
    "agent_approval_bindings",
    metadata,
    Column("approval_binding_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("candidate_id", UUID(as_uuid=True), nullable=False),
    Column("agent_id", UUID(as_uuid=True), nullable=False),
    Column("approval_instance_id", UUID(as_uuid=True), nullable=False),
    Column("evaluation_run_id", UUID(as_uuid=True), nullable=False),
    Column("evaluation_policy_version_id", UUID(as_uuid=True), nullable=False),
    Column("approval_policy_version_id", UUID(as_uuid=True), nullable=False),
    Column("candidate_hash", String(64), nullable=False),
    Column("config_hash", String(64), nullable=False),
    Column("evaluation_result_hash", String(64), nullable=False),
    Column("subject_digest", String(64), nullable=False),
    Column("chain_digest", String(64), nullable=False),
    Column("personal_owner_confirmation", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "approval_binding_id",
        "workspace_id",
        name="uq_agent_approval_bindings_id_workspace",
    ),
    UniqueConstraint(
        "candidate_id",
        name="uq_agent_approval_bindings_candidate",
    ),
    UniqueConstraint(
        "approval_instance_id",
        name="uq_agent_approval_bindings_instance",
    ),
    ForeignKeyConstraint(
        ["candidate_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_release_candidates.candidate_id",
            f"{SCHEMA_TOKEN}.agent_release_candidates.workspace_id",
        ],
        name="fk_agent_approval_bindings_candidate",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["agent_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agents.agent_id",
            f"{SCHEMA_TOKEN}.agents.workspace_id",
        ],
        name="fk_agent_approval_bindings_agent",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["approval_instance_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.approval_instances.approval_instance_id",
            f"{SCHEMA_TOKEN}.approval_instances.workspace_id",
        ],
        name="fk_agent_approval_bindings_instance",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["evaluation_run_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_evaluation_runs.evaluation_run_id",
            f"{SCHEMA_TOKEN}.agent_evaluation_runs.workspace_id",
        ],
        name="fk_agent_approval_bindings_evaluation",
    ),
    ForeignKeyConstraint(
        ["evaluation_policy_version_id"],
        [f"{SCHEMA_TOKEN}.agent_evaluation_policy_versions.evaluation_policy_version_id"],
        name="fk_agent_approval_bindings_evaluation_policy",
    ),
    CheckConstraint(
        "candidate_hash ~ '^[0-9a-f]{64}$' "
        "AND config_hash ~ '^[0-9a-f]{64}$' "
        "AND evaluation_result_hash ~ '^[0-9a-f]{64}$' "
        "AND subject_digest ~ '^[0-9a-f]{64}$' "
        "AND chain_digest ~ '^[0-9a-f]{64}$'",
        name="ck_agent_approval_bindings_hashes",
    ),
    CheckConstraint(
        "(personal_owner_confirmation = false) OR "
        "approval_policy_version_id = 'a5000000-0000-4000-8000-000000000305'::uuid",
        name="ck_agent_approval_bindings_personal_policy",
    ),
)
Index(
    "ix_agent_approval_bindings_workspace_time",
    agent_approval_bindings.c.workspace_id,
    agent_approval_bindings.c.created_at,
)

agent_control_requests = Table(
    "agent_control_requests",
    metadata,
    Column("request_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("actor_id", UUID(as_uuid=True), nullable=False),
    Column("operation", String(64), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("result_type", String(32), nullable=False),
    Column("result_id", UUID(as_uuid=True), nullable=False),
    Column("result_revision", Integer, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "workspace_id",
        "actor_id",
        "operation",
        "idempotency_key",
        name="uq_agent_control_requests_idempotency",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_agent_control_requests_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["actor_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_agent_control_requests_actor",
    ),
    CheckConstraint(
        "operation IN ('agent.create', 'agent.draft.update', 'agent.release.request', "
        "'agent.release.publish', 'agent.archive')",
        name="ck_agent_control_requests_operation",
    ),
    CheckConstraint(
        "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'",
        name="ck_agent_control_requests_idempotency",
    ),
    CheckConstraint(
        "request_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_control_requests_hash",
    ),
    CheckConstraint(
        "result_type IN ('agent', 'draft', 'candidate', 'release')",
        name="ck_agent_control_requests_result_type",
    ),
    CheckConstraint(
        "result_revision IS NULL OR result_revision >= 1",
        name="ck_agent_control_requests_result_revision",
    ),
)
Index(
    "ix_agent_control_requests_workspace_time",
    agent_control_requests.c.workspace_id,
    agent_control_requests.c.created_at,
)

agent_releases = Table(
    "agent_releases",
    metadata,
    Column("release_id", UUID(as_uuid=True), primary_key=True),
    Column("agent_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("release_kind", String(16), nullable=False, server_default="system"),
    Column("version", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("runtime_config_version_id", UUID(as_uuid=True), nullable=False),
    Column("config_hash", String(64), nullable=False),
    Column("candidate_id", UUID(as_uuid=True), nullable=True),
    Column("candidate_hash", String(64), nullable=True),
    Column("source_draft_id", UUID(as_uuid=True), nullable=True),
    Column("source_draft_revision", Integer, nullable=True),
    Column("evaluation_run_id", UUID(as_uuid=True), nullable=True),
    Column("approval_binding_id", UUID(as_uuid=True), nullable=True),
    Column("snapshot", JSONB, nullable=True),
    Column("snapshot_hash", String(64), nullable=True),
    Column("released_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("released_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("agent_id", "version", name="uq_agent_releases_version"),
    UniqueConstraint("candidate_id", name="uq_agent_releases_candidate"),
    UniqueConstraint("release_id", "workspace_id", name="uq_agent_releases_id_workspace"),
    ForeignKeyConstraint(
        ["agent_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.agents.agent_id", f"{SCHEMA_TOKEN}.agents.workspace_id"],
        name="fk_agent_releases_agent",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["candidate_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_release_candidates.candidate_id",
            f"{SCHEMA_TOKEN}.agent_release_candidates.workspace_id",
        ],
        name="fk_agent_releases_candidate",
    ),
    ForeignKeyConstraint(
        ["source_draft_id", "source_draft_revision", "agent_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_draft_revisions.draft_id",
            f"{SCHEMA_TOKEN}.agent_draft_revisions.revision",
            f"{SCHEMA_TOKEN}.agent_draft_revisions.agent_id",
            f"{SCHEMA_TOKEN}.agent_draft_revisions.workspace_id",
        ],
        name="fk_agent_releases_source_revision",
    ),
    ForeignKeyConstraint(
        ["evaluation_run_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_evaluation_runs.evaluation_run_id",
            f"{SCHEMA_TOKEN}.agent_evaluation_runs.workspace_id",
        ],
        name="fk_agent_releases_evaluation_run",
    ),
    ForeignKeyConstraint(
        ["approval_binding_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_approval_bindings.approval_binding_id",
            f"{SCHEMA_TOKEN}.agent_approval_bindings.workspace_id",
        ],
        name="fk_agent_releases_approval_binding",
    ),
    ForeignKeyConstraint(
        ["runtime_config_version_id"],
        [f"{SCHEMA_TOKEN}.ai_runtime_config_versions.runtime_config_version_id"],
        name="fk_agent_releases_runtime_config",
    ),
    ForeignKeyConstraint(
        ["released_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_agent_releases_releaser",
    ),
    CheckConstraint("version >= 1", name="ck_agent_releases_version"),
    CheckConstraint("status = 'released'", name="ck_agent_releases_status"),
    CheckConstraint("release_kind IN ('system', 'custom')", name="ck_agent_releases_kind"),
    CheckConstraint(
        "config_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_releases_config_hash",
    ),
    CheckConstraint(
        "candidate_hash IS NULL OR candidate_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_releases_candidate_hash",
    ),
    CheckConstraint(
        "snapshot_hash IS NULL OR snapshot_hash ~ '^[0-9a-f]{64}$'",
        name="ck_agent_releases_snapshot_hash",
    ),
    CheckConstraint(
        "(release_kind = 'system' AND candidate_id IS NULL AND candidate_hash IS NULL "
        "AND source_draft_id IS NULL AND source_draft_revision IS NULL "
        "AND evaluation_run_id IS NULL AND approval_binding_id IS NULL "
        "AND snapshot IS NULL AND snapshot_hash IS NULL) OR "
        "(release_kind = 'custom' AND candidate_id IS NOT NULL AND candidate_hash IS NOT NULL "
        "AND source_draft_id IS NOT NULL AND source_draft_revision IS NOT NULL "
        "AND evaluation_run_id IS NOT NULL AND approval_binding_id IS NOT NULL "
        "AND jsonb_typeof(snapshot) = 'object' AND snapshot_hash IS NOT NULL)",
        name="ck_agent_releases_kind_payload",
    ),
    CheckConstraint(
        "source_draft_revision IS NULL OR source_draft_revision >= 1",
        name="ck_agent_releases_source_revision",
    ),
)
Index(
    "ix_agent_releases_runtime_config",
    agent_releases.c.runtime_config_version_id,
    agent_releases.c.released_at,
)

agent_publications = Table(
    "agent_publications",
    metadata,
    Column("agent_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("release_id", UUID(as_uuid=True), nullable=False),
    Column("generation", Integer, nullable=False),
    Column("published_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["agent_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.agents.agent_id", f"{SCHEMA_TOKEN}.agents.workspace_id"],
        name="fk_agent_publications_agent",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["release_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_releases.release_id",
            f"{SCHEMA_TOKEN}.agent_releases.workspace_id",
        ],
        name="fk_agent_publications_release",
    ),
    ForeignKeyConstraint(
        ["published_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_agent_publications_publisher",
    ),
    CheckConstraint("generation >= 1", name="ck_agent_publications_generation"),
)

service_access_policy_versions = Table(
    "service_access_policy_versions",
    metadata,
    Column("access_policy_version_id", UUID(as_uuid=True), primary_key=True),
    Column("service_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("version", Integer, nullable=False),
    Column("visibility", String(32), nullable=False),
    Column(
        "allowed_department_ids",
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        server_default="{}",
    ),
    Column(
        "allowed_account_ids",
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        server_default="{}",
    ),
    Column("policy_hash", String(64), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "service_id",
        "version",
        name="uq_service_access_policies_version",
    ),
    UniqueConstraint(
        "access_policy_version_id",
        "service_id",
        "workspace_id",
        name="uq_service_access_policies_identity",
    ),
    ForeignKeyConstraint(
        ["service_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.services.service_id", f"{SCHEMA_TOKEN}.services.workspace_id"],
        name="fk_service_access_policies_service",
        deferrable=True,
        initially="DEFERRED",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_service_access_policies_creator",
    ),
    CheckConstraint("version >= 1", name="ck_service_access_policies_version"),
    CheckConstraint(
        "visibility IN ('workspace', 'restricted')",
        name="ck_service_access_policies_visibility",
    ),
    CheckConstraint(
        "(visibility = 'workspace' AND cardinality(allowed_department_ids) = 0 "
        "AND cardinality(allowed_account_ids) = 0) OR "
        "(visibility = 'restricted' AND "
        "(cardinality(allowed_department_ids) > 0 OR cardinality(allowed_account_ids) > 0))",
        name="ck_service_access_policies_subjects",
    ),
    CheckConstraint(
        "policy_hash ~ '^[0-9a-f]{64}$'",
        name="ck_service_access_policies_hash",
    ),
)

services = Table(
    "services",
    metadata,
    Column("service_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("agent_id", UUID(as_uuid=True), nullable=False),
    Column("service_key", String(80), nullable=False),
    Column("name", String(120), nullable=False),
    Column("service_type", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("access_policy_version_id", UUID(as_uuid=True), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint("service_id", "workspace_id", name="uq_services_id_workspace"),
    UniqueConstraint("workspace_id", "service_key", name="uq_services_workspace_key"),
    ForeignKeyConstraint(
        ["agent_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.agents.agent_id", f"{SCHEMA_TOKEN}.agents.workspace_id"],
        name="fk_services_agent",
    ),
    ForeignKeyConstraint(
        ["access_policy_version_id", "service_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.service_access_policy_versions.access_policy_version_id",
            f"{SCHEMA_TOKEN}.service_access_policy_versions.service_id",
            f"{SCHEMA_TOKEN}.service_access_policy_versions.workspace_id",
        ],
        name="fk_services_access_policy",
        deferrable=True,
        initially="DEFERRED",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_services_creator",
    ),
    ForeignKeyConstraint(
        ["updated_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_services_updater",
    ),
    CheckConstraint(
        "service_key ~ '^[a-z][a-z0-9-]{2,79}$'",
        name="ck_services_key",
    ),
    CheckConstraint(
        "char_length(btrim(name)) BETWEEN 1 AND 120",
        name="ck_services_name",
    ),
    CheckConstraint(
        "service_type IN ('system_assistant', 'custom_knowledge_agent', "
        "'scenario_application', 'open_api')",
        name="ck_services_type",
    ),
    CheckConstraint(
        "status IN ('draft', 'active', 'suspended', 'archived')",
        name="ck_services_status",
    ),
    CheckConstraint("version >= 1", name="ck_services_version"),
)
Index("ix_services_workspace_updated", services.c.workspace_id, services.c.updated_at)

service_routes = Table(
    "service_routes",
    metadata,
    Column("route_id", UUID(as_uuid=True), primary_key=True),
    Column("service_id", UUID(as_uuid=True), nullable=False),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("route_version", Integer, nullable=False),
    Column("route_mode", String(32), nullable=False),
    Column("primary_release_id", UUID(as_uuid=True), nullable=False),
    Column("canary_release_id", UUID(as_uuid=True), nullable=True),
    Column("canary_percent", Integer, nullable=False),
    Column("previous_route_id", UUID(as_uuid=True), nullable=True),
    Column("route_hash", String(64), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("service_id", "route_version", name="uq_service_routes_version"),
    UniqueConstraint(
        "route_id",
        "service_id",
        "workspace_id",
        name="uq_service_routes_identity",
    ),
    UniqueConstraint(
        "route_id",
        "service_id",
        "workspace_id",
        "route_version",
        name="uq_service_routes_run_binding",
    ),
    ForeignKeyConstraint(
        ["service_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.services.service_id", f"{SCHEMA_TOKEN}.services.workspace_id"],
        name="fk_service_routes_service",
    ),
    ForeignKeyConstraint(
        ["primary_release_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_releases.release_id",
            f"{SCHEMA_TOKEN}.agent_releases.workspace_id",
        ],
        name="fk_service_routes_primary_release",
    ),
    ForeignKeyConstraint(
        ["canary_release_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_releases.release_id",
            f"{SCHEMA_TOKEN}.agent_releases.workspace_id",
        ],
        name="fk_service_routes_canary_release",
    ),
    ForeignKeyConstraint(
        ["previous_route_id", "service_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.service_routes.route_id",
            f"{SCHEMA_TOKEN}.service_routes.service_id",
            f"{SCHEMA_TOKEN}.service_routes.workspace_id",
        ],
        name="fk_service_routes_previous",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_service_routes_creator",
    ),
    CheckConstraint("route_version >= 1", name="ck_service_routes_version"),
    CheckConstraint(
        "route_mode IN ('active', 'canary', 'rollback')",
        name="ck_service_routes_mode",
    ),
    CheckConstraint(
        "(route_mode = 'canary' AND canary_release_id IS NOT NULL "
        "AND canary_release_id <> primary_release_id AND canary_percent BETWEEN 1 AND 99) OR "
        "(route_mode <> 'canary' AND canary_release_id IS NULL AND canary_percent = 0)",
        name="ck_service_routes_canary",
    ),
    CheckConstraint(
        "(route_version = 1 AND previous_route_id IS NULL) OR "
        "(route_version > 1 AND previous_route_id IS NOT NULL)",
        name="ck_service_routes_previous",
    ),
    CheckConstraint(
        "route_hash ~ '^[0-9a-f]{64}$'",
        name="ck_service_routes_hash",
    ),
)
Index(
    "ix_service_routes_workspace_service_time",
    service_routes.c.workspace_id,
    service_routes.c.service_id,
    service_routes.c.created_at,
)

service_route_publications = Table(
    "service_route_publications",
    metadata,
    Column("service_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("route_id", UUID(as_uuid=True), nullable=False),
    Column("generation", Integer, nullable=False),
    Column("published_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["service_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.services.service_id", f"{SCHEMA_TOKEN}.services.workspace_id"],
        name="fk_service_route_publications_service",
    ),
    ForeignKeyConstraint(
        ["route_id", "service_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.service_routes.route_id",
            f"{SCHEMA_TOKEN}.service_routes.service_id",
            f"{SCHEMA_TOKEN}.service_routes.workspace_id",
        ],
        name="fk_service_route_publications_route",
    ),
    ForeignKeyConstraint(
        ["published_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_service_route_publications_publisher",
    ),
    CheckConstraint("generation >= 1", name="ck_service_route_publications_generation"),
)

service_control_requests = Table(
    "service_control_requests",
    metadata,
    Column("request_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("actor_id", UUID(as_uuid=True), nullable=False),
    Column("operation", String(64), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("service_id", UUID(as_uuid=True), nullable=False),
    Column("result_snapshot", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "workspace_id",
        "actor_id",
        "operation",
        "idempotency_key",
        name="uq_service_control_requests_idempotency",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_service_control_requests_workspace",
    ),
    ForeignKeyConstraint(
        ["actor_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_service_control_requests_actor",
    ),
    ForeignKeyConstraint(
        ["service_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.services.service_id", f"{SCHEMA_TOKEN}.services.workspace_id"],
        name="fk_service_control_requests_service",
    ),
    CheckConstraint(
        "operation IN ('service.create', 'service.update', 'service.route.canary', "
        "'service.route.promote', 'service.route.rollback')",
        name="ck_service_control_requests_operation",
    ),
    CheckConstraint(
        "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'",
        name="ck_service_control_requests_idempotency",
    ),
    CheckConstraint(
        "request_hash ~ '^[0-9a-f]{64}$'",
        name="ck_service_control_requests_hash",
    ),
    CheckConstraint(
        "jsonb_typeof(result_snapshot) = 'object'",
        name="ck_service_control_requests_snapshot",
    ),
)
Index(
    "ix_service_control_requests_workspace_time",
    service_control_requests.c.workspace_id,
    service_control_requests.c.created_at,
)

conversations = Table(
    "conversations",
    metadata,
    Column("conversation_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("conversation_kind", String(32), nullable=False, server_default="private"),
    Column("title", String(200), nullable=True),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint(
        "conversation_id",
        "workspace_id",
        name="uq_conversations_id_workspace",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_conversations_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_conversations_creator",
    ),
    CheckConstraint("status IN ('active', 'archived')", name="ck_conversations_status"),
    CheckConstraint(
        "conversation_kind IN ('private', 'service_invocation')",
        name="ck_conversations_kind",
    ),
    CheckConstraint("version >= 1", name="ck_conversations_version"),
    CheckConstraint(
        "title IS NULL OR char_length(btrim(title)) BETWEEN 1 AND 200",
        name="ck_conversations_title",
    ),
)
Index(
    "ix_conversations_workspace_creator_time",
    conversations.c.workspace_id,
    conversations.c.created_by_account_id,
    conversations.c.updated_at,
)

messages = Table(
    "messages",
    metadata,
    Column("message_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("conversation_id", UUID(as_uuid=True), nullable=False),
    Column("role", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint("message_id", "workspace_id", name="uq_messages_id_workspace"),
    ForeignKeyConstraint(
        ["conversation_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.conversations.conversation_id",
            f"{SCHEMA_TOKEN}.conversations.workspace_id",
        ],
        name="fk_messages_conversation",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["created_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_messages_creator",
    ),
    CheckConstraint(
        "role IN ('system', 'user', 'assistant', 'tool')",
        name="ck_messages_role",
    ),
    CheckConstraint(
        "status IN ('streaming', 'completed', 'failed')",
        name="ck_messages_status",
    ),
    CheckConstraint("version >= 1", name="ck_messages_version"),
)
Index(
    "ix_messages_workspace_conversation_time",
    messages.c.workspace_id,
    messages.c.conversation_id,
    messages.c.created_at,
)

message_parts = Table(
    "message_parts",
    metadata,
    Column("part_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("message_id", UUID(as_uuid=True), nullable=False),
    Column("sequence_no", Integer, nullable=False),
    Column("part_type", String(32), nullable=False),
    Column("text_content", Text, nullable=True),
    Column("object_ref", String(2048), nullable=True),
    Column("media_type", String(255), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("message_id", "sequence_no", name="uq_message_parts_sequence"),
    ForeignKeyConstraint(
        ["message_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.messages.message_id", f"{SCHEMA_TOKEN}.messages.workspace_id"],
        name="fk_message_parts_message",
        ondelete="CASCADE",
    ),
    CheckConstraint("sequence_no >= 1", name="ck_message_parts_sequence"),
    CheckConstraint("part_type IN ('text', 'image_ref')", name="ck_message_parts_type"),
    CheckConstraint(
        "(part_type = 'text' AND text_content IS NOT NULL AND char_length(text_content) >= 1 "
        "AND object_ref IS NULL AND media_type IS NULL) OR "
        "(part_type = 'image_ref' AND text_content IS NULL AND object_ref IS NOT NULL "
        "AND media_type LIKE 'image/%')",
        name="ck_message_parts_content",
    ),
)

assistant_runs = Table(
    "assistant_runs",
    metadata,
    Column("run_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("conversation_id", UUID(as_uuid=True), nullable=False),
    Column("user_message_id", UUID(as_uuid=True), nullable=False),
    Column("assistant_message_id", UUID(as_uuid=True), nullable=True),
    Column("service_id", UUID(as_uuid=True), nullable=True),
    Column("service_route_id", UUID(as_uuid=True), nullable=True),
    Column("service_route_version", Integer, nullable=True),
    Column("agent_release_id", UUID(as_uuid=True), nullable=False),
    Column("runtime_config_version_id", UUID(as_uuid=True), nullable=False),
    Column("requested_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("requested_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(32), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("traceparent", String(55), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("error_code", String(128), nullable=True),
    UniqueConstraint(
        "workspace_id",
        "requested_by_actor_id",
        "idempotency_key",
        name="uq_assistant_runs_idempotency",
    ),
    ForeignKeyConstraint(
        ["conversation_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.conversations.conversation_id",
            f"{SCHEMA_TOKEN}.conversations.workspace_id",
        ],
        name="fk_assistant_runs_conversation",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["user_message_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.messages.message_id", f"{SCHEMA_TOKEN}.messages.workspace_id"],
        name="fk_assistant_runs_user_message",
    ),
    ForeignKeyConstraint(
        ["assistant_message_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.messages.message_id", f"{SCHEMA_TOKEN}.messages.workspace_id"],
        name="fk_assistant_runs_assistant_message",
    ),
    ForeignKeyConstraint(
        ["service_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.services.service_id", f"{SCHEMA_TOKEN}.services.workspace_id"],
        name="fk_assistant_runs_service",
    ),
    ForeignKeyConstraint(
        ["service_route_id", "service_id", "workspace_id", "service_route_version"],
        [
            f"{SCHEMA_TOKEN}.service_routes.route_id",
            f"{SCHEMA_TOKEN}.service_routes.service_id",
            f"{SCHEMA_TOKEN}.service_routes.workspace_id",
            f"{SCHEMA_TOKEN}.service_routes.route_version",
        ],
        name="fk_assistant_runs_service_route",
    ),
    ForeignKeyConstraint(
        ["agent_release_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.agent_releases.release_id",
            f"{SCHEMA_TOKEN}.agent_releases.workspace_id",
        ],
        name="fk_assistant_runs_agent_release",
    ),
    ForeignKeyConstraint(
        ["runtime_config_version_id"],
        [f"{SCHEMA_TOKEN}.ai_runtime_config_versions.runtime_config_version_id"],
        name="fk_assistant_runs_runtime_config",
    ),
    ForeignKeyConstraint(
        ["requested_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_assistant_runs_requester",
    ),
    CheckConstraint(
        "(service_id IS NULL AND service_route_id IS NULL AND service_route_version IS NULL) OR "
        "(service_id IS NOT NULL AND service_route_id IS NOT NULL "
        "AND service_route_version >= 1)",
        name="ck_assistant_runs_service_binding",
    ),
    CheckConstraint(
        "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
        name="ck_assistant_runs_status",
    ),
    CheckConstraint(
        "(status IN ('queued', 'running') AND completed_at IS NULL) OR "
        "(status IN ('completed', 'failed', 'cancelled') AND completed_at IS NOT NULL)",
        name="ck_assistant_runs_completion",
    ),
    CheckConstraint(
        "request_hash ~ '^[0-9a-f]{64}$'",
        name="ck_assistant_runs_request_hash",
    ),
    CheckConstraint("trace_id ~ '^[0-9a-f]{32}$'", name="ck_assistant_runs_trace_id"),
)
Index(
    "ix_assistant_runs_workspace_time",
    assistant_runs.c.workspace_id,
    assistant_runs.c.created_at,
)
Index(
    "ix_assistant_runs_service_release_time",
    assistant_runs.c.workspace_id,
    assistant_runs.c.service_id,
    assistant_runs.c.agent_release_id,
    assistant_runs.c.created_at,
)
Index(
    "uq_assistant_runs_active_conversation",
    assistant_runs.c.conversation_id,
    unique=True,
    postgresql_where=assistant_runs.c.status.in_(("queued", "running")),
)

message_feedbacks = Table(
    "message_feedbacks",
    metadata,
    Column("feedback_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("conversation_id", UUID(as_uuid=True), nullable=False),
    Column("message_id", UUID(as_uuid=True), nullable=False),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("account_id", UUID(as_uuid=True), nullable=False),
    Column("rating", String(32), nullable=False),
    Column("issue_codes", ARRAY(String(32)), nullable=False),
    Column("comment", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint(
        "workspace_id",
        "message_id",
        "account_id",
        name="uq_message_feedbacks_account_message",
    ),
    ForeignKeyConstraint(
        ["conversation_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.conversations.conversation_id",
            f"{SCHEMA_TOKEN}.conversations.workspace_id",
        ],
        name="fk_message_feedbacks_conversation",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["message_id", "workspace_id"],
        [f"{SCHEMA_TOKEN}.messages.message_id", f"{SCHEMA_TOKEN}.messages.workspace_id"],
        name="fk_message_feedbacks_message",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["run_id"],
        [f"{SCHEMA_TOKEN}.assistant_runs.run_id"],
        name="fk_message_feedbacks_run",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_message_feedbacks_account",
    ),
    CheckConstraint("rating IN ('helpful', 'unhelpful')", name="ck_message_feedbacks_rating"),
    CheckConstraint(
        "issue_codes <@ ARRAY['incorrect', 'missing_source', 'source_mismatch', "
        "'unsafe', 'other']::varchar[]",
        name="ck_message_feedbacks_issue_codes",
    ),
    CheckConstraint(
        "(rating = 'helpful' AND cardinality(issue_codes) = 0) OR "
        "(rating = 'unhelpful' AND cardinality(issue_codes) BETWEEN 1 AND 5)",
        name="ck_message_feedbacks_issue_shape",
    ),
    CheckConstraint(
        "comment IS NULL OR char_length(btrim(comment)) BETWEEN 1 AND 1000",
        name="ck_message_feedbacks_comment",
    ),
    CheckConstraint("version >= 1", name="ck_message_feedbacks_version"),
)
Index(
    "ix_message_feedbacks_workspace_time",
    message_feedbacks.c.workspace_id,
    message_feedbacks.c.updated_at,
)

retrieval_plans = Table(
    "retrieval_plans",
    metadata,
    Column("retrieval_plan_id", UUID(as_uuid=True), primary_key=True),
    Column("run_id", UUID(as_uuid=True), nullable=False, unique=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("requested_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("original_query_hash", String(64), nullable=False),
    Column("classification", String(32), nullable=False),
    Column("strategy_version", String(128), nullable=False),
    Column("policy_decision_id", UUID(as_uuid=True), nullable=False),
    Column("policy_version", Integer, nullable=False),
    Column("maximum_security_level", String(32), nullable=False),
    Column("field_mask", ARRAY(String(128)), nullable=False),
    Column("embedding_model_version", String(255), nullable=False),
    Column("tokenizer_version", String(128), nullable=False),
    Column("max_query_characters", Integer, nullable=False),
    Column("max_query_variants", Integer, nullable=False),
    Column("max_search_operations", Integer, nullable=False),
    Column("per_channel_candidates", Integer, nullable=False),
    Column("final_candidate_limit", Integer, nullable=False),
    Column("rrf_constant", Integer, nullable=False),
    Column("max_elapsed_ms", Integer, nullable=False),
    Column("search_operation_count", Integer, nullable=False),
    Column("keyword_candidate_count", Integer, nullable=False),
    Column("vector_candidate_count", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=False),
    Column("duration_ms", Integer, nullable=False),
    ForeignKeyConstraint(
        ["run_id"],
        [f"{SCHEMA_TOKEN}.assistant_runs.run_id"],
        name="fk_retrieval_plans_run",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["requested_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_retrieval_plans_requester",
    ),
    CheckConstraint(
        "original_query_hash ~ '^[0-9a-f]{64}$'",
        name="ck_retrieval_plans_query_hash",
    ),
    CheckConstraint(
        "classification IN ('exact_lookup', 'summary', 'comparison', 'knowledge')",
        name="ck_retrieval_plans_classification",
    ),
    CheckConstraint(
        "maximum_security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
        name="ck_retrieval_plans_security_level",
    ),
    CheckConstraint("policy_version >= 1", name="ck_retrieval_plans_policy_version"),
    CheckConstraint(
        "max_query_characters >= 1 AND max_query_variants >= 1 "
        "AND max_search_operations >= 1 AND per_channel_candidates >= 1 "
        "AND final_candidate_limit >= 1 AND rrf_constant >= 1 AND max_elapsed_ms >= 1",
        name="ck_retrieval_plans_budget",
    ),
    CheckConstraint(
        "search_operation_count >= 0 AND keyword_candidate_count >= 0 "
        "AND vector_candidate_count >= 0 AND duration_ms >= 0",
        name="ck_retrieval_plans_metrics",
    ),
    CheckConstraint("status = 'completed'", name="ck_retrieval_plans_status"),
)
Index(
    "ix_retrieval_plans_workspace_time",
    retrieval_plans.c.workspace_id,
    retrieval_plans.c.created_at,
)

retrieval_query_variants = Table(
    "retrieval_query_variants",
    metadata,
    Column("retrieval_plan_id", UUID(as_uuid=True), primary_key=True),
    Column("sequence_no", Integer, primary_key=True),
    Column("kind", String(32), nullable=False),
    Column("query_text", Text, nullable=False),
    Column("query_hash", String(64), nullable=False),
    ForeignKeyConstraint(
        ["retrieval_plan_id"],
        [f"{SCHEMA_TOKEN}.retrieval_plans.retrieval_plan_id"],
        name="fk_retrieval_query_variants_plan",
        ondelete="CASCADE",
    ),
    CheckConstraint("sequence_no >= 1", name="ck_retrieval_query_variants_sequence"),
    CheckConstraint("kind IN ('original', 'focused')", name="ck_retrieval_query_variants_kind"),
    CheckConstraint(
        "char_length(btrim(query_text)) BETWEEN 1 AND 4000",
        name="ck_retrieval_query_variants_text",
    ),
    CheckConstraint(
        "query_hash ~ '^[0-9a-f]{64}$'",
        name="ck_retrieval_query_variants_hash",
    ),
)

retrieval_candidate_snapshots = Table(
    "retrieval_candidate_snapshots",
    metadata,
    Column("retrieval_plan_id", UUID(as_uuid=True), primary_key=True),
    Column("rank", Integer, primary_key=True),
    Column("chunk_id", UUID(as_uuid=True), nullable=False),
    Column("index_version_id", UUID(as_uuid=True), nullable=False),
    Column("knowledge_base_id", UUID(as_uuid=True), nullable=False),
    Column("document_id", UUID(as_uuid=True), nullable=False),
    Column("document_version_id", UUID(as_uuid=True), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("source_position", JSONB, nullable=False),
    Column("score", Float, nullable=False),
    Column("query_hit_count", Integer, nullable=False),
    Column("keyword_hit_count", Integer, nullable=False),
    Column("vector_hit_count", Integer, nullable=False),
    ForeignKeyConstraint(
        ["retrieval_plan_id"],
        [f"{SCHEMA_TOKEN}.retrieval_plans.retrieval_plan_id"],
        name="fk_retrieval_candidate_snapshots_plan",
        ondelete="CASCADE",
    ),
    CheckConstraint("rank >= 1", name="ck_retrieval_candidate_snapshots_rank"),
    CheckConstraint(
        "content_hash ~ '^[0-9a-f]{64}$'",
        name="ck_retrieval_candidate_snapshots_hash",
    ),
    CheckConstraint(
        "score >= 0 AND query_hit_count >= 1 AND keyword_hit_count >= 0 AND vector_hit_count >= 0",
        name="ck_retrieval_candidate_snapshots_metrics",
    ),
)

retrieval_evidence_sets = Table(
    "retrieval_evidence_sets",
    metadata,
    Column("evidence_set_id", UUID(as_uuid=True), primary_key=True),
    Column("retrieval_plan_id", UUID(as_uuid=True), nullable=False, unique=True),
    Column("run_id", UUID(as_uuid=True), nullable=False, unique=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("requested_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(32), nullable=False),
    Column("degradation_reason", String(64), nullable=True),
    Column("policy_decision_id", UUID(as_uuid=True), nullable=False),
    Column("policy_version", Integer, nullable=False),
    Column("reranker_model_version", String(255), nullable=False),
    Column("source_ranking_version", String(128), nullable=False),
    Column("fastpass_used", Boolean, nullable=False),
    Column("reranker_used", Boolean, nullable=False),
    Column("candidate_count", Integer, nullable=False),
    Column("rejected_candidate_count", Integer, nullable=False),
    Column("conflict_count", Integer, nullable=False),
    Column("read_document_count", Integer, nullable=False),
    Column("read_chunk_count", Integer, nullable=False),
    Column("read_character_count", Integer, nullable=False),
    Column("estimated_token_count", Integer, nullable=False),
    Column("rerank_candidate_limit", Integer, nullable=False),
    Column("final_evidence_limit", Integer, nullable=False),
    Column("max_documents", Integer, nullable=False),
    Column("surrounding_chunks", Integer, nullable=False),
    Column("max_chunks", Integer, nullable=False),
    Column("max_characters", Integer, nullable=False),
    Column("max_tokens", Integer, nullable=False),
    Column("max_elapsed_ms", Integer, nullable=False),
    Column("fastpass_score_ratio", Float, nullable=False),
    Column("minimum_final_score", Float, nullable=False),
    Column("max_quote_characters", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=False),
    Column("duration_ms", Integer, nullable=False),
    ForeignKeyConstraint(
        ["retrieval_plan_id"],
        [f"{SCHEMA_TOKEN}.retrieval_plans.retrieval_plan_id"],
        name="fk_retrieval_evidence_sets_plan",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["run_id"],
        [f"{SCHEMA_TOKEN}.assistant_runs.run_id"],
        name="fk_retrieval_evidence_sets_run",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["requested_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_retrieval_evidence_sets_requester",
    ),
    CheckConstraint(
        "(status = 'sufficient' AND degradation_reason IS NULL) OR "
        "(status = 'uncertain' AND degradation_reason IN "
        "('no_candidates', 'no_current_evidence', 'insufficient_sources', "
        "'conflicting_evidence'))",
        name="ck_retrieval_evidence_sets_status",
    ),
    CheckConstraint("policy_version >= 1", name="ck_retrieval_evidence_sets_policy"),
    CheckConstraint(
        "candidate_count >= 0 AND rejected_candidate_count >= 0 "
        "AND rejected_candidate_count <= candidate_count AND conflict_count >= 0 "
        "AND read_document_count >= 0 AND read_chunk_count >= 0 "
        "AND read_character_count >= 0 AND estimated_token_count >= 0 AND duration_ms >= 0",
        name="ck_retrieval_evidence_sets_metrics",
    ),
    CheckConstraint(
        "rerank_candidate_limit >= 1 AND final_evidence_limit >= 1 AND max_documents >= 1 "
        "AND surrounding_chunks >= 0 AND max_chunks >= 1 AND max_characters >= 1 "
        "AND max_tokens >= 1 AND max_elapsed_ms >= 1 AND fastpass_score_ratio >= 1 "
        "AND minimum_final_score BETWEEN 0 AND 1 AND max_quote_characters >= 1",
        name="ck_retrieval_evidence_sets_budget",
    ),
)
Index(
    "ix_retrieval_evidence_sets_workspace_time",
    retrieval_evidence_sets.c.workspace_id,
    retrieval_evidence_sets.c.created_at,
)

retrieval_evidence_items = Table(
    "retrieval_evidence_items",
    metadata,
    Column("evidence_set_id", UUID(as_uuid=True), primary_key=True),
    Column("rank", Integer, primary_key=True),
    Column("chunk_id", UUID(as_uuid=True), nullable=False),
    Column("index_version_id", UUID(as_uuid=True), nullable=False),
    Column("knowledge_base_id", UUID(as_uuid=True), nullable=False),
    Column("document_id", UUID(as_uuid=True), nullable=False),
    Column("document_version_id", UUID(as_uuid=True), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("quote", Text, nullable=False),
    Column("context_text", Text, nullable=False),
    Column("context_hash", String(64), nullable=False),
    Column("context_chunk_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
    Column("source_position", JSONB, nullable=False),
    Column("document_title", String(255), nullable=False),
    Column("source_kind", String(32), nullable=False),
    Column("source_name", String(255), nullable=False),
    Column("retrieval_score", Float, nullable=False),
    Column("relevance_score", Float, nullable=False),
    Column("authority_score", Float, nullable=False),
    Column("freshness_score", Float, nullable=False),
    Column("final_score", Float, nullable=False),
    Column("conflict_detected", Boolean, nullable=False),
    ForeignKeyConstraint(
        ["evidence_set_id"],
        [f"{SCHEMA_TOKEN}.retrieval_evidence_sets.evidence_set_id"],
        name="fk_retrieval_evidence_items_set",
        ondelete="CASCADE",
    ),
    CheckConstraint("rank >= 1", name="ck_retrieval_evidence_items_rank"),
    CheckConstraint(
        "content_hash ~ '^[0-9a-f]{64}$' AND context_hash ~ '^[0-9a-f]{64}$'",
        name="ck_retrieval_evidence_items_hashes",
    ),
    CheckConstraint(
        "char_length(btrim(quote)) BETWEEN 1 AND 1000 "
        "AND char_length(context_text) BETWEEN 1 AND 12000 "
        "AND cardinality(context_chunk_ids) BETWEEN 1 AND 12",
        name="ck_retrieval_evidence_items_content",
    ),
    CheckConstraint(
        "source_kind IN ('manual', 'upload', 'web', 'data_source')",
        name="ck_retrieval_evidence_items_source_kind",
    ),
    CheckConstraint(
        "retrieval_score >= 0 AND relevance_score BETWEEN 0 AND 1 "
        "AND authority_score BETWEEN 0 AND 1 AND freshness_score BETWEEN 0 AND 1 "
        "AND final_score BETWEEN 0 AND 1",
        name="ck_retrieval_evidence_items_scores",
    ),
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

lifecycle_export_records = Table(
    "lifecycle_export_records",
    metadata,
    Column("export_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("registry_version", Integer, nullable=False),
    Column("object_key", String(2048), nullable=True),
    Column("bundle_size_bytes", BigInteger, nullable=True),
    Column("bundle_sha256", String(64), nullable=True),
    Column("object_manifest_sha256", String(64), nullable=True),
    Column("table_count", Integer, nullable=True),
    Column("object_count", Integer, nullable=True),
    Column("requested_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("request_id", UUID(as_uuid=True), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("traceparent", String(55), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("error_code", String(128), nullable=True),
    UniqueConstraint(
        "workspace_id",
        "idempotency_key",
        name="uq_lifecycle_exports_idempotency",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_lifecycle_exports_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["requested_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_lifecycle_exports_requester",
    ),
    CheckConstraint(
        "status IN ('running', 'completed', 'failed')",
        name="ck_lifecycle_exports_status",
    ),
    CheckConstraint("registry_version >= 1", name="ck_lifecycle_exports_registry"),
    CheckConstraint(
        "request_hash ~ '^[0-9a-f]{64}$'",
        name="ck_lifecycle_exports_request_hash",
    ),
    CheckConstraint(
        "(status = 'running' AND completed_at IS NULL AND error_code IS NULL) OR "
        "(status = 'completed' AND completed_at IS NOT NULL AND error_code IS NULL "
        "AND object_key IS NOT NULL AND bundle_size_bytes >= 0 "
        "AND bundle_sha256 ~ '^[0-9a-f]{64}$' "
        "AND object_manifest_sha256 ~ '^[0-9a-f]{64}$' "
        "AND table_count >= 0 AND object_count >= 0) OR "
        "(status = 'failed' AND completed_at IS NOT NULL AND error_code IS NOT NULL)",
        name="ck_lifecycle_exports_completion",
    ),
)

lifecycle_purge_requests = Table(
    "lifecycle_purge_requests",
    metadata,
    Column("purge_request_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("reason_code", String(64), nullable=False),
    Column("confirmed_workspace_name", String(120), nullable=False),
    Column("status", String(32), nullable=False),
    Column("database_cleared", Boolean, nullable=False),
    Column("objects_cleared", Boolean, nullable=False),
    Column("cache_cleared", Boolean, nullable=False),
    Column("deleted_table_counts", JSONB, nullable=False),
    Column("deleted_object_count", Integer, nullable=False),
    Column("deleted_cache_key_count", Integer, nullable=False),
    Column("last_error_code", String(128), nullable=True),
    Column("requested_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("request_id", UUID(as_uuid=True), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("traceparent", String(55), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint(
        "workspace_id",
        "idempotency_key",
        name="uq_lifecycle_purges_idempotency",
    ),
    UniqueConstraint(
        "purge_request_id",
        "workspace_id",
        name="uq_lifecycle_purges_id_workspace",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_lifecycle_purges_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["requested_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_lifecycle_purges_requester",
    ),
    CheckConstraint(
        "status IN ('pending', 'retryable', 'completed')",
        name="ck_lifecycle_purges_status",
    ),
    CheckConstraint(
        "reason_code ~ '^[A-Z][A-Z0-9_]{2,63}$' AND request_hash ~ '^[0-9a-f]{64}$'",
        name="ck_lifecycle_purges_request",
    ),
    CheckConstraint(
        "jsonb_typeof(deleted_table_counts) = 'object' "
        "AND deleted_object_count >= 0 AND deleted_cache_key_count >= 0",
        name="ck_lifecycle_purges_counts",
    ),
    CheckConstraint(
        "(status = 'completed' AND database_cleared AND objects_cleared AND cache_cleared "
        "AND completed_at IS NOT NULL AND last_error_code IS NULL) OR "
        "(status <> 'completed' AND completed_at IS NULL)",
        name="ck_lifecycle_purges_completion",
    ),
)

lifecycle_deletion_certificates = Table(
    "lifecycle_deletion_certificates",
    metadata,
    Column("certificate_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("purge_request_id", UUID(as_uuid=True), nullable=False, unique=True),
    Column("registry_version", Integer, nullable=False),
    Column("deleted_table_counts", JSONB, nullable=False),
    Column("deleted_object_count", Integer, nullable=False),
    Column("deleted_cache_key_count", Integer, nullable=False),
    Column("result_sha256", String(64), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["purge_request_id", "workspace_id"],
        [
            f"{SCHEMA_TOKEN}.lifecycle_purge_requests.purge_request_id",
            f"{SCHEMA_TOKEN}.lifecycle_purge_requests.workspace_id",
        ],
        name="fk_lifecycle_certificates_request",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "registry_version >= 1 AND deleted_object_count >= 0 "
        "AND deleted_cache_key_count >= 0 AND jsonb_typeof(deleted_table_counts) = 'object' "
        "AND result_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_lifecycle_certificates_result",
    ),
)

lifecycle_retention_runs = Table(
    "lifecycle_retention_runs",
    metadata,
    Column("retention_run_id", UUID(as_uuid=True), primary_key=True),
    Column("workspace_id", UUID(as_uuid=True), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("status", String(32), nullable=False),
    Column("cutoffs", JSONB, nullable=False),
    Column("deleted_table_counts", JSONB, nullable=False),
    Column("result_sha256", String(64), nullable=True),
    Column("requested_by_account_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("error_code", String(128), nullable=True),
    UniqueConstraint(
        "workspace_id",
        "idempotency_key",
        name="uq_lifecycle_retention_idempotency",
    ),
    ForeignKeyConstraint(
        ["workspace_id"],
        [f"{SCHEMA_TOKEN}.workspaces.workspace_id"],
        name="fk_lifecycle_retention_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["requested_by_account_id"],
        [f"{SCHEMA_TOKEN}.accounts.account_id"],
        name="fk_lifecycle_retention_requester",
    ),
    CheckConstraint(
        "status IN ('running', 'completed', 'failed')",
        name="ck_lifecycle_retention_status",
    ),
    CheckConstraint(
        "jsonb_typeof(cutoffs) = 'object' AND jsonb_typeof(deleted_table_counts) = 'object'",
        name="ck_lifecycle_retention_documents",
    ),
    CheckConstraint(
        "(status = 'running' AND completed_at IS NULL AND error_code IS NULL) OR "
        "(status = 'completed' AND completed_at IS NOT NULL AND error_code IS NULL "
        "AND result_sha256 ~ '^[0-9a-f]{64}$') OR "
        "(status = 'failed' AND completed_at IS NOT NULL AND error_code IS NOT NULL)",
        name="ck_lifecycle_retention_completion",
    ),
)
Index(
    "ix_lifecycle_exports_workspace_created",
    lifecycle_export_records.c.workspace_id,
    lifecycle_export_records.c.created_at,
)
Index(
    "ix_lifecycle_purges_workspace_created",
    lifecycle_purge_requests.c.workspace_id,
    lifecycle_purge_requests.c.created_at,
)
Index(
    "ix_lifecycle_certificates_workspace_completed",
    lifecycle_deletion_certificates.c.workspace_id,
    lifecycle_deletion_certificates.c.completed_at,
)
Index(
    "ix_lifecycle_retention_workspace_created",
    lifecycle_retention_runs.c.workspace_id,
    lifecycle_retention_runs.c.created_at,
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
