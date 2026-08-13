"""建立 P1C-02 角色权限和数据范围事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0011"
down_revision: str | None = "20260814_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNER_PERMISSION_CODES = (
    "authorization.binding.create",
    "authorization.binding.revoke",
    "authorization.effective_role.read",
    "authorization.role.create",
    "authorization.role.read",
    "authorization.role.status",
    "authorization.role_permission.manage",
    "authorization.role_permission.read",
    "enterprise.invitation.accept",
    "enterprise.workspace.create",
    "organization.assignment.read",
    "organization.assignment.write",
    "organization.department.create",
    "organization.department.move",
    "organization.department.read",
    "organization.department.status",
    "organization.position.create",
    "organization.position.read",
    "organization.position.status",
    "organization.structure.access",
    "system.runtime.access",
    "workspace.context.switch",
    "workspace.entitlement.read",
    "workspace.member.disable",
    "workspace.member.invite",
    "workspace.member.read",
    "workspace.members.access",
    "workspace.membership.leave",
    "workspace.open_api.manage",
    "workspace.overview.access",
)
MEMBER_PERMISSION_CODES = (
    "workspace.context.switch",
    "workspace.entitlement.read",
    "workspace.membership.leave",
    "workspace.overview.access",
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "role_permission_grants",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("permission_code", sa.String(length=160), primary_key=True),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column(
            "department_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "resource_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "role_id"],
            [f"{schema}.roles.workspace_id", f"{schema}.roles.role_id"],
            name="fk_role_permission_grants_role",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "permission_code ~ '^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*){2,}$'",
            name="ck_role_permission_grants_code",
        ),
        sa.CheckConstraint(
            "scope_type IN ('workspace', 'department_tree', 'self', 'resource')",
            name="ck_role_permission_grants_scope",
        ),
        sa.CheckConstraint(
            "(scope_type IN ('workspace', 'self') AND cardinality(department_ids) = 0 "
            "AND cardinality(resource_ids) = 0) OR "
            "(scope_type = 'department_tree' AND cardinality(department_ids) > 0 "
            "AND cardinality(resource_ids) = 0) OR "
            "(scope_type = 'resource' AND cardinality(resource_ids) > 0 "
            "AND cardinality(department_ids) = 0)",
            name="ck_role_permission_grants_targets",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_role_permission_grants_lookup",
        "role_permission_grants",
        ["workspace_id", "permission_code", "role_id"],
        schema=schema,
    )
    _seed_system_role_permissions(schema)


def _seed_system_role_permissions(schema: str) -> None:
    # 旧空间与新空间使用同一系统角色授权集合，升级后不会出现权限真空期。
    _seed_role(schema, "workspace_owner", OWNER_PERMISSION_CODES)
    _seed_role(schema, "workspace_member", MEMBER_PERMISSION_CODES)


def _seed_role(schema: str, role_key: str, permission_codes: tuple[str, ...]) -> None:
    values = ", ".join(f"('{code}')" for code in permission_codes)
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids
            )
            SELECT r.workspace_id, r.role_id, permissions.permission_code,
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[]
            FROM "{schema}".roles r
            CROSS JOIN (VALUES {values}) AS permissions(permission_code)
            WHERE r.role_key = '{role_key}' AND r.system_managed = true
            """
        )
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "ix_role_permission_grants_lookup",
        table_name="role_permission_grants",
        schema=schema,
    )
    op.drop_table("role_permission_grants", schema=schema)
