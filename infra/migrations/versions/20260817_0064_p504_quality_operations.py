"""建立 P5-04 质量运营窗口与三来源发布门禁事实。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260817_0064"
down_revision: str | None = "20260817_0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """创建两张只追加质量运营表，并复用质量不可变保护函数。"""

    schema = _schema()
    _create_windows(schema)
    _create_source_results(schema)
    for table_name in _table_names():
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_{table_name}_immutable
                BEFORE UPDATE OR DELETE ON "{schema}"."{table_name}"
                FOR EACH ROW EXECUTE FUNCTION "{schema}".protect_quality_immutable_fact()
                """
            )
        )


def downgrade() -> None:
    """仅在不存在质量运营事实时允许移除新增表。"""

    schema = _schema()
    connection = op.get_bind()
    for table_name in _table_names():
        count = connection.scalar(sa.text(f'SELECT count(*) FROM "{schema}"."{table_name}"'))
        if int(count or 0) > 0:
            raise RuntimeError("存在质量运营事实, 拒绝破坏性降级")
    for table_name in reversed(_table_names()):
        op.execute(
            sa.text(
                f'DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON "{schema}"."{table_name}"'
            )
        )
        op.drop_table(table_name, schema=schema)


def _create_windows(schema: str) -> None:
    op.create_table(
        "quality_operation_windows",
        sa.Column("quality_window_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("window_identity_digest", sa.String(64), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evaluation_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evaluation_result_digest", sa.String(64), nullable=False),
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_digest", sa.String(64), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_configuration_digest", sa.String(64), nullable=False),
        sa.Column("gate_stage", sa.String(32), nullable=False),
        sa.Column("evidence_kind", sa.String(32), nullable=False),
        sa.Column("collector_version", sa.String(128), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_configuration_version", sa.Integer(), nullable=True),
        sa.Column("model_id", sa.String(255), nullable=True),
        sa.Column("model_parameters_digest", sa.String(64), nullable=False),
        sa.Column("network_region", sa.String(64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("core_functional_status", sa.String(32), nullable=False),
        sa.Column("provider_integration_status", sa.String(32), nullable=False),
        sa.Column("ai_quality_status", sa.String(32), nullable=False),
        sa.Column("capacity_certification_status", sa.String(32), nullable=False),
        sa.Column("release_gate_status", sa.String(16), nullable=False),
        sa.Column("reason_codes", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("result_digest", sa.String(64), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "quality_window_id",
            "workspace_id",
            name="uq_quality_operation_windows_id_workspace",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "window_identity_digest",
            name="uq_quality_operation_windows_identity",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_quality_operation_windows_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id", "workspace_id"],
            [
                f"{schema}.quality_evaluation_runs.evaluation_run_id",
                f"{schema}.quality_evaluation_runs.workspace_id",
            ],
            name="fk_quality_operation_windows_evaluation",
        ),
        sa.CheckConstraint(
            "gate_stage IN ('offline_release', 'canary_promotion', 'online_continuation')",
            name="ck_quality_operation_windows_stage",
        ),
        sa.CheckConstraint(
            "evidence_kind IN ('synthetic', 'authorized_real')",
            name="ck_quality_operation_windows_evidence",
        ),
        sa.CheckConstraint(
            "(provider_id IS NULL AND provider_configuration_version IS NULL AND model_id IS NULL) "
            "OR (provider_id IS NOT NULL AND provider_configuration_version >= 1 "
            "AND char_length(btrim(model_id)) BETWEEN 1 AND 255)",
            name="ck_quality_operation_windows_provider",
        ),
        sa.CheckConstraint(
            "window_identity_digest ~ '^[0-9a-f]{64}$' "
            "AND evaluation_result_digest ~ '^[0-9a-f]{64}$' "
            "AND dataset_digest ~ '^[0-9a-f]{64}$' "
            "AND run_configuration_digest ~ '^[0-9a-f]{64}$' "
            "AND model_parameters_digest ~ '^[0-9a-f]{64}$' "
            "AND result_digest ~ '^[0-9a-f]{64}$'",
            name="ck_quality_operation_windows_digests",
        ),
        sa.CheckConstraint(
            "network_region ~ '^[a-z0-9][a-z0-9.-]{1,63}$' AND window_ended_at > window_started_at",
            name="ck_quality_operation_windows_window",
        ),
        sa.CheckConstraint(
            "core_functional_status IN ('not_configured', 'not_run', 'passed', 'failed') "
            "AND provider_integration_status IN "
            "('not_configured', 'not_run', 'passed', 'failed') "
            "AND ai_quality_status IN ('not_configured', 'not_run', 'passed', 'failed') "
            "AND capacity_certification_status IN "
            "('not_configured', 'not_run', 'passed', 'failed')",
            name="ck_quality_operation_windows_verification",
        ),
        sa.CheckConstraint(
            "release_gate_status IN ('blocked', 'passed', 'failed')",
            name="ck_quality_operation_windows_gate",
        ),
        sa.CheckConstraint(
            "cardinality(reason_codes) <= 32 AND array_position(reason_codes, NULL) IS NULL",
            name="ck_quality_operation_windows_reasons",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_quality_operation_windows_workspace_completed",
        "quality_operation_windows",
        ["workspace_id", "completed_at"],
        schema=schema,
    )


def _create_source_results(schema: str) -> None:
    op.create_table(
        "quality_operation_source_results",
        sa.Column("quality_window_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(32), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("citation_claim_count", sa.Integer(), nullable=False),
        sa.Column("valid_citation_count", sa.Integer(), nullable=False),
        sa.Column("supported_citation_count", sa.Integer(), nullable=False),
        sa.Column("citation_presence_rate_bps", sa.Integer(), nullable=True),
        sa.Column("citation_support_rate_bps", sa.Integer(), nullable=True),
        sa.Column("answer_evaluated_count", sa.Integer(), nullable=False),
        sa.Column("acceptable_answer_count", sa.Integer(), nullable=False),
        sa.Column("answer_acceptance_rate_bps", sa.Integer(), nullable=True),
        sa.Column("feedback_count", sa.Integer(), nullable=False),
        sa.Column("positive_feedback_count", sa.Integer(), nullable=False),
        sa.Column("positive_feedback_rate_bps", sa.Integer(), nullable=True),
        sa.Column("tool_call_count", sa.Integer(), nullable=False),
        sa.Column("unauthorized_access_count", sa.Integer(), nullable=False),
        sa.Column("restricted_field_leakage_count", sa.Integer(), nullable=False),
        sa.Column("unauthorized_tool_call_count", sa.Integer(), nullable=False),
        sa.Column("reason_codes", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "quality_window_id",
            "position",
            name="uq_quality_operation_sources_position",
        ),
        sa.ForeignKeyConstraint(
            ["quality_window_id", "workspace_id"],
            [
                f"{schema}.quality_operation_windows.quality_window_id",
                f"{schema}.quality_operation_windows.workspace_id",
            ],
            name="fk_quality_operation_sources_window",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "source IN ('offline', 'canary', 'online_feedback') AND position BETWEEN 1 AND 3",
            name="ck_quality_operation_sources_source",
        ),
        sa.CheckConstraint(
            "status IN ('not_run', 'passed', 'failed')",
            name="ck_quality_operation_sources_status",
        ),
        sa.CheckConstraint(
            "sample_count BETWEEN 0 AND 1000000 "
            "AND citation_claim_count BETWEEN 0 AND 1000000 "
            "AND valid_citation_count BETWEEN 0 AND citation_claim_count "
            "AND supported_citation_count BETWEEN 0 AND valid_citation_count "
            "AND answer_evaluated_count BETWEEN 0 AND 1000000 "
            "AND acceptable_answer_count BETWEEN 0 AND answer_evaluated_count "
            "AND feedback_count BETWEEN 0 AND 1000000 "
            "AND positive_feedback_count BETWEEN 0 AND feedback_count "
            "AND tool_call_count BETWEEN 0 AND 1000000 "
            "AND unauthorized_access_count BETWEEN 0 AND 1000000 "
            "AND restricted_field_leakage_count BETWEEN 0 AND 1000000 "
            "AND unauthorized_tool_call_count BETWEEN 0 AND tool_call_count",
            name="ck_quality_operation_sources_counts",
        ),
        sa.CheckConstraint(
            "(citation_presence_rate_bps IS NULL OR "
            "citation_presence_rate_bps BETWEEN 0 AND 10000) "
            "AND (citation_support_rate_bps IS NULL OR "
            "citation_support_rate_bps BETWEEN 0 AND 10000) "
            "AND (answer_acceptance_rate_bps IS NULL OR "
            "answer_acceptance_rate_bps BETWEEN 0 AND 10000) "
            "AND (positive_feedback_rate_bps IS NULL OR "
            "positive_feedback_rate_bps BETWEEN 0 AND 10000)",
            name="ck_quality_operation_sources_rates",
        ),
        sa.CheckConstraint(
            "cardinality(reason_codes) <= 16 AND array_position(reason_codes, NULL) IS NULL",
            name="ck_quality_operation_sources_reasons",
        ),
        sa.CheckConstraint(
            "evidence_digest ~ '^[0-9a-f]{64}$'",
            name="ck_quality_operation_sources_digest",
        ),
        schema=schema,
    )


def _table_names() -> tuple[str, ...]:
    return ("quality_operation_windows", "quality_operation_source_results")
