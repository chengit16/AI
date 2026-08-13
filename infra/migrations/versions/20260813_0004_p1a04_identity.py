"""建立 P1A-04 账号、空间成员和 Open API Key 事实模型。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0004"
down_revision: str | None = "20260813_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "accounts",
        sa.Column("account_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("login_name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_accounts_status"),
        sa.CheckConstraint("auth_version >= 1", name="ck_accounts_auth_version"),
        sa.CheckConstraint("version >= 1", name="ck_accounts_version"),
        schema=schema,
    )
    op.create_table(
        "workspaces",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("owner_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entitlement_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "workspace_type IN ('personal', 'enterprise')",
            name="ck_workspaces_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'archived')",
            name="ck_workspaces_status",
        ),
        sa.CheckConstraint(
            "entitlement_version >= 1",
            name="ck_workspaces_entitlement_version",
        ),
        sa.CheckConstraint("version >= 1", name="ck_workspaces_version"),
        sa.ForeignKeyConstraint(
            ["owner_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_workspaces_owner",
        ),
        schema=schema,
    )
    op.create_table(
        "workspace_memberships",
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "account_id",
            name="uq_workspace_memberships_member",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_workspace_memberships_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_workspace_memberships_account",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'left')",
            name="ck_workspace_memberships_status",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_workspace_memberships_account_workspace",
        "workspace_memberships",
        ["account_id", "workspace_id"],
        schema=schema,
    )
    op.create_table(
        "open_api_keys",
        sa.Column("key_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("secret_digest", sa.String(length=64), nullable=False),
        sa.Column("last_four", sa.String(length=4), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.String(length=128)), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("char_length(last_four) = 4", name="ck_open_api_keys_last_four"),
        sa.CheckConstraint("char_length(secret_digest) = 64", name="ck_open_api_keys_digest"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_open_api_keys_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_open_api_keys_creator",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_open_api_keys_workspace_status",
        "open_api_keys",
        ["workspace_id", "revoked_at", "expires_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "ix_open_api_keys_workspace_status",
        table_name="open_api_keys",
        schema=schema,
    )
    op.drop_table("open_api_keys", schema=schema)
    op.drop_index(
        "ix_workspace_memberships_account_workspace",
        table_name="workspace_memberships",
        schema=schema,
    )
    op.drop_table("workspace_memberships", schema=schema)
    op.drop_table("workspaces", schema=schema)
    op.drop_table("accounts", schema=schema)
