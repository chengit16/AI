"""建立 P1C-03 字段密级与显式遮罩授权。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0012"
down_revision: str | None = "20260814_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    op.add_column(
        "role_permission_grants",
        sa.Column(
            "maximum_security_level",
            sa.String(length=32),
            nullable=False,
            server_default="RESTRICTED",
        ),
        schema=schema,
    )
    op.add_column(
        "role_permission_grants",
        sa.Column(
            "field_mask",
            postgresql.ARRAY(sa.String(length=160)),
            nullable=False,
            server_default="{}",
        ),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_role_permission_grants_security_level",
        "role_permission_grants",
        "maximum_security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
        schema=schema,
    )
    # 普通成员的历史默认授权限制到 INTERNAL；所有者保持 RESTRICTED 以兼容既有治理能力。
    op.execute(
        sa.text(
            f"""
            UPDATE "{schema}".role_permission_grants AS grants
            SET maximum_security_level = 'INTERNAL'
            FROM "{schema}".roles AS roles
            WHERE roles.workspace_id = grants.workspace_id
              AND roles.role_id = grants.role_id
              AND roles.role_key = 'workspace_member'
              AND roles.system_managed = true
            """
        )
    )
    op.alter_column(
        "role_permission_grants",
        "maximum_security_level",
        server_default=None,
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_constraint(
        "ck_role_permission_grants_security_level",
        "role_permission_grants",
        type_="check",
        schema=schema,
    )
    op.drop_column("role_permission_grants", "field_mask", schema=schema)
    op.drop_column("role_permission_grants", "maximum_security_level", schema=schema)
