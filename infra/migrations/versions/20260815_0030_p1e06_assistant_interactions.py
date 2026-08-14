"""建立 P1E-06 消息反馈事实、交互权限和菜单接口绑定。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260815_0030"
down_revision: str | None = "20260815_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ASSISTANT_INTERACTION_PERMISSIONS = (
    "assistant.page.access",
    "assistant.run.cancel",
    "assistant.source.read",
    "assistant.feedback.manage",
)
ASSISTANT_INTERACTION_BINDINGS = (
    (164, 75, "query"),
    (168, 76, "mutation"),
    (169, 77, "query"),
    (170, 78, "query"),
    (170, 79, "mutation"),
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _create_feedbacks(schema)
    _backfill_permissions(schema)
    _register_bindings(schema)


def downgrade() -> None:
    schema = _schema()
    api_ids = ", ".join(
        f"'81000000-0000-4000-8000-{api_number:012d}'::uuid"
        for _, api_number, _ in ASSISTANT_INTERACTION_BINDINGS
    )
    permissions = ", ".join(f"'{code}'" for code in ASSISTANT_INTERACTION_PERMISSIONS)
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE api_resource_id IN ({api_ids});
            DELETE FROM "{schema}".role_permission_grants
            WHERE permission_code IN ({permissions});
            """
        )
    )
    op.drop_index(
        "ix_message_feedbacks_workspace_time",
        table_name="message_feedbacks",
        schema=schema,
    )
    op.drop_table("message_feedbacks", schema=schema)


def _create_feedbacks(schema: str) -> None:
    op.create_table(
        "message_feedbacks",
        sa.Column("feedback_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rating", sa.String(32), nullable=False),
        sa.Column("issue_codes", postgresql.ARRAY(sa.String(32)), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "message_id",
            "account_id",
            name="uq_message_feedbacks_account_message",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            [f"{schema}.conversations.conversation_id", f"{schema}.conversations.workspace_id"],
            name="fk_message_feedbacks_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id", "workspace_id"],
            [f"{schema}.messages.message_id", f"{schema}.messages.workspace_id"],
            name="fk_message_feedbacks_message",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{schema}.assistant_runs.run_id"],
            name="fk_message_feedbacks_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_message_feedbacks_account",
        ),
        sa.CheckConstraint(
            "rating IN ('helpful', 'unhelpful')",
            name="ck_message_feedbacks_rating",
        ),
        sa.CheckConstraint(
            "issue_codes <@ ARRAY['incorrect', 'missing_source', 'source_mismatch', "
            "'unsafe', 'other']::varchar[]",
            name="ck_message_feedbacks_issue_codes",
        ),
        sa.CheckConstraint(
            "(rating = 'helpful' AND cardinality(issue_codes) = 0) OR "
            "(rating = 'unhelpful' AND cardinality(issue_codes) BETWEEN 1 AND 5)",
            name="ck_message_feedbacks_issue_shape",
        ),
        sa.CheckConstraint(
            "comment IS NULL OR char_length(btrim(comment)) BETWEEN 1 AND 1000",
            name="ck_message_feedbacks_comment",
        ),
        sa.CheckConstraint("version >= 1", name="ck_message_feedbacks_version"),
        schema=schema,
    )
    op.create_index(
        "ix_message_feedbacks_workspace_time",
        "message_feedbacks",
        ["workspace_id", "updated_at"],
        schema=schema,
    )


def _backfill_permissions(schema: str) -> None:
    permissions = ", ".join(f"('{code}')" for code in ASSISTANT_INTERACTION_PERMISSIONS)
    # 个人/企业系统角色都获得问答交互权限；会话创建者隔离仍由助手领域独立执行。
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
            WHERE roles.role_key IN ('workspace_owner', 'workspace_member')
              AND roles.system_managed = true
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
        for menu_number, api_number, action_type in ASSISTANT_INTERACTION_BINDINGS
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
