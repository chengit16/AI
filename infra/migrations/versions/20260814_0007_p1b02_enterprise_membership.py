"""建立 P1B-02 企业成员类型与定向邀请事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0007"
down_revision: str | None = "20260814_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    enterprise_membership_exists = op.get_bind().scalar(
        sa.text(
            f'SELECT EXISTS (SELECT 1 FROM "{schema}".workspace_memberships m '
            f'JOIN "{schema}".workspaces w ON w.workspace_id = m.workspace_id '
            "WHERE w.workspace_type = 'enterprise')"
        )
    )
    if enterprise_membership_exists:
        # 旧结构无法证明哪个成员是所有者，拒绝猜测和静默提升权限。
        raise RuntimeError("现有企业成员缺少可验证的所有者类型且不能自动升级")

    op.add_column(
        "workspace_memberships",
        sa.Column("membership_type", sa.String(length=32), nullable=True),
        schema=schema,
    )
    op.add_column(
        "workspace_memberships",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        schema=schema,
    )
    op.execute(
        sa.text(
            f"UPDATE \"{schema}\".workspace_memberships m SET membership_type = 'owner' "
            f'FROM "{schema}".workspaces w '
            "WHERE w.workspace_id = m.workspace_id "
            "AND w.workspace_type = 'personal' "
            "AND w.owner_account_id = m.account_id"
        )
    )
    missing_type = op.get_bind().scalar(
        sa.text(
            f'SELECT EXISTS (SELECT 1 FROM "{schema}".workspace_memberships '
            "WHERE membership_type IS NULL)"
        )
    )
    if missing_type:
        raise RuntimeError("现有成员关系无法确定成员类型")
    op.alter_column(
        "workspace_memberships",
        "membership_type",
        existing_type=sa.String(length=32),
        nullable=False,
        schema=schema,
    )
    op.create_check_constraint(
        "ck_workspace_memberships_type",
        "workspace_memberships",
        "membership_type IN ('owner', 'member')",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_workspace_memberships_version",
        "workspace_memberships",
        "version >= 1",
        schema=schema,
    )
    op.create_index(
        "uq_workspace_memberships_owner",
        "workspace_memberships",
        ["workspace_id"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("membership_type = 'owner'"),
    )
    op.create_table(
        "workspace_invitations",
        sa.Column("invitation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invited_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invited_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'cancelled', 'expired')",
            name="ck_workspace_invitations_status",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_workspace_invitations_expiry",
        ),
        sa.CheckConstraint(
            "(status = 'accepted' AND accepted_at IS NOT NULL) "
            "OR (status <> 'accepted' AND accepted_at IS NULL)",
            name="ck_workspace_invitations_accepted_at",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_workspace_invitations_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["invited_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_workspace_invitations_account",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_workspace_invitations_inviter",
        ),
        schema=schema,
    )
    op.create_index(
        "uq_workspace_invitations_pending",
        "workspace_invitations",
        ["workspace_id", "invited_account_id"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "uq_workspace_invitations_pending",
        table_name="workspace_invitations",
        schema=schema,
    )
    op.drop_table("workspace_invitations", schema=schema)
    op.drop_index(
        "uq_workspace_memberships_owner",
        table_name="workspace_memberships",
        schema=schema,
    )
    op.drop_constraint(
        "ck_workspace_memberships_version",
        "workspace_memberships",
        type_="check",
        schema=schema,
    )
    op.drop_constraint(
        "ck_workspace_memberships_type",
        "workspace_memberships",
        type_="check",
        schema=schema,
    )
    op.drop_column("workspace_memberships", "version", schema=schema)
    op.drop_column("workspace_memberships", "membership_type", schema=schema)
