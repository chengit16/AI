"""建立 P5-05 成本归因窗口、逐尝试账本和固定组件聚合事实。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260817_0065"
down_revision: str | None = "20260817_0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """创建三张只追加成本事实表，并复用质量不可变保护函数。"""

    schema = _schema()
    _create_windows(schema)
    _create_entries(schema)
    _create_lines(schema)
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
    """仅在不存在成本事实时允许移除新增表。"""

    schema = _schema()
    connection = op.get_bind()
    for table_name in _table_names():
        count = connection.scalar(sa.text(f'SELECT count(*) FROM "{schema}"."{table_name}"'))
        if int(count or 0) > 0:
            raise RuntimeError("存在成本归因事实, 拒绝破坏性降级")
    for table_name in reversed(_table_names()):
        op.execute(
            sa.text(
                f'DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON "{schema}"."{table_name}"'
            )
        )
        op.drop_table(table_name, schema=schema)


def _create_windows(schema: str) -> None:
    op.create_table(
        "cost_attribution_windows",
        sa.Column("cost_window_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("window_identity_digest", sa.String(64), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("runtime_config_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_configuration_digest", sa.String(64), nullable=False),
        sa.Column("price_version", sa.String(128), nullable=False),
        sa.Column("price_catalog_digest", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("network_region", sa.String(64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_kind", sa.String(32), nullable=False),
        sa.Column("collector_version", sa.String(64), nullable=False),
        sa.Column("attribution_status", sa.String(16), nullable=False),
        sa.Column("price_verification_status", sa.String(16), nullable=False),
        sa.Column("reconciliation_status", sa.String(24), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("failed_entry_count", sa.Integer(), nullable=False),
        sa.Column("retry_entry_count", sa.Integer(), nullable=False),
        sa.Column("estimated_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("reported_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("recognized_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("supplier_statement_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("reconciliation_difference_minor", sa.BigInteger(), nullable=True),
        sa.Column("supplier_account_digest", sa.String(64), nullable=True),
        sa.Column("supplier_statement_digest", sa.String(64), nullable=True),
        sa.Column("supplier_evidence_digest", sa.String(64), nullable=False),
        sa.Column("reason_codes", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("result_digest", sa.String(64), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "cost_window_id",
            "workspace_id",
            name="uq_cost_windows_id_workspace",
        ),
        sa.UniqueConstraint(
            "workspace_id", "window_identity_digest", name="uq_cost_windows_identity"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_cost_windows_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id", "workspace_id"],
            [f"{schema}.services.service_id", f"{schema}.services.workspace_id"],
            name="fk_cost_windows_service",
        ),
        sa.ForeignKeyConstraint(
            ["agent_release_id", "workspace_id"],
            [f"{schema}.agent_releases.release_id", f"{schema}.agent_releases.workspace_id"],
            name="fk_cost_windows_release",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_config_version_id"],
            [f"{schema}.ai_runtime_config_versions.runtime_config_version_id"],
            name="fk_cost_windows_runtime_config",
        ),
        sa.CheckConstraint(
            "evidence_kind IN ('synthetic', 'authorized_real')",
            name="ck_cost_windows_evidence",
        ),
        sa.CheckConstraint(
            "attribution_status IN ('not_configured', 'not_run', 'passed', 'failed') "
            "AND price_verification_status IN ('not_configured', 'not_run', 'passed', 'failed')",
            name="ck_cost_windows_verification",
        ),
        sa.CheckConstraint(
            "reconciliation_status IN "
            "('not_configured', 'not_run', 'matched', 'explained', 'failed')",
            name="ck_cost_windows_reconciliation",
        ),
        sa.CheckConstraint(
            "entry_count BETWEEN 0 AND 100000 AND failed_entry_count BETWEEN 0 AND entry_count "
            "AND retry_entry_count BETWEEN 0 AND entry_count",
            name="ck_cost_windows_counts",
        ),
        sa.CheckConstraint(
            "estimated_amount_minor BETWEEN 0 AND 9000000000000000 "
            "AND reported_amount_minor BETWEEN 0 AND 9000000000000000 "
            "AND recognized_amount_minor BETWEEN 0 AND 9000000000000000 "
            "AND (supplier_statement_amount_minor IS NULL OR "
            "supplier_statement_amount_minor BETWEEN 0 AND 9000000000000000) "
            "AND (reconciliation_difference_minor IS NULL OR "
            "reconciliation_difference_minor BETWEEN -9000000000000000 "
            "AND 9000000000000000)",
            name="ck_cost_windows_amounts",
        ),
        sa.CheckConstraint(
            "window_ended_at > window_started_at AND completed_at >= window_started_at",
            name="ck_cost_windows_time",
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$' "
            "AND price_version ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$' "
            "AND network_region ~ '^[a-z0-9][a-z0-9.-]{1,63}$'",
            name="ck_cost_windows_identity_values",
        ),
        sa.CheckConstraint(
            "window_identity_digest ~ '^[0-9a-f]{64}$' "
            "AND run_configuration_digest ~ '^[0-9a-f]{64}$' "
            "AND price_catalog_digest ~ '^[0-9a-f]{64}$' "
            "AND supplier_evidence_digest ~ '^[0-9a-f]{64}$' "
            "AND result_digest ~ '^[0-9a-f]{64}$' "
            "AND (supplier_account_digest IS NULL OR "
            "supplier_account_digest ~ '^[0-9a-f]{64}$') "
            "AND (supplier_statement_digest IS NULL OR "
            "supplier_statement_digest ~ '^[0-9a-f]{64}$')",
            name="ck_cost_windows_digests",
        ),
        sa.CheckConstraint(
            "cardinality(reason_codes) <= 24 AND array_position(reason_codes, NULL) IS NULL",
            name="ck_cost_windows_reasons",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_cost_windows_workspace_completed",
        "cost_attribution_windows",
        ["workspace_id", "completed_at"],
        schema=schema,
    )


def _create_entries(schema: str) -> None:
    op.create_table(
        "cost_ledger_entries",
        sa.Column("ledger_entry_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cost_window_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component", sa.String(32), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("meter_key", sa.String(64), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("is_retry", sa.Boolean(), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("usage_unit", sa.String(16), nullable=False),
        sa.Column("unit_size", sa.BigInteger(), nullable=False),
        sa.Column("unit_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("estimated_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("reported_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("recognized_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("amount_source", sa.String(32), nullable=False),
        sa.Column("price_version", sa.String(128), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint("cost_window_id", "position", name="uq_cost_entries_position"),
        sa.UniqueConstraint(
            "cost_window_id",
            "source_kind",
            "source_record_id",
            "meter_key",
            name="uq_cost_entries_source_meter",
        ),
        sa.ForeignKeyConstraint(
            ["cost_window_id", "workspace_id"],
            [
                f"{schema}.cost_attribution_windows.cost_window_id",
                f"{schema}.cost_attribution_windows.workspace_id",
            ],
            name="fk_cost_entries_window",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "component IN "
            "('model', 'retrieval', 'ocr', 'indexing', 'embedding', 'reranker', 'tool')",
            name="ck_cost_entries_component",
        ),
        sa.CheckConstraint(
            "source_kind IN ('model_attempt', 'retrieval_run', 'ocr_attempt', 'index_build', "
            "'embedding_batch', 'reranker_request', 'tool_attempt')",
            name="ck_cost_entries_source",
        ),
        sa.CheckConstraint(
            "usage_unit IN ('token', 'request', 'page', 'chunk', 'pair', 'millisecond')",
            name="ck_cost_entries_unit",
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'failed', 'timed_out', 'cancelled', 'degraded')",
            name="ck_cost_entries_outcome",
        ),
        sa.CheckConstraint(
            "amount_source IN "
            "('synthetic_rate', 'contract_rate', 'provider_rate', 'provider_reported')",
            name="ck_cost_entries_amount_source",
        ),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 100000 AND attempt_no >= 1 AND is_retry = (attempt_no > 1)",
            name="ck_cost_entries_attempt",
        ),
        sa.CheckConstraint(
            "quantity BETWEEN 0 AND 1000000000000 AND unit_size BETWEEN 1 AND 1000000000000 "
            "AND unit_price_minor BETWEEN 0 AND 9000000000000000 "
            "AND estimated_amount_minor BETWEEN 0 AND 9000000000000000 "
            "AND (reported_amount_minor IS NULL OR "
            "reported_amount_minor BETWEEN 0 AND 9000000000000000) "
            "AND recognized_amount_minor BETWEEN 0 AND 9000000000000000",
            name="ck_cost_entries_amounts",
        ),
        sa.CheckConstraint(
            "meter_key ~ '^[a-z][a-z0-9_.-]{0,63}$' "
            "AND price_version ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$' "
            "AND currency ~ '^[A-Z]{3}$' "
            "AND evidence_digest ~ '^[0-9a-f]{64}$'",
            name="ck_cost_entries_identity",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_cost_entries_workspace_component",
        "cost_ledger_entries",
        ["workspace_id", "component"],
        schema=schema,
    )


def _create_lines(schema: str) -> None:
    op.create_table(
        "cost_attribution_lines",
        sa.Column("cost_window_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("component", sa.String(32), primary_key=True),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("failed_entry_count", sa.Integer(), nullable=False),
        sa.Column("retry_entry_count", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("estimated_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("reported_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("recognized_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint("cost_window_id", "position", name="uq_cost_lines_position"),
        sa.ForeignKeyConstraint(
            ["cost_window_id", "workspace_id"],
            [
                f"{schema}.cost_attribution_windows.cost_window_id",
                f"{schema}.cost_attribution_windows.workspace_id",
            ],
            name="fk_cost_lines_window",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "component IN "
            "('model', 'retrieval', 'ocr', 'indexing', 'embedding', 'reranker', 'tool') "
            "AND position BETWEEN 1 AND 7",
            name="ck_cost_lines_component",
        ),
        sa.CheckConstraint(
            "entry_count BETWEEN 0 AND 100000 AND failed_entry_count BETWEEN 0 AND entry_count "
            "AND retry_entry_count BETWEEN 0 AND entry_count",
            name="ck_cost_lines_counts",
        ),
        sa.CheckConstraint(
            "quantity BETWEEN 0 AND 1000000000000 "
            "AND estimated_amount_minor BETWEEN 0 AND 9000000000000000 "
            "AND reported_amount_minor BETWEEN 0 AND 9000000000000000 "
            "AND recognized_amount_minor BETWEEN 0 AND 9000000000000000",
            name="ck_cost_lines_amounts",
        ),
        sa.CheckConstraint(
            "evidence_digest ~ '^[0-9a-f]{64}$'",
            name="ck_cost_lines_digest",
        ),
        schema=schema,
    )


def _table_names() -> tuple[str, ...]:
    return ("cost_attribution_windows", "cost_ledger_entries", "cost_attribution_lines")
