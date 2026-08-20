"""为最终证据快照补充实际数据密级。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config

revision: str = "20260820_0071"
down_revision: str | None = "20260818_0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """新证据记录精确密级；无法重新证明的历史证据按最高密级保守回填。"""

    schema = _schema()
    op.add_column(
        "retrieval_evidence_items",
        sa.Column(
            "security_level",
            sa.String(length=32),
            nullable=False,
            server_default="RESTRICTED",
        ),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_retrieval_evidence_items_security_level",
        "retrieval_evidence_items",
        "security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
        schema=schema,
    )
    op.alter_column(
        "retrieval_evidence_items",
        "security_level",
        server_default=None,
        schema=schema,
    )


def downgrade() -> None:
    """删除证据密级字段，不改写其他不可变证据事实。"""

    schema = _schema()
    op.drop_constraint(
        "ck_retrieval_evidence_items_security_level",
        "retrieval_evidence_items",
        type_="check",
        schema=schema,
    )
    op.drop_column("retrieval_evidence_items", "security_level", schema=schema)
