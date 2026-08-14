"""建立 P1E-01 会话、消息、系统助手发布快照和排队运行事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260815_0025"
down_revision: str | None = "20260814_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ASSISTANT_PERMISSIONS = (
    "assistant.conversation.create",
    "assistant.conversation.read",
    "assistant.conversation.archive",
    "assistant.message.create",
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _create_agents(schema)
    _create_conversations(schema)
    _create_messages(schema)
    _create_assistant_runs(schema)
    _protect_immutable_facts(schema)
    _backfill_assistant_permissions(schema)


def downgrade() -> None:
    schema = _schema()
    _drop_assistant_permissions(schema)
    _drop_immutable_protection(schema)
    op.drop_index(
        "uq_assistant_runs_active_conversation",
        table_name="assistant_runs",
        schema=schema,
    )
    op.drop_index(
        "ix_assistant_runs_workspace_time",
        table_name="assistant_runs",
        schema=schema,
    )
    op.drop_table("assistant_runs", schema=schema)
    op.drop_table("message_parts", schema=schema)
    op.drop_index(
        "ix_messages_workspace_conversation_time",
        table_name="messages",
        schema=schema,
    )
    op.drop_table("messages", schema=schema)
    op.drop_index(
        "ix_conversations_workspace_creator_time",
        table_name="conversations",
        schema=schema,
    )
    op.drop_table("conversations", schema=schema)
    op.drop_table("agent_publications", schema=schema)
    op.drop_index(
        "ix_agent_releases_runtime_config",
        table_name="agent_releases",
        schema=schema,
    )
    op.drop_table("agent_releases", schema=schema)
    op.drop_table("agents", schema=schema)


def _create_agents(schema: str) -> None:
    op.create_table(
        "agents",
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("workspace_id", "agent_key", name="uq_agents_workspace_key"),
        sa.UniqueConstraint("agent_id", "workspace_id", name="uq_agents_id_workspace"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_agents_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_agents_creator",
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_agents_status"),
        sa.CheckConstraint("version >= 1", name="ck_agents_version"),
        schema=schema,
    )
    op.create_table(
        "agent_releases",
        sa.Column("release_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("runtime_config_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("released_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_releases_version"),
        sa.UniqueConstraint(
            "release_id",
            "workspace_id",
            name="uq_agent_releases_id_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id", "workspace_id"],
            [f"{schema}.agents.agent_id", f"{schema}.agents.workspace_id"],
            name="fk_agent_releases_agent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_config_version_id"],
            [f"{schema}.ai_runtime_config_versions.runtime_config_version_id"],
            name="fk_agent_releases_runtime_config",
        ),
        sa.ForeignKeyConstraint(
            ["released_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_agent_releases_releaser",
        ),
        sa.CheckConstraint("version >= 1", name="ck_agent_releases_version"),
        sa.CheckConstraint("status = 'released'", name="ck_agent_releases_status"),
        sa.CheckConstraint(
            "config_hash ~ '^[0-9a-f]{64}$'",
            name="ck_agent_releases_config_hash",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_agent_releases_runtime_config",
        "agent_releases",
        ["runtime_config_version_id", "released_at"],
        schema=schema,
    )
    _create_agent_publications(schema)


def _create_agent_publications(schema: str) -> None:
    op.create_table(
        "agent_publications",
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("published_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id", "workspace_id"],
            [f"{schema}.agents.agent_id", f"{schema}.agents.workspace_id"],
            name="fk_agent_publications_agent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["release_id", "workspace_id"],
            [
                f"{schema}.agent_releases.release_id",
                f"{schema}.agent_releases.workspace_id",
            ],
            name="fk_agent_publications_release",
        ),
        sa.ForeignKeyConstraint(
            ["published_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_agent_publications_publisher",
        ),
        sa.CheckConstraint("generation >= 1", name="ck_agent_publications_generation"),
        schema=schema,
    )


def _create_conversations(schema: str) -> None:
    op.create_table(
        "conversations",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "conversation_id",
            "workspace_id",
            name="uq_conversations_id_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_conversations_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_conversations_creator",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_conversations_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_conversations_version"),
        sa.CheckConstraint(
            "title IS NULL OR char_length(btrim(title)) BETWEEN 1 AND 200",
            name="ck_conversations_title",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_conversations_workspace_creator_time",
        "conversations",
        ["workspace_id", "created_by_account_id", "updated_at"],
        schema=schema,
    )


def _create_messages(schema: str) -> None:
    op.create_table(
        "messages",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("message_id", "workspace_id", name="uq_messages_id_workspace"),
        sa.ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            [f"{schema}.conversations.conversation_id", f"{schema}.conversations.workspace_id"],
            name="fk_messages_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_messages_creator",
        ),
        sa.CheckConstraint(
            "role IN ('system', 'user', 'assistant', 'tool')",
            name="ck_messages_role",
        ),
        sa.CheckConstraint(
            "status IN ('streaming', 'completed', 'failed')",
            name="ck_messages_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_messages_version"),
        schema=schema,
    )
    op.create_index(
        "ix_messages_workspace_conversation_time",
        "messages",
        ["workspace_id", "conversation_id", "created_at"],
        schema=schema,
    )
    _create_message_parts(schema)


def _create_message_parts(schema: str) -> None:
    op.create_table(
        "message_parts",
        sa.Column("part_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("part_type", sa.String(32), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("object_ref", sa.String(2048), nullable=True),
        sa.Column("media_type", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("message_id", "sequence_no", name="uq_message_parts_sequence"),
        sa.ForeignKeyConstraint(
            ["message_id", "workspace_id"],
            [f"{schema}.messages.message_id", f"{schema}.messages.workspace_id"],
            name="fk_message_parts_message",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("sequence_no >= 1", name="ck_message_parts_sequence"),
        sa.CheckConstraint(
            "part_type IN ('text', 'image_ref')",
            name="ck_message_parts_type",
        ),
        sa.CheckConstraint(
            "(part_type = 'text' AND text_content IS NOT NULL AND char_length(text_content) >= 1 "
            "AND object_ref IS NULL AND media_type IS NULL) OR "
            "(part_type = 'image_ref' AND text_content IS NULL AND object_ref IS NOT NULL "
            "AND media_type LIKE 'image/%')",
            name="ck_message_parts_content",
        ),
        schema=schema,
    )


def _create_assistant_runs(schema: str) -> None:
    op.create_table(
        "assistant_runs",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assistant_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("runtime_config_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("traceparent", sa.String(55), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.UniqueConstraint(
            "workspace_id",
            "requested_by_account_id",
            "idempotency_key",
            name="uq_assistant_runs_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            [f"{schema}.conversations.conversation_id", f"{schema}.conversations.workspace_id"],
            name="fk_assistant_runs_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_message_id", "workspace_id"],
            [f"{schema}.messages.message_id", f"{schema}.messages.workspace_id"],
            name="fk_assistant_runs_user_message",
        ),
        sa.ForeignKeyConstraint(
            ["assistant_message_id", "workspace_id"],
            [f"{schema}.messages.message_id", f"{schema}.messages.workspace_id"],
            name="fk_assistant_runs_assistant_message",
        ),
        sa.ForeignKeyConstraint(
            ["agent_release_id", "workspace_id"],
            [
                f"{schema}.agent_releases.release_id",
                f"{schema}.agent_releases.workspace_id",
            ],
            name="fk_assistant_runs_agent_release",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_config_version_id"],
            [f"{schema}.ai_runtime_config_versions.runtime_config_version_id"],
            name="fk_assistant_runs_runtime_config",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_assistant_runs_requester",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_assistant_runs_status",
        ),
        sa.CheckConstraint(
            "(status IN ('queued', 'running') AND completed_at IS NULL) OR "
            "(status IN ('completed', 'failed', 'cancelled') AND completed_at IS NOT NULL)",
            name="ck_assistant_runs_completion",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_assistant_runs_request_hash",
        ),
        sa.CheckConstraint(
            "trace_id ~ '^[0-9a-f]{32}$'",
            name="ck_assistant_runs_trace_id",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_assistant_runs_workspace_time",
        "assistant_runs",
        ["workspace_id", "created_at"],
        schema=schema,
    )
    op.create_index(
        "uq_assistant_runs_active_conversation",
        "assistant_runs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
        schema=schema,
    )


def _protect_immutable_facts(schema: str) -> None:
    # Release 和 Part 是历史证据；后续状态变化只能新建版本或修改消息头，不能覆写内容。
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".prevent_assistant_snapshot_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              RAISE EXCEPTION 'assistant snapshots are immutable' USING ERRCODE = '55000';
            END;
            $$
            """
        )
    )
    for table in ("agent_releases", "message_parts"):
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_{table}_immutable
                BEFORE UPDATE OR DELETE ON "{schema}"."{table}"
                FOR EACH ROW EXECUTE FUNCTION "{schema}".prevent_assistant_snapshot_mutation()
                """
            )
        )


def _drop_immutable_protection(schema: str) -> None:
    for table in ("message_parts", "agent_releases"):
        op.execute(sa.text(f'DROP TRIGGER trg_{table}_immutable ON "{schema}"."{table}"'))
    op.execute(sa.text(f'DROP FUNCTION "{schema}".prevent_assistant_snapshot_mutation()'))


def _backfill_assistant_permissions(schema: str) -> None:
    permissions = ", ".join(f"('{code}')" for code in ASSISTANT_PERMISSIONS)
    # 两类系统角色都可使用私有会话；服务端创建者校验仍独立于工作空间级 RBAC。
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, permission_code,
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[],
                   CASE WHEN roles.role_key = 'workspace_owner'
                        THEN 'RESTRICTED' ELSE 'INTERNAL' END,
                   ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            CROSS JOIN (VALUES {permissions}) AS permission_codes(permission_code)
            WHERE roles.role_key IN ('workspace_owner', 'workspace_member')
              AND roles.system_managed = true
            ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
            """
        )
    )


def _drop_assistant_permissions(schema: str) -> None:
    values = ", ".join(f"'{code}'" for code in ASSISTANT_PERMISSIONS)
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".role_permission_grants
            WHERE permission_code IN ({values})
              AND role_id IN (
                SELECT role_id FROM "{schema}".roles
                WHERE role_key IN ('workspace_owner', 'workspace_member')
                  AND system_managed = true
              )
            """
        )
    )
