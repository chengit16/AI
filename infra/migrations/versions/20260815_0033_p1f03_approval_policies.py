"""建立 P1F-03 审批策略身份、不可变版本和当前版本指针。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260815_0033"
down_revision: str | None = "20260815_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNER_PERMISSIONS = (
    "approval.chain.preview",
    "approval.policy.create",
    "approval.policy.read",
    "approval.policy.update",
)
MEMBER_PERMISSIONS = ("approval.chain.preview",)
APPROVAL_BINDINGS = (
    (178, 88, "mutation"),
    (179, 89, "query"),
    (181, 90, "mutation"),
    (179, 91, "query"),
    (180, 92, "mutation"),
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """先建身份和版本，再补延迟指针外键以支持同事务首版本写入。"""

    schema = _schema()
    _create_policies(schema)
    _create_versions(schema)
    op.create_foreign_key(
        "fk_approval_policies_current_version",
        "approval_policies",
        "approval_policy_versions",
        ["approval_policy_id", "workspace_id", "current_version_id"],
        ["approval_policy_id", "workspace_id", "approval_policy_version_id"],
        source_schema=schema,
        referent_schema=schema,
        deferrable=True,
        initially="DEFERRED",
    )
    _protect_versions(schema)
    _backfill_permissions(schema)
    _register_bindings(schema)


def downgrade() -> None:
    """先断开循环指针，再按版本和身份顺序移除本节点数据表。"""

    schema = _schema()
    api_ids = ", ".join(
        f"'81000000-0000-4000-8000-{api_number:012d}'::uuid"
        for _, api_number, _ in APPROVAL_BINDINGS
    )
    permissions = ", ".join(
        f"'{code}'" for code in sorted(set(OWNER_PERMISSIONS) | set(MEMBER_PERMISSIONS))
    )
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE api_resource_id IN ({api_ids});
            DELETE FROM "{schema}".role_permission_grants
            WHERE permission_code IN ({permissions});
            DROP TRIGGER IF EXISTS protect_approval_policy_versions
            ON "{schema}".approval_policy_versions;
            DROP FUNCTION IF EXISTS "{schema}".reject_approval_policy_version_mutation();
            """
        )
    )
    op.drop_constraint(
        "fk_approval_policies_current_version",
        "approval_policies",
        schema=schema,
        type_="foreignkey",
    )
    op.drop_index(
        "ix_approval_policy_versions_policy_time",
        table_name="approval_policy_versions",
        schema=schema,
    )
    op.drop_table("approval_policy_versions", schema=schema)
    op.drop_index(
        "ix_approval_policies_workspace_time",
        table_name="approval_policies",
        schema=schema,
    )
    op.drop_index(
        "uq_approval_policies_workspace_name",
        table_name="approval_policies",
        schema=schema,
    )
    op.drop_table("approval_policies", schema=schema)


def _create_policies(schema: str) -> None:
    op.create_table(
        "approval_policies",
        sa.Column("approval_policy_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "approval_policy_id",
            "workspace_id",
            name="uq_approval_policies_id_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_approval_policies_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_approval_policies_creator",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_approval_policies_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_approval_policies_version"),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120",
            name="ck_approval_policies_name",
        ),
        schema=schema,
    )
    op.create_index(
        "uq_approval_policies_workspace_name",
        "approval_policies",
        ["workspace_id", sa.text("lower(name)")],
        unique=True,
        schema=schema,
    )
    op.create_index(
        "ix_approval_policies_workspace_time",
        "approval_policies",
        ["workspace_id", "updated_at"],
        schema=schema,
    )


def _create_versions(schema: str) -> None:
    op.create_table(
        "approval_policy_versions",
        sa.Column(
            "approval_policy_version_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("approval_policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("definition_digest", sa.String(64), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "approval_policy_id",
            "workspace_id",
            "approval_policy_version_id",
            name="uq_approval_policy_versions_identity",
        ),
        sa.UniqueConstraint(
            "approval_policy_id",
            "version_number",
            name="uq_approval_policy_versions_number",
        ),
        sa.ForeignKeyConstraint(
            ["approval_policy_id", "workspace_id"],
            [
                f"{schema}.approval_policies.approval_policy_id",
                f"{schema}.approval_policies.workspace_id",
            ],
            name="fk_approval_policy_versions_policy",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_approval_policy_versions_creator",
        ),
        sa.CheckConstraint(
            "version_number >= 1",
            name="ck_approval_policy_versions_number",
        ),
        sa.CheckConstraint(
            "definition_digest ~ '^[0-9a-f]{64}$'",
            name="ck_approval_policy_versions_digest",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_approval_policy_versions_policy_time",
        "approval_policy_versions",
        ["workspace_id", "approval_policy_id", "created_at"],
        schema=schema,
    )


def _protect_versions(schema: str) -> None:
    """用数据库触发器阻断历史定义更新或删除，策略修订只能新增版本。"""

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".reject_approval_policy_version_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'approval_policy_versions are immutable';
            END;
            $$;
            CREATE TRIGGER protect_approval_policy_versions
            BEFORE UPDATE OR DELETE ON "{schema}".approval_policy_versions
            FOR EACH ROW EXECUTE FUNCTION "{schema}".reject_approval_policy_version_mutation();
            """
        )
    )


def _backfill_permissions(schema: str) -> None:
    _seed_role_permissions(schema, "workspace_owner", OWNER_PERMISSIONS, "RESTRICTED")
    _seed_role_permissions(schema, "workspace_member", MEMBER_PERMISSIONS, "INTERNAL")


def _seed_role_permissions(
    schema: str,
    role_key: str,
    permission_codes: tuple[str, ...],
    security_level: str,
) -> None:
    permissions = ", ".join(f"('{code}')" for code in permission_codes)
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, permission_code,
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[],
                   '{security_level}', ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            CROSS JOIN (VALUES {permissions}) AS permission_codes(permission_code)
            WHERE roles.role_key = '{role_key}' AND roles.system_managed = true
            ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
            """
        )
    )


def _register_bindings(schema: str) -> None:
    values = ", ".join(
        "("
        + ", ".join(
            (
                f"'82000000-0000-4000-8000-{menu_number:012d}'::uuid",
                f"'81000000-0000-4000-8000-{api_number:012d}'::uuid",
                f"'{action_type}'",
            )
        )
        + ")"
        for menu_number, api_number, action_type in APPROVAL_BINDINGS
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".registered_menu_api_bindings (
                menu_id, api_resource_id, action_type
            ) VALUES {values}
            ON CONFLICT (menu_id, api_resource_id) DO UPDATE
            SET action_type = EXCLUDED.action_type
            """
        )
    )
