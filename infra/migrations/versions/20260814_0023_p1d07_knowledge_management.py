"""建立 P1D-07 知识管理读权限与人工重试事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0023"
down_revision: str | None = "20260814_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNER_PERMISSIONS = (
    "knowledge.production.access",
    "knowledge.base.read",
    "knowledge.document.read",
    "knowledge.ingestion.read",
    "knowledge.ingestion.retry",
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    op.add_column(
        "ingestion_jobs",
        sa.Column("manual_retry_count", sa.Integer(), nullable=False, server_default="0"),
        schema=schema,
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "last_retried_by_actor_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        schema=schema,
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("last_retried_at", sa.DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_ingestion_jobs_manual_retry",
        "ingestion_jobs",
        "manual_retry_count >= 0 AND "
        "((manual_retry_count = 0 AND last_retried_by_actor_id IS NULL "
        "AND last_retried_at IS NULL) OR "
        "(manual_retry_count > 0 AND last_retried_by_actor_id IS NOT NULL "
        "AND last_retried_at IS NOT NULL))",
        schema=schema,
    )

    permissions = ", ".join(f"('{code}')" for code in OWNER_PERMISSIONS)
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
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".registered_menu_api_bindings (
                menu_id, api_resource_id, action_type
            ) VALUES
              ('82000000-0000-4000-8000-000000000146'::uuid,
               '81000000-0000-4000-8000-000000000065'::uuid, 'query'),
              ('82000000-0000-4000-8000-000000000147'::uuid,
               '81000000-0000-4000-8000-000000000066'::uuid, 'query'),
              ('82000000-0000-4000-8000-000000000148'::uuid,
               '81000000-0000-4000-8000-000000000067'::uuid, 'query'),
              ('82000000-0000-4000-8000-000000000149'::uuid,
               '81000000-0000-4000-8000-000000000068'::uuid, 'mutation')
            """
        )
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE menu_id BETWEEN
                  '82000000-0000-4000-8000-000000000146'::uuid AND
                  '82000000-0000-4000-8000-000000000149'::uuid
            """
        )
    )
    permissions = ", ".join(f"'{code}'" for code in OWNER_PERMISSIONS)
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".role_permission_grants AS grants
            USING "{schema}".roles AS roles
            WHERE grants.workspace_id = roles.workspace_id
              AND grants.role_id = roles.role_id
              AND roles.role_key = 'workspace_owner'
              AND roles.system_managed = true
              AND grants.permission_code IN ({permissions})
            """
        )
    )
    op.drop_constraint(
        "ck_ingestion_jobs_manual_retry",
        "ingestion_jobs",
        type_="check",
        schema=schema,
    )
    op.drop_column("ingestion_jobs", "last_retried_at", schema=schema)
    op.drop_column("ingestion_jobs", "last_retried_by_actor_id", schema=schema)
    op.drop_column("ingestion_jobs", "manual_retry_count", schema=schema)
