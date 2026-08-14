"""建立 P1D-05 平台管理员、模型供应商、版本化凭证和平台审计事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0021"
down_revision: str | None = "20260814_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _create_platform_administrators(schema)
    _create_provider_configurations(schema)
    _create_provider_credentials(schema)
    _create_platform_audit(schema)


def downgrade() -> None:
    schema = _schema()
    op.drop_table("platform_audit_records", schema=schema)
    op.drop_table("model_provider_credentials", schema=schema)
    op.drop_table("model_provider_configurations", schema=schema)
    op.drop_table("platform_administrators", schema=schema)


def _create_platform_administrators(schema: str) -> None:
    op.create_table(
        "platform_administrators",
        sa.Column("account_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("granted_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_platform_administrators_account",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_platform_administrators_granter",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_platform_administrators_status"
        ),
        sa.CheckConstraint(
            "(status = 'active' AND revoked_at IS NULL) OR "
            "(status = 'revoked' AND revoked_at IS NOT NULL)",
            name="ck_platform_administrators_revocation",
        ),
        schema=schema,
    )


def _create_provider_configurations(schema: str) -> None:
    op.create_table(
        "model_provider_configurations",
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_key", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("adapter_kind", sa.String(32), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=False),
        sa.Column("probe_model_id", sa.String(255), nullable=False),
        sa.Column("location", sa.String(32), nullable=False),
        sa.Column("declared_capabilities", postgresql.ARRAY(sa.String(32)), nullable=False),
        sa.Column("policy_review_status", sa.String(32), nullable=False),
        sa.Column("max_security_level", sa.String(32), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("training_usage_allowed", sa.Boolean(), nullable=False),
        sa.Column("policy_url", sa.String(2048), nullable=True),
        sa.Column("policy_version", sa.String(128), nullable=True),
        sa.Column("policy_reviewed_by_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("probe_status", sa.String(32), nullable=False),
        sa.Column("probed_capabilities", postgresql.ARRAY(sa.String(32)), nullable=False),
        sa.Column("last_probe_error_code", sa.String(128), nullable=True),
        sa.Column("last_probed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_reviewed_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_model_provider_configurations_policy_reviewer",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_model_provider_configurations_creator",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_model_provider_configurations_updater",
        ),
        sa.CheckConstraint("adapter_kind = 'openai_compatible'", name="ck_model_providers_adapter"),
        sa.CheckConstraint(
            "location IN ('external', 'private')", name="ck_model_providers_location"
        ),
        sa.CheckConstraint(
            "policy_review_status IN ('pending', 'approved', 'rejected')",
            name="ck_model_providers_policy_status",
        ),
        sa.CheckConstraint(
            "max_security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
            name="ck_model_providers_security_level",
        ),
        sa.CheckConstraint(
            "retention_days IS NULL OR retention_days BETWEEN 0 AND 3650",
            name="ck_model_providers_retention",
        ),
        sa.CheckConstraint(
            "probe_status IN ('not_run', 'passed', 'failed')",
            name="ck_model_providers_probe_status",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'disabled')", name="ck_model_providers_status"
        ),
        sa.CheckConstraint("version >= 1", name="ck_model_providers_version"),
        sa.CheckConstraint(
            "(policy_review_status = 'pending' AND policy_reviewed_by_account_id IS NULL "
            "AND policy_reviewed_at IS NULL) OR "
            "(policy_review_status <> 'pending' AND policy_reviewed_by_account_id IS NOT NULL "
            "AND policy_reviewed_at IS NOT NULL)",
            name="ck_model_providers_policy_review",
        ),
        sa.CheckConstraint(
            "(probe_status = 'not_run' AND last_probed_at IS NULL) OR "
            "(probe_status <> 'not_run' AND last_probed_at IS NOT NULL)",
            name="ck_model_providers_probe_time",
        ),
        schema=schema,
    )


def _create_provider_credentials(schema: str) -> None:
    op.create_table(
        "model_provider_credentials",
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_version", sa.Integer(), nullable=False),
        sa.Column("master_key_version", sa.Integer(), nullable=False),
        sa.Column("encrypted_data_key", sa.LargeBinary(), nullable=False),
        sa.Column("data_key_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("data_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("last_four", sa.String(4), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "provider_id", "credential_version", name="uq_model_provider_credentials_version"
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            [f"{schema}.model_provider_configurations.provider_id"],
            name="fk_model_provider_credentials_provider",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_model_provider_credentials_creator",
        ),
        sa.CheckConstraint("credential_version >= 1", name="ck_model_credentials_version"),
        sa.CheckConstraint("master_key_version >= 1", name="ck_model_credentials_master_key"),
        sa.CheckConstraint("char_length(last_four) = 4", name="ck_model_credentials_last_four"),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_model_credentials_status"),
        sa.CheckConstraint(
            "(status = 'active' AND revoked_at IS NULL) OR "
            "(status = 'revoked' AND revoked_at IS NOT NULL)",
            name="ck_model_credentials_revocation",
        ),
        schema=schema,
    )
    op.create_index(
        "uq_model_provider_credentials_active",
        "model_provider_credentials",
        ["provider_id"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("status = 'active'"),
    )


def _create_platform_audit(schema: str) -> None:
    op.create_table(
        "platform_audit_records",
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_platform_audit_records_account",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            [f"{schema}.model_provider_configurations.provider_id"],
            name="fk_platform_audit_records_provider",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("trace_id ~ '^[0-9a-f]{32}$'", name="ck_platform_audit_trace_id"),
        schema=schema,
    )
    op.create_index(
        "ix_platform_audit_provider_time",
        "platform_audit_records",
        ["provider_id", "occurred_at"],
        schema=schema,
    )
