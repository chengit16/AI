"""建立 P1C-05 不可变菜单发布快照与当前版本指针。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0014"
down_revision: str | None = "20260814_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MENU_RELEASE_BINDINGS = tuple(
    (menu_number, api_number, action_type)
    for menu_number, api_number, action_type in (
        (131, 38, "mutation"),
        (132, 39, "mutation"),
        (133, 40, "approve"),
        (134, 41, "publish"),
        (135, 42, "publish"),
        (136, 43, "query"),
        (137, 44, "query"),
    )
)
MENU_RELEASE_PERMISSIONS = (
    "authorization.menu_release.approve",
    "authorization.menu_release.create",
    "authorization.menu_release.publish",
    "authorization.menu_release.read",
    "authorization.menu_release.rollback",
    "authorization.menu_release.validate",
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    binding_values = ", ".join(
        "("
        + ", ".join(
            (
                f"'82000000-0000-4000-8000-{menu_number:012d}'::uuid",
                f"'81000000-0000-4000-8000-{api_number:012d}'::uuid",
                f"'{action_type}'",
            )
        )
        + ")"
        for menu_number, api_number, action_type in MENU_RELEASE_BINDINGS
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".registered_menu_api_bindings (
                menu_id, api_resource_id, action_type
            ) VALUES {binding_values}
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, 'authorization.menu_release.read',
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[], 'INTERNAL', ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            WHERE roles.role_key = 'workspace_member' AND roles.system_managed = true
            """
        )
    )
    permissions = ", ".join(f"('{code}')" for code in MENU_RELEASE_PERMISSIONS)
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, permission_code,
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[], 'RESTRICTED', ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            CROSS JOIN (VALUES {permissions}) AS permission_codes(permission_code)
            WHERE roles.role_key = 'workspace_owner' AND roles.system_managed = true
            """
        )
    )
    op.create_table(
        "menu_releases",
        sa.Column("release_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("release_number", sa.Integer(), nullable=False),
        sa.Column("release_kind", sa.String(length=32), nullable=False),
        sa.Column("source_release_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "validation_errors",
            postgresql.ARRAY(sa.String(length=500)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decided_by_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "release_number",
            name="uq_menu_releases_workspace_number",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "release_id",
            name="uq_menu_releases_workspace_release",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_menu_releases_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_release_id"],
            [f"{schema}.menu_releases.workspace_id", f"{schema}.menu_releases.release_id"],
            name="fk_menu_releases_source",
        ),
        sa.CheckConstraint(
            "release_kind IN ('standard', 'rollback')",
            name="ck_menu_releases_kind",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'validated', 'approved', 'rejected', 'published')",
            name="ck_menu_releases_status",
        ),
        sa.CheckConstraint("release_number >= 1", name="ck_menu_releases_number"),
        sa.CheckConstraint("char_length(snapshot_digest) = 64", name="ck_menu_releases_digest"),
        sa.CheckConstraint("version >= 1", name="ck_menu_releases_version"),
        schema=schema,
    )
    op.create_index(
        "ix_menu_releases_workspace_status",
        "menu_releases",
        ["workspace_id", "status", "release_number"],
        schema=schema,
    )
    op.create_table(
        "workspace_menu_publications",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("current_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_workspace_menu_publications_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "current_release_id"],
            [f"{schema}.menu_releases.workspace_id", f"{schema}.menu_releases.release_id"],
            name="fk_workspace_menu_publications_release",
        ),
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_table("workspace_menu_publications", schema=schema)
    op.drop_index(
        "ix_menu_releases_workspace_status",
        table_name="menu_releases",
        schema=schema,
    )
    op.drop_table("menu_releases", schema=schema)
    permissions = ", ".join(f"'{code}'" for code in MENU_RELEASE_PERMISSIONS)
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".role_permission_grants AS grants
            USING "{schema}".roles AS roles
            WHERE grants.workspace_id = roles.workspace_id
              AND grants.role_id = roles.role_id
              AND roles.role_key = 'workspace_owner'
              AND roles.system_managed = true
              AND grants.permission_code IN ({permissions})
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".role_permission_grants AS grants
            USING "{schema}".roles AS roles
            WHERE grants.workspace_id = roles.workspace_id
              AND grants.role_id = roles.role_id
              AND roles.role_key = 'workspace_member'
              AND roles.system_managed = true
              AND grants.permission_code = 'authorization.menu_release.read'
            """
        )
    )
    menu_ids = ", ".join(
        f"'82000000-0000-4000-8000-{menu_number:012d}'::uuid"
        for menu_number, _, _ in MENU_RELEASE_BINDINGS
    )
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE menu_id IN ({menu_ids})
            """
        )
    )
