"""激活 P4-11 工具控制台权限、API 绑定与菜单发布快照。"""

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

revision: str = "20260816_0061"
down_revision: str | None = "20260816_0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSION_CODES = (
    "tool.page.access",
    "tool.catalog.read",
    "tool.run.create",
    "tool.run.read",
    "tool.run.cancel",
    "tool.confirmation.respond",
)
WORKSPACE_MENU_ID = "82000000-0000-4000-8000-000000000001"
MENU_BINDINGS = (
    ("82000000-0000-4000-8000-000000000226", "81000000-0000-4000-8000-000000000140", "query"),
    ("82000000-0000-4000-8000-000000000222", "81000000-0000-4000-8000-000000000141", "mutation"),
    ("82000000-0000-4000-8000-000000000227", "81000000-0000-4000-8000-000000000142", "query"),
    ("82000000-0000-4000-8000-000000000227", "81000000-0000-4000-8000-000000000143", "query"),
    ("82000000-0000-4000-8000-000000000227", "81000000-0000-4000-8000-000000000144", "query"),
    ("82000000-0000-4000-8000-000000000224", "81000000-0000-4000-8000-000000000145", "approve"),
    ("82000000-0000-4000-8000-000000000224", "81000000-0000-4000-8000-000000000146", "approve"),
    ("82000000-0000-4000-8000-000000000225", "81000000-0000-4000-8000-000000000147", "mutation"),
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """为既有 Owner 授权，并发布包含工具控制台的 Registry 22 菜单。"""

    schema = _schema()
    _grant_owner_permissions(schema)
    _register_bindings(schema)
    _publish_upgraded_menu_snapshots(schema)


def downgrade() -> None:
    """仅在不存在自定义授权且当前快照仍由本节点生成时恢复 Registry 21。"""

    schema = _schema()
    _reject_custom_grants(schema)
    _restore_menu_snapshots(schema)
    connection = op.get_bind()
    for menu_id, api_resource_id, _ in MENU_BINDINGS:
        connection.execute(
            sa.text(
                f'DELETE FROM "{schema}".registered_menu_api_bindings '
                "WHERE menu_id = CAST(:menu_id AS uuid) "
                "AND api_resource_id = CAST(:api_resource_id AS uuid)"
            ),
            {"menu_id": menu_id, "api_resource_id": api_resource_id},
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
              AND grants.permission_code = ANY(:permission_codes)
            """
        ),
        {"permission_codes": list(PERMISSION_CODES)},
    )


def _grant_owner_permissions(schema: str) -> None:
    """个人与企业 Owner 默认获得全部工具控制台能力，其他角色保持显式授权。"""

    op.get_bind().execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, permissions.permission_code,
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[],
                   'RESTRICTED', ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            CROSS JOIN unnest(CAST(:permission_codes AS varchar[]))
                AS permissions(permission_code)
            WHERE roles.role_key = 'workspace_owner' AND roles.system_managed = true
            ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
            """
        ),
        {"permission_codes": list(PERMISSION_CODES)},
    )


def _register_bindings(schema: str) -> None:
    connection = op.get_bind()
    for menu_id, api_resource_id, action_type in MENU_BINDINGS:
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
    """复制每个当前发布并追加工具菜单，历史与工作空间自定义项保持不变。"""

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
    """追加确定性 Registry 22 发布，并原子切换当前菜单指针。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 22
    menus = cast(list[dict[str, Any]], snapshot["menus"])
    menus.extend(_tool_snapshot_menus())
    snapshot["menus"] = sorted(
        {item["menu_id"]: item for item in menus}.values(),
        key=lambda item: item["menu_id"],
    )
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    bindings.extend(_tool_snapshot_bindings())
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
                :occurred_at, 5
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
            raise RuntimeError("当前菜单发布已在 P4-11 后变化, 拒绝破坏性降级")
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


def _reject_custom_grants(schema: str) -> None:
    """发现非系统 Owner 的工具授权时拒绝降级，避免丢失用户权限事实。"""

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
        {"permission_codes": list(PERMISSION_CODES)},
    )
    if int(count or 0) > 0:
        raise RuntimeError("存在自定义工具控制台授权, 拒绝降级以避免权限事实丢失")


def _tool_snapshot_menus() -> list[dict[str, object]]:
    """生成与 Registry 22 一致的页面和动作菜单快照。"""

    definitions = (
        (
            "221",
            "workspace.tools",
            WORKSPACE_MENU_ID,
            "工具执行",
            "page",
            "013",
            "tool.page.access",
            "wrench",
            390,
        ),
        (
            "222",
            "workspace.tools.execute",
            "82000000-0000-4000-8000-000000000221",
            "创建工具任务",
            "action",
            None,
            "tool.run.create",
            None,
            100,
        ),
        (
            "223",
            "workspace.tool_runs",
            WORKSPACE_MENU_ID,
            "工具任务",
            "page",
            "014",
            "tool.run.read",
            "history",
            395,
        ),
        (
            "224",
            "workspace.tool_runs.confirm",
            "82000000-0000-4000-8000-000000000223",
            "处理工具确认",
            "action",
            None,
            "tool.confirmation.respond",
            None,
            100,
        ),
        (
            "225",
            "workspace.tool_runs.cancel",
            "82000000-0000-4000-8000-000000000223",
            "取消工具任务",
            "action",
            None,
            "tool.run.cancel",
            None,
            110,
        ),
        (
            "226",
            "workspace.tools.catalog_read",
            "82000000-0000-4000-8000-000000000221",
            "查看工具目录",
            "action",
            None,
            "tool.catalog.read",
            None,
            90,
        ),
        (
            "227",
            "workspace.tool_runs.read",
            "82000000-0000-4000-8000-000000000223",
            "查看工具任务",
            "action",
            None,
            "tool.run.read",
            None,
            90,
        ),
    )
    return [
        {
            "menu_id": f"82000000-0000-4000-8000-000000000{suffix}",
            "menu_key": menu_key,
            "parent_menu_id": parent_id,
            "name": name,
            "menu_type": menu_type,
            "page_resource_id": (
                f"80000000-0000-4000-8000-000000000{page_suffix}"
                if page_suffix is not None
                else None
            ),
            "permission_code": permission_code,
            "icon_key": icon_key,
            "sort_order": sort_order,
            "source": "system",
            "status": "active",
            "visible": True,
        }
        for (
            suffix,
            menu_key,
            parent_id,
            name,
            menu_type,
            page_suffix,
            permission_code,
            icon_key,
            sort_order,
        ) in definitions
    ]


def _tool_snapshot_bindings() -> list[dict[str, str]]:
    return [
        {"menu_id": menu_id, "api_resource_id": api_resource_id, "action_type": action_type}
        for menu_id, api_resource_id, action_type in MENU_BINDINGS
    ]


def _upgrade_release_id(workspace_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"ai-platform:p411-menu:{workspace_id}")
