"""建立 P5-03 六层质量评估运行、层结果和样本结果事实。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260817_0063"
down_revision: str | None = "20260817_0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """创建三张只追加评估事实表，并复用质量不可变保护函数。"""

    schema = _schema()
    _create_runs(schema)
    _create_layer_results(schema)
    _create_sample_results(schema)
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
    """仅在没有评估事实时允许移除新增表。"""

    schema = _schema()
    connection = op.get_bind()
    for table_name in _table_names():
        count = connection.scalar(sa.text(f'SELECT count(*) FROM "{schema}"."{table_name}"'))
        if int(count or 0) > 0:
            raise RuntimeError("存在质量评估事实, 拒绝破坏性降级")
    for table_name in reversed(_table_names()):
        op.execute(
            sa.text(
                f'DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON "{schema}"."{table_name}"'
            )
        )
        op.drop_table(table_name, schema=schema)


def _create_runs(schema: str) -> None:
    op.create_table(
        "quality_evaluation_runs",
        sa.Column("evaluation_run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_identity_digest", sa.String(64), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_digest", sa.String(64), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_configuration_digest", sa.String(64), nullable=False),
        sa.Column("policy_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("evaluator_version", sa.String(128), nullable=False),
        sa.Column("evaluator_identity_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("timeout_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("reason_codes", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("result_digest", sa.String(64), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "evaluation_run_id",
            "workspace_id",
            name="uq_quality_evaluation_runs_id_workspace",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "run_identity_digest",
            name="uq_quality_evaluation_runs_identity",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_quality_evaluation_runs_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id", "workspace_id"],
            [
                f"{schema}.quality_dataset_versions.dataset_version_id",
                f"{schema}.quality_dataset_versions.workspace_id",
            ],
            name="fk_quality_evaluation_runs_dataset",
        ),
        sa.CheckConstraint(
            "status IN ('passed', 'failed')",
            name="ck_quality_evaluation_runs_status",
        ),
        sa.CheckConstraint(
            "run_identity_digest ~ '^[0-9a-f]{64}$' "
            "AND dataset_digest ~ '^[0-9a-f]{64}$' "
            "AND run_configuration_digest ~ '^[0-9a-f]{64}$' "
            "AND policy_digest ~ '^[0-9a-f]{64}$' "
            "AND evaluator_identity_digest ~ '^[0-9a-f]{64}$' "
            "AND result_digest ~ '^[0-9a-f]{64}$'",
            name="ck_quality_evaluation_runs_digests",
        ),
        sa.CheckConstraint(
            "observation_count >= 0 AND passed_count >= 0 AND failed_count >= 0 "
            "AND timeout_count >= 0 AND skipped_count >= 0 "
            "AND observation_count = passed_count + failed_count + timeout_count + skipped_count",
            name="ck_quality_evaluation_runs_counts",
        ),
        sa.CheckConstraint(
            "cardinality(reason_codes) <= 32 AND array_position(reason_codes, NULL) IS NULL",
            name="ck_quality_evaluation_runs_reasons",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_quality_evaluation_runs_workspace_completed",
        "quality_evaluation_runs",
        ["workspace_id", "completed_at"],
        schema=schema,
    )


def _create_layer_results(schema: str) -> None:
    op.create_table(
        "quality_evaluation_layer_results",
        sa.Column("evaluation_run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("layer", sa.String(32), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False),
        sa.Column("score_bps", sa.Integer(), nullable=False),
        sa.Column("reason_codes", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "evaluation_run_id",
            "position",
            name="uq_quality_evaluation_layers_position",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id", "workspace_id"],
            [
                f"{schema}.quality_evaluation_runs.evaluation_run_id",
                f"{schema}.quality_evaluation_runs.workspace_id",
            ],
            name="fk_quality_evaluation_layers_run",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "layer IN ('retrieval', 'citation', 'model', 'prompt', 'workflow', 'tool')",
            name="ck_quality_evaluation_layers_layer",
        ),
        sa.CheckConstraint(
            "status IN ('passed', 'failed')",
            name="ck_quality_evaluation_layers_status",
        ),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 6 AND sample_count >= 0 AND passed_count >= 0 "
            "AND passed_count <= sample_count AND score_bps BETWEEN 0 AND 10000",
            name="ck_quality_evaluation_layers_metrics",
        ),
        sa.CheckConstraint(
            "cardinality(reason_codes) <= 32 AND array_position(reason_codes, NULL) IS NULL",
            name="ck_quality_evaluation_layers_reasons",
        ),
        sa.CheckConstraint(
            "evidence_digest ~ '^[0-9a-f]{64}$'",
            name="ck_quality_evaluation_layers_digest",
        ),
        schema=schema,
    )


def _create_sample_results(schema: str) -> None:
    op.create_table(
        "quality_evaluation_sample_results",
        sa.Column("evaluation_run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("sample_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("layer", sa.String(32), primary_key=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("score_bps", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("reason_codes", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "evaluation_run_id",
            "position",
            name="uq_quality_evaluation_samples_position",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id", "workspace_id"],
            [
                f"{schema}.quality_evaluation_runs.evaluation_run_id",
                f"{schema}.quality_evaluation_runs.workspace_id",
            ],
            name="fk_quality_evaluation_samples_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sample_version_id", "workspace_id"],
            [
                f"{schema}.quality_sample_versions.sample_version_id",
                f"{schema}.quality_sample_versions.workspace_id",
            ],
            name="fk_quality_evaluation_samples_sample",
        ),
        sa.CheckConstraint(
            "layer IN ('retrieval', 'citation', 'model', 'prompt', 'workflow', 'tool')",
            name="ck_quality_evaluation_samples_layer",
        ),
        sa.CheckConstraint(
            "outcome IN ('passed', 'failed', 'timeout', 'skipped')",
            name="ck_quality_evaluation_samples_outcome",
        ),
        sa.CheckConstraint(
            "position >= 1 AND score_bps BETWEEN 0 AND 10000 AND duration_ms BETWEEN 0 AND 3600000",
            name="ck_quality_evaluation_samples_metrics",
        ),
        sa.CheckConstraint(
            "cardinality(reason_codes) <= 8 AND array_position(reason_codes, NULL) IS NULL",
            name="ck_quality_evaluation_samples_reasons",
        ),
        sa.CheckConstraint(
            "evidence_digest ~ '^[0-9a-f]{64}$'",
            name="ck_quality_evaluation_samples_digest",
        ),
        schema=schema,
    )


def _table_names() -> tuple[str, ...]:
    return (
        "quality_evaluation_runs",
        "quality_evaluation_layer_results",
        "quality_evaluation_sample_results",
    )
