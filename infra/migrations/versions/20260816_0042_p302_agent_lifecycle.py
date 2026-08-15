"""扩展共享 Agent 表并建立 P3-02 草稿历史、候选和幂等事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260816_0042"
down_revision: str | None = "20260815_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DRAFT_STATUSES = (
    "editing",
    "testing",
    "test_failed",
    "ready_for_approval",
    "approval_pending",
    "approved",
    "rejected",
    "superseded",
)
CANDIDATE_STATUSES = ("created", *DRAFT_STATUSES[1:-1], "released", "superseded")


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _extend_agents(schema)
    _create_agent_drafts(schema)
    _create_agent_draft_revisions(schema)
    _create_agent_release_candidates(schema)
    _create_agent_control_requests(schema)
    _extend_agent_releases(schema)
    _protect_agent_control_history(schema)


def downgrade() -> None:
    schema = _schema()
    _reject_unsafe_downgrade(schema)
    _drop_agent_control_history_protection(schema)
    _contract_agent_releases(schema)
    op.drop_index(
        "ix_agent_control_requests_workspace_time",
        table_name="agent_control_requests",
        schema=schema,
    )
    op.drop_table("agent_control_requests", schema=schema)
    op.drop_index(
        "ix_agent_release_candidates_workspace_time",
        table_name="agent_release_candidates",
        schema=schema,
    )
    op.drop_table("agent_release_candidates", schema=schema)
    op.drop_index(
        "ix_agent_draft_revisions_agent_revision",
        table_name="agent_draft_revisions",
        schema=schema,
    )
    op.drop_table("agent_draft_revisions", schema=schema)
    op.drop_table("agent_drafts", schema=schema)
    _contract_agents(schema)


def _extend_agents(schema: str) -> None:
    op.add_column(
        "agents",
        sa.Column("agent_kind", sa.String(16), nullable=False, server_default="system"),
        schema=schema,
    )
    op.add_column(
        "agents",
        sa.Column("description", sa.String(1000), nullable=True),
        schema=schema,
    )
    op.drop_constraint("ck_agents_status", "agents", schema=schema, type_="check")
    op.create_check_constraint(
        "ck_agents_kind",
        "agents",
        "agent_kind IN ('system', 'custom')",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_agents_kind_status",
        "agents",
        "(agent_kind = 'system' AND status IN ('active', 'disabled')) OR "
        "(agent_kind = 'custom' AND status IN ('active', 'archived'))",
        schema=schema,
    )


def _create_agent_drafts(schema: str) -> None:
    op.create_table(
        "agent_drafts",
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("updated_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("draft_id", "workspace_id", name="uq_agent_drafts_id_workspace"),
        sa.UniqueConstraint(
            "agent_id",
            "workspace_id",
            name="uq_agent_drafts_agent_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id", "workspace_id"],
            [f"{schema}.agents.agent_id", f"{schema}.agents.workspace_id"],
            name="fk_agent_drafts_agent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_agent_drafts_updater",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_agent_drafts_revision"),
        sa.CheckConstraint(
            f"status IN ({_quoted(DRAFT_STATUSES)})",
            name="ck_agent_drafts_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(configuration) = 'object'",
            name="ck_agent_drafts_configuration",
        ),
        sa.CheckConstraint(
            "config_hash ~ '^[0-9a-f]{64}$'",
            name="ck_agent_drafts_config_hash",
        ),
        schema=schema,
    )


def _create_agent_draft_revisions(schema: str) -> None:
    op.create_table(
        "agent_draft_revisions",
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("updated_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "draft_id",
            "revision",
            "agent_id",
            "workspace_id",
            name="uq_agent_draft_revisions_source",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id", "workspace_id"],
            [f"{schema}.agent_drafts.draft_id", f"{schema}.agent_drafts.workspace_id"],
            name="fk_agent_draft_revisions_draft",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id", "workspace_id"],
            [f"{schema}.agents.agent_id", f"{schema}.agents.workspace_id"],
            name="fk_agent_draft_revisions_agent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_agent_draft_revisions_updater",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_agent_draft_revisions_revision"),
        sa.CheckConstraint(
            f"status IN ({_quoted(DRAFT_STATUSES)})",
            name="ck_agent_draft_revisions_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(configuration) = 'object'",
            name="ck_agent_draft_revisions_configuration",
        ),
        sa.CheckConstraint(
            "config_hash ~ '^[0-9a-f]{64}$'",
            name="ck_agent_draft_revisions_config_hash",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_agent_draft_revisions_agent_revision",
        "agent_draft_revisions",
        ["workspace_id", "agent_id", "revision"],
        schema=schema,
    )


def _create_agent_release_candidates(schema: str) -> None:
    op.create_table(
        "agent_release_candidates",
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("candidate_hash", sa.String(64), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "candidate_id",
            "workspace_id",
            name="uq_agent_release_candidates_id_workspace",
        ),
        sa.UniqueConstraint(
            "draft_id",
            "draft_revision",
            name="uq_agent_release_candidates_draft_revision",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id", "draft_revision", "agent_id", "workspace_id"],
            [
                f"{schema}.agent_draft_revisions.draft_id",
                f"{schema}.agent_draft_revisions.revision",
                f"{schema}.agent_draft_revisions.agent_id",
                f"{schema}.agent_draft_revisions.workspace_id",
            ],
            name="fk_agent_release_candidates_revision",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id", "workspace_id"],
            [f"{schema}.agents.agent_id", f"{schema}.agents.workspace_id"],
            name="fk_agent_release_candidates_agent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_agent_release_candidates_creator",
        ),
        sa.CheckConstraint("draft_revision >= 1", name="ck_agent_release_candidates_revision"),
        sa.CheckConstraint(
            "candidate_hash ~ '^[0-9a-f]{64}$'",
            name="ck_agent_release_candidates_candidate_hash",
        ),
        sa.CheckConstraint(
            "config_hash ~ '^[0-9a-f]{64}$'",
            name="ck_agent_release_candidates_config_hash",
        ),
        sa.CheckConstraint(
            f"status IN ({_quoted(CANDIDATE_STATUSES)})",
            name="ck_agent_release_candidates_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_agent_release_candidates_version"),
        schema=schema,
    )
    op.create_index(
        "ix_agent_release_candidates_workspace_time",
        "agent_release_candidates",
        ["workspace_id", "created_at"],
        schema=schema,
    )


def _create_agent_control_requests(schema: str) -> None:
    op.create_table(
        "agent_control_requests",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result_type", sa.String(32), nullable=False),
        sa.Column("result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "actor_id",
            "operation",
            "idempotency_key",
            name="uq_agent_control_requests_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_agent_control_requests_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_agent_control_requests_actor",
        ),
        sa.CheckConstraint(
            "operation IN ('agent.create', 'agent.draft.update', "
            "'agent.release.request', 'agent.archive')",
            name="ck_agent_control_requests_operation",
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'",
            name="ck_agent_control_requests_idempotency",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_agent_control_requests_hash",
        ),
        sa.CheckConstraint(
            "result_type IN ('agent', 'draft', 'candidate')",
            name="ck_agent_control_requests_result_type",
        ),
        sa.CheckConstraint(
            "result_revision IS NULL OR result_revision >= 1",
            name="ck_agent_control_requests_result_revision",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_agent_control_requests_workspace_time",
        "agent_control_requests",
        ["workspace_id", "created_at"],
        schema=schema,
    )


def _extend_agent_releases(schema: str) -> None:
    op.add_column(
        "agent_releases",
        sa.Column("release_kind", sa.String(16), nullable=False, server_default="system"),
        schema=schema,
    )
    for column in (
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_hash", sa.String(64), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("snapshot_hash", sa.String(64), nullable=True),
    ):
        op.add_column("agent_releases", column, schema=schema)
    op.create_unique_constraint(
        "uq_agent_releases_candidate",
        "agent_releases",
        ["candidate_id"],
        schema=schema,
    )
    op.create_foreign_key(
        "fk_agent_releases_candidate",
        "agent_releases",
        "agent_release_candidates",
        ["candidate_id", "workspace_id"],
        ["candidate_id", "workspace_id"],
        source_schema=schema,
        referent_schema=schema,
    )
    op.create_check_constraint(
        "ck_agent_releases_kind",
        "agent_releases",
        "release_kind IN ('system', 'custom')",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_agent_releases_candidate_hash",
        "agent_releases",
        "candidate_hash IS NULL OR candidate_hash ~ '^[0-9a-f]{64}$'",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_agent_releases_snapshot_hash",
        "agent_releases",
        "snapshot_hash IS NULL OR snapshot_hash ~ '^[0-9a-f]{64}$'",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_agent_releases_kind_payload",
        "agent_releases",
        "(release_kind = 'system' AND candidate_id IS NULL AND candidate_hash IS NULL "
        "AND snapshot IS NULL AND snapshot_hash IS NULL) OR "
        "(release_kind = 'custom' AND candidate_id IS NOT NULL AND candidate_hash IS NOT NULL "
        "AND jsonb_typeof(snapshot) = 'object' AND snapshot_hash IS NOT NULL)",
        schema=schema,
    )


def _protect_agent_control_history(schema: str) -> None:
    # Revision、幂等请求和候选来源是审计证据；生命周期清除只在受限 GUC 事务放行 DELETE。
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".reject_agent_control_history_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'DELETE'
                   AND current_setting('ai_platform.lifecycle_purge', true) = 'on'
                THEN RETURN OLD;
                END IF;
                RAISE EXCEPTION 'agent control history is immutable' USING ERRCODE = '55000';
            END;
            $$
            """
        )
    )
    for table in ("agent_draft_revisions", "agent_control_requests"):
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_{table}_immutable
                BEFORE UPDATE OR DELETE ON "{schema}"."{table}"
                FOR EACH ROW EXECUTE FUNCTION "{schema}".reject_agent_control_history_mutation()
                """
            )
        )

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".protect_agent_candidate_identity()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    IF current_setting('ai_platform.lifecycle_purge', true) = 'on'
                    THEN RETURN OLD;
                    END IF;
                    RAISE EXCEPTION 'agent release candidates cannot be deleted'
                        USING ERRCODE = '55000';
                END IF;
                IF ROW(
                    NEW.candidate_id, NEW.agent_id, NEW.draft_id, NEW.workspace_id,
                    NEW.draft_revision, NEW.candidate_hash, NEW.config_hash,
                    NEW.created_by_account_id, NEW.created_at
                ) IS DISTINCT FROM ROW(
                    OLD.candidate_id, OLD.agent_id, OLD.draft_id, OLD.workspace_id,
                    OLD.draft_revision, OLD.candidate_hash, OLD.config_hash,
                    OLD.created_by_account_id, OLD.created_at
                ) THEN
                    RAISE EXCEPTION 'agent release candidate identity is immutable'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_agent_release_candidates_identity
            BEFORE UPDATE OR DELETE ON "{schema}"."agent_release_candidates"
            FOR EACH ROW EXECUTE FUNCTION "{schema}".protect_agent_candidate_identity()
            """
        )
    )


def _drop_agent_control_history_protection(schema: str) -> None:
    op.execute(
        sa.text(
            f"DROP TRIGGER trg_agent_release_candidates_identity "
            f'ON "{schema}"."agent_release_candidates"'
        )
    )
    for table in ("agent_control_requests", "agent_draft_revisions"):
        op.execute(sa.text(f'DROP TRIGGER trg_{table}_immutable ON "{schema}"."{table}"'))
    op.execute(sa.text(f'DROP FUNCTION "{schema}".protect_agent_candidate_identity()'))
    op.execute(sa.text(f'DROP FUNCTION "{schema}".reject_agent_control_history_mutation()'))


def _contract_agent_releases(schema: str) -> None:
    for constraint, constraint_type in (
        ("ck_agent_releases_kind_payload", "check"),
        ("ck_agent_releases_snapshot_hash", "check"),
        ("ck_agent_releases_candidate_hash", "check"),
        ("ck_agent_releases_kind", "check"),
        ("fk_agent_releases_candidate", "foreignkey"),
        ("uq_agent_releases_candidate", "unique"),
    ):
        op.drop_constraint(
            constraint,
            "agent_releases",
            schema=schema,
            type_=constraint_type,
        )
    for column in ("snapshot_hash", "snapshot", "candidate_hash", "candidate_id", "release_kind"):
        op.drop_column("agent_releases", column, schema=schema)


def _contract_agents(schema: str) -> None:
    op.drop_constraint("ck_agents_kind_status", "agents", schema=schema, type_="check")
    op.drop_constraint("ck_agents_kind", "agents", schema=schema, type_="check")
    op.drop_column("agents", "description", schema=schema)
    op.drop_column("agents", "agent_kind", schema=schema)
    op.create_check_constraint(
        "ck_agents_status",
        "agents",
        "status IN ('active', 'disabled')",
        schema=schema,
    )


def _reject_unsafe_downgrade(schema: str) -> None:
    count = op.get_bind().scalar(
        sa.text(f"SELECT count(*) FROM \"{schema}\".agents WHERE agent_kind = 'custom'")
    )
    if count:
        raise RuntimeError("检测到自定义 Agent 数据, 拒绝降级以避免丢失草稿和候选事实")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)
