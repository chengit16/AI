"""建立 P5-02 不含正文的质量样本版本和数据集快照。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260817_0062"
down_revision: str | None = "20260816_0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """创建样本、数据集和成员表，并阻止普通事务改写历史。"""

    schema = _schema()
    _create_sample_versions(schema)
    _create_dataset_versions(schema)
    _create_dataset_members(schema)
    _protect_immutable_facts(schema)


def downgrade() -> None:
    """仅在没有质量事实时允许移除表，避免降级静默丢失历史。"""

    schema = _schema()
    _reject_unsafe_downgrade(schema)
    _drop_immutable_protection(schema)
    op.drop_table("quality_dataset_members", schema=schema)
    op.drop_table("quality_dataset_versions", schema=schema)
    op.drop_table("quality_sample_versions", schema=schema)


def _create_sample_versions(schema: str) -> None:
    op.create_table(
        "quality_sample_versions",
        sa.Column("sample_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("logical_sample_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("signal_code", sa.String(64), nullable=False),
        sa.Column("reason_codes", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=True),
        sa.Column("output_digest", sa.String(64), nullable=True),
        sa.Column("feedback_digest", sa.String(64), nullable=True),
        sa.Column("correction_digest", sa.String(64), nullable=True),
        sa.Column(
            "supersedes_sample_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("authorized_permission_code", sa.String(160), nullable=False),
        sa.Column("policy_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("workspace_scope", sa.Boolean(), nullable=False),
        sa.Column(
            "department_scope_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column(
            "account_scope_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column(
            "resource_scope_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column("field_mask", postgresql.ARRAY(sa.String(128)), nullable=False),
        sa.Column("maximum_security_level", sa.String(32), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "sample_version_id",
            "workspace_id",
            name="uq_quality_samples_id_workspace",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "source_type",
            "source_id",
            "source_version",
            name="uq_quality_samples_source_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_quality_samples_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_sample_version_id", "workspace_id"],
            [
                f"{schema}.quality_sample_versions.sample_version_id",
                f"{schema}.quality_sample_versions.workspace_id",
            ],
            name="fk_quality_samples_superseded",
        ),
        sa.CheckConstraint(
            "source_type IN ('run_failure', 'user_feedback', 'human_correction')",
            name="ck_quality_samples_source_type",
        ),
        sa.CheckConstraint("source_version >= 1", name="ck_quality_samples_source_version"),
        sa.CheckConstraint(
            "operation IN ('upsert', 'deleted')",
            name="ck_quality_samples_operation",
        ),
        sa.CheckConstraint(
            "signal_code ~ '^[a-z][a-z0-9_.-]{1,63}$'",
            name="ck_quality_samples_signal_code",
        ),
        sa.CheckConstraint(
            "cardinality(reason_codes) <= 16 AND array_position(reason_codes, NULL) IS NULL",
            name="ck_quality_samples_reason_codes",
        ),
        sa.CheckConstraint(
            "source_digest ~ '^[0-9a-f]{64}$' "
            "AND (input_digest IS NULL OR input_digest ~ '^[0-9a-f]{64}$') "
            "AND (output_digest IS NULL OR output_digest ~ '^[0-9a-f]{64}$') "
            "AND (feedback_digest IS NULL OR feedback_digest ~ '^[0-9a-f]{64}$') "
            "AND (correction_digest IS NULL OR correction_digest ~ '^[0-9a-f]{64}$')",
            name="ck_quality_samples_digests",
        ),
        sa.CheckConstraint(
            "(operation = 'upsert' AND num_nonnulls(input_digest, output_digest, "
            "feedback_digest, correction_digest) >= 1) OR "
            "(operation = 'deleted' AND num_nonnulls(input_digest, output_digest, "
            "feedback_digest, correction_digest) = 0)",
            name="ck_quality_samples_content_operation",
        ),
        sa.CheckConstraint("policy_version >= 1", name="ck_quality_samples_policy_version"),
        sa.CheckConstraint(
            "maximum_security_level IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
            name="ck_quality_samples_security_level",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_quality_samples_workspace_source",
        "quality_sample_versions",
        ["workspace_id", "source_type", "source_id", "source_version"],
        schema=schema,
    )


def _create_dataset_versions(schema: str) -> None:
    op.create_table(
        "quality_dataset_versions",
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "previous_dataset_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "trigger_sample_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("dataset_digest", sa.String(64), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "dataset_version_id",
            "workspace_id",
            name="uq_quality_datasets_id_workspace",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "version_number",
            name="uq_quality_datasets_workspace_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_quality_datasets_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["previous_dataset_version_id", "workspace_id"],
            [
                f"{schema}.quality_dataset_versions.dataset_version_id",
                f"{schema}.quality_dataset_versions.workspace_id",
            ],
            name="fk_quality_datasets_previous",
        ),
        sa.ForeignKeyConstraint(
            ["trigger_sample_version_id", "workspace_id"],
            [
                f"{schema}.quality_sample_versions.sample_version_id",
                f"{schema}.quality_sample_versions.workspace_id",
            ],
            name="fk_quality_datasets_trigger_sample",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_quality_datasets_version"),
        sa.CheckConstraint("sample_count >= 0", name="ck_quality_datasets_sample_count"),
        sa.CheckConstraint(
            "dataset_digest ~ '^[0-9a-f]{64}$'",
            name="ck_quality_datasets_digest",
        ),
        sa.CheckConstraint(
            "(version_number = 1 AND previous_dataset_version_id IS NULL) OR "
            "(version_number > 1 AND previous_dataset_version_id IS NOT NULL)",
            name="ck_quality_datasets_previous",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_quality_datasets_workspace_version",
        "quality_dataset_versions",
        ["workspace_id", "version_number"],
        schema=schema,
    )


def _create_dataset_members(schema: str) -> None:
    op.create_table(
        "quality_dataset_members",
        sa.Column(
            "dataset_version_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "sample_version_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("logical_sample_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "dataset_version_id",
            "logical_sample_id",
            name="uq_quality_dataset_members_logical",
        ),
        sa.UniqueConstraint(
            "dataset_version_id",
            "position",
            name="uq_quality_dataset_members_position",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id", "workspace_id"],
            [
                f"{schema}.quality_dataset_versions.dataset_version_id",
                f"{schema}.quality_dataset_versions.workspace_id",
            ],
            name="fk_quality_dataset_members_dataset",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sample_version_id", "workspace_id"],
            [
                f"{schema}.quality_sample_versions.sample_version_id",
                f"{schema}.quality_sample_versions.workspace_id",
            ],
            name="fk_quality_dataset_members_sample",
        ),
        sa.CheckConstraint("position >= 1", name="ck_quality_dataset_members_position"),
        schema=schema,
    )


def _protect_immutable_facts(schema: str) -> None:
    """普通事务禁止改写或删除质量历史，仅生命周期清理可旁路删除。"""

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".protect_quality_immutable_fact()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            BEGIN
                IF TG_OP = 'DELETE'
                   AND current_setting('ai_platform.lifecycle_purge', true) = 'on' THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION 'quality facts are immutable'
                    USING ERRCODE = '55000';
            END;
            $function$
            """
        )
    )
    for table_name in (
        "quality_sample_versions",
        "quality_dataset_versions",
        "quality_dataset_members",
    ):
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_{table_name}_immutable
                BEFORE UPDATE OR DELETE ON "{schema}"."{table_name}"
                FOR EACH ROW EXECUTE FUNCTION "{schema}".protect_quality_immutable_fact()
                """
            )
        )


def _drop_immutable_protection(schema: str) -> None:
    for table_name in (
        "quality_dataset_members",
        "quality_dataset_versions",
        "quality_sample_versions",
    ):
        op.execute(
            sa.text(
                f'DROP TRIGGER IF EXISTS trg_{table_name}_immutable ON "{schema}"."{table_name}"'
            )
        )
    op.execute(sa.text(f'DROP FUNCTION IF EXISTS "{schema}".protect_quality_immutable_fact()'))


def _reject_unsafe_downgrade(schema: str) -> None:
    connection = op.get_bind()
    for table_name in (
        "quality_sample_versions",
        "quality_dataset_versions",
        "quality_dataset_members",
    ):
        count = connection.scalar(sa.text(f'SELECT count(*) FROM "{schema}"."{table_name}"'))
        if int(count or 0) > 0:
            raise RuntimeError("存在质量样本或数据集事实, 拒绝破坏性降级")
