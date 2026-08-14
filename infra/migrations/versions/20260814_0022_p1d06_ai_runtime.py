"""建立 P1D-06 不可变 AI 运行配置、发布指针和模型调用事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0022"
down_revision: str | None = "20260814_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _create_runtime_configurations(schema)
    _extend_platform_audit(schema)
    _create_invocation_facts(schema)
    _protect_immutable_snapshots(schema)


def downgrade() -> None:
    schema = _schema()
    _drop_snapshot_protection(schema)
    op.drop_table("model_invocation_attempts", schema=schema)
    op.drop_index(
        "ix_model_invocations_runtime_config", table_name="model_invocations", schema=schema
    )
    op.drop_index(
        "ix_model_invocations_workspace_time", table_name="model_invocations", schema=schema
    )
    op.drop_table("model_invocations", schema=schema)
    op.drop_index(
        "ix_platform_audit_runtime_config_time",
        table_name="platform_audit_records",
        schema=schema,
    )
    op.drop_constraint(
        "ck_platform_audit_subject",
        "platform_audit_records",
        type_="check",
        schema=schema,
    )
    op.drop_constraint(
        "fk_platform_audit_records_runtime_config",
        "platform_audit_records",
        type_="foreignkey",
        schema=schema,
    )
    op.drop_column("platform_audit_records", "runtime_config_version_id", schema=schema)
    op.alter_column("platform_audit_records", "provider_id", nullable=False, schema=schema)
    op.drop_table("ai_runtime_config_publication", schema=schema)
    op.drop_table("ai_runtime_model_routes", schema=schema)
    op.drop_table("ai_runtime_config_versions", schema=schema)


def _create_runtime_configurations(schema: str) -> None:
    op.create_table(
        "ai_runtime_config_versions",
        sa.Column("runtime_config_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version_number", sa.Integer(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("system_prompt_template", sa.Text(), nullable=False),
        sa.Column("system_prompt_hash", sa.String(64), nullable=False),
        sa.Column("component_versions", postgresql.JSONB(), nullable=False),
        sa.Column("attempt_timeout_ms", sa.Integer(), nullable=False),
        sa.Column("total_timeout_ms", sa.Integer(), nullable=False),
        sa.Column("max_attempts_per_route", sa.Integer(), nullable=False),
        sa.Column("max_prompt_characters", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("max_response_characters", sa.Integer(), nullable=False),
        sa.Column("circuit_failure_threshold", sa.Integer(), nullable=False),
        sa.Column("circuit_recovery_ms", sa.Integer(), nullable=False),
        sa.Column("rule_degradation_message", sa.Text(), nullable=True),
        sa.Column("max_estimated_cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_ai_runtime_configs_creator",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_ai_runtime_configs_version"),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$' AND system_prompt_hash ~ '^[0-9a-f]{64}$'",
            name="ck_ai_runtime_configs_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(component_versions) = 'object'",
            name="ck_ai_runtime_configs_components",
        ),
        sa.CheckConstraint(
            "attempt_timeout_ms BETWEEN 1 AND 120000 "
            "AND total_timeout_ms BETWEEN attempt_timeout_ms AND 300000 "
            "AND max_attempts_per_route BETWEEN 1 AND 5 "
            "AND max_prompt_characters BETWEEN 1 AND 2000000 "
            "AND max_output_tokens BETWEEN 1 AND 65536 "
            "AND max_response_characters BETWEEN 1 AND 4000000 "
            "AND circuit_failure_threshold BETWEEN 1 AND 20 "
            "AND circuit_recovery_ms BETWEEN 1 AND 3600000 "
            "AND max_estimated_cost_microunits BETWEEN 1 AND 1000000000000",
            name="ck_ai_runtime_configs_budgets",
        ),
        schema=schema,
    )
    op.create_table(
        "ai_runtime_model_routes",
        sa.Column("route_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("runtime_config_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_configuration_version", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("location", sa.String(32), nullable=False),
        sa.Column("capabilities", postgresql.ARRAY(sa.String(32)), nullable=False),
        sa.Column("input_price_microunits_per_million_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_price_microunits_per_million_tokens", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.UniqueConstraint(
            "runtime_config_version_id",
            "priority",
            name="uq_ai_runtime_routes_priority",
        ),
        sa.UniqueConstraint(
            "runtime_config_version_id",
            "provider_id",
            "model_id",
            name="uq_ai_runtime_routes_provider_model",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_config_version_id"],
            [f"{schema}.ai_runtime_config_versions.runtime_config_version_id"],
            name="fk_ai_runtime_routes_config",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            [f"{schema}.model_provider_configurations.provider_id"],
            name="fk_ai_runtime_routes_provider",
        ),
        sa.CheckConstraint(
            "provider_configuration_version >= 1",
            name="ck_ai_runtime_routes_provider_version",
        ),
        sa.CheckConstraint("priority BETWEEN 1 AND 8", name="ck_ai_runtime_routes_priority"),
        sa.CheckConstraint(
            "location IN ('external', 'private')", name="ck_ai_runtime_routes_location"
        ),
        sa.CheckConstraint("currency = 'CNY'", name="ck_ai_runtime_routes_currency"),
        sa.CheckConstraint(
            "input_price_microunits_per_million_tokens BETWEEN 0 AND 1000000000000 "
            "AND output_price_microunits_per_million_tokens BETWEEN 0 AND 1000000000000",
            name="ck_ai_runtime_routes_prices",
        ),
        schema=schema,
    )
    op.create_table(
        "ai_runtime_config_publication",
        sa.Column("publication_key", sa.String(32), primary_key=True),
        sa.Column("runtime_config_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("published_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["runtime_config_version_id"],
            [f"{schema}.ai_runtime_config_versions.runtime_config_version_id"],
            name="fk_ai_runtime_publication_config",
        ),
        sa.ForeignKeyConstraint(
            ["published_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_ai_runtime_publication_publisher",
        ),
        sa.CheckConstraint("publication_key = 'current'", name="ck_ai_runtime_publication_key"),
        sa.CheckConstraint("generation >= 1", name="ck_ai_runtime_publication_generation"),
        schema=schema,
    )


def _extend_platform_audit(schema: str) -> None:
    op.alter_column("platform_audit_records", "provider_id", nullable=True, schema=schema)
    op.add_column(
        "platform_audit_records",
        sa.Column("runtime_config_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.create_foreign_key(
        "fk_platform_audit_records_runtime_config",
        "platform_audit_records",
        "ai_runtime_config_versions",
        ["runtime_config_version_id"],
        ["runtime_config_version_id"],
        source_schema=schema,
        referent_schema=schema,
    )
    op.create_check_constraint(
        "ck_platform_audit_subject",
        "platform_audit_records",
        "(provider_id IS NOT NULL)::integer + (runtime_config_version_id IS NOT NULL)::integer = 1",
        schema=schema,
    )
    op.create_index(
        "ix_platform_audit_runtime_config_time",
        "platform_audit_records",
        ["runtime_config_version_id", "occurred_at"],
        schema=schema,
    )


def _create_invocation_facts(schema: str) -> None:
    op.create_table(
        "model_invocations",
        sa.Column("invocation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("runtime_config_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_type", sa.String(128), nullable=False),
        sa.Column("security_level", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("traceparent", sa.String(55), nullable=False),
        sa.Column("external_data_allowed", sa.Boolean(), nullable=False),
        sa.Column("requested_max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("selected_route_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("selected_provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("selected_model_id", sa.String(255), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("estimated_cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("finish_reason", sa.String(128), nullable=True),
        sa.Column("degradation_reason", sa.String(128), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_model_invocations_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_config_version_id"],
            [f"{schema}.ai_runtime_config_versions.runtime_config_version_id"],
            name="fk_model_invocations_runtime_config",
        ),
        sa.ForeignKeyConstraint(
            ["selected_route_id"],
            [f"{schema}.ai_runtime_model_routes.route_id"],
            name="fk_model_invocations_selected_route",
        ),
        sa.ForeignKeyConstraint(
            ["selected_provider_id"],
            [f"{schema}.model_provider_configurations.provider_id"],
            name="fk_model_invocations_selected_provider",
        ),
        sa.CheckConstraint(
            "security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
            name="ck_model_invocations_security_level",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'degraded', 'failed', 'rejected')",
            name="ck_model_invocations_status",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND completed_at IS NULL) OR "
            "(status <> 'running' AND completed_at IS NOT NULL)",
            name="ck_model_invocations_completion",
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND estimated_cost_microunits >= 0",
            name="ck_model_invocations_usage",
        ),
        sa.CheckConstraint("currency = 'CNY'", name="ck_model_invocations_currency"),
        sa.CheckConstraint("trace_id ~ '^[0-9a-f]{32}$'", name="ck_model_invocations_trace_id"),
        schema=schema,
    )
    op.create_index(
        "ix_model_invocations_workspace_time",
        "model_invocations",
        ["workspace_id", "started_at"],
        schema=schema,
    )
    op.create_index(
        "ix_model_invocations_runtime_config",
        "model_invocations",
        ["runtime_config_version_id", "started_at"],
        schema=schema,
    )
    op.create_table(
        "model_invocation_attempts",
        sa.Column("invocation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("attempt_index", sa.Integer(), primary_key=True),
        sa.Column("route_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("credential_version", sa.Integer(), nullable=True),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("failure_kind", sa.String(64), nullable=True),
        sa.Column("provider_request_id", sa.String(255), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("estimated_cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("traceparent", sa.String(55), nullable=False),
        sa.ForeignKeyConstraint(
            ["invocation_id"],
            [f"{schema}.model_invocations.invocation_id"],
            name="fk_model_invocation_attempts_invocation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["route_id"],
            [f"{schema}.ai_runtime_model_routes.route_id"],
            name="fk_model_invocation_attempts_route",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            [f"{schema}.model_provider_configurations.provider_id"],
            name="fk_model_invocation_attempts_provider",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id", "credential_version"],
            [
                f"{schema}.model_provider_credentials.provider_id",
                f"{schema}.model_provider_credentials.credential_version",
            ],
            name="fk_model_invocation_attempts_credential",
        ),
        sa.CheckConstraint("attempt_index >= 1", name="ck_model_attempts_index"),
        sa.CheckConstraint("attempt_no >= 0", name="ck_model_attempts_number"),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed', 'circuit_open')",
            name="ck_model_attempts_status",
        ),
        sa.CheckConstraint(
            "duration_ms >= 0 AND estimated_cost_microunits >= 0",
            name="ck_model_attempts_usage",
        ),
        sa.CheckConstraint(
            "(input_tokens IS NULL AND output_tokens IS NULL) OR "
            "(input_tokens >= 0 AND output_tokens >= 0)",
            name="ck_model_attempts_tokens",
        ),
        sa.CheckConstraint("trace_id ~ '^[0-9a-f]{32}$'", name="ck_model_attempts_trace_id"),
        schema=schema,
    )


def _protect_immutable_snapshots(schema: str) -> None:
    # 数据库级保护避免维护脚本或未来代码路径绕过应用层改写已发布运行事实。
    op.execute(
        sa.text(
            f'''
            CREATE FUNCTION "{schema}".prevent_ai_runtime_snapshot_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              RAISE EXCEPTION 'AI runtime snapshots are immutable' USING ERRCODE = '55000';
            END;
            $$
            '''
        )
    )
    for table in ("ai_runtime_config_versions", "ai_runtime_model_routes"):
        op.execute(
            sa.text(
                f'''
                CREATE TRIGGER trg_{table}_immutable
                BEFORE UPDATE OR DELETE ON "{schema}"."{table}"
                FOR EACH ROW EXECUTE FUNCTION "{schema}".prevent_ai_runtime_snapshot_mutation()
                '''
            )
        )


def _drop_snapshot_protection(schema: str) -> None:
    for table in ("ai_runtime_model_routes", "ai_runtime_config_versions"):
        op.execute(sa.text(f'DROP TRIGGER trg_{table}_immutable ON "{schema}"."{table}"'))
    op.execute(sa.text(f'DROP FUNCTION "{schema}".prevent_ai_runtime_snapshot_mutation()'))
