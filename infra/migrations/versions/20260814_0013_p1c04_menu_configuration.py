"""建立 P1C-04 菜单覆盖、动作接口绑定与角色可见性事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0013"
down_revision: str | None = "20260814_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MENU_API_BINDINGS = (
    (101, 8, "mutation"),
    (102, 9, "mutation"),
    (103, 10, "mutation"),
    (104, 11, "mutation"),
    (105, 12, "mutation"),
    (106, 13, "mutation"),
    (107, 14, "query"),
    (108, 15, "query"),
    (109, 16, "mutation"),
    (110, 17, "mutation"),
    (111, 18, "query"),
    (112, 19, "mutation"),
    (113, 20, "mutation"),
    (114, 21, "mutation"),
    (115, 22, "query"),
    (116, 23, "mutation"),
    (117, 24, "mutation"),
    (118, 25, "query"),
    (119, 26, "mutation"),
    (120, 27, "query"),
    (121, 28, "mutation"),
    (122, 29, "mutation"),
    (123, 30, "mutation"),
    (124, 31, "query"),
    (125, 32, "query"),
    (126, 33, "mutation"),
    (127, 34, "query"),
    (128, 35, "mutation"),
    (129, 36, "query"),
    (130, 37, "mutation"),
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    op.add_column(
        "workspaces",
        sa.Column("menu_version", sa.Integer(), nullable=False, server_default="1"),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_workspaces_menu_version",
        "workspaces",
        "menu_version >= 1",
        schema=schema,
    )
    op.create_table(
        "registered_menu_api_bindings",
        sa.Column("menu_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("api_resource_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "action_type IN ('query', 'mutation', 'publish', 'approve')",
            name="ck_registered_menu_api_bindings_action",
        ),
        schema=schema,
    )
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
        for menu_number, api_number, action_type in MENU_API_BINDINGS
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
    op.create_table(
        "workspace_menu_overrides",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("menu_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("parent_menu_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("icon_key", sa.String(length=80), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("visible", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_workspace_menu_overrides_workspace",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("sort_order >= 0", name="ck_workspace_menu_overrides_sort"),
        sa.CheckConstraint("version >= 1", name="ck_workspace_menu_overrides_version"),
        schema=schema,
    )
    op.create_table(
        "role_menus",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("menu_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("visible", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "role_id"],
            [f"{schema}.roles.workspace_id", f"{schema}.roles.role_id"],
            name="fk_role_menus_role",
            ondelete="CASCADE",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_role_menus_lookup",
        "role_menus",
        ["workspace_id", "role_id", "visible"],
        schema=schema,
    )
    # 已有和未来空间共用静态菜单接口镜像；管理员只能引用这些冻结标识。
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
            CROSS JOIN (VALUES
                ('authorization.menu.read'),
                ('authorization.menu.manage')
            ) AS permissions(permission_code)
            WHERE roles.role_key = 'workspace_owner' AND roles.system_managed = true
            """
        )
    )
    # 回滚前删除本 Revision 注入的系统授权，恢复到 P1C-03 的精确状态。


def downgrade() -> None:
    schema = _schema()
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".role_permission_grants AS grants
            USING "{schema}".roles AS roles
            WHERE grants.workspace_id = roles.workspace_id
              AND grants.role_id = roles.role_id
              AND roles.role_key = 'workspace_owner'
              AND roles.system_managed = true
              AND grants.permission_code IN (
                  'authorization.menu.read',
                  'authorization.menu.manage'
              )
            """
        )
    )
    op.drop_index("ix_role_menus_lookup", table_name="role_menus", schema=schema)
    op.drop_table("role_menus", schema=schema)
    op.drop_table("workspace_menu_overrides", schema=schema)
    op.drop_table("registered_menu_api_bindings", schema=schema)
    op.drop_constraint(
        "ck_workspaces_menu_version",
        "workspaces",
        type_="check",
        schema=schema,
    )
    op.drop_column("workspaces", "menu_version", schema=schema)
