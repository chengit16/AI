"""登记 P6B-02 团队治理权限、接口绑定与不可变菜单快照。"""

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

revision: str = "20260831_0077"
down_revision: str | None = "20260831_0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MEMBERS_MENU_ID = "82000000-0000-4000-8000-000000000003"
_PERMISSION_CODES = (
    "workspace.team.read",
    "workspace.invitation.cancel",
    "workspace.member.update",
    "workspace.member.activate",
    "workspace.member.remove",
)
_MENU_DEFINITIONS = (
    ("259", "team_query", "查看团队治理", "workspace.team.read", 130),
    ("260", "invitation_cancel", "撤销邀请", "workspace.invitation.cancel", 140),
    ("261", "update", "编辑成员配置", "workspace.member.update", 150),
    ("262", "activate", "恢复成员", "workspace.member.activate", 160),
    ("263", "remove", "移除成员", "workspace.member.remove", 170),
)
_MENU_BINDINGS = (
    (
        "82000000-0000-4000-8000-000000000259",
        "81000000-0000-4000-8000-000000000184",
        "query",
    ),
    (
        "82000000-0000-4000-8000-000000000260",
        "81000000-0000-4000-8000-000000000185",
        "mutation",
    ),
    (
        "82000000-0000-4000-8000-000000000261",
        "81000000-0000-4000-8000-000000000186",
        "mutation",
    ),
    (
        "82000000-0000-4000-8000-000000000262",
        "81000000-0000-4000-8000-000000000187",
        "mutation",
    ),
    (
        "82000000-0000-4000-8000-000000000263",
        "81000000-0000-4000-8000-000000000188",
        "mutation",
    ),
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """为存量企业 Owner 回填权限，并发布 Registry 30 菜单快照。"""

    schema = _schema()
    _grant_enterprise_owner_permissions(schema)
    _register_menu_bindings(schema)
    _publish_upgraded_menu_snapshots(schema)


def downgrade() -> None:
    """仅在没有自定义授权或后续菜单发布时恢复 Registry 29。"""

    schema = _schema()
    _reject_custom_grants(schema)
    _restore_menu_snapshots(schema)
    _remove_authorization_upgrade(schema)


def _grant_enterprise_owner_permissions(schema: str) -> None:
    """只回填存量企业系统 Owner，自定义角色仍由管理员显式授权。"""

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
            JOIN "{schema}".workspaces AS workspaces
              ON workspaces.workspace_id = roles.workspace_id
            CROSS JOIN unnest(CAST(:permission_codes AS varchar[]))
                AS permissions(permission_code)
            WHERE workspaces.workspace_type = 'enterprise'
              AND roles.role_key = 'workspace_owner'
              AND roles.system_managed = true
            ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
            """
        ),
        {"permission_codes": list(_PERMISSION_CODES)},
    )


def _register_menu_bindings(schema: str) -> None:
    """幂等登记团队治理动作和五个授权接口的关系。"""

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


def _publish_upgraded_menu_snapshots(schema: str) -> None:
    """保留每个空间当前菜单事实，追加 Registry 30 团队治理动作。"""

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
    """生成确定性发布，不覆盖管理员已有的菜单定制。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 30
    menus = cast(list[dict[str, Any]], snapshot["menus"])
    menus.extend(_team_snapshot_menus())
    snapshot["menus"] = sorted(
        {item["menu_id"]: item for item in menus}.values(),
        key=lambda item: item["menu_id"],
    )
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    bindings.extend(_team_snapshot_bindings())
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
                :occurred_at, 12
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


def _team_snapshot_menus() -> list[dict[str, object]]:
    """构造成员管理页面下的五个 Registry 30 动作菜单。"""

    return [
        {
            "menu_id": f"82000000-0000-4000-8000-000000000{suffix}",
            "menu_key": f"navigation.workspace.members.{key}",
            "parent_menu_id": _MEMBERS_MENU_ID,
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
        for suffix, key, name, permission_code, sort_order in _MENU_DEFINITIONS
    ]


def _team_snapshot_bindings() -> list[dict[str, str]]:
    """构造五个团队治理动作的 Registry 30 快照绑定。"""

    return [
        {
            "menu_id": menu_id,
            "api_resource_id": api_resource_id,
            "action_type": action_type,
        }
        for menu_id, api_resource_id, action_type in _MENU_BINDINGS
    ]


def _upgrade_release_id(workspace_id: UUID) -> UUID:
    """为每个工作空间生成可复核且跨重试稳定的 P6B-02 发布 ID。"""

    return uuid5(NAMESPACE_URL, f"ai-platform:p6b02-menu:{workspace_id}")


def _reject_custom_grants(schema: str) -> None:
    """存在非系统 Owner 团队授权时拒绝降级，避免丢失管理员事实。"""

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
        {"permission_codes": list(_PERMISSION_CODES)},
    )
    if int(count or 0) > 0:
        raise RuntimeError("存在自定义团队治理授权, 拒绝降级以避免权限事实丢失")


def _restore_menu_snapshots(schema: str) -> None:
    """拒绝覆盖后续发布，只撤销本 Revision 产生的确定性快照。"""

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
            raise RuntimeError("当前菜单发布已在 P6B-02 后变化, 拒绝破坏性降级")
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


def _remove_authorization_upgrade(schema: str) -> None:
    """移除五组绑定及本 Revision 注入的企业系统 Owner 授权。"""

    connection = op.get_bind()
    connection.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE api_resource_id = ANY(CAST(:api_resource_ids AS uuid[]))
            """
        ),
        {"api_resource_ids": [api_id for _, api_id, _ in _MENU_BINDINGS]},
    )
    connection.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".role_permission_grants AS grants
            USING "{schema}".roles AS roles, "{schema}".workspaces AS workspaces
            WHERE grants.workspace_id = roles.workspace_id
              AND grants.role_id = roles.role_id
              AND workspaces.workspace_id = grants.workspace_id
              AND workspaces.workspace_type = 'enterprise'
              AND roles.role_key = 'workspace_owner'
              AND roles.system_managed = true
              AND grants.permission_code = ANY(:permission_codes)
            """
        ),
        {"permission_codes": list(_PERMISSION_CODES)},
    )
