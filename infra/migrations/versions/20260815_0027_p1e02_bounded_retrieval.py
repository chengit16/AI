"""建立 P1E-02 有界检索计划、查询变体和候选不可变快照。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260815_0027"
down_revision: str | None = "20260815_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "retrieval_plans",
        sa.Column("retrieval_plan_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_query_hash", sa.String(64), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("strategy_version", sa.String(128), nullable=False),
        sa.Column("policy_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("maximum_security_level", sa.String(32), nullable=False),
        sa.Column("field_mask", postgresql.ARRAY(sa.String(128)), nullable=False),
        sa.Column("embedding_model_version", sa.String(255), nullable=False),
        sa.Column("tokenizer_version", sa.String(128), nullable=False),
        sa.Column("max_query_characters", sa.Integer(), nullable=False),
        sa.Column("max_query_variants", sa.Integer(), nullable=False),
        sa.Column("max_search_operations", sa.Integer(), nullable=False),
        sa.Column("per_channel_candidates", sa.Integer(), nullable=False),
        sa.Column("final_candidate_limit", sa.Integer(), nullable=False),
        sa.Column("rrf_constant", sa.Integer(), nullable=False),
        sa.Column("max_elapsed_ms", sa.Integer(), nullable=False),
        sa.Column("search_operation_count", sa.Integer(), nullable=False),
        sa.Column("keyword_candidate_count", sa.Integer(), nullable=False),
        sa.Column("vector_candidate_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{schema}.assistant_runs.run_id"],
            name="fk_retrieval_plans_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_retrieval_plans_requester",
        ),
        sa.CheckConstraint(
            "original_query_hash ~ '^[0-9a-f]{64}$'",
            name="ck_retrieval_plans_query_hash",
        ),
        sa.CheckConstraint(
            "classification IN ('exact_lookup', 'summary', 'comparison', 'knowledge')",
            name="ck_retrieval_plans_classification",
        ),
        sa.CheckConstraint(
            "maximum_security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
            name="ck_retrieval_plans_security_level",
        ),
        sa.CheckConstraint("policy_version >= 1", name="ck_retrieval_plans_policy_version"),
        sa.CheckConstraint(
            "max_query_characters >= 1 AND max_query_variants >= 1 "
            "AND max_search_operations >= 1 AND per_channel_candidates >= 1 "
            "AND final_candidate_limit >= 1 AND rrf_constant >= 1 AND max_elapsed_ms >= 1",
            name="ck_retrieval_plans_budget",
        ),
        sa.CheckConstraint(
            "search_operation_count >= 0 AND keyword_candidate_count >= 0 "
            "AND vector_candidate_count >= 0 AND duration_ms >= 0",
            name="ck_retrieval_plans_metrics",
        ),
        sa.CheckConstraint("status = 'completed'", name="ck_retrieval_plans_status"),
        schema=schema,
    )
    op.create_index(
        "ix_retrieval_plans_workspace_time",
        "retrieval_plans",
        ["workspace_id", "created_at"],
        schema=schema,
    )
    op.create_table(
        "retrieval_query_variants",
        sa.Column("retrieval_plan_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sequence_no", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["retrieval_plan_id"],
            [f"{schema}.retrieval_plans.retrieval_plan_id"],
            name="fk_retrieval_query_variants_plan",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("sequence_no >= 1", name="ck_retrieval_query_variants_sequence"),
        sa.CheckConstraint(
            "kind IN ('original', 'focused')",
            name="ck_retrieval_query_variants_kind",
        ),
        sa.CheckConstraint(
            "char_length(btrim(query_text)) BETWEEN 1 AND 4000",
            name="ck_retrieval_query_variants_text",
        ),
        sa.CheckConstraint(
            "query_hash ~ '^[0-9a-f]{64}$'",
            name="ck_retrieval_query_variants_hash",
        ),
        schema=schema,
    )
    op.create_table(
        "retrieval_candidate_snapshots",
        sa.Column("retrieval_plan_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rank", sa.Integer(), primary_key=True),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("index_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_position", postgresql.JSONB(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("query_hit_count", sa.Integer(), nullable=False),
        sa.Column("keyword_hit_count", sa.Integer(), nullable=False),
        sa.Column("vector_hit_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["retrieval_plan_id"],
            [f"{schema}.retrieval_plans.retrieval_plan_id"],
            name="fk_retrieval_candidate_snapshots_plan",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("rank >= 1", name="ck_retrieval_candidate_snapshots_rank"),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_retrieval_candidate_snapshots_hash",
        ),
        sa.CheckConstraint(
            "score >= 0 AND query_hit_count >= 1 AND keyword_hit_count >= 0 "
            "AND vector_hit_count >= 0",
            name="ck_retrieval_candidate_snapshots_metrics",
        ),
        schema=schema,
    )
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".prevent_retrieval_snapshot_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'retrieval snapshot facts are immutable';
            END;
            $$;
            """
        )
    )
    for table in ("retrieval_plans", "retrieval_query_variants", "retrieval_candidate_snapshots"):
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
    for table in ("retrieval_candidate_snapshots", "retrieval_query_variants", "retrieval_plans"):
        op.execute(sa.text(f'DROP TRIGGER trg_{table}_immutable ON "{schema}"."{table}"'))
    op.execute(sa.text(f'DROP FUNCTION "{schema}".prevent_retrieval_snapshot_mutation()'))
    op.drop_table("retrieval_candidate_snapshots", schema=schema)
    op.drop_table("retrieval_query_variants", schema=schema)
    op.drop_index("ix_retrieval_plans_workspace_time", table_name="retrieval_plans", schema=schema)
    op.drop_table("retrieval_plans", schema=schema)
