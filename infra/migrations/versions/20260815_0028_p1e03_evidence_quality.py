"""建立 P1E-03 来源排序、受控精读和引用证据不可变快照。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260815_0028"
down_revision: str | None = "20260815_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    # 1. 证据集冻结当前策略、组件版本、预算、消耗和确定性降级结论。
    op.create_table(
        "retrieval_evidence_sets",
        sa.Column("evidence_set_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("retrieval_plan_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("degradation_reason", sa.String(64), nullable=True),
        sa.Column("policy_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("reranker_model_version", sa.String(255), nullable=False),
        sa.Column("source_ranking_version", sa.String(128), nullable=False),
        sa.Column("fastpass_used", sa.Boolean(), nullable=False),
        sa.Column("reranker_used", sa.Boolean(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("rejected_candidate_count", sa.Integer(), nullable=False),
        sa.Column("conflict_count", sa.Integer(), nullable=False),
        sa.Column("read_document_count", sa.Integer(), nullable=False),
        sa.Column("read_chunk_count", sa.Integer(), nullable=False),
        sa.Column("read_character_count", sa.Integer(), nullable=False),
        sa.Column("estimated_token_count", sa.Integer(), nullable=False),
        sa.Column("rerank_candidate_limit", sa.Integer(), nullable=False),
        sa.Column("final_evidence_limit", sa.Integer(), nullable=False),
        sa.Column("max_documents", sa.Integer(), nullable=False),
        sa.Column("surrounding_chunks", sa.Integer(), nullable=False),
        sa.Column("max_chunks", sa.Integer(), nullable=False),
        sa.Column("max_characters", sa.Integer(), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("max_elapsed_ms", sa.Integer(), nullable=False),
        sa.Column("fastpass_score_ratio", sa.Float(), nullable=False),
        sa.Column("minimum_final_score", sa.Float(), nullable=False),
        sa.Column("max_quote_characters", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["retrieval_plan_id"],
            [f"{schema}.retrieval_plans.retrieval_plan_id"],
            name="fk_retrieval_evidence_sets_plan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{schema}.assistant_runs.run_id"],
            name="fk_retrieval_evidence_sets_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_retrieval_evidence_sets_requester",
        ),
        sa.CheckConstraint(
            "(status = 'sufficient' AND degradation_reason IS NULL) OR "
            "(status = 'uncertain' AND degradation_reason IN "
            "('no_candidates', 'no_current_evidence', 'insufficient_sources', "
            "'conflicting_evidence'))",
            name="ck_retrieval_evidence_sets_status",
        ),
        sa.CheckConstraint("policy_version >= 1", name="ck_retrieval_evidence_sets_policy"),
        sa.CheckConstraint(
            "candidate_count >= 0 AND rejected_candidate_count >= 0 "
            "AND rejected_candidate_count <= candidate_count AND conflict_count >= 0 "
            "AND read_document_count >= 0 AND read_chunk_count >= 0 "
            "AND read_character_count >= 0 AND estimated_token_count >= 0 "
            "AND duration_ms >= 0",
            name="ck_retrieval_evidence_sets_metrics",
        ),
        sa.CheckConstraint(
            "rerank_candidate_limit >= 1 AND final_evidence_limit >= 1 "
            "AND max_documents >= 1 AND surrounding_chunks >= 0 AND max_chunks >= 1 "
            "AND max_characters >= 1 AND max_tokens >= 1 AND max_elapsed_ms >= 1 "
            "AND fastpass_score_ratio >= 1 AND minimum_final_score BETWEEN 0 AND 1 "
            "AND max_quote_characters >= 1",
            name="ck_retrieval_evidence_sets_budget",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_retrieval_evidence_sets_workspace_time",
        "retrieval_evidence_sets",
        ["workspace_id", "created_at"],
        schema=schema,
    )
    # 2. 只有精读和引用复核通过的有限正文进入证据项，候选表仍保持不含正文。
    op.create_table(
        "retrieval_evidence_items",
        sa.Column("evidence_set_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rank", sa.Integer(), primary_key=True),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("index_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("context_text", sa.Text(), nullable=False),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column(
            "context_chunk_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False
        ),
        sa.Column("source_position", postgresql.JSONB(), nullable=False),
        sa.Column("document_title", sa.String(255), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("retrieval_score", sa.Float(), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("authority_score", sa.Float(), nullable=False),
        sa.Column("freshness_score", sa.Float(), nullable=False),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.Column("conflict_detected", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_set_id"],
            [f"{schema}.retrieval_evidence_sets.evidence_set_id"],
            name="fk_retrieval_evidence_items_set",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("rank >= 1", name="ck_retrieval_evidence_items_rank"),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$' AND context_hash ~ '^[0-9a-f]{64}$'",
            name="ck_retrieval_evidence_items_hashes",
        ),
        sa.CheckConstraint(
            "char_length(btrim(quote)) BETWEEN 1 AND 1000 "
            "AND char_length(context_text) BETWEEN 1 AND 12000 "
            "AND cardinality(context_chunk_ids) BETWEEN 1 AND 12",
            name="ck_retrieval_evidence_items_content",
        ),
        sa.CheckConstraint(
            "source_kind IN ('manual', 'upload', 'web', 'data_source')",
            name="ck_retrieval_evidence_items_source_kind",
        ),
        sa.CheckConstraint(
            "retrieval_score >= 0 AND relevance_score BETWEEN 0 AND 1 "
            "AND authority_score BETWEEN 0 AND 1 AND freshness_score BETWEEN 0 AND 1 "
            "AND final_score BETWEEN 0 AND 1",
            name="ck_retrieval_evidence_items_scores",
        ),
        schema=schema,
    )
    # 3. 复用上一 Revision 的不可变函数，保证重试只能读取首次证据快照。
    for table in ("retrieval_evidence_sets", "retrieval_evidence_items"):
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_{table}_immutable
                BEFORE UPDATE OR DELETE ON "{schema}"."{table}"
                FOR EACH ROW EXECUTE FUNCTION "{schema}".prevent_retrieval_snapshot_mutation()
                """
            )
        )


def downgrade() -> None:
    schema = _schema()
    for table in ("retrieval_evidence_items", "retrieval_evidence_sets"):
        op.execute(sa.text(f'DROP TRIGGER trg_{table}_immutable ON "{schema}"."{table}"'))
    op.drop_table("retrieval_evidence_items", schema=schema)
    op.drop_index(
        "ix_retrieval_evidence_sets_workspace_time",
        table_name="retrieval_evidence_sets",
        schema=schema,
    )
    op.drop_table("retrieval_evidence_sets", schema=schema)
