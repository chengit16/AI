"""建立 P3-03 Agent 配置资源版本、只读工具目录和安全策略目录。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260816_0043"
down_revision: str | None = "20260816_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _create_prompt_versions(schema)
    _create_knowledge_scope_versions(schema)
    _create_output_schema_versions(schema)
    _create_safety_policy_versions(schema)
    _create_tool_definitions(schema)
    _seed_fixed_catalogs(schema)
    _protect_configuration_versions(schema)


def downgrade() -> None:
    schema = _schema()
    _reject_unsafe_downgrade(schema)
    _drop_configuration_protection(schema)
    op.drop_table("agent_tool_definitions", schema=schema)
    op.drop_table("agent_safety_policy_versions", schema=schema)
    op.drop_table("agent_output_schema_versions", schema=schema)
    op.drop_table("agent_knowledge_scope_items", schema=schema)
    op.drop_table("agent_knowledge_scope_versions", schema=schema)
    op.drop_table("agent_prompt_versions", schema=schema)


def _create_prompt_versions(schema: str) -> None:
    op.create_table(
        "agent_prompt_versions",
        sa.Column("prompt_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "prompt_version_id",
            "workspace_id",
            name="uq_agent_prompt_versions_id_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_agent_prompt_versions_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_agent_prompt_versions_creator",
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120",
            name="ck_agent_prompt_versions_name",
        ),
        sa.CheckConstraint(
            "char_length(btrim(template)) BETWEEN 1 AND 32000",
            name="ck_agent_prompt_versions_template",
        ),
        sa.CheckConstraint(
            "prompt_hash ~ '^[0-9a-f]{64}$'",
            name="ck_agent_prompt_versions_hash",
        ),
        schema=schema,
    )


def _create_knowledge_scope_versions(schema: str) -> None:
    op.create_table(
        "agent_knowledge_scope_versions",
        sa.Column(
            "knowledge_scope_version_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("scope_hash", sa.String(64), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "knowledge_scope_version_id",
            "workspace_id",
            name="uq_agent_knowledge_scopes_id_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_agent_knowledge_scopes_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_agent_knowledge_scopes_creator",
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120",
            name="ck_agent_knowledge_scopes_name",
        ),
        sa.CheckConstraint(
            "scope_hash ~ '^[0-9a-f]{64}$'",
            name="ck_agent_knowledge_scopes_hash",
        ),
        schema=schema,
    )
    op.create_table(
        "agent_knowledge_scope_items",
        sa.Column(
            "knowledge_scope_version_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "knowledge_scope_version_id",
            "position",
            name="uq_agent_knowledge_scope_items_position",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_scope_version_id", "workspace_id"],
            [
                f"{schema}.agent_knowledge_scope_versions.knowledge_scope_version_id",
                f"{schema}.agent_knowledge_scope_versions.workspace_id",
            ],
            name="fk_agent_knowledge_scope_items_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id", "workspace_id"],
            [
                f"{schema}.knowledge_bases.knowledge_base_id",
                f"{schema}.knowledge_bases.workspace_id",
            ],
            name="fk_agent_knowledge_scope_items_base",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 50",
            name="ck_agent_knowledge_scope_items_position",
        ),
        schema=schema,
    )


def _create_output_schema_versions(schema: str) -> None:
    op.create_table(
        "agent_output_schema_versions",
        sa.Column(
            "output_schema_version_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("schema_document", postgresql.JSONB(), nullable=False),
        sa.Column("schema_hash", sa.String(64), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "output_schema_version_id",
            "workspace_id",
            name="uq_agent_output_schemas_id_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_agent_output_schemas_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_agent_output_schemas_creator",
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120",
            name="ck_agent_output_schemas_name",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(schema_document) = 'object'",
            name="ck_agent_output_schemas_document",
        ),
        sa.CheckConstraint(
            "schema_hash ~ '^[0-9a-f]{64}$'",
            name="ck_agent_output_schemas_hash",
        ),
        schema=schema,
    )


def _create_safety_policy_versions(schema: str) -> None:
    op.create_table(
        "agent_safety_policy_versions",
        sa.Column(
            "safety_policy_version_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("policy_key", sa.String(64), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("implementation_version", sa.String(64), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.UniqueConstraint(
            "policy_key",
            "version_number",
            name="uq_agent_safety_policies_key_version",
        ),
        sa.CheckConstraint(
            "version_number >= 1",
            name="ck_agent_safety_policies_version",
        ),
        sa.CheckConstraint(
            "policy_hash ~ '^[0-9a-f]{64}$'",
            name="ck_agent_safety_policies_hash",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'retired')",
            name="ck_agent_safety_policies_status",
        ),
        schema=schema,
    )


def _create_tool_definitions(schema: str) -> None:
    op.create_table(
        "agent_tool_definitions",
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tool_version", sa.Integer(), primary_key=True),
        sa.Column("tool_key", sa.String(120), nullable=False),
        sa.Column("access_mode", sa.String(16), nullable=False),
        sa.Column("permission_code", sa.String(160), nullable=False),
        sa.UniqueConstraint(
            "tool_key",
            "tool_version",
            name="uq_agent_tool_definitions_key_version",
        ),
        sa.CheckConstraint(
            "tool_version >= 1",
            name="ck_agent_tool_definitions_version",
        ),
        sa.CheckConstraint(
            "access_mode IN ('read', 'write')",
            name="ck_agent_tool_definitions_access_mode",
        ),
        sa.CheckConstraint(
            "permission_code ~ '^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*){2,}$'",
            name="ck_agent_tool_definitions_permission",
        ),
        schema=schema,
    )


def _seed_fixed_catalogs(schema: str) -> None:
    safety_table = sa.table(
        "agent_safety_policy_versions",
        sa.column("safety_policy_version_id", postgresql.UUID(as_uuid=True)),
        sa.column("policy_key", sa.String()),
        sa.column("version_number", sa.Integer()),
        sa.column("implementation_version", sa.String()),
        sa.column("policy_hash", sa.String()),
        sa.column("status", sa.String()),
        schema=schema,
    )
    op.bulk_insert(
        safety_table,
        [
            {
                "safety_policy_version_id": "a9000000-0000-4000-8000-000000000001",
                "policy_key": "rag-safety",
                "version_number": 1,
                "implementation_version": "rag-safety-v2",
                "policy_hash": ("4b7a460fa0a2ce10283b7f5affe3e04bd69c0a9b40238669a7c266bb63f83738"),
                "status": "active",
            }
        ],
    )
    tool_table = sa.table(
        "agent_tool_definitions",
        sa.column("tool_id", postgresql.UUID(as_uuid=True)),
        sa.column("tool_version", sa.Integer()),
        sa.column("tool_key", sa.String()),
        sa.column("access_mode", sa.String()),
        sa.column("permission_code", sa.String()),
        schema=schema,
    )
    op.bulk_insert(
        tool_table,
        [
            _tool("001", "knowledge.search", "knowledge.document.read"),
            _tool(
                "002",
                "document.read_authorized_range",
                "knowledge.document.read",
            ),
            _tool("003", "workflow.get_status", "workflow.run.read"),
            _tool("004", "approval.get_status", "approval.instance.read"),
            _tool("005", "quota.get_usage", "workspace.entitlement.read"),
        ],
    )


def _tool(suffix: str, key: str, permission_code: str) -> dict[str, object]:
    return {
        "tool_id": f"a7000000-0000-4000-8000-000000000{suffix}",
        "tool_version": 1,
        "tool_key": key,
        "access_mode": "read",
        "permission_code": permission_code,
    }


def _protect_configuration_versions(schema: str) -> None:
    # 工作空间资源只允许生命周期清除；全局安全与工具目录在任何事务中都不可修改。
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".reject_agent_configuration_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_TABLE_NAME NOT IN (
                    'agent_safety_policy_versions', 'agent_tool_definitions'
                ) AND TG_OP = 'DELETE'
                   AND current_setting('ai_platform.lifecycle_purge', true) = 'on'
                THEN RETURN OLD;
                END IF;
                RAISE EXCEPTION 'agent configuration version is immutable'
                    USING ERRCODE = '55000';
            END;
            $$
            """
        )
    )
    for table in _immutable_tables():
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_{table}_immutable
                BEFORE UPDATE OR DELETE ON "{schema}"."{table}"
                FOR EACH ROW EXECUTE FUNCTION "{schema}".reject_agent_configuration_mutation()
                """
            )
        )


def _drop_configuration_protection(schema: str) -> None:
    for table in reversed(_immutable_tables()):
        op.execute(sa.text(f'DROP TRIGGER IF EXISTS trg_{table}_immutable ON "{schema}"."{table}"'))
    op.execute(sa.text(f'DROP FUNCTION IF EXISTS "{schema}".reject_agent_configuration_mutation'))


def _immutable_tables() -> tuple[str, ...]:
    return (
        "agent_prompt_versions",
        "agent_knowledge_scope_versions",
        "agent_knowledge_scope_items",
        "agent_output_schema_versions",
        "agent_safety_policy_versions",
        "agent_tool_definitions",
    )


def _reject_unsafe_downgrade(schema: str) -> None:
    connection = op.get_bind()
    for table in (
        "agent_prompt_versions",
        "agent_knowledge_scope_versions",
        "agent_output_schema_versions",
    ):
        count = connection.execute(
            sa.text(f'SELECT count(*) FROM "{schema}"."{table}"')
        ).scalar_one()
        if int(count) > 0:
            raise RuntimeError("存在 Agent 配置资源版本, 拒绝降级并静默丢失数据")
