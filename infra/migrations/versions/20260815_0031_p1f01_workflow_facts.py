"""建立 P1F-01 工作流草稿、不可变版本、发布指针和运行事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260815_0031"
down_revision: str | None = "20260815_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNER_PERMISSIONS = (
    "workflow.definition.create",
    "workflow.definition.read",
    "workflow.definition.update",
    "workflow.definition.publish",
    "workflow.run.create",
    "workflow.run.read",
)
MEMBER_PERMISSIONS = (
    "workflow.definition.read",
    "workflow.run.create",
    "workflow.run.read",
)
WORKFLOW_BINDINGS = (
    (172, 80, "mutation"),
    (173, 81, "query"),
    (173, 82, "query"),
    (174, 83, "mutation"),
    (174, 84, "mutation"),
    (175, 85, "publish"),
    (176, 86, "mutation"),
    (177, 87, "query"),
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _create_definitions(schema)
    _create_versions(schema)
    _create_runs(schema)
    _protect_immutable_versions(schema)
    _backfill_permissions(schema)
    _register_bindings(schema)


def downgrade() -> None:
    schema = _schema()
    api_ids = ", ".join(
        f"'81000000-0000-4000-8000-{api_number:012d}'::uuid"
        for _, api_number, _ in WORKFLOW_BINDINGS
    )
    permissions = ", ".join(
        f"'{code}'" for code in sorted(set(OWNER_PERMISSIONS) | set(MEMBER_PERMISSIONS))
    )
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE api_resource_id IN ({api_ids});
            DELETE FROM "{schema}".role_permission_grants
            WHERE permission_code IN ({permissions});
            DROP TRIGGER IF EXISTS protect_workflow_versions
            ON "{schema}".workflow_versions;
            DROP FUNCTION IF EXISTS "{schema}".reject_workflow_version_mutation();
            """
        )
    )
    op.drop_index(
        "ix_workflow_runs_workflow_time",
        table_name="workflow_runs",
        schema=schema,
    )
    op.drop_table("workflow_runs", schema=schema)
    op.drop_table("workflow_publications", schema=schema)
    op.drop_index(
        "ix_workflow_versions_workflow_time",
        table_name="workflow_versions",
        schema=schema,
    )
    op.drop_table("workflow_versions", schema=schema)
    op.drop_table("workflow_drafts", schema=schema)
    op.drop_index("ix_workflows_workspace_time", table_name="workflows", schema=schema)
    op.drop_table("workflows", schema=schema)


def _create_definitions(schema: str) -> None:
    op.create_table(
        "workflows",
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("workflow_id", "workspace_id", name="uq_workflows_id_workspace"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_workflows_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_workflows_creator",
        ),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_workflows_status"),
        sa.CheckConstraint("version >= 1", name="ck_workflows_version"),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120",
            name="ck_workflows_name",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_workflows_workspace_time",
        "workflows",
        ["workspace_id", "updated_at"],
        schema=schema,
    )
    op.create_table(
        "workflow_drafts",
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("graph", postgresql.JSONB(), nullable=False),
        sa.Column("graph_digest", sa.String(64), nullable=False),
        sa.Column("validation_errors", postgresql.JSONB(), nullable=False),
        sa.Column("updated_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_id", "workspace_id"],
            [f"{schema}.workflows.workflow_id", f"{schema}.workflows.workspace_id"],
            name="fk_workflow_drafts_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_workflow_drafts_updater",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_workflow_drafts_revision"),
        sa.CheckConstraint(
            "graph_digest ~ '^[0-9a-f]{64}$'",
            name="ck_workflow_drafts_digest",
        ),
        schema=schema,
    )


def _create_versions(schema: str) -> None:
    op.create_table(
        "workflow_versions",
        sa.Column("workflow_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("source_draft_revision", sa.Integer(), nullable=False),
        sa.Column("graph", postgresql.JSONB(), nullable=False),
        sa.Column("graph_digest", sa.String(64), nullable=False),
        sa.Column("published_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workflow_version_id",
            "workspace_id",
            name="uq_workflow_versions_id_workspace",
        ),
        sa.UniqueConstraint(
            "workflow_id",
            "workspace_id",
            "workflow_version_id",
            name="uq_workflow_versions_workflow_version",
        ),
        sa.UniqueConstraint("workflow_id", "version_number", name="uq_workflow_versions_number"),
        sa.UniqueConstraint(
            "workflow_id",
            "source_draft_revision",
            name="uq_workflow_versions_draft_revision",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "workspace_id"],
            [f"{schema}.workflows.workflow_id", f"{schema}.workflows.workspace_id"],
            name="fk_workflow_versions_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["published_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_workflow_versions_publisher",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_workflow_versions_number"),
        sa.CheckConstraint(
            "source_draft_revision >= 1",
            name="ck_workflow_versions_draft_revision",
        ),
        sa.CheckConstraint(
            "graph_digest ~ '^[0-9a-f]{64}$'",
            name="ck_workflow_versions_digest",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_workflow_versions_workflow_time",
        "workflow_versions",
        ["workspace_id", "workflow_id", "published_at"],
        schema=schema,
    )
    op.create_table(
        "workflow_publications",
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("published_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_id", "workspace_id"],
            [f"{schema}.workflows.workflow_id", f"{schema}.workflows.workspace_id"],
            name="fk_workflow_publications_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "workspace_id", "workflow_version_id"],
            [
                f"{schema}.workflow_versions.workflow_id",
                f"{schema}.workflow_versions.workspace_id",
                f"{schema}.workflow_versions.workflow_version_id",
            ],
            name="fk_workflow_publications_version",
        ),
        sa.ForeignKeyConstraint(
            ["published_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_workflow_publications_publisher",
        ),
        sa.CheckConstraint("generation >= 1", name="ck_workflow_publications_generation"),
        schema=schema,
    )


def _create_runs(schema: str) -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("input_payload", postgresql.JSONB(), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("traceparent", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "workflow_run_id", "workspace_id", name="uq_workflow_runs_id_workspace"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "requested_by_account_id",
            "idempotency_key",
            name="uq_workflow_runs_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "workspace_id"],
            [f"{schema}.workflows.workflow_id", f"{schema}.workflows.workspace_id"],
            name="fk_workflow_runs_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "workspace_id", "workflow_version_id"],
            [
                f"{schema}.workflow_versions.workflow_id",
                f"{schema}.workflow_versions.workspace_id",
                f"{schema}.workflow_versions.workflow_version_id",
            ],
            name="fk_workflow_runs_version",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_workflow_runs_requester",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_workflow_runs_status",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_workflow_runs_request_hash",
        ),
        sa.CheckConstraint("version >= 1", name="ck_workflow_runs_version"),
        sa.CheckConstraint(
            "(status IN ('queued', 'running') AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'cancelled') AND completed_at IS NOT NULL)",
            name="ck_workflow_runs_completion",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_workflow_runs_workflow_time",
        "workflow_runs",
        ["workspace_id", "workflow_id", "created_at"],
        schema=schema,
    )


def _protect_immutable_versions(schema: str) -> None:
    # 数据库层拒绝任何历史版本变更，避免绕过 Repository 篡改已排队运行的冻结事实。
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".reject_workflow_version_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'workflow_versions are immutable';
            END;
            $$;
            CREATE TRIGGER protect_workflow_versions
            BEFORE UPDATE OR DELETE ON "{schema}".workflow_versions
            FOR EACH ROW EXECUTE FUNCTION "{schema}".reject_workflow_version_mutation();
            """
        )
    )


def _backfill_permissions(schema: str) -> None:
    _seed_role_permissions(schema, "workspace_owner", OWNER_PERMISSIONS, "RESTRICTED")
    _seed_role_permissions(schema, "workspace_member", MEMBER_PERMISSIONS, "INTERNAL")


def _seed_role_permissions(
    schema: str,
    role_key: str,
    permission_codes: tuple[str, ...],
    security_level: str,
) -> None:
    permissions = ", ".join(f"('{code}')" for code in permission_codes)
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, permission_code,
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[],
                   '{security_level}', ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            CROSS JOIN (VALUES {permissions}) AS permission_codes(permission_code)
            WHERE roles.role_key = '{role_key}' AND roles.system_managed = true
            ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
            """
        )
    )


def _register_bindings(schema: str) -> None:
    values = ", ".join(
        "("
        + ", ".join(
            (
                f"'82000000-0000-4000-8000-{menu_number:012d}'::uuid",
                f"'81000000-0000-4000-8000-{api_number:012d}'::uuid",
                f"'{action_type}'",
            )
        )
        + ")"
        for menu_number, api_number, action_type in WORKFLOW_BINDINGS
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".registered_menu_api_bindings (
                menu_id, api_resource_id, action_type
            ) VALUES {values}
            ON CONFLICT (menu_id, api_resource_id) DO UPDATE
            SET action_type = EXCLUDED.action_type
            """
        )
    )
