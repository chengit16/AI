"""创建 P6B-05 审计异步导出请求事实。"""

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

revision: str = "20260903_0080"
down_revision: str | None = "20260831_0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PERMISSIONS_AUDIT_MENU_ID = "82000000-0000-4000-8000-000000000277"
_MENU_DEFINITIONS = (
    ("277", "permissions_audit", "权限与审计", "page", "authorization.role_permission.read", 320),
    ("278", "role_read", "查看角色权限", "action", "authorization.role_permission.read", 100),
    ("279", "role_manage", "保存角色权限", "action", "authorization.role_permission.manage", 110),
    ("280", "audit_read", "查看审计记录", "action", "operations.records.read", 120),
    ("281", "audit_export", "创建审计导出", "action", "operations.records.read", 130),
)
_MENU_BINDINGS = (
    ("82000000-0000-4000-8000-000000000125", "81000000-0000-4000-8000-000000000203", "query"),
    ("82000000-0000-4000-8000-000000000189", "81000000-0000-4000-8000-000000000202", "query"),
    ("82000000-0000-4000-8000-000000000278", "81000000-0000-4000-8000-000000000203", "query"),
    ("82000000-0000-4000-8000-000000000279", "81000000-0000-4000-8000-000000000033", "mutation"),
    ("82000000-0000-4000-8000-000000000280", "81000000-0000-4000-8000-000000000102", "query"),
    ("82000000-0000-4000-8000-000000000280", "81000000-0000-4000-8000-000000000202", "query"),
    ("82000000-0000-4000-8000-000000000281", "81000000-0000-4000-8000-000000000204", "mutation"),
    ("82000000-0000-4000-8000-000000000281", "81000000-0000-4000-8000-000000000205", "query"),
    ("82000000-0000-4000-8000-000000000281", "81000000-0000-4000-8000-000000000206", "query"),
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """创建带幂等、租约、有限重试和安全摘要的审计导出状态机。"""

    schema = _schema()
    op.create_table(
        "audit_export_requests",
        sa.Column("audit_export_request_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(255), nullable=True),
        sa.Column("resource_type", sa.String(128), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=True),
        sa.Column("occurred_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("field_mask", postgresql.ARRAY(sa.String(128)), nullable=False),
        sa.Column("requested_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("traceparent", sa.String(55), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("claimed_by", sa.String(255), nullable=True),
        sa.Column("claim_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(128), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("result_sha256", sa.String(64), nullable=True),
        sa.Column("result_summary", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_audit_export_requests_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_audit_export_requests_workspace",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('succeeded', 'denied', 'failed')",
            name="ck_audit_export_requests_outcome",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'retry_wait', 'completed', 'dead_letter')",
            name="ck_audit_export_requests_status",
        ),
        sa.CheckConstraint(
            "attempt_count BETWEEN 0 AND 3", name="ck_audit_export_requests_attempts"
        ),
        sa.CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="ck_audit_export_requests_hash"),
        sa.CheckConstraint(
            "occurred_from IS NULL OR occurred_to IS NULL OR occurred_from < occurred_to",
            name="ck_audit_export_requests_window",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND claimed_by IS NOT NULL AND claim_until IS NOT NULL) OR "
            "(status <> 'running' AND claimed_by IS NULL AND claim_until IS NULL)",
            name="ck_audit_export_requests_claim",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL AND row_count IS NOT NULL "
            "AND row_count >= 0 AND result_sha256 IS NOT NULL AND result_summary IS NOT NULL "
            "AND last_error_code IS NULL) OR "
            "(status = 'dead_letter' AND completed_at IS NOT NULL AND row_count IS NULL "
            "AND result_sha256 IS NULL AND result_summary IS NULL "
            "AND last_error_code IS NOT NULL) OR "
            "(status NOT IN ('completed', 'dead_letter') AND completed_at IS NULL "
            "AND row_count IS NULL AND result_sha256 IS NULL AND result_summary IS NULL)",
            name="ck_audit_export_requests_completion",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_audit_export_requests_claim",
        "audit_export_requests",
        ["status", "updated_at"],
        schema=schema,
    )
    _register_menu_bindings(schema)
    _publish_menu_snapshots(schema)
    op.create_index(
        "ix_audit_export_requests_workspace_created",
        "audit_export_requests",
        ["workspace_id", "created_at", "audit_export_request_id"],
        schema=schema,
    )


def downgrade() -> None:
    """仅在没有导出请求事实时允许移除状态机。"""

    schema = _schema()
    connection = op.get_bind()
    count = connection.scalar(sa.text(f'SELECT count(*) FROM "{schema}".audit_export_requests'))
    if count:
        raise RuntimeError("存在审计导出请求事实，不能执行破坏性降级")  # noqa: RUF001
    _restore_menu_snapshots(schema)
    _remove_menu_bindings(schema)
    op.drop_index(
        "ix_audit_export_requests_workspace_created",
        table_name="audit_export_requests",
        schema=schema,
    )
    op.drop_index(
        "ix_audit_export_requests_claim",
        table_name="audit_export_requests",
        schema=schema,
    )
    op.drop_table("audit_export_requests", schema=schema)


def _register_menu_bindings(schema: str) -> None:
    """幂等登记权限矩阵、审计详情和异步导出接口绑定。"""

    op.get_bind().execute(
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
        [
            {
                "menu_id": menu_id,
                "api_resource_id": api_resource_id,
                "action_type": action_type,
            }
            for menu_id, api_resource_id, action_type in _MENU_BINDINGS
        ],
    )


def _publish_menu_snapshots(schema: str) -> None:
    """保留管理员已有定制，并追加 Registry 35 权限与审计入口。"""

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
        _insert_menu_release(connection, schema, dict(row))


def _insert_menu_release(
    connection: sa.engine.Connection, schema: str, row: dict[str, Any]
) -> None:
    """生成跨重试稳定的菜单发布，并维持既有角色可见性事实。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 35
    menus = cast(list[dict[str, Any]], snapshot["menus"])
    menus.extend(_snapshot_menus())
    snapshot["menus"] = sorted(
        {item["menu_id"]: item for item in menus}.values(), key=lambda item: item["menu_id"]
    )
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    bindings.extend(_snapshot_bindings())
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
                :occurred_at, 15
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


def _snapshot_menus() -> list[dict[str, object]]:
    """构造权限与审计页面及其四个体验动作。"""

    menus: list[dict[str, object]] = []
    for suffix, key, name, menu_type, permission_code, sort_order in _MENU_DEFINITIONS:
        is_page = menu_type == "page"
        menus.append(
            {
                "menu_id": f"82000000-0000-4000-8000-000000000{suffix}",
                "menu_key": f"navigation.workspace.{key}"
                if is_page
                else f"navigation.workspace.permissions_audit.{key}",
                "parent_menu_id": "82000000-0000-4000-8000-000000000001"
                if is_page
                else _PERMISSIONS_AUDIT_MENU_ID,
                "name": name,
                "menu_type": menu_type,
                "page_resource_id": "80000000-0000-4000-8000-000000000017" if is_page else None,
                "permission_code": permission_code,
                "icon_key": "shield-check" if is_page else None,
                "sort_order": sort_order,
                "source": "system",
                "status": "active",
                "visible": True,
            }
        )
    return menus


def _snapshot_bindings() -> list[dict[str, str]]:
    """构造 Registry 35 的七条菜单接口绑定。"""

    return [
        {"menu_id": menu_id, "api_resource_id": api_id, "action_type": action_type}
        for menu_id, api_id, action_type in _MENU_BINDINGS
    ]


def _upgrade_release_id(workspace_id: UUID) -> UUID:
    """为每个空间生成确定性的 P6B-05 菜单发布 ID。"""

    return uuid5(NAMESPACE_URL, f"ai-platform:p6b05-menu:{workspace_id}")


def _restore_menu_snapshots(schema: str) -> None:
    """拒绝覆盖后续发布，只撤销本 Revision 的确定性快照。"""

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
            raise RuntimeError("当前菜单发布已在 P6B-05 后变化，拒绝破坏性降级")  # noqa: RUF001
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


def _remove_menu_bindings(schema: str) -> None:
    """移除本 Revision 新增的七条接口绑定。"""

    op.get_bind().execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE api_resource_id = ANY(CAST(:api_resource_ids AS uuid[]))
              AND menu_id = ANY(CAST(:menu_ids AS uuid[]))
            """
        ),
        {
            "api_resource_ids": sorted({api_id for _, api_id, _ in _MENU_BINDINGS}),
            "menu_ids": sorted({menu_id for menu_id, _, _ in _MENU_BINDINGS}),
        },
    )
