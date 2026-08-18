"""为模型供应商增加 Chat Completions 与 Responses 协议事实。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config

revision: str = "20260818_0070"
down_revision: str | None = "20260817_0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """旧供应商保持 Chat Completions，新配置可显式选择 Responses。"""

    schema = _schema()
    op.add_column(
        "model_provider_configurations",
        sa.Column(
            "wire_api",
            sa.String(length=32),
            nullable=False,
            server_default="chat_completions",
        ),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_model_providers_wire_api",
        "model_provider_configurations",
        "wire_api IN ('chat_completions', 'responses')",
        schema=schema,
    )
    op.alter_column(
        "model_provider_configurations",
        "wire_api",
        server_default=None,
        schema=schema,
    )


def downgrade() -> None:
    """仅删除协议区分字段，既有供应商的其他治理事实保持不变。"""

    schema = _schema()
    op.drop_constraint(
        "ck_model_providers_wire_api",
        "model_provider_configurations",
        type_="check",
        schema=schema,
    )
    op.drop_column("model_provider_configurations", "wire_api", schema=schema)
