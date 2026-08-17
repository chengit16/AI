"""建立 P5-08 L3 隔离迁移、验证和恢复事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "20260817_0067"
down_revision: str | None = "20260817_0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    return context.get_context().version_table_schema or "public"


def upgrade() -> None:
    schema = _schema()
    _create_profiles(schema)
    _create_checkpoints(schema)
    _create_recovery_records(schema)
    _create_guards(schema)


def downgrade() -> None:
    schema = _schema()
    _restore_p507_route_guard(schema)
    op.execute(
        sa.text(
            f"DROP TRIGGER IF EXISTS trg_l3_isolation_recovery_records_insert_guard "
            f'ON "{schema}".l3_isolation_recovery_records'
        )
    )
    op.execute(
        sa.text(
            f"DROP TRIGGER IF EXISTS trg_l3_isolation_recovery_records_guard "
            f'ON "{schema}".l3_isolation_recovery_records'
        )
    )
    op.execute(
        sa.text(
            f"DROP TRIGGER IF EXISTS trg_l3_isolation_checkpoints_guard "
            f'ON "{schema}".l3_isolation_migration_checkpoints'
        )
    )
    op.execute(
        sa.text(
            f"DROP TRIGGER IF EXISTS trg_l3_isolation_resource_profiles_guard "
            f'ON "{schema}".l3_isolation_resource_profiles'
        )
    )
    op.execute(
        sa.text(
            f"DROP TRIGGER IF EXISTS trg_l3_isolation_resource_profiles_insert_guard "
            f'ON "{schema}".l3_isolation_resource_profiles'
        )
    )
    op.execute(sa.text(f'DROP FUNCTION IF EXISTS "{schema}".guard_l3_recovery_insert()'))
    op.execute(sa.text(f'DROP FUNCTION IF EXISTS "{schema}".guard_l3_profile_insert()'))
    op.drop_table("l3_isolation_recovery_records", schema=schema)
    op.drop_table("l3_isolation_migration_checkpoints", schema=schema)
    op.drop_table("l3_isolation_resource_profiles", schema=schema)


def _create_profiles(schema: str) -> None:
    op.create_table(
        "l3_isolation_resource_profiles",
        sa.Column("resource_profile_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("migration_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("database_route_key", sa.String(128), nullable=False, unique=True),
        sa.Column("object_storage_route_key", sa.String(128), nullable=False, unique=True),
        sa.Column("encryption_key_route_key", sa.String(128), nullable=False, unique=True),
        sa.Column("search_namespace", sa.String(128), nullable=False),
        sa.Column("database_identity_digest", sa.String(64), nullable=False),
        sa.Column("object_storage_identity_digest", sa.String(64), nullable=False),
        sa.Column("encryption_key_fingerprint", sa.String(64), nullable=False),
        sa.Column("configuration_digest", sa.String(64), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "resource_profile_id",
            "workspace_id",
            "migration_plan_id",
            name="uq_l3_isolation_profiles_identity",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "migration_plan_id",
            name="uq_l3_isolation_profiles_plan",
        ),
        sa.ForeignKeyConstraint(
            ["migration_plan_id", "workspace_id"],
            [
                f"{schema}.workspace_isolation_migration_plans.migration_plan_id",
                f"{schema}.workspace_isolation_migration_plans.workspace_id",
            ],
            name="fk_l3_isolation_profiles_migration",
        ),
        sa.CheckConstraint(
            "database_route_key = 'database.l3.' || replace(workspace_id::text, '-', '') "
            "AND object_storage_route_key = 'objects.l3.' || replace(workspace_id::text, '-', '') "
            "AND encryption_key_route_key = 'key.l3.' || replace(workspace_id::text, '-', '') "
            "AND search_namespace = 'workspace.' || replace(workspace_id::text, '-', '')",
            name="ck_l3_isolation_profiles_routes",
        ),
        sa.CheckConstraint(
            "database_identity_digest ~ '^[0-9a-f]{64}$' "
            "AND object_storage_identity_digest ~ '^[0-9a-f]{64}$' "
            "AND encryption_key_fingerprint ~ '^[0-9a-f]{64}$' "
            "AND configuration_digest ~ '^[0-9a-f]{64}$'",
            name="ck_l3_isolation_profiles_digests",
        ),
        schema=schema,
    )


def _create_checkpoints(schema: str) -> None:
    op.create_table(
        "l3_isolation_migration_checkpoints",
        sa.Column("checkpoint_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("resource_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("migration_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("checkpoint_type", sa.String(32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("target_digest", sa.String(64), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "migration_plan_id",
            "checkpoint_type",
            name="uq_l3_isolation_checkpoints_type",
        ),
        sa.UniqueConstraint(
            "migration_plan_id",
            "position",
            name="uq_l3_isolation_checkpoints_position",
        ),
        sa.ForeignKeyConstraint(
            ["resource_profile_id", "workspace_id", "migration_plan_id"],
            [
                f"{schema}.l3_isolation_resource_profiles.resource_profile_id",
                f"{schema}.l3_isolation_resource_profiles.workspace_id",
                f"{schema}.l3_isolation_resource_profiles.migration_plan_id",
            ],
            name="fk_l3_isolation_checkpoints_profile",
        ),
        sa.CheckConstraint(
            "(checkpoint_type = 'source_snapshot' AND position = 1) "
            "OR (checkpoint_type = 'database_copy' AND position = 2) "
            "OR (checkpoint_type = 'object_copy' AND position = 3) "
            "OR (checkpoint_type = 'derived_index_rebuild' AND position = 4) "
            "OR (checkpoint_type = 'backup_restore' AND position = 5) "
            "OR (checkpoint_type = 'deletion_propagation' AND position = 6)",
            name="ck_l3_isolation_checkpoints_order",
        ),
        sa.CheckConstraint(
            "status IN ('passed', 'failed') AND item_count >= 0",
            name="ck_l3_isolation_checkpoints_status",
        ),
        sa.CheckConstraint(
            "source_digest ~ '^[0-9a-f]{64}$' "
            "AND target_digest ~ '^[0-9a-f]{64}$' "
            "AND evidence_digest ~ '^[0-9a-f]{64}$'",
            name="ck_l3_isolation_checkpoints_digests",
        ),
        schema=schema,
    )


def _create_recovery_records(schema: str) -> None:
    op.create_table(
        "l3_isolation_recovery_records",
        sa.Column("recovery_record_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("migration_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source_is_authoritative", sa.Boolean(), nullable=False),
        sa.Column("target_writes_enabled", sa.Boolean(), nullable=False),
        sa.Column("cleanup_digest", sa.String(64), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("recovered_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "migration_plan_id",
            "attempt_no",
            name="uq_l3_isolation_recovery_attempt",
        ),
        sa.ForeignKeyConstraint(
            ["migration_plan_id", "workspace_id"],
            [
                f"{schema}.workspace_isolation_migration_plans.migration_plan_id",
                f"{schema}.workspace_isolation_migration_plans.workspace_id",
            ],
            name="fk_l3_isolation_recovery_migration",
        ),
        sa.CheckConstraint(
            "attempt_no >= 1 AND status IN ('passed', 'failed')",
            name="ck_l3_isolation_recovery_status",
        ),
        sa.CheckConstraint(
            "cleanup_digest ~ '^[0-9a-f]{64}$' AND evidence_digest ~ '^[0-9a-f]{64}$'",
            name="ck_l3_isolation_recovery_digests",
        ),
        sa.CheckConstraint(
            "status <> 'passed' OR (source_is_authoritative AND NOT target_writes_enabled)",
            name="ck_l3_isolation_recovery_single_writer",
        ),
        schema=schema,
    )


def _create_guards(schema: str) -> None:
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".guard_l3_profile_insert()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM "{schema}".workspace_isolation_migration_plans plan
                    WHERE plan.migration_plan_id = NEW.migration_plan_id
                      AND plan.workspace_id = NEW.workspace_id
                      AND plan.target_level = 'L3'
                      AND plan.status = 'executing'
                ) THEN
                    RAISE EXCEPTION 'L3 资源档案只能绑定执行中的 L3 迁移'
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
            CREATE FUNCTION "{schema}".guard_l3_recovery_insert()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM "{schema}".workspace_isolation_migration_plans plan
                    WHERE plan.migration_plan_id = NEW.migration_plan_id
                      AND plan.workspace_id = NEW.workspace_id
                      AND plan.target_level = 'L3'
                      AND plan.status = 'rollback_required'
                ) THEN
                    RAISE EXCEPTION 'L3 恢复事实只能绑定待回滚迁移'
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
            CREATE OR REPLACE FUNCTION "{schema}".guard_isolation_route_insert()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            BEGIN
                IF NEW.isolation_level = 'L4' THEN
                    RAISE EXCEPTION 'P5-10 完成前不得激活 L4 路由'
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
                IF NEW.isolation_level = 'L3' AND NOT EXISTS (
                    SELECT 1
                    FROM "{schema}".l3_isolation_resource_profiles profile
                    WHERE profile.workspace_id = NEW.workspace_id
                      AND profile.migration_plan_id = NEW.migration_plan_id
                      AND profile.database_route_key = NEW.database_route_key
                      AND profile.object_storage_route_key = NEW.object_storage_route_key
                      AND profile.encryption_key_route_key = NEW.encryption_key_route_key
                      AND profile.search_namespace = NEW.search_namespace
                      AND (
                          SELECT count(*) = 6
                             AND bool_and(checkpoint.status = 'passed')
                             AND bool_and(checkpoint.source_digest = checkpoint.target_digest)
                          FROM "{schema}".l3_isolation_migration_checkpoints checkpoint
                          WHERE checkpoint.workspace_id = profile.workspace_id
                            AND checkpoint.migration_plan_id = profile.migration_plan_id
                      )
                ) THEN
                    RAISE EXCEPTION 'L3 路由缺少匹配资源档案或完整验证检查点'
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
            CREATE TRIGGER trg_l3_isolation_resource_profiles_insert_guard
            BEFORE INSERT ON "{schema}".l3_isolation_resource_profiles
            FOR EACH ROW EXECUTE FUNCTION "{schema}".guard_l3_profile_insert()
            """
        )
    )
    for table, trigger in (
        ("l3_isolation_resource_profiles", "trg_l3_isolation_resource_profiles_guard"),
        ("l3_isolation_migration_checkpoints", "trg_l3_isolation_checkpoints_guard"),
        ("l3_isolation_recovery_records", "trg_l3_isolation_recovery_records_guard"),
    ):
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER {trigger}
                BEFORE UPDATE OR DELETE ON "{schema}".{table}
                FOR EACH ROW EXECUTE FUNCTION "{schema}".protect_isolation_immutable_fact()
                """
            )
        )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_l3_isolation_recovery_records_insert_guard
            BEFORE INSERT ON "{schema}".l3_isolation_recovery_records
            FOR EACH ROW EXECUTE FUNCTION "{schema}".guard_l3_recovery_insert()
            """
        )
    )


def _restore_p507_route_guard(schema: str) -> None:
    op.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION "{schema}".guard_isolation_route_insert()
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
