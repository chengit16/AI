"""注册 P1E-05 HTTP SSE 接口与现有会话只读菜单的数据库绑定。"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from alembic.config import Config

revision: str = "20260815_0029"
down_revision: str | None = "20260815_0028"
branch_labels: str | None = None
depends_on: str | None = None

MENU_ID = "82000000-0000-4000-8000-000000000164"
API_RESOURCE_ID = "81000000-0000-4000-8000-000000000074"


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    # 数据库只镜像菜单与 API 的绑定；API 完整事实仍由版本化资源注册表维护。
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".registered_menu_api_bindings (
              menu_id, api_resource_id, action_type
            ) VALUES (
              '{MENU_ID}'::uuid, '{API_RESOURCE_ID}'::uuid, 'query'
            )
            ON CONFLICT (menu_id, api_resource_id) DO UPDATE
            SET action_type = EXCLUDED.action_type
            """
        )
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE menu_id = '{MENU_ID}'::uuid
              AND api_resource_id = '{API_RESOURCE_ID}'::uuid
            """
        )
    )
