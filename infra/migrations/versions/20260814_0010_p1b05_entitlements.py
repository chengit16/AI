"""建立 P1B-05 工作空间权益、功能开关和幂等用量事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0010"
down_revision: str | None = "20260814_0009"
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
        "workspace_entitlements",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("plan_code", sa.String(length=64), nullable=False),
        sa.Column("max_storage_bytes", sa.BigInteger(), nullable=False),
        sa.Column("max_members", sa.Integer(), nullable=False),
        sa.Column("max_knowledge_bases", sa.Integer(), nullable=False),
        sa.Column("max_published_agents", sa.Integer(), nullable=False),
        sa.Column("max_monthly_questions", sa.Integer(), nullable=False),
        sa.Column("open_api_allowed", sa.Boolean(), nullable=False),
        sa.Column("public_publish_allowed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_workspace_entitlements_workspace",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "plan_code ~ '^[a-z][a-z0-9_]{2,63}$'",
            name="ck_entitlements_plan_code",
        ),
        sa.CheckConstraint("max_storage_bytes >= 0", name="ck_entitlements_storage"),
        sa.CheckConstraint("max_members >= 1", name="ck_entitlements_members"),
        sa.CheckConstraint(
            "max_knowledge_bases >= 0",
            name="ck_entitlements_knowledge_bases",
        ),
        sa.CheckConstraint("max_published_agents >= 0", name="ck_entitlements_agents"),
        sa.CheckConstraint("max_monthly_questions >= 0", name="ck_entitlements_questions"),
        sa.CheckConstraint("version >= 1", name="ck_entitlements_version"),
        schema=schema,
    )
    op.create_table(
        "workspace_feature_settings",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("open_api_enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_workspace_feature_settings_workspace",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("version >= 1", name="ck_workspace_feature_settings_version"),
        schema=schema,
    )
    op.create_table(
        "workspace_usage_counters",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("metric", sa.String(length=64), primary_key=True),
        sa.Column("period_key", sa.String(length=16), primary_key=True),
        sa.Column("used_value", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_workspace_usage_counters_workspace",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "metric IN ('storage_bytes', 'knowledge_bases', 'published_agents', "
            "'questions_monthly')",
            name="ck_workspace_usage_counters_metric",
        ),
        sa.CheckConstraint("used_value >= 0", name="ck_workspace_usage_counters_value"),
        sa.CheckConstraint("version >= 1", name="ck_workspace_usage_counters_version"),
        schema=schema,
    )
    op.create_table(
        "workspace_usage_records",
        sa.Column(
            "usage_record_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("period_key", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("delta_value", sa.BigInteger(), nullable=False),
        sa.Column("resulting_value", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_workspace_usage_records_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_workspace_usage_records_workspace",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "metric IN ('storage_bytes', 'knowledge_bases', 'published_agents', "
            "'questions_monthly')",
            name="ck_workspace_usage_records_metric",
        ),
        sa.CheckConstraint("delta_value <> 0", name="ck_workspace_usage_records_delta"),
        sa.CheckConstraint("resulting_value >= 0", name="ck_workspace_usage_records_result"),
        schema=schema,
    )
    op.create_index(
        "ix_workspace_usage_records_period",
        "workspace_usage_records",
        ["workspace_id", "metric", "period_key", "occurred_at"],
        schema=schema,
    )
    _seed_defaults(schema)


def _seed_defaults(schema: str) -> None:
    # 既有个人和企业空间使用与应用写入相同的本地验证套餐，避免升级后出现无权益空间。
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".workspace_entitlements (
                workspace_id, plan_code, max_storage_bytes, max_members,
                max_knowledge_bases, max_published_agents, max_monthly_questions,
                open_api_allowed, public_publish_allowed, created_at, updated_at, version
            )
            SELECT workspace_id,
                   CASE workspace_type WHEN 'personal' THEN 'personal_local'
                                       ELSE 'enterprise_simulated' END,
                   CASE workspace_type WHEN 'personal' THEN 5368709120
                                       ELSE 107374182400 END,
                   CASE workspace_type WHEN 'personal' THEN 1 ELSE 100 END,
                   CASE workspace_type WHEN 'personal' THEN 5 ELSE 50 END,
                   CASE workspace_type WHEN 'personal' THEN 3 ELSE 20 END,
                   CASE workspace_type WHEN 'personal' THEN 2000 ELSE 20000 END,
                   workspace_type = 'enterprise', false, created_at, updated_at, 1
            FROM "{schema}".workspaces
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".workspace_feature_settings (
                workspace_id, open_api_enabled, updated_at, version
            )
            SELECT workspace_id, false, updated_at, 1
            FROM "{schema}".workspaces
            """
        )
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "ix_workspace_usage_records_period",
        table_name="workspace_usage_records",
        schema=schema,
    )
    op.drop_table("workspace_usage_records", schema=schema)
    op.drop_table("workspace_usage_counters", schema=schema)
    op.drop_table("workspace_feature_settings", schema=schema)
    op.drop_table("workspace_entitlements", schema=schema)
