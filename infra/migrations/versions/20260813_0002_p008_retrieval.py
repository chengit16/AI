"""建立 P0-08 权限约束的混合检索索引。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0002"
down_revision: str | None = "20260813_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    # pgvector Extension 属于数据库级对象；重复升级不同测试 Schema 时必须保持幂等。
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "retrieval_chunks",
        sa.Column("index_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", VECTOR(1024), nullable=False),
        sa.Column("keyword_text", sa.Text(), nullable=False),
        sa.Column(
            "keyword_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', keyword_text)", persisted=True),
        ),
        sa.Column(
            "department_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("security_level", sa.String(length=32), nullable=False),
        sa.Column("source_position", postgresql.JSONB(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.CheckConstraint("sequence_no >= 1", name="ck_retrieval_chunks_sequence_no"),
        sa.CheckConstraint(
            "visibility IN ('private', 'workspace', 'departments', 'public')",
            name="ck_retrieval_chunks_visibility",
        ),
        sa.CheckConstraint(
            "security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
            name="ck_retrieval_chunks_security_level",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_retrieval_chunks_scope",
        "retrieval_chunks",
        ["workspace_id", "index_version_id", "knowledge_base_id", "document_id"],
        schema=schema,
    )
    op.create_index(
        "ix_retrieval_chunks_document_sequence",
        "retrieval_chunks",
        ["workspace_id", "document_version_id", "sequence_no"],
        schema=schema,
    )
    op.create_index(
        "ix_retrieval_chunks_keyword",
        "retrieval_chunks",
        ["keyword_vector"],
        schema=schema,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_retrieval_chunks_embedding_hnsw",
        "retrieval_chunks",
        ["embedding"],
        schema=schema,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "ix_retrieval_chunks_embedding_hnsw", table_name="retrieval_chunks", schema=schema
    )
    op.drop_index("ix_retrieval_chunks_keyword", table_name="retrieval_chunks", schema=schema)
    op.drop_index(
        "ix_retrieval_chunks_document_sequence",
        table_name="retrieval_chunks",
        schema=schema,
    )
    op.drop_index("ix_retrieval_chunks_scope", table_name="retrieval_chunks", schema=schema)
    op.drop_table("retrieval_chunks", schema=schema)
