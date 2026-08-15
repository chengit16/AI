"""建立 P2-10 受控运营工作台索引请求、权限、绑定和菜单快照。"""

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

revision: str = "20260815_0041"
down_revision: str | None = "20260815_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKBENCH_PERMISSIONS = (
    "knowledge.ingestion.cancel",
    "operations.index.inspect",
    "operations.index.rebuild",
    "operations.index.cleanup",
)
WORKBENCH_BINDINGS = (
    (194, 111, "mutation"),
    (189, 112, "query"),
    (189, 113, "query"),
    (189, 114, "query"),
    (189, 115, "query"),
    (189, 116, "query"),
    (195, 117, "mutation"),
    (196, 118, "mutation"),
    (197, 119, "mutation"),
)
STATUS_MENU_ID = "82000000-0000-4000-8000-000000000005"


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """建立维护请求事实，并为现有个人和企业 Owner 发布完整入口。"""

    schema = _schema()
    _create_index_maintenance_requests(schema)
    _grant_permissions(schema)
    _register_bindings(schema)
    _publish_upgraded_snapshots(schema)


def downgrade() -> None:
    """仅恢复仍指向本次升级快照的空间，再移除新增绑定、授权和请求表。"""

    schema = _schema()
    _restore_menu_snapshots(schema)
    api_ids = ", ".join(
        f"'81000000-0000-4000-8000-{api_number:012d}'::uuid"
        for _, api_number, _ in WORKBENCH_BINDINGS
    )
    permissions = ", ".join(f"'{code}'" for code in WORKBENCH_PERMISSIONS)
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
    op.drop_index(
        "ix_index_maintenance_requests_workspace_created",
        table_name="index_maintenance_requests",
        schema=schema,
    )
    op.drop_index(
        "ix_index_maintenance_requests_claim",
        table_name="index_maintenance_requests",
        schema=schema,
    )
    op.drop_table("index_maintenance_requests", schema=schema)


def _create_index_maintenance_requests(schema: str) -> None:
    op.create_table(
        "index_maintenance_requests",
        sa.Column("maintenance_request_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("claimed_by", sa.String(255), nullable=True),
        sa.Column("claim_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(128), nullable=True),
        sa.Column("requested_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("traceparent", sa.String(55), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_index_maintenance_requests_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_index_maintenance_requests_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_actor_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_index_maintenance_requests_actor",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_index_maintenance_requests_user",
        ),
        sa.CheckConstraint(
            "command IN ('inspection', 'full_rebuild', 'cleanup')",
            name="ck_index_maintenance_requests_command",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'retry_wait', 'completed', 'dead_letter')",
            name="ck_index_maintenance_requests_status",
        ),
        sa.CheckConstraint(
            "attempt_count BETWEEN 0 AND 3",
            name="ck_index_maintenance_requests_attempts",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' AND reason_code ~ '^[A-Z][A-Z0-9_]{2,63}$'",
            name="ck_index_maintenance_requests_contract",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND claimed_by IS NOT NULL AND claim_until IS NOT NULL) OR "
            "(status <> 'running' AND claimed_by IS NULL AND claim_until IS NULL)",
            name="ck_index_maintenance_requests_claim",
        ),
        sa.CheckConstraint(
            "(status IN ('completed', 'dead_letter') AND completed_at IS NOT NULL) OR "
            "(status NOT IN ('completed', 'dead_letter') AND completed_at IS NULL)",
            name="ck_index_maintenance_requests_completion",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_index_maintenance_requests_claim",
        "index_maintenance_requests",
        ["status", "updated_at"],
        schema=schema,
    )
    op.create_index(
        "ix_index_maintenance_requests_workspace_created",
        "index_maintenance_requests",
        ["workspace_id", "created_at"],
        schema=schema,
    )


def _grant_permissions(schema: str) -> None:
    permissions = ", ".join(f"('{code}')" for code in WORKBENCH_PERMISSIONS)
    # 普通成员不自动获得取消和索引维护权限；个人与企业系统 Owner 使用同一控制面。
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
        for menu, api, action in WORKBENCH_BINDINGS
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
    """为每个当前发布空间复制注册表 18 快照，不覆写历史发布事实。"""

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
    """追加工作台动作与绑定，并原子切换当前发布指针。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 18
    menus = cast(list[dict[str, Any]], snapshot["menus"])
    menus.extend(_workbench_snapshot_menus())
    snapshot["menus"] = sorted(
        {menu["menu_id"]: menu for menu in menus}.values(),
        key=lambda item: item["menu_id"],
    )
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    bindings.extend(_workbench_snapshot_bindings())
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


def _workbench_snapshot_menus() -> list[dict[str, object]]:
    definitions = (
        (194, "ingestion_cancel", "取消入库任务", "knowledge.ingestion.cancel", 150),
        (195, "index_inspect", "执行索引巡检", "operations.index.inspect", 160),
        (196, "index_rebuild", "重建空间索引", "operations.index.rebuild", 170),
        (197, "index_cleanup", "清理失败索引", "operations.index.cleanup", 180),
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


def _workbench_snapshot_bindings() -> list[dict[str, str]]:
    return [
        {
            "menu_id": f"82000000-0000-4000-8000-{menu:012d}",
            "api_resource_id": f"81000000-0000-4000-8000-{api:012d}",
            "action_type": action,
        }
        for menu, api, action in WORKBENCH_BINDINGS
    ]


def _upgrade_release_id(workspace_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"ai-platform:p210-menu:{workspace_id}")
