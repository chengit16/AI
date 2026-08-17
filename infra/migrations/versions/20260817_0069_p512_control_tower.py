"""激活 P5-12 运营控制台权限、菜单发布和 API 绑定。"""

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

revision: str = "20260817_0069"
down_revision: str | None = "20260817_0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONTROL_TOWER_PERMISSION = "operations.control_tower.read"
CONTROL_TOWER_BINDING = (
    "82000000-0000-4000-8000-000000000233",
    "81000000-0000-4000-8000-000000000152",
    "query",
)
WORKSPACE_MENU_ID = "82000000-0000-4000-8000-000000000001"


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """为既有 Owner 授权，并追加 Registry 24 的控制台发布快照。"""

    schema = _schema()
    _grant_owner_permission(schema)
    _register_binding(schema)
    _publish_upgraded_menu_snapshots(schema)


def downgrade() -> None:
    """仅在没有自定义控制台授权和后续菜单发布时安全恢复 Registry 23。"""

    schema = _schema()
    _reject_custom_grants(schema)
    _restore_menu_snapshots(schema)
    connection = op.get_bind()
    connection.execute(
        sa.text(
            f'DELETE FROM "{schema}".registered_menu_api_bindings '
            "WHERE menu_id = CAST(:menu_id AS uuid) "
            "AND api_resource_id = CAST(:api_resource_id AS uuid)"
        ),
        {"menu_id": CONTROL_TOWER_BINDING[0], "api_resource_id": CONTROL_TOWER_BINDING[1]},
    )
    connection.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".role_permission_grants AS grants
            USING "{schema}".roles AS roles
            WHERE grants.workspace_id = roles.workspace_id
              AND grants.role_id = roles.role_id
              AND roles.role_key = 'workspace_owner'
              AND roles.system_managed = true
              AND grants.permission_code = :permission_code
            """
        ),
        {"permission_code": CONTROL_TOWER_PERMISSION},
    )


def _grant_owner_permission(schema: str) -> None:
    """个人与企业 Owner 默认获得控制台读取权，其他角色仍需显式授权。"""

    op.get_bind().execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, :permission_code,
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[],
                   'RESTRICTED', ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            WHERE roles.role_key = 'workspace_owner' AND roles.system_managed = true
            ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
            """
        ),
        {"permission_code": CONTROL_TOWER_PERMISSION},
    )


def _register_binding(schema: str) -> None:
    """把控制台查看动作绑定到唯一的只读 API 资源。"""

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
        {
            "menu_id": CONTROL_TOWER_BINDING[0],
            "api_resource_id": CONTROL_TOWER_BINDING[1],
            "action_type": CONTROL_TOWER_BINDING[2],
        },
    )


def _publish_upgraded_menu_snapshots(schema: str) -> None:
    """为每个当前发布追加 Registry 24 快照，不修改历史发布事实。"""

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
    """追加确定性 Registry 24 发布并原子切换当前菜单指针。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 24
    menus = cast(list[dict[str, Any]], snapshot["menus"])
    menus.extend(_control_tower_snapshot_menus())
    snapshot["menus"] = sorted(
        {item["menu_id"]: item for item in menus}.values(),
        key=lambda item: item["menu_id"],
    )
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    bindings.extend(_control_tower_snapshot_bindings())
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
                :occurred_at, 7
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
    """存在非系统 Owner 的控制台授权时拒绝降级，防止丢失管理员配置。"""

    count = op.get_bind().scalar(
        sa.text(
            f"""
            SELECT count(*)
            FROM "{schema}".role_permission_grants AS grants
            LEFT JOIN "{schema}".roles AS roles
              ON roles.workspace_id = grants.workspace_id AND roles.role_id = grants.role_id
            WHERE grants.permission_code = :permission_code
              AND NOT (roles.role_key = 'workspace_owner' AND roles.system_managed = true)
            """
        ),
        {"permission_code": CONTROL_TOWER_PERMISSION},
    )
    if int(count or 0) > 0:
        raise RuntimeError("存在自定义运营控制台授权, 拒绝降级以避免权限事实丢失")


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
            raise RuntimeError("当前菜单发布已在 P5-12 后变化, 拒绝破坏性降级")
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


def _control_tower_snapshot_menus() -> list[dict[str, object]]:
    return [
        {
            "menu_id": "82000000-0000-4000-8000-000000000232",
            "menu_key": "navigation.workspace.control_tower",
            "parent_menu_id": WORKSPACE_MENU_ID,
            "name": "治理控制台",
            "menu_type": "page",
            "page_resource_id": "80000000-0000-4000-8000-000000000015",
            "permission_code": CONTROL_TOWER_PERMISSION,
            "icon_key": "activity",
            "sort_order": 410,
            "source": "system",
            "status": "active",
            "visible": True,
        },
        {
            "menu_id": "82000000-0000-4000-8000-000000000233",
            "menu_key": "navigation.workspace.control_tower.read",
            "parent_menu_id": "82000000-0000-4000-8000-000000000232",
            "name": "查看治理控制台",
            "menu_type": "action",
            "page_resource_id": None,
            "permission_code": CONTROL_TOWER_PERMISSION,
            "icon_key": None,
            "sort_order": 100,
            "source": "system",
            "status": "active",
            "visible": True,
        },
    ]


def _control_tower_snapshot_bindings() -> list[dict[str, str]]:
    return [
        {
            "menu_id": CONTROL_TOWER_BINDING[0],
            "api_resource_id": CONTROL_TOWER_BINDING[1],
            "action_type": CONTROL_TOWER_BINDING[2],
        }
    ]


def _upgrade_release_id(workspace_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"ai-platform:p512-menu:{workspace_id}")
