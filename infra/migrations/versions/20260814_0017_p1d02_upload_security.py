"""建立 P1D-02 上传安全元数据与接口绑定。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config

revision: str = "20260814_0017"
down_revision: str | None = "20260814_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    for column in (
        sa.Column("media_type", sa.String(255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("scan_status", sa.String(32), nullable=True),
        sa.Column("scanner_version", sa.String(255), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("document_sources", column, schema=schema)
    op.create_check_constraint(
        "ck_document_sources_upload_security",
        "document_sources",
        "(media_type IS NULL AND size_bytes IS NULL AND content_hash IS NULL "
        "AND scan_status IS NULL AND scanner_version IS NULL AND scanned_at IS NULL) OR "
        "(source_kind = 'upload' AND char_length(btrim(media_type)) > 0 "
        "AND size_bytes > 0 AND content_hash ~ '^[0-9a-f]{64}$' "
        "AND scan_status = 'clean' AND char_length(btrim(scanner_version)) > 0 "
        "AND scanned_at IS NOT NULL)",
        schema=schema,
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".registered_menu_api_bindings (
                menu_id, api_resource_id, action_type
            ) VALUES
                ('82000000-0000-4000-8000-000000000140'::uuid,
                 '81000000-0000-4000-8000-000000000052'::uuid, 'mutation'),
                ('82000000-0000-4000-8000-000000000142'::uuid,
                 '81000000-0000-4000-8000-000000000053'::uuid, 'mutation')
            """
        )
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE api_resource_id IN (
                '81000000-0000-4000-8000-000000000052'::uuid,
                '81000000-0000-4000-8000-000000000053'::uuid
            )
            """
        )
    )
    op.drop_constraint(
        "ck_document_sources_upload_security",
        "document_sources",
        type_="check",
        schema=schema,
    )
    for column in (
        "scanned_at",
        "scanner_version",
        "scan_status",
        "content_hash",
        "size_bytes",
        "media_type",
    ):
        op.drop_column("document_sources", column, schema=schema)
