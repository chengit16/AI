"""回填 P1D-08 个人与企业所有者的知识管理读取权限。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config

revision: str = "20260814_0024"
down_revision: str | None = "20260814_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNER_KNOWLEDGE_PERMISSIONS = (
    "knowledge.base.read",
    "knowledge.document.read",
    "knowledge.ingestion.read",
    "knowledge.ingestion.retry",
    "knowledge.production.access",
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    # P1D-07 只覆盖迁移时存在的空间；新空间由领域种子使用同一集合授权。
    permissions = ", ".join(f"('{code}')" for code in OWNER_KNOWLEDGE_PERMISSIONS)
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
            WHERE roles.role_key = 'workspace_owner' AND roles.system_managed = true
            ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # `0023` 已注册并授予这些权限；降级只撤销 Revision，不删除仍兼容的授权事实。
    pass
