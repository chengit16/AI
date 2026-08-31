"""登记 P6B-01 企业控制台接口并升级不可变菜单快照。"""

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

revision: str = "20260831_0076"
down_revision: str | None = "20260830_0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OVERVIEW_MENU_ID = "82000000-0000-4000-8000-000000000002"
_CONSOLE_ACTION_MENU_ID = "82000000-0000-4000-8000-000000000258"
_ENTERPRISE_CONSOLE_API_ID = "81000000-0000-4000-8000-000000000183"
_BINDING = (_CONSOLE_ACTION_MENU_ID, _ENTERPRISE_CONSOLE_API_ID, "query")


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """登记企业控制台接口，并为已有空间追加 Registry 29 快照。"""

    schema = _schema()
    _register_binding(schema)
    _publish_upgraded_menu_snapshots(schema)


def downgrade() -> None:
    """仅在当前发布仍由本节点创建时精确恢复 Registry 28。"""

    schema = _schema()
    _restore_menu_snapshots(schema)
    op.get_bind().execute(
        sa.text(
            f'DELETE FROM "{schema}".registered_menu_api_bindings '
            "WHERE api_resource_id = CAST(:api_resource_id AS uuid)"
        ),
        {"api_resource_id": _ENTERPRISE_CONSOLE_API_ID},
    )


def _register_binding(schema: str) -> None:
    """幂等登记企业控制台动作与聚合查询的授权关系。"""

    menu_id, api_resource_id, action_type = _BINDING
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
            "menu_id": menu_id,
            "api_resource_id": api_resource_id,
            "action_type": action_type,
        },
    )


def _publish_upgraded_menu_snapshots(schema: str) -> None:
    """保留各空间当前菜单事实，仅追加本节点绑定与 Registry 版本。"""

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
    """生成确定性发布，不覆盖管理员在本节点后的菜单事实。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 29
    menus = cast(list[dict[str, Any]], snapshot["menus"])
    menus.append(
        {
            "menu_id": _CONSOLE_ACTION_MENU_ID,
            "menu_key": "navigation.workspace.overview.enterprise_console",
            "parent_menu_id": _OVERVIEW_MENU_ID,
            "name": "查看企业控制台",
            "menu_type": "action",
            "page_resource_id": None,
            "permission_code": "workspace.overview.access",
            "icon_key": None,
            "sort_order": 90,
            "source": "system",
            "status": "active",
            "visible": True,
        }
    )
    snapshot["menus"] = sorted(
        {item["menu_id"]: item for item in menus}.values(),
        key=lambda item: item["menu_id"],
    )
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    menu_id, api_resource_id, action_type = _BINDING
    bindings.append(
        {
            "menu_id": menu_id,
            "api_resource_id": api_resource_id,
            "action_type": action_type,
        }
    )
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
                :occurred_at, 11
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


def _upgrade_release_id(workspace_id: UUID) -> UUID:
    """生成可复核且跨重试稳定的 P6B-01 菜单发布 ID。"""

    return uuid5(NAMESPACE_URL, f"ai-platform:p6b01-menu:{workspace_id}")


def _restore_menu_snapshots(schema: str) -> None:
    """拒绝覆盖后续发布，仅撤销本 Revision 产生的确定性快照。"""

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
            raise RuntimeError("当前菜单发布已在 P6B-01 后变化, 拒绝破坏性降级")
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
