"""建立 P5-07 版本化隔离策略、迁移计划和路由控制面。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260817_0066"
down_revision: str | None = "20260817_0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """创建三张控制面表，并在数据库强制迁移状态和路由激活顺序。"""

    schema = _schema()
    _create_policies(schema)
    _create_migrations(schema)
    _create_routes(schema)
    _create_guards(schema)


def downgrade() -> None:
    """存在任何隔离治理事实时拒绝破坏性降级。"""

    schema = _schema()
    connection = op.get_bind()
    for table_name in _table_names():
        count = connection.scalar(sa.text(f'SELECT count(*) FROM "{schema}"."{table_name}"'))
        if int(count or 0) > 0:
            raise RuntimeError("存在工作空间隔离治理事实, 拒绝破坏性降级")
    for table_name in _table_names():
        op.execute(
            sa.text(f'DROP TRIGGER IF EXISTS trg_{table_name}_guard ON "{schema}"."{table_name}"')
        )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_workspace_isolation_route_versions_immutable_guard "
            f'ON "{schema}".workspace_isolation_route_versions'
        )
    )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_workspace_isolation_migration_plans_delete_guard "
            f'ON "{schema}".workspace_isolation_migration_plans'
        )
    )
    op.execute(sa.text(f'DROP FUNCTION IF EXISTS "{schema}".guard_isolation_route_insert()'))
    op.execute(sa.text(f'DROP FUNCTION IF EXISTS "{schema}".guard_isolation_migration_update()'))
    op.execute(sa.text(f'DROP FUNCTION IF EXISTS "{schema}".protect_isolation_immutable_fact()'))
    for table_name in reversed(_table_names()):
        op.drop_table(table_name, schema=schema)


def _create_policies(schema: str) -> None:
    op.create_table(
        "workspace_isolation_policy_versions",
        sa.Column("isolation_policy_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("requested_level", sa.String(2), nullable=False),
        sa.Column("current_level", sa.String(2), nullable=False),
        sa.Column("maximum_eligible_level", sa.String(2), nullable=False),
        sa.Column("plan_code", sa.String(64), nullable=False),
        sa.Column("entitlement_version", sa.Integer(), nullable=False),
        sa.Column("compliance_status", sa.String(24), nullable=False),
        sa.Column("compliance_policy_digest", sa.String(64), nullable=True),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("reason_codes", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("decision_digest", sa.String(64), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "isolation_policy_id",
            "workspace_id",
            name="uq_workspace_isolation_policies_id_workspace",
        ),
        sa.UniqueConstraint(
            "isolation_policy_id",
            "workspace_id",
            "requested_level",
            name="uq_workspace_isolation_policies_plan_target",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "policy_version",
            name="uq_workspace_isolation_policies_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_workspace_isolation_policies_workspace",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "requested_level IN ('L1', 'L2', 'L3', 'L4') "
            "AND current_level IN ('L1', 'L2', 'L3', 'L4') "
            "AND maximum_eligible_level IN ('L1', 'L2', 'L3', 'L4')",
            name="ck_workspace_isolation_policies_levels",
        ),
        sa.CheckConstraint(
            "(current_level = 'L1' AND requested_level IN ('L2', 'L3', 'L4')) "
            "OR (current_level = 'L2' AND requested_level IN ('L3', 'L4')) "
            "OR (current_level = 'L3' AND requested_level = 'L4')",
            name="ck_workspace_isolation_policies_upgrade",
        ),
        sa.CheckConstraint(
            "plan_code ~ '^[a-z][a-z0-9_]{2,63}$' AND entitlement_version >= 1 "
            "AND policy_version >= 1",
            name="ck_workspace_isolation_policies_versions",
        ),
        sa.CheckConstraint(
            "compliance_status IN ('not_required', 'not_configured', 'approved', 'rejected') "
            "AND decision IN ('allowed', 'denied', 'not_configured')",
            name="ck_workspace_isolation_policies_decision",
        ),
        sa.CheckConstraint(
            "decision_digest ~ '^[0-9a-f]{64}$' "
            "AND (compliance_policy_digest IS NULL OR "
            "compliance_policy_digest ~ '^[0-9a-f]{64}$')",
            name="ck_workspace_isolation_policies_digests",
        ),
        sa.CheckConstraint(
            "(compliance_status IN ('not_required', 'not_configured') "
            "AND compliance_policy_digest IS NULL) "
            "OR (compliance_status IN ('approved', 'rejected') "
            "AND compliance_policy_digest IS NOT NULL)",
            name="ck_workspace_isolation_policies_compliance",
        ),
        sa.CheckConstraint(
            "cardinality(reason_codes) <= 16 AND array_position(reason_codes, NULL) IS NULL "
            "AND reason_codes <@ ARRAY['workspace_inactive', 'personal_workspace_l1_only', "
            "'plan_not_supported', 'plan_not_eligible', 'compliance_policy_not_configured', "
            "'compliance_policy_rejected']::varchar[] "
            "AND ((decision = 'allowed' AND cardinality(reason_codes) = 0) "
            "OR (decision <> 'allowed' AND cardinality(reason_codes) > 0))",
            name="ck_workspace_isolation_policies_reasons",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_workspace_isolation_policies_workspace_created",
        "workspace_isolation_policy_versions",
        ["workspace_id", "created_at"],
        schema=schema,
    )


def _create_migrations(schema: str) -> None:
    op.create_table(
        "workspace_isolation_migration_plans",
        sa.Column("migration_plan_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("isolation_policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_level", sa.String(2), nullable=False),
        sa.Column("target_level", sa.String(2), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("route_requirement_digest", sa.String(64), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "migration_plan_id",
            "workspace_id",
            name="uq_workspace_isolation_migrations_id_workspace",
        ),
        sa.UniqueConstraint(
            "migration_plan_id",
            "workspace_id",
            "target_level",
            name="uq_workspace_isolation_migrations_route_target",
        ),
        sa.ForeignKeyConstraint(
            ["isolation_policy_id", "workspace_id", "target_level"],
            [
                f"{schema}.workspace_isolation_policy_versions.isolation_policy_id",
                f"{schema}.workspace_isolation_policy_versions.workspace_id",
                f"{schema}.workspace_isolation_policy_versions.requested_level",
            ],
            name="fk_workspace_isolation_migrations_policy",
        ),
        sa.CheckConstraint(
            "from_level IN ('L1', 'L2', 'L3') AND target_level IN ('L2', 'L3', 'L4')",
            name="ck_workspace_isolation_migrations_levels",
        ),
        sa.CheckConstraint(
            "(from_level = 'L1' AND target_level IN ('L2', 'L3', 'L4')) "
            "OR (from_level = 'L2' AND target_level IN ('L3', 'L4')) "
            "OR (from_level = 'L3' AND target_level = 'L4')",
            name="ck_workspace_isolation_migrations_upgrade",
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'approved', 'executing', 'verifying', 'switch_ready', "
            "'completed', 'rollback_required', 'rolled_back', 'cancelled')",
            name="ck_workspace_isolation_migrations_status",
        ),
        sa.CheckConstraint(
            "route_requirement_digest ~ '^[0-9a-f]{64}$' AND version >= 1 "
            "AND updated_at >= created_at",
            name="ck_workspace_isolation_migrations_identity",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_workspace_isolation_migrations_workspace_updated",
        "workspace_isolation_migration_plans",
        ["workspace_id", "updated_at"],
        schema=schema,
    )
    op.create_index(
        "uq_workspace_isolation_migrations_active",
        "workspace_isolation_migration_plans",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('planned', 'approved', 'executing', 'verifying', 'switch_ready', "
            "'rollback_required')"
        ),
        schema=schema,
    )


def _create_routes(schema: str) -> None:
    op.create_table(
        "workspace_isolation_route_versions",
        sa.Column("route_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("route_version", sa.Integer(), nullable=False),
        sa.Column("isolation_level", sa.String(2), nullable=False),
        sa.Column("database_route_key", sa.String(128), nullable=False),
        sa.Column("object_storage_route_key", sa.String(128), nullable=False),
        sa.Column("encryption_key_route_key", sa.String(128), nullable=False),
        sa.Column("search_namespace", sa.String(128), nullable=False),
        sa.Column("deployment_route_key", sa.String(128), nullable=False),
        sa.Column("migration_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("route_digest", sa.String(64), nullable=False),
        sa.Column("activated_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "route_id",
            "workspace_id",
            name="uq_workspace_isolation_routes_id_workspace",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "route_version",
            name="uq_workspace_isolation_routes_version",
        ),
        sa.ForeignKeyConstraint(
            ["migration_plan_id", "workspace_id", "isolation_level"],
            [
                f"{schema}.workspace_isolation_migration_plans.migration_plan_id",
                f"{schema}.workspace_isolation_migration_plans.workspace_id",
                f"{schema}.workspace_isolation_migration_plans.target_level",
            ],
            name="fk_workspace_isolation_routes_migration",
        ),
        sa.CheckConstraint(
            "isolation_level IN ('L2', 'L3', 'L4') AND route_version >= 1",
            name="ck_workspace_isolation_routes_level",
        ),
        sa.CheckConstraint(
            "database_route_key ~ '^[a-z][a-z0-9._:-]{2,127}$' "
            "AND object_storage_route_key ~ '^[a-z][a-z0-9._:-]{2,127}$' "
            "AND encryption_key_route_key ~ '^[a-z][a-z0-9._:-]{2,127}$' "
            "AND search_namespace ~ '^[a-z][a-z0-9._:-]{2,127}$' "
            "AND deployment_route_key ~ '^[a-z][a-z0-9._:-]{2,127}$'",
            name="ck_workspace_isolation_routes_keys",
        ),
        sa.CheckConstraint(
            "(isolation_level = 'L2' "
            "AND database_route_key = 'shared.primary' "
            "AND object_storage_route_key = 'shared.objects' "
            "AND encryption_key_route_key = 'shared.workspace' "
            "AND deployment_route_key = 'shared.runtime' "
            "AND search_namespace = "
            "'workspace.' || replace(workspace_id::text, '-', '')) "
            "OR isolation_level IN ('L3', 'L4')",
            name="ck_workspace_isolation_routes_l2",
        ),
        sa.CheckConstraint(
            "route_digest ~ '^[0-9a-f]{64}$'",
            name="ck_workspace_isolation_routes_digest",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_workspace_isolation_routes_workspace_activated",
        "workspace_isolation_route_versions",
        ["workspace_id", "activated_at"],
        schema=schema,
    )


def _create_guards(schema: str) -> None:
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".protect_isolation_immutable_fact()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            BEGIN
                RAISE EXCEPTION '工作空间隔离策略和路由事实不可修改或删除'
                    USING ERRCODE = '23000';
            END;
            $function$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".guard_isolation_migration_update()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            BEGIN
                IF NEW.workspace_id <> OLD.workspace_id
                   OR NEW.isolation_policy_id <> OLD.isolation_policy_id
                   OR NEW.from_level <> OLD.from_level
                   OR NEW.target_level <> OLD.target_level
                   OR NEW.route_requirement_digest <> OLD.route_requirement_digest
                   OR NEW.created_by_actor_id <> OLD.created_by_actor_id
                   OR NEW.created_at <> OLD.created_at
                   OR NEW.version <> OLD.version + 1
                   OR NEW.updated_at < OLD.updated_at THEN
                    RAISE EXCEPTION '工作空间隔离迁移身份或版本漂移'
                        USING ERRCODE = '23000';
                END IF;
                IF NOT (
                    (OLD.status = 'planned' AND NEW.status IN ('approved', 'cancelled'))
                    OR (OLD.status = 'approved' AND NEW.status IN ('executing', 'cancelled'))
                    OR (
                        OLD.status = 'executing'
                        AND NEW.status IN ('verifying', 'rollback_required')
                    )
                    OR (
                        OLD.status = 'verifying'
                        AND NEW.status IN ('switch_ready', 'rollback_required')
                    )
                    OR (
                        OLD.status = 'switch_ready'
                        AND NEW.status IN ('completed', 'rollback_required')
                    )
                    OR (OLD.status = 'rollback_required' AND NEW.status = 'rolled_back')
                ) THEN
                    RAISE EXCEPTION '工作空间隔离迁移状态转换非法'
                        USING ERRCODE = '23000';
                END IF;
                RETURN NEW;
            END;
            $function$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".guard_isolation_route_insert()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            BEGIN
                IF NEW.isolation_level IN ('L3', 'L4') THEN
                    RAISE EXCEPTION 'P5-08 或 P5-10 完成前不得激活 L3/L4 路由'
                        USING ERRCODE = '23000';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                    FROM "{schema}".workspace_isolation_migration_plans plan
                    JOIN "{schema}".workspace_isolation_policy_versions policy
                      ON policy.isolation_policy_id = plan.isolation_policy_id
                     AND policy.workspace_id = plan.workspace_id
                    WHERE plan.migration_plan_id = NEW.migration_plan_id
                      AND plan.workspace_id = NEW.workspace_id
                      AND plan.target_level = NEW.isolation_level
                      AND plan.status = 'switch_ready'
                      AND policy.decision = 'allowed'
                ) THEN
                    RAISE EXCEPTION '未批准或未就绪的隔离迁移不得激活路由'
                        USING ERRCODE = '23000';
                END IF;
                RETURN NEW;
            END;
            $function$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_workspace_isolation_policy_versions_guard
            BEFORE UPDATE OR DELETE ON "{schema}".workspace_isolation_policy_versions
            FOR EACH ROW EXECUTE FUNCTION "{schema}".protect_isolation_immutable_fact()
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_workspace_isolation_migration_plans_guard
            BEFORE UPDATE ON "{schema}".workspace_isolation_migration_plans
            FOR EACH ROW EXECUTE FUNCTION "{schema}".guard_isolation_migration_update()
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_workspace_isolation_migration_plans_delete_guard
            BEFORE DELETE ON "{schema}".workspace_isolation_migration_plans
            FOR EACH ROW EXECUTE FUNCTION "{schema}".protect_isolation_immutable_fact()
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_workspace_isolation_route_versions_guard
            BEFORE INSERT ON "{schema}".workspace_isolation_route_versions
            FOR EACH ROW EXECUTE FUNCTION "{schema}".guard_isolation_route_insert()
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_workspace_isolation_route_versions_immutable_guard
            BEFORE UPDATE OR DELETE ON "{schema}".workspace_isolation_route_versions
            FOR EACH ROW EXECUTE FUNCTION "{schema}".protect_isolation_immutable_fact()
            """
        )
    )


def _table_names() -> tuple[str, ...]:
    return (
        "workspace_isolation_policy_versions",
        "workspace_isolation_migration_plans",
        "workspace_isolation_route_versions",
    )
