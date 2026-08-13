"""建立 P1B-04 空间、部门与成员角色事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0009"
down_revision: str | None = "20260814_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNER_ROLE_NAMESPACE = "9e7bdf6f-572d-49d0-9aa1-ecb13dc57520"
MEMBER_ROLE_NAMESPACE = "3eb54a68-38d4-484c-888a-15b56db82f05"
OWNER_BINDING_NAMESPACE = "13cb98f4-88eb-49f0-ae9a-57b31e0dbe19"
MEMBER_BINDING_NAMESPACE = "760d6766-f06d-419e-a661-f7d8b86df2e9"


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    op.add_column(
        "workspaces",
        sa.Column("role_version", sa.Integer(), nullable=False, server_default="1"),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_workspaces_role_version",
        "workspaces",
        "role_version >= 1",
        schema=schema,
    )
    op.create_table(
        "roles",
        sa.Column("role_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("system_managed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("workspace_id", "role_id", name="uq_roles_workspace_role"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_roles_workspace",
        ),
        sa.CheckConstraint("role_key ~ '^[a-z][a-z0-9_]{2,63}$'", name="ck_roles_key"),
        sa.CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 120", name="ck_roles_name"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_roles_status"),
        sa.CheckConstraint("version >= 1", name="ck_roles_version"),
        schema=schema,
    )
    op.create_index(
        "uq_roles_workspace_key",
        "roles",
        ["workspace_id", "role_key"],
        unique=True,
        schema=schema,
    )
    op.create_index(
        "uq_roles_workspace_name",
        "roles",
        ["workspace_id", sa.text("lower(name)")],
        unique=True,
        schema=schema,
    )
    op.create_table(
        "role_bindings",
        sa.Column("binding_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "role_id"],
            [f"{schema}.roles.workspace_id", f"{schema}.roles.role_id"],
            name="fk_role_bindings_role",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "department_id"],
            [f"{schema}.departments.workspace_id", f"{schema}.departments.department_id"],
            name="fk_role_bindings_department",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "membership_id"],
            [
                f"{schema}.workspace_memberships.workspace_id",
                f"{schema}.workspace_memberships.membership_id",
            ],
            name="fk_role_bindings_membership",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "scope_type IN ('workspace', 'department', 'member')",
            name="ck_role_bindings_scope",
        ),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_role_bindings_status"),
        sa.CheckConstraint(
            "(scope_type = 'workspace' AND department_id IS NULL AND membership_id IS NULL) "
            "OR (scope_type = 'department' AND department_id IS NOT NULL "
            "AND membership_id IS NULL) "
            "OR (scope_type = 'member' AND department_id IS NULL "
            "AND membership_id IS NOT NULL)",
            name="ck_role_bindings_target",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND revoked_at IS NULL) "
            "OR (status = 'revoked' AND revoked_at IS NOT NULL)",
            name="ck_role_bindings_revoked_at",
        ),
        sa.CheckConstraint("version >= 1", name="ck_role_bindings_version"),
        schema=schema,
    )
    op.create_index(
        "uq_role_bindings_active_scope",
        "role_bindings",
        ["workspace_id", "role_id", "scope_type", "department_id", "membership_id"],
        unique=True,
        schema=schema,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_role_bindings_member",
        "role_bindings",
        ["workspace_id", "membership_id", "status"],
        schema=schema,
    )
    _seed_system_roles(schema)


def _seed_system_roles(schema: str) -> None:
    # UUID v5 由数据库内 md5 以固定命名空间等价生成，保证往返迁移结果可复现。
    role_id = _uuid_v5_sql("w.workspace_id", OWNER_ROLE_NAMESPACE, "owner")
    member_role_id = _uuid_v5_sql("w.workspace_id", MEMBER_ROLE_NAMESPACE, "member")
    owner_binding_id = _uuid_v5_sql("wm.membership_id", OWNER_BINDING_NAMESPACE, "owner")
    member_binding_id = _uuid_v5_sql("w.workspace_id", MEMBER_BINDING_NAMESPACE, "member")
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".roles (
                role_id, workspace_id, role_key, name, status, system_managed,
                created_at, updated_at, version
            )
            SELECT {role_id}, w.workspace_id, 'workspace_owner', '空间所有者',
                   'active', true, w.created_at, w.updated_at, 1
            FROM "{schema}".workspaces w
            UNION ALL
            SELECT {member_role_id}, w.workspace_id, 'workspace_member', '空间成员',
                   'active', true, w.created_at, w.updated_at, 1
            FROM "{schema}".workspaces w
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_bindings (
                binding_id, workspace_id, role_id, scope_type, department_id,
                membership_id, status, created_at, revoked_at, version
            )
            SELECT {owner_binding_id}, wm.workspace_id, {role_id}, 'member', NULL,
                   wm.membership_id, 'active', wm.created_at, NULL, 1
            FROM "{schema}".workspace_memberships wm
            JOIN "{schema}".workspaces w ON w.workspace_id = wm.workspace_id
            WHERE wm.membership_type = 'owner'
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_bindings (
                binding_id, workspace_id, role_id, scope_type, department_id,
                membership_id, status, created_at, revoked_at, version
            )
            SELECT {member_binding_id}, w.workspace_id, {member_role_id},
                   'workspace', NULL, NULL, 'active', w.created_at, NULL, 1
            FROM "{schema}".workspaces w
            """
        )
    )


def _uuid_v5_sql(value: str, namespace: str, suffix: str) -> str:
    # 使用 chr(58) 拼接分隔符，避免 SQLAlchemy 把字面量中的冒号误判为绑定参数。
    digest = f"md5('{namespace}:' || {value}::text || chr(58) || '{suffix}')"
    return (
        f"((substr({digest},1,8)||'-'||substr({digest},9,4)||'-5'||"
        f"substr({digest},14,3)||'-a'||substr({digest},18,3)||'-'||"
        f"substr({digest},21,12))::uuid)"
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("ix_role_bindings_member", table_name="role_bindings", schema=schema)
    op.drop_index("uq_role_bindings_active_scope", table_name="role_bindings", schema=schema)
    op.drop_table("role_bindings", schema=schema)
    op.drop_index("uq_roles_workspace_name", table_name="roles", schema=schema)
    op.drop_index("uq_roles_workspace_key", table_name="roles", schema=schema)
    op.drop_table("roles", schema=schema)
    op.drop_constraint(
        "ck_workspaces_role_version",
        "workspaces",
        type_="check",
        schema=schema,
    )
    op.drop_column("workspaces", "role_version", schema=schema)
