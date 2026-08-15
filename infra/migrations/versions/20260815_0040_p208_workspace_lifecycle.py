"""建立 P2-08 工作空间导出、业务数据清除和保留期事实。"""

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

revision: str = "20260815_0040"
down_revision: str | None = "20260815_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LIFECYCLE_PERMISSIONS = (
    "workspace.lifecycle.export",
    "workspace.lifecycle.purge",
    "workspace.lifecycle.retention.execute",
)
LIFECYCLE_BINDINGS = (
    (191, 108, "mutation"),
    (192, 109, "mutation"),
    (193, 110, "mutation"),
)
STATUS_MENU_ID = "82000000-0000-4000-8000-000000000005"


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """建立生命周期事实、受限触发器旁路、Owner 权限和发布快照。"""

    schema = _schema()
    _create_export_records(schema)
    _create_purge_requests(schema)
    _create_deletion_certificates(schema)
    _create_retention_runs(schema)
    _install_lifecycle_bypasses(schema, enabled=True)
    _grant_permissions(schema)
    _register_bindings(schema)
    _publish_upgraded_snapshots(schema)


def downgrade() -> None:
    """恢复菜单与原不可变函数，再按反向依赖移除生命周期事实。"""

    schema = _schema()
    _restore_menu_snapshots(schema)
    api_ids = ", ".join(
        f"'81000000-0000-4000-8000-{api_number:012d}'::uuid"
        for _, api_number, _ in LIFECYCLE_BINDINGS
    )
    permissions = ", ".join(f"'{code}'" for code in LIFECYCLE_PERMISSIONS)
    op.execute(
        sa.text(
            f'DELETE FROM "{schema}".registered_menu_api_bindings '
            f"WHERE api_resource_id IN ({api_ids})"
        )
    )
    op.execute(
        sa.text(
            f'DELETE FROM "{schema}".role_permission_grants '
            f"WHERE permission_code IN ({permissions})"
        )
    )
    _install_lifecycle_bypasses(schema, enabled=False)
    op.execute(
        sa.text(
            f'DROP TRIGGER protect_lifecycle_deletion_certificates ON "{schema}".'
            "lifecycle_deletion_certificates"
        )
    )
    op.execute(sa.text(f'DROP FUNCTION "{schema}".protect_lifecycle_certificate()'))
    op.drop_table("lifecycle_retention_runs", schema=schema)
    op.drop_table("lifecycle_deletion_certificates", schema=schema)
    op.drop_table("lifecycle_purge_requests", schema=schema)
    op.drop_table("lifecycle_export_records", schema=schema)


def _create_export_records(schema: str) -> None:
    op.create_table(
        "lifecycle_export_records",
        sa.Column("export_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("registry_version", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(2048), nullable=True),
        sa.Column("bundle_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("bundle_sha256", sa.String(64), nullable=True),
        sa.Column("object_manifest_sha256", sa.String(64), nullable=True),
        sa.Column("table_count", sa.Integer(), nullable=True),
        sa.Column("object_count", sa.Integer(), nullable=True),
        sa.Column("requested_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("traceparent", sa.String(55), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_lifecycle_exports_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_lifecycle_exports_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_lifecycle_exports_requester",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_lifecycle_exports_status",
        ),
        sa.CheckConstraint(
            "registry_version >= 1 AND request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_lifecycle_exports_request",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND completed_at IS NULL AND error_code IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL AND error_code IS NULL "
            "AND object_key IS NOT NULL AND bundle_size_bytes >= 0 "
            "AND bundle_sha256 ~ '^[0-9a-f]{64}$' "
            "AND object_manifest_sha256 ~ '^[0-9a-f]{64}$' "
            "AND table_count >= 0 AND object_count >= 0) OR "
            "(status = 'failed' AND completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name="ck_lifecycle_exports_completion",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_lifecycle_exports_workspace_created",
        "lifecycle_export_records",
        ["workspace_id", "created_at"],
        schema=schema,
    )


def _create_purge_requests(schema: str) -> None:
    op.create_table(
        "lifecycle_purge_requests",
        sa.Column("purge_request_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("confirmed_workspace_name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("database_cleared", sa.Boolean(), nullable=False),
        sa.Column("objects_cleared", sa.Boolean(), nullable=False),
        sa.Column("cache_cleared", sa.Boolean(), nullable=False),
        sa.Column("deleted_table_counts", postgresql.JSONB(), nullable=False),
        sa.Column("deleted_object_count", sa.Integer(), nullable=False),
        sa.Column("deleted_cache_key_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(128), nullable=True),
        sa.Column("requested_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("traceparent", sa.String(55), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_lifecycle_purges_idempotency",
        ),
        sa.UniqueConstraint(
            "purge_request_id",
            "workspace_id",
            name="uq_lifecycle_purges_id_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_lifecycle_purges_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_lifecycle_purges_requester",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'retryable', 'completed')",
            name="ck_lifecycle_purges_status",
        ),
        sa.CheckConstraint(
            "reason_code ~ '^[A-Z][A-Z0-9_]{2,63}$' AND request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_lifecycle_purges_request",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(deleted_table_counts) = 'object' "
            "AND deleted_object_count >= 0 AND deleted_cache_key_count >= 0",
            name="ck_lifecycle_purges_counts",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND database_cleared AND objects_cleared AND cache_cleared "
            "AND completed_at IS NOT NULL AND last_error_code IS NULL) OR "
            "(status <> 'completed' AND completed_at IS NULL)",
            name="ck_lifecycle_purges_completion",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_lifecycle_purges_workspace_created",
        "lifecycle_purge_requests",
        ["workspace_id", "created_at"],
        schema=schema,
    )


def _create_deletion_certificates(schema: str) -> None:
    op.create_table(
        "lifecycle_deletion_certificates",
        sa.Column("certificate_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purge_request_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("registry_version", sa.Integer(), nullable=False),
        sa.Column("deleted_table_counts", postgresql.JSONB(), nullable=False),
        sa.Column("deleted_object_count", sa.Integer(), nullable=False),
        sa.Column("deleted_cache_key_count", sa.Integer(), nullable=False),
        sa.Column("result_sha256", sa.String(64), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["purge_request_id", "workspace_id"],
            [
                f"{schema}.lifecycle_purge_requests.purge_request_id",
                f"{schema}.lifecycle_purge_requests.workspace_id",
            ],
            name="fk_lifecycle_certificates_request",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "registry_version >= 1 AND deleted_object_count >= 0 "
            "AND deleted_cache_key_count >= 0 "
            "AND jsonb_typeof(deleted_table_counts) = 'object' "
            "AND result_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_lifecycle_certificates_result",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_lifecycle_certificates_workspace_completed",
        "lifecycle_deletion_certificates",
        ["workspace_id", "completed_at"],
        schema=schema,
    )
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".protect_lifecycle_certificate()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'DELETE'
                   AND current_setting('ai_platform.lifecycle_purge', true) = 'on' THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION 'lifecycle deletion certificates are immutable'
                    USING ERRCODE = '55000';
            END;
            $$;
            CREATE TRIGGER protect_lifecycle_deletion_certificates
            BEFORE UPDATE OR DELETE ON "{schema}".lifecycle_deletion_certificates
            FOR EACH ROW EXECUTE FUNCTION "{schema}".protect_lifecycle_certificate();
            """
        )
    )


def _create_retention_runs(schema: str) -> None:
    op.create_table(
        "lifecycle_retention_runs",
        sa.Column("retention_run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("cutoffs", postgresql.JSONB(), nullable=False),
        sa.Column("deleted_table_counts", postgresql.JSONB(), nullable=False),
        sa.Column("result_sha256", sa.String(64), nullable=True),
        sa.Column("requested_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_lifecycle_retention_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_lifecycle_retention_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_lifecycle_retention_requester",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_lifecycle_retention_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(cutoffs) = 'object' AND jsonb_typeof(deleted_table_counts) = 'object'",
            name="ck_lifecycle_retention_documents",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND completed_at IS NULL AND error_code IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL AND error_code IS NULL "
            "AND result_sha256 ~ '^[0-9a-f]{64}$') OR "
            "(status = 'failed' AND completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name="ck_lifecycle_retention_completion",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_lifecycle_retention_workspace_created",
        "lifecycle_retention_runs",
        ["workspace_id", "created_at"],
        schema=schema,
    )


def _install_lifecycle_bypasses(schema: str, *, enabled: bool) -> None:
    """替换既有触发器函数，仅在受限事务中放行 DELETE。"""

    guard = (
        "IF TG_OP = 'DELETE' AND current_setting('ai_platform.lifecycle_purge', true) = 'on' "
        "THEN RETURN OLD; END IF;"
        if enabled
        else ""
    )
    immutable_functions = {
        "reject_audit_record_mutation": "audit records are immutable",
        "reject_outbox_replay_request_mutation": "outbox replay requests are immutable",
        "prevent_assistant_snapshot_mutation": "assistant snapshots are immutable",
        "prevent_retrieval_snapshot_mutation": "retrieval snapshot facts are immutable",
        "reject_workflow_version_mutation": "workflow_versions are immutable",
        "reject_approval_policy_version_mutation": "approval_policy_versions are immutable",
        "reject_approval_action_mutation": "approval_actions are immutable",
    }
    for function_name, message in immutable_functions.items():
        op.execute(
            sa.text(
                f"""
                CREATE OR REPLACE FUNCTION "{schema}"."{function_name}"()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    {guard}
                    RAISE EXCEPTION '{message}' USING ERRCODE = '55000';
                END;
                $$
                """
            )
        )
    op.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION "{schema}".protect_ingestion_attempt_history()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                {guard}
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'ingestion job attempts are immutable';
                END IF;
                IF OLD.status <> 'running' THEN
                    RAISE EXCEPTION 'completed ingestion job attempts are immutable';
                END IF;
                IF NEW.status = 'running' OR NEW.completed_at IS NULL THEN
                    RAISE EXCEPTION
                        'ingestion job attempt must transition directly to a terminal status';
                END IF;
                IF ROW(
                    NEW.job_attempt_id, NEW.workspace_id, NEW.job_stage_id,
                    NEW.ingestion_job_id, NEW.generation, NEW.attempt_no,
                    NEW.trigger, NEW.worker_id, NEW.initiated_by_actor_id,
                    NEW.trace_id, NEW.traceparent, NEW.lease_started_at,
                    NEW.lease_expires_at, NEW.started_at, NEW.created_at
                ) IS DISTINCT FROM ROW(
                    OLD.job_attempt_id, OLD.workspace_id, OLD.job_stage_id,
                    OLD.ingestion_job_id, OLD.generation, OLD.attempt_no,
                    OLD.trigger, OLD.worker_id, OLD.initiated_by_actor_id,
                    OLD.trace_id, OLD.traceparent, OLD.lease_started_at,
                    OLD.lease_expires_at, OLD.started_at, OLD.created_at
                ) THEN
                    RAISE EXCEPTION 'ingestion job attempt identity is immutable';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )


def _grant_permissions(schema: str) -> None:
    permissions = ", ".join(f"('{code}')" for code in LIFECYCLE_PERMISSIONS)
    # 个人和企业系统 Owner 使用同一权限；普通成员与 Open API Key 不自动获得危险操作。
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


def _register_bindings(schema: str) -> None:
    values = ", ".join(
        f"('82000000-0000-4000-8000-{menu:012d}'::uuid, "
        f"'81000000-0000-4000-8000-{api:012d}'::uuid, '{action}')"
        for menu, api, action in LIFECYCLE_BINDINGS
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


def _publish_upgraded_snapshots(schema: str) -> None:
    """为每个当前发布空间复制注册表 17 快照，不覆写历史事实。"""

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
        _insert_upgraded_release(connection, schema, dict(row))


def _insert_upgraded_release(
    connection: sa.engine.Connection,
    schema: str,
    row: dict[str, Any],
) -> None:
    """追加生命周期动作与绑定，并原子切换当前发布指针。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 17
    menus = cast(list[dict[str, Any]], snapshot["menus"])
    menus.extend(_lifecycle_snapshot_menus())
    snapshot["menus"] = sorted(
        {menu["menu_id"]: menu for menu in menus}.values(),
        key=lambda item: item["menu_id"],
    )
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    bindings.extend(_lifecycle_snapshot_bindings())
    snapshot["menu_api_bindings"] = sorted(
        {(item["menu_id"], item["api_resource_id"]): item for item in bindings}.values(),
        key=lambda item: (item["menu_id"], item["api_resource_id"]),
    )
    digest = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    now = datetime.now(UTC)
    parameters = {
        "release_id": _upgrade_release_id(workspace_id),
        "workspace_id": workspace_id,
        "release_number": row["next_release_number"],
        "source_release_id": row["release_id"],
        "snapshot": json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        "snapshot_digest": digest,
        "created_by_account_id": row["created_by_account_id"],
        "occurred_at": now,
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
                :occurred_at, 4
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


def _restore_menu_snapshots(schema: str) -> None:
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
            continue
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


def _lifecycle_snapshot_menus() -> list[dict[str, object]]:
    definitions = (
        (191, "lifecycle_export", "导出空间数据", "workspace.lifecycle.export", 120),
        (192, "lifecycle_purge", "清除业务数据", "workspace.lifecycle.purge", 130),
        (
            193,
            "lifecycle_retention",
            "执行保留策略",
            "workspace.lifecycle.retention.execute",
            140,
        ),
    )
    return [
        {
            "menu_id": f"82000000-0000-4000-8000-{number:012d}",
            "menu_key": f"navigation.workspace.status.{key}",
            "parent_menu_id": STATUS_MENU_ID,
            "name": name,
            "menu_type": "action",
            "page_resource_id": None,
            "permission_code": permission,
            "icon_key": None,
            "sort_order": order,
            "source": "system",
            "status": "active",
            "visible": True,
        }
        for number, key, name, permission, order in definitions
    ]


def _lifecycle_snapshot_bindings() -> list[dict[str, str]]:
    return [
        {
            "menu_id": f"82000000-0000-4000-8000-{menu:012d}",
            "api_resource_id": f"81000000-0000-4000-8000-{api:012d}",
            "action_type": action,
        }
        for menu, api, action in LIFECYCLE_BINDINGS
    ]


def _upgrade_release_id(workspace_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"ai-platform:p208-menu:{workspace_id}")
