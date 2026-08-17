"""建立 P5-09 法规策略、法律保留、解除和合规证明。"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260817_0068"
down_revision: str | None = "20260817_0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COMPLIANCE_PERMISSIONS = (
    "workspace.lifecycle.regulatory_policy.manage",
    "workspace.lifecycle.legal_hold.create",
    "workspace.lifecycle.legal_hold.release",
    "workspace.lifecycle.compliance_proof.read",
)
STATUS_MENU_ID = "82000000-0000-4000-8000-000000000005"
COMPLIANCE_BINDINGS = (
    ("82000000-0000-4000-8000-000000000228", "81000000-0000-4000-8000-000000000148", "publish"),
    ("82000000-0000-4000-8000-000000000229", "81000000-0000-4000-8000-000000000149", "mutation"),
    ("82000000-0000-4000-8000-000000000230", "81000000-0000-4000-8000-000000000150", "approve"),
    ("82000000-0000-4000-8000-000000000231", "81000000-0000-4000-8000-000000000151", "query"),
)
REASON_CODES_SQL = (
    "'authorized_export_preserved', 'jurisdiction_not_configured', "
    "'regulatory_policy_configured', 'active_legal_hold', "
    "'external_review_not_configured', 'external_review_rejected'"
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """建立四张治理表，并把裁决证明接入既有生命周期操作。"""

    schema = _schema()
    _create_regulatory_policies(schema)
    _create_legal_holds(schema)
    _create_legal_hold_releases(schema)
    _create_compliance_proofs(schema)
    _extend_lifecycle_operations(schema)
    _create_guards(schema)
    _grant_permissions(schema)
    _register_compliance_bindings(schema)
    _publish_upgraded_menu_snapshots(schema)


def downgrade() -> None:
    """存在法规治理事实时拒绝破坏性降级。"""

    schema = _schema()
    connection = op.get_bind()
    for table_name in _table_names():
        count = connection.scalar(sa.text(f'SELECT count(*) FROM "{schema}"."{table_name}"'))
        if int(count or 0) > 0:
            raise RuntimeError("存在法规策略、法律保留或合规证明事实, 拒绝破坏性降级")
    _reject_custom_grants(schema)
    _restore_menu_snapshots(schema)
    _remove_compliance_bindings(schema)
    for table_name in (
        "lifecycle_export_records",
        "lifecycle_purge_requests",
        "lifecycle_retention_runs",
    ):
        op.execute(
            sa.text(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_compliance_guard "
                f'ON "{schema}"."{table_name}"'
            )
        )
    op.execute(
        sa.text(
            f"DROP TRIGGER IF EXISTS trg_lifecycle_legal_holds_insert_guard "
            f'ON "{schema}".lifecycle_legal_holds'
        )
    )
    op.execute(
        sa.text(
            f"DROP TRIGGER IF EXISTS trg_lifecycle_legal_hold_releases_insert_guard "
            f'ON "{schema}".lifecycle_legal_hold_releases'
        )
    )
    for table_name in _table_names():
        op.execute(
            sa.text(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable_guard "
                f'ON "{schema}"."{table_name}"'
            )
        )
    op.execute(sa.text(f'DROP FUNCTION IF EXISTS "{schema}".guard_lifecycle_operation()'))
    op.execute(sa.text(f'DROP FUNCTION IF EXISTS "{schema}".guard_legal_hold_release()'))
    op.execute(sa.text(f'DROP FUNCTION IF EXISTS "{schema}".guard_legal_hold_activation()'))
    op.execute(sa.text(f'DROP FUNCTION IF EXISTS "{schema}".protect_compliance_fact()'))
    for table_name, constraint in (
        ("lifecycle_export_records", "fk_lifecycle_exports_compliance_proof"),
        ("lifecycle_purge_requests", "fk_lifecycle_purges_compliance_proof"),
        ("lifecycle_retention_runs", "fk_lifecycle_retention_policy"),
        ("lifecycle_retention_runs", "fk_lifecycle_retention_compliance_proof"),
    ):
        op.drop_constraint(constraint, table_name, schema=schema, type_="foreignkey")
    op.drop_column("lifecycle_export_records", "compliance_proof_id", schema=schema)
    op.drop_column("lifecycle_purge_requests", "compliance_proof_id", schema=schema)
    for column in ("compliance_proof_id", "regulatory_policy_id", "request_hash"):
        op.drop_column("lifecycle_retention_runs", column, schema=schema)
    for table_name in reversed(_table_names()):
        op.drop_table(table_name, schema=schema)
    permissions = ", ".join(f"'{code}'" for code in COMPLIANCE_PERMISSIONS)
    op.execute(
        sa.text(
            f'DELETE FROM "{schema}".role_permission_grants '
            f"WHERE permission_code IN ({permissions})"
        )
    )


def _create_regulatory_policies(schema: str) -> None:
    op.create_table(
        "lifecycle_regulatory_policy_versions",
        sa.Column("regulatory_policy_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("jurisdiction_status", sa.String(24), nullable=False),
        sa.Column("jurisdiction_codes", postgresql.ARRAY(sa.String(32)), nullable=False),
        sa.Column("retention_period_days", postgresql.JSONB(), nullable=False),
        sa.Column("external_review_status", sa.String(24), nullable=False),
        sa.Column("external_review_digest", sa.String(64), nullable=True),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "regulatory_policy_id",
            "workspace_id",
            name="uq_lifecycle_regulatory_policies_id_workspace",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "policy_version",
            name="uq_lifecycle_regulatory_policies_version",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_lifecycle_regulatory_policies_workspace",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "policy_version >= 1 AND policy_digest ~ '^[0-9a-f]{64}$'",
            name="ck_lifecycle_regulatory_policies_identity",
        ),
        sa.CheckConstraint(
            "jurisdiction_status IN ('not_configured', 'configured') "
            "AND external_review_status IN ('not_configured', 'approved', 'rejected')",
            name="ck_lifecycle_regulatory_policies_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(retention_period_days) = 'object' "
            "AND ((jurisdiction_status = 'not_configured' "
            "AND cardinality(jurisdiction_codes) = 0 "
            "AND retention_period_days = '{}'::jsonb "
            "AND external_review_status = 'not_configured' "
            "AND external_review_digest IS NULL) OR "
            "(jurisdiction_status = 'configured' "
            "AND cardinality(jurisdiction_codes) >= 1 "
            "AND retention_period_days ?& ARRAY['stream_events', 'published_outbox', "
            "'attempts_and_dead_letters', 'minimum_records'] "
            "AND (retention_period_days - ARRAY['stream_events', 'published_outbox', "
            "'attempts_and_dead_letters', 'minimum_records']::text[]) = '{}'::jsonb "
            "AND (retention_period_days->>'stream_events') ~ '^[0-9]+$' "
            "AND (retention_period_days->>'stream_events')::integer BETWEEN 1 AND 3650 "
            "AND (retention_period_days->>'published_outbox') ~ '^[0-9]+$' "
            "AND (retention_period_days->>'published_outbox')::integer BETWEEN 1 AND 3650 "
            "AND (retention_period_days->>'attempts_and_dead_letters') ~ '^[0-9]+$' "
            "AND (retention_period_days->>'attempts_and_dead_letters')::integer "
            "BETWEEN 1 AND 3650 "
            "AND (retention_period_days->>'minimum_records') ~ '^[0-9]+$' "
            "AND (retention_period_days->>'minimum_records')::integer BETWEEN 1 AND 3650 "
            "AND ((external_review_status = 'not_configured' "
            "AND external_review_digest IS NULL) OR "
            "(external_review_status IN ('approved', 'rejected') "
            "AND external_review_digest ~ '^[0-9a-f]{64}$'))))",
            name="ck_lifecycle_regulatory_policies_configuration",
        ),
        sa.CheckConstraint(
            "array_position(jurisdiction_codes, NULL) IS NULL",
            name="ck_lifecycle_regulatory_policies_jurisdictions",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_lifecycle_regulatory_policies_workspace_created",
        "lifecycle_regulatory_policy_versions",
        ["workspace_id", "created_at"],
        schema=schema,
    )


def _create_legal_holds(schema: str) -> None:
    op.create_table(
        "lifecycle_legal_holds",
        sa.Column("legal_hold_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("regulatory_policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("case_reference_digest", sa.String(64), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("activated_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "legal_hold_id",
            "workspace_id",
            name="uq_lifecycle_legal_holds_id_workspace",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_lifecycle_legal_holds_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["regulatory_policy_id", "workspace_id"],
            [
                f"{schema}.lifecycle_regulatory_policy_versions.regulatory_policy_id",
                f"{schema}.lifecycle_regulatory_policy_versions.workspace_id",
            ],
            name="fk_lifecycle_legal_holds_policy",
        ),
        sa.CheckConstraint(
            "scope_type = 'workspace' AND request_hash ~ '^[0-9a-f]{64}$' "
            "AND scope_digest ~ '^[0-9a-f]{64}$' "
            "AND case_reference_digest ~ '^[0-9a-f]{64}$' "
            "AND reason_code ~ '^[A-Z][A-Z0-9_]{2,63}$'",
            name="ck_lifecycle_legal_holds_evidence",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_lifecycle_legal_holds_workspace_activated",
        "lifecycle_legal_holds",
        ["workspace_id", "activated_at"],
        schema=schema,
    )


def _create_legal_hold_releases(schema: str) -> None:
    op.create_table(
        "lifecycle_legal_hold_releases",
        sa.Column("release_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("legal_hold_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("release_evidence_digest", sa.String(64), nullable=False),
        sa.Column("released_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "legal_hold_id",
            name="uq_lifecycle_legal_hold_releases_hold",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_lifecycle_legal_hold_releases_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["legal_hold_id", "workspace_id"],
            [
                f"{schema}.lifecycle_legal_holds.legal_hold_id",
                f"{schema}.lifecycle_legal_holds.workspace_id",
            ],
            name="fk_lifecycle_legal_hold_releases_hold",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' "
            "AND release_evidence_digest ~ '^[0-9a-f]{64}$' "
            "AND reason_code ~ '^[A-Z][A-Z0-9_]{2,63}$'",
            name="ck_lifecycle_legal_hold_releases_evidence",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_lifecycle_legal_hold_releases_workspace_released",
        "lifecycle_legal_hold_releases",
        ["workspace_id", "released_at"],
        schema=schema,
    )


def _create_compliance_proofs(schema: str) -> None:
    op.create_table(
        "lifecycle_compliance_proofs",
        sa.Column("compliance_proof_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(24), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_key_digest", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("reason_codes", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("regulatory_policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_digest", sa.String(64), nullable=True),
        sa.Column("external_review_status", sa.String(24), nullable=False),
        sa.Column("active_hold_count", sa.Integer(), nullable=False),
        sa.Column("hold_set_digest", sa.String(64), nullable=False),
        sa.Column("proof_digest", sa.String(64), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "operation",
            "request_key_digest",
            name="uq_lifecycle_compliance_proofs_request",
        ),
        sa.UniqueConstraint(
            "compliance_proof_id",
            "workspace_id",
            "operation",
            "operation_id",
            name="uq_lifecycle_compliance_proofs_operation",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_lifecycle_compliance_proofs_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["regulatory_policy_id", "workspace_id"],
            [
                f"{schema}.lifecycle_regulatory_policy_versions.regulatory_policy_id",
                f"{schema}.lifecycle_regulatory_policy_versions.workspace_id",
            ],
            name="fk_lifecycle_compliance_proofs_policy",
        ),
        sa.CheckConstraint(
            "operation IN ('export', 'purge', 'retention') "
            "AND decision IN ('allowed', 'blocked', 'not_configured') "
            "AND external_review_status IN ('not_configured', 'approved', 'rejected')",
            name="ck_lifecycle_compliance_proofs_decision",
        ),
        sa.CheckConstraint(
            "request_key_digest ~ '^[0-9a-f]{64}$' AND request_hash ~ '^[0-9a-f]{64}$' "
            "AND hold_set_digest ~ '^[0-9a-f]{64}$' AND proof_digest ~ '^[0-9a-f]{64}$' "
            "AND (policy_digest IS NULL OR policy_digest ~ '^[0-9a-f]{64}$') "
            "AND active_hold_count >= 0 AND cardinality(reason_codes) >= 1 "
            f"AND reason_codes <@ ARRAY[{REASON_CODES_SQL}]::varchar[]",
            name="ck_lifecycle_compliance_proofs_evidence",
        ),
        sa.CheckConstraint(
            "(regulatory_policy_id IS NULL AND policy_digest IS NULL) OR "
            "(regulatory_policy_id IS NOT NULL AND policy_digest IS NOT NULL)",
            name="ck_lifecycle_compliance_proofs_policy",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_lifecycle_compliance_proofs_workspace_created",
        "lifecycle_compliance_proofs",
        ["workspace_id", "created_at"],
        schema=schema,
    )


def _extend_lifecycle_operations(schema: str) -> None:
    op.add_column(
        "lifecycle_export_records",
        sa.Column("compliance_proof_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "lifecycle_purge_requests",
        sa.Column("compliance_proof_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "lifecycle_retention_runs",
        sa.Column("request_hash", sa.String(64), nullable=True),
        schema=schema,
    )
    op.add_column(
        "lifecycle_retention_runs",
        sa.Column("regulatory_policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "lifecycle_retention_runs",
        sa.Column("compliance_proof_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.create_foreign_key(
        "fk_lifecycle_exports_compliance_proof",
        "lifecycle_export_records",
        "lifecycle_compliance_proofs",
        ["compliance_proof_id"],
        ["compliance_proof_id"],
        source_schema=schema,
        referent_schema=schema,
    )
    op.create_foreign_key(
        "fk_lifecycle_purges_compliance_proof",
        "lifecycle_purge_requests",
        "lifecycle_compliance_proofs",
        ["compliance_proof_id"],
        ["compliance_proof_id"],
        source_schema=schema,
        referent_schema=schema,
    )
    op.create_foreign_key(
        "fk_lifecycle_retention_policy",
        "lifecycle_retention_runs",
        "lifecycle_regulatory_policy_versions",
        ["regulatory_policy_id", "workspace_id"],
        ["regulatory_policy_id", "workspace_id"],
        source_schema=schema,
        referent_schema=schema,
    )
    op.create_foreign_key(
        "fk_lifecycle_retention_compliance_proof",
        "lifecycle_retention_runs",
        "lifecycle_compliance_proofs",
        ["compliance_proof_id"],
        ["compliance_proof_id"],
        source_schema=schema,
        referent_schema=schema,
    )


def _create_guards(schema: str) -> None:
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".protect_compliance_fact()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                RAISE EXCEPTION 'lifecycle compliance facts are immutable'
                    USING ERRCODE = '55000';
            END;
            $function$;

            CREATE FUNCTION "{schema}".guard_legal_hold_activation()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                PERFORM pg_advisory_xact_lock(hashtextextended(NEW.workspace_id::text, 509));
                IF NOT EXISTS (
                    SELECT 1
                    FROM "{schema}".lifecycle_regulatory_policy_versions policy
                    WHERE policy.regulatory_policy_id = NEW.regulatory_policy_id
                      AND policy.workspace_id = NEW.workspace_id
                      AND policy.jurisdiction_status = 'configured'
                ) THEN
                    RAISE EXCEPTION '未配置法域不得激活法律保留'
                        USING ERRCODE = '23000';
                END IF;
                IF EXISTS (
                    SELECT 1 FROM "{schema}".lifecycle_purge_requests purge
                    WHERE purge.workspace_id = NEW.workspace_id
                      AND purge.status IN ('pending', 'retryable')
                ) OR EXISTS (
                    SELECT 1 FROM "{schema}".lifecycle_retention_runs run
                    WHERE run.workspace_id = NEW.workspace_id AND run.status = 'running'
                ) THEN
                    RAISE EXCEPTION '破坏性生命周期操作执行中不得激活法律保留'
                        USING ERRCODE = '23000';
                END IF;
                RETURN NEW;
            END;
            $function$;

            CREATE FUNCTION "{schema}".guard_legal_hold_release()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                PERFORM pg_advisory_xact_lock(hashtextextended(NEW.workspace_id::text, 509));
                IF NOT EXISTS (
                    SELECT 1 FROM "{schema}".lifecycle_legal_holds hold
                    WHERE hold.legal_hold_id = NEW.legal_hold_id
                      AND hold.workspace_id = NEW.workspace_id
                      AND hold.activated_at <= NEW.released_at
                ) THEN
                    RAISE EXCEPTION '法律保留不存在或解除时间早于激活时间'
                        USING ERRCODE = '23000';
                END IF;
                RETURN NEW;
            END;
            $function$;

            CREATE FUNCTION "{schema}".guard_lifecycle_operation()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            DECLARE
                operation_name varchar;
                operation_identifier uuid;
                proof_identifier uuid;
            BEGIN
                PERFORM pg_advisory_xact_lock(hashtextextended(NEW.workspace_id::text, 509));
                IF TG_TABLE_NAME = 'lifecycle_export_records' THEN
                    operation_name := 'export';
                    operation_identifier := NEW.export_id;
                    proof_identifier := NEW.compliance_proof_id;
                ELSIF TG_TABLE_NAME = 'lifecycle_purge_requests' THEN
                    operation_name := 'purge';
                    operation_identifier := NEW.purge_request_id;
                    proof_identifier := NEW.compliance_proof_id;
                ELSE
                    operation_name := 'retention';
                    operation_identifier := NEW.retention_run_id;
                    proof_identifier := NEW.compliance_proof_id;
                    IF NEW.request_hash IS NULL OR NEW.regulatory_policy_id IS NULL THEN
                        RAISE EXCEPTION '新保留期运行缺少冻结请求与法规策略'
                            USING ERRCODE = '23000';
                    END IF;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM "{schema}".lifecycle_compliance_proofs proof
                    WHERE proof.compliance_proof_id = proof_identifier
                      AND proof.workspace_id = NEW.workspace_id
                      AND proof.operation = operation_name
                      AND proof.operation_id = operation_identifier
                      AND proof.decision = 'allowed'
                      AND (operation_name = 'export' OR proof.active_hold_count = 0)
                ) THEN
                    RAISE EXCEPTION '生命周期操作缺少匹配的允许裁决证明'
                        USING ERRCODE = '23000';
                END IF;
                IF operation_name IN ('purge', 'retention') AND EXISTS (
                    SELECT 1
                    FROM "{schema}".lifecycle_legal_holds hold
                    LEFT JOIN "{schema}".lifecycle_legal_hold_releases release
                      ON release.workspace_id = hold.workspace_id
                     AND release.legal_hold_id = hold.legal_hold_id
                    WHERE hold.workspace_id = NEW.workspace_id
                      AND release.release_id IS NULL
                ) THEN
                    RAISE EXCEPTION '活动法律保留阻止破坏性生命周期操作'
                        USING ERRCODE = '23000';
                END IF;
                RETURN NEW;
            END;
            $function$;
            """
        )
    )
    for table_name in _table_names():
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_{table_name}_immutable_guard
                BEFORE UPDATE OR DELETE ON "{schema}"."{table_name}"
                FOR EACH ROW EXECUTE FUNCTION "{schema}".protect_compliance_fact()
                """
            )
        )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_lifecycle_legal_holds_insert_guard
            BEFORE INSERT ON "{schema}".lifecycle_legal_holds
            FOR EACH ROW EXECUTE FUNCTION "{schema}".guard_legal_hold_activation();

            CREATE TRIGGER trg_lifecycle_legal_hold_releases_insert_guard
            BEFORE INSERT ON "{schema}".lifecycle_legal_hold_releases
            FOR EACH ROW EXECUTE FUNCTION "{schema}".guard_legal_hold_release()
            """
        )
    )
    for table_name in (
        "lifecycle_export_records",
        "lifecycle_purge_requests",
        "lifecycle_retention_runs",
    ):
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_{table_name}_compliance_guard
                BEFORE INSERT ON "{schema}"."{table_name}"
                FOR EACH ROW EXECUTE FUNCTION "{schema}".guard_lifecycle_operation()
                """
            )
        )


def _grant_permissions(schema: str) -> None:
    permissions = ", ".join(f"('{code}')" for code in COMPLIANCE_PERMISSIONS)
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, permission_code, 'workspace',
                   ARRAY[]::uuid[], ARRAY[]::uuid[], 'RESTRICTED', ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            CROSS JOIN (VALUES {permissions}) AS permission_codes(permission_code)
            WHERE roles.role_key = 'workspace_owner' AND roles.system_managed = true
            ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
            """
        )
    )


def _register_compliance_bindings(schema: str) -> None:
    """注册四个动作与 API 的关系，菜单服务据此执行同源权限校验。"""

    connection = op.get_bind()
    for menu_id, api_resource_id, action_type in COMPLIANCE_BINDINGS:
        connection.execute(
            sa.text(
                f"""
                INSERT INTO "{schema}".registered_menu_api_bindings (
                    menu_id, api_resource_id, action_type
                ) VALUES (
                    CAST(:menu_id AS uuid), CAST(:api_resource_id AS uuid), :action_type
                )
                ON CONFLICT (menu_id, api_resource_id) DO UPDATE
                SET action_type = EXCLUDED.action_type
                """
            ),
            {
                "menu_id": menu_id,
                "api_resource_id": api_resource_id,
                "action_type": action_type,
            },
        )


def _publish_upgraded_menu_snapshots(schema: str) -> None:
    """为每个当前发布追加 Registry 23 快照，历史版本与自定义项保持不变。"""

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            f"""
            SELECT releases.*, numbers.next_release_number
            FROM "{schema}".workspace_menu_publications AS publications
            JOIN "{schema}".menu_releases AS releases
              ON releases.workspace_id = publications.workspace_id
             AND releases.release_id = publications.current_release_id
            JOIN LATERAL (
                SELECT COALESCE(MAX(history.release_number), 0) + 1 AS next_release_number
                FROM "{schema}".menu_releases AS history
                WHERE history.workspace_id = releases.workspace_id
            ) AS numbers ON true
            """
        )
    ).mappings()
    for row in rows:
        _insert_upgraded_menu_release(connection, schema, dict(row))


def _insert_upgraded_menu_release(
    connection: sa.engine.Connection,
    schema: str,
    row: dict[str, Any],
) -> None:
    """追加确定性 Registry 23 发布并原子切换当前菜单指针。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 23
    menus = cast(list[dict[str, Any]], snapshot["menus"])
    menus.extend(_compliance_snapshot_menus())
    snapshot["menus"] = sorted(
        {item["menu_id"]: item for item in menus}.values(),
        key=lambda item: item["menu_id"],
    )
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    bindings.extend(_compliance_snapshot_bindings())
    snapshot["menu_api_bindings"] = sorted(
        {(item["menu_id"], item["api_resource_id"]): item for item in bindings}.values(),
        key=lambda item: (item["menu_id"], item["api_resource_id"]),
    )
    digest = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    occurred_at = datetime.now(UTC)
    parameters = {
        "release_id": _upgrade_release_id(workspace_id),
        "workspace_id": workspace_id,
        "release_number": row["next_release_number"],
        "source_release_id": row["release_id"],
        "snapshot": json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        "snapshot_digest": digest,
        "created_by_account_id": row["created_by_account_id"],
        "occurred_at": occurred_at,
    }
    connection.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".menu_releases (
                release_id, workspace_id, release_number, release_kind,
                source_release_id, status, snapshot, snapshot_digest,
                validation_errors, rejection_reason, created_by_account_id,
                decided_by_account_id, created_at, validated_at, decided_at,
                published_at, version
            ) VALUES (
                :release_id, :workspace_id, :release_number, 'standard',
                :source_release_id, 'published', CAST(:snapshot AS jsonb), :snapshot_digest,
                ARRAY[]::varchar[], NULL, :created_by_account_id,
                :created_by_account_id, :occurred_at, :occurred_at, :occurred_at,
                :occurred_at, 6
            )
            """
        ),
        parameters,
    )
    connection.execute(
        sa.text(
            f"""
            UPDATE "{schema}".workspace_menu_publications
            SET current_release_id = :release_id, published_at = :occurred_at
            WHERE workspace_id = :workspace_id
            """
        ),
        parameters,
    )


def _reject_custom_grants(schema: str) -> None:
    """存在非系统 Owner 的合规授权时拒绝降级，防止丢失管理员配置。"""

    count = op.get_bind().scalar(
        sa.text(
            f"""
            SELECT count(*)
            FROM "{schema}".role_permission_grants AS grants
            LEFT JOIN "{schema}".roles AS roles
              ON roles.workspace_id = grants.workspace_id AND roles.role_id = grants.role_id
            WHERE grants.permission_code = ANY(:permission_codes)
              AND NOT (roles.role_key = 'workspace_owner' AND roles.system_managed = true)
            """
        ),
        {"permission_codes": list(COMPLIANCE_PERMISSIONS)},
    )
    if int(count or 0) > 0:
        raise RuntimeError("存在自定义法规治理授权, 拒绝降级以避免权限事实丢失")


def _restore_menu_snapshots(schema: str) -> None:
    """仅恢复本 Revision 创建且尚未被后续发布替换的当前菜单快照。"""

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            f"""
            SELECT publications.workspace_id, releases.release_id, releases.source_release_id
            FROM "{schema}".workspace_menu_publications AS publications
            JOIN "{schema}".menu_releases AS releases
              ON releases.workspace_id = publications.workspace_id
             AND releases.release_id = publications.current_release_id
            """
        )
    ).mappings()
    for row in rows:
        workspace_id = cast(UUID, row["workspace_id"])
        if row["release_id"] != _upgrade_release_id(workspace_id):
            raise RuntimeError("当前菜单发布已在 P5-09 后变化, 拒绝破坏性降级")
        parameters = {
            "workspace_id": workspace_id,
            "source_release_id": row["source_release_id"],
            "release_id": row["release_id"],
        }
        connection.execute(
            sa.text(
                f"""
                UPDATE "{schema}".workspace_menu_publications
                SET current_release_id = :source_release_id, published_at = now()
                WHERE workspace_id = :workspace_id
                """
            ),
            parameters,
        )
        connection.execute(
            sa.text(
                f'DELETE FROM "{schema}".menu_releases '
                "WHERE workspace_id = :workspace_id AND release_id = :release_id"
            ),
            parameters,
        )


def _remove_compliance_bindings(schema: str) -> None:
    connection = op.get_bind()
    for menu_id, api_resource_id, _ in COMPLIANCE_BINDINGS:
        connection.execute(
            sa.text(
                f'DELETE FROM "{schema}".registered_menu_api_bindings '
                "WHERE menu_id = CAST(:menu_id AS uuid) "
                "AND api_resource_id = CAST(:api_resource_id AS uuid)"
            ),
            {"menu_id": menu_id, "api_resource_id": api_resource_id},
        )


def _compliance_snapshot_menus() -> list[dict[str, object]]:
    definitions = (
        ("228", "regulatory_policy_publish", "发布法规策略", COMPLIANCE_PERMISSIONS[0], 190),
        ("229", "legal_hold_activate", "激活法律保留", COMPLIANCE_PERMISSIONS[1], 200),
        ("230", "legal_hold_release", "解除法律保留", COMPLIANCE_PERMISSIONS[2], 210),
        ("231", "compliance_proof_read", "查看合规证明", COMPLIANCE_PERMISSIONS[3], 220),
    )
    return [
        {
            "menu_id": f"82000000-0000-4000-8000-000000000{suffix}",
            "menu_key": f"navigation.workspace.status.{menu_key}",
            "parent_menu_id": STATUS_MENU_ID,
            "name": name,
            "menu_type": "action",
            "page_resource_id": None,
            "permission_code": permission_code,
            "icon_key": None,
            "sort_order": sort_order,
            "source": "system",
            "status": "active",
            "visible": True,
        }
        for suffix, menu_key, name, permission_code, sort_order in definitions
    ]


def _compliance_snapshot_bindings() -> list[dict[str, str]]:
    return [
        {"menu_id": menu_id, "api_resource_id": api_resource_id, "action_type": action_type}
        for menu_id, api_resource_id, action_type in COMPLIANCE_BINDINGS
    ]


def _upgrade_release_id(workspace_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"ai-platform:p509-menu:{workspace_id}")


def _table_names() -> tuple[str, ...]:
    return (
        "lifecycle_regulatory_policy_versions",
        "lifecycle_legal_holds",
        "lifecycle_legal_hold_releases",
        "lifecycle_compliance_proofs",
    )
