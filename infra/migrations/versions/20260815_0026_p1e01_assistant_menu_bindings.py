"""幂等补齐 P1E-01 助手菜单与会话接口的数据库镜像。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config

revision: str = "20260815_0026"
down_revision: str | None = "20260815_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ASSISTANT_MENU_API_BINDINGS = (
    (163, 69, "mutation"),
    (164, 70, "query"),
    (164, 71, "query"),
    (166, 72, "mutation"),
    (165, 73, "mutation"),
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
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
        for menu_number, api_number, action_type in ASSISTANT_MENU_API_BINDINGS
    )
    # 独立 Revision 可安全推进已经应用 0025 的本地库，并保持新建 Schema 结果一致。
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


def downgrade() -> None:
    schema = _schema()
    api_ids = ", ".join(
        f"'81000000-0000-4000-8000-{api_number:012d}'::uuid"
        for _, api_number, _ in ASSISTANT_MENU_API_BINDINGS
    )
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE api_resource_id IN ({api_ids})
            """
        )
    )
