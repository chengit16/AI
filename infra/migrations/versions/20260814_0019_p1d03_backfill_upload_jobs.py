"""为 P1D-02 已存在的上传来源幂等补建入库任务。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config

revision: str = "20260814_0019"
down_revision: str | None = "20260814_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    # source_id 在来源表中全局唯一，可作为确定性任务 ID；摘要生成的 Trace 仅用于延续旧事实，
    # 不伪造原请求链路，也不把对象键写入消息或审计。
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".ingestion_jobs (
                ingestion_job_id, workspace_id, knowledge_base_id, document_id,
                document_version_id, source_id, source_name, source_object_key,
                source_media_type, source_content_hash, status, attempt_count,
                max_attempts, available_at, requested_by_actor_id, trace_id,
                traceparent, created_at, updated_at
            )
            SELECT
                source.source_id,
                source.workspace_id,
                document.knowledge_base_id,
                version.document_id,
                version.document_version_id,
                source.source_id,
                source.source_name,
                source.original_object_key,
                source.media_type,
                source.content_hash,
                'queued',
                0,
                3,
                CURRENT_TIMESTAMP,
                version.created_by_account_id,
                md5(source.source_id::text),
                '00-' || md5(source.source_id::text) || '-' ||
                    substring(md5(version.document_version_id::text), 1, 16) || '-01',
                source.created_at,
                CURRENT_TIMESTAMP
            FROM "{schema}".document_sources AS source
            JOIN "{schema}".document_versions AS version
              ON version.workspace_id = source.workspace_id
             AND version.document_version_id = source.document_version_id
            JOIN "{schema}".documents AS document
              ON document.workspace_id = version.workspace_id
             AND document.document_id = version.document_id
            WHERE source.source_kind = 'upload'
              AND source.scan_status = 'clean'
              AND source.original_object_key IS NOT NULL
              AND source.media_type IS NOT NULL
              AND source.content_hash IS NOT NULL
            ON CONFLICT (workspace_id, document_version_id) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # 回填任务可能已经形成解析产物或运行历史，降级结构时由 0018 统一移除任务表。
    return None
