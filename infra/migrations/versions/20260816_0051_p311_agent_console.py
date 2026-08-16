"""激活 P3-11 Agent 与服务控制台权限、绑定和菜单发布快照。"""

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

revision: str = "20260816_0051"
down_revision: str | None = "20260816_0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNER_PERMISSIONS = (
    "agent.definition.archive",
    "agent.definition.create",
    "agent.definition.read",
    "agent.definition.update",
    "agent.page.access",
    "agent.release.approve",
    "agent.release.publish",
    "agent.release.read",
    "agent.release.request",
    "agent.test.execute",
    "agent.test.read",
    "service.definition.create",
    "service.definition.update",
    "service.page.access",
    "service.route.canary",
    "service.route.promote",
    "service.route.rollback",
)
MEMBER_PERMISSIONS = (
    "agent.definition.read",
    "agent.page.access",
    "agent.release.read",
    "agent.test.read",
    "service.page.access",
)
CONSOLE_BINDINGS = (
    (215, 123, "query"),
    (202, 124, "mutation"),
    (203, 125, "mutation"),
    (204, 126, "mutation"),
    (205, 127, "mutation"),
    (216, 128, "query"),
    (206, 129, "mutation"),
    (217, 130, "query"),
    (207, 131, "approve"),
    (208, 132, "publish"),
    (218, 133, "query"),
    (210, 134, "mutation"),
    (211, 135, "mutation"),
    (212, 136, "publish"),
    (213, 137, "publish"),
    (214, 138, "publish"),
)
WORKSPACE_MENU_ID = "82000000-0000-4000-8000-000000000001"
AGENT_MENU_ID = "82000000-0000-4000-8000-000000000201"
SERVICE_MENU_ID = "82000000-0000-4000-8000-000000000209"


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """为既有角色和菜单发布追加 Registry 20 控制台能力。"""

    schema = _schema()
    _grant_permissions(schema)
    _register_bindings(schema)
    _publish_upgraded_menu_snapshots(schema)


def downgrade() -> None:
    """只恢复仍指向本次升级快照的空间，并移除本节点授权绑定。"""

    schema = _schema()
    _restore_menu_snapshots(schema)
    api_ids = ", ".join(
        f"'81000000-0000-4000-8000-{api_number:012d}'::uuid"
        for _, api_number, _ in CONSOLE_BINDINGS
    )
    owner_permissions = ", ".join(f"'{code}'" for code in OWNER_PERMISSIONS)
    member_permissions = ", ".join(f"'{code}'" for code in MEMBER_PERMISSIONS)
    op.execute(
        sa.text(
            f'DELETE FROM "{schema}".registered_menu_api_bindings '
            f"WHERE api_resource_id IN ({api_ids})"
        )
    )
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".role_permission_grants AS grants
            USING "{schema}".roles AS roles
            WHERE grants.workspace_id = roles.workspace_id
              AND grants.role_id = roles.role_id
              AND roles.system_managed = true
              AND (
                (roles.role_key = 'workspace_owner'
                 AND grants.permission_code IN ({owner_permissions}))
                OR
                (roles.role_key = 'workspace_member'
                 AND grants.permission_code IN ({member_permissions}))
              )
            """
        )
    )


def _grant_permissions(schema: str) -> None:
    """Owner 获得完整管理能力，Member 只获得 Agent 与服务只读页面。"""

    owner_values = ", ".join(f"('{code}')" for code in OWNER_PERMISSIONS)
    member_values = ", ".join(f"('{code}')" for code in MEMBER_PERMISSIONS)
    for role_key, values, maximum_level in (
        ("workspace_owner", owner_values, "RESTRICTED"),
        ("workspace_member", member_values, "INTERNAL"),
    ):
        op.execute(
            sa.text(
                f"""
                INSERT INTO "{schema}".role_permission_grants (
                    workspace_id, role_id, permission_code, scope_type,
                    department_ids, resource_ids, maximum_security_level, field_mask
                )
                SELECT roles.workspace_id, roles.role_id, permission_code, 'workspace',
                       ARRAY[]::uuid[], ARRAY[]::uuid[], '{maximum_level}', ARRAY[]::varchar[]
                FROM "{schema}".roles AS roles
                CROSS JOIN (VALUES {values}) AS permission_codes(permission_code)
                WHERE roles.role_key = '{role_key}' AND roles.system_managed = true
                ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
                """
            )
        )


def _register_bindings(schema: str) -> None:
    values = ", ".join(
        f"('82000000-0000-4000-8000-{menu:012d}'::uuid, "
        f"'81000000-0000-4000-8000-{api:012d}'::uuid, '{action}')"
        for menu, api, action in CONSOLE_BINDINGS
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


def _publish_upgraded_menu_snapshots(schema: str) -> None:
    """复制每个当前发布并追加 Registry 20 菜单，不覆写历史或管理员自定义项。"""

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
    """追加控制台菜单与绑定，并原子切换当前不可变发布。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 20
    menus = cast(list[dict[str, Any]], snapshot["menus"])
    menus.extend(_console_snapshot_menus())
    snapshot["menus"] = sorted(
        {menu["menu_id"]: menu for menu in menus}.values(),
        key=lambda item: item["menu_id"],
    )
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    bindings.extend(_console_snapshot_bindings())
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


def _console_snapshot_menus() -> list[dict[str, object]]:
    definitions = (
        (
            201,
            "workspace.agents",
            WORKSPACE_MENU_ID,
            "Agent 控制台",
            "page",
            10,
            "agent.page.access",
            "bot",
            360,
        ),
        (
            202,
            "workspace.agents.create",
            AGENT_MENU_ID,
            "创建 Agent",
            "action",
            None,
            "agent.definition.create",
            None,
            100,
        ),
        (
            203,
            "workspace.agents.update",
            AGENT_MENU_ID,
            "编辑 Agent 草稿",
            "action",
            None,
            "agent.definition.update",
            None,
            110,
        ),
        (
            204,
            "workspace.agents.archive",
            AGENT_MENU_ID,
            "归档 Agent",
            "action",
            None,
            "agent.definition.archive",
            None,
            120,
        ),
        (
            205,
            "workspace.agents.test",
            AGENT_MENU_ID,
            "执行 Agent 测试",
            "action",
            None,
            "agent.test.execute",
            None,
            130,
        ),
        (
            206,
            "workspace.agents.release_request",
            AGENT_MENU_ID,
            "申请 Agent 发布",
            "action",
            None,
            "agent.release.request",
            None,
            140,
        ),
        (
            207,
            "workspace.agents.release_approve",
            AGENT_MENU_ID,
            "发起 Agent 审批",
            "action",
            None,
            "agent.release.approve",
            None,
            150,
        ),
        (
            208,
            "workspace.agents.release_publish",
            AGENT_MENU_ID,
            "发布 Agent Release",
            "action",
            None,
            "agent.release.publish",
            None,
            160,
        ),
        (
            209,
            "workspace.services",
            WORKSPACE_MENU_ID,
            "服务发布",
            "page",
            11,
            "service.page.access",
            "route",
            370,
        ),
        (
            210,
            "workspace.services.create",
            SERVICE_MENU_ID,
            "创建服务",
            "action",
            None,
            "service.definition.create",
            None,
            100,
        ),
        (
            211,
            "workspace.services.update",
            SERVICE_MENU_ID,
            "更新服务",
            "action",
            None,
            "service.definition.update",
            None,
            110,
        ),
        (
            212,
            "workspace.services.canary",
            SERVICE_MENU_ID,
            "启动灰度",
            "action",
            None,
            "service.route.canary",
            None,
            120,
        ),
        (
            213,
            "workspace.services.promote",
            SERVICE_MENU_ID,
            "晋级正式版本",
            "action",
            None,
            "service.route.promote",
            None,
            130,
        ),
        (
            214,
            "workspace.services.rollback",
            SERVICE_MENU_ID,
            "回滚服务版本",
            "action",
            None,
            "service.route.rollback",
            None,
            140,
        ),
        (
            215,
            "workspace.agents.read",
            AGENT_MENU_ID,
            "查看 Agent",
            "action",
            None,
            "agent.definition.read",
            None,
            170,
        ),
        (
            216,
            "workspace.agents.test_read",
            AGENT_MENU_ID,
            "查看 Agent 测试",
            "action",
            None,
            "agent.test.read",
            None,
            180,
        ),
        (
            217,
            "workspace.agents.release_read",
            AGENT_MENU_ID,
            "查看 Agent Release",
            "action",
            None,
            "agent.release.read",
            None,
            190,
        ),
        (
            218,
            "workspace.services.read",
            SERVICE_MENU_ID,
            "查看服务",
            "action",
            None,
            "service.definition.read",
            None,
            150,
        ),
    )
    return [
        {
            "menu_id": f"82000000-0000-4000-8000-{menu_number:012d}",
            "menu_key": menu_key,
            "parent_menu_id": parent_menu_id,
            "name": name,
            "menu_type": menu_type,
            "page_resource_id": (
                f"80000000-0000-4000-8000-{page_number:012d}" if page_number is not None else None
            ),
            "permission_code": permission_code,
            "icon_key": icon_key,
            "sort_order": sort_order,
            "source": "system",
            "status": "active",
            "visible": True,
        }
        for (
            menu_number,
            menu_key,
            parent_menu_id,
            name,
            menu_type,
            page_number,
            permission_code,
            icon_key,
            sort_order,
        ) in definitions
    ]


def _console_snapshot_bindings() -> list[dict[str, str]]:
    return [
        {
            "menu_id": f"82000000-0000-4000-8000-{menu:012d}",
            "api_resource_id": f"81000000-0000-4000-8000-{api:012d}",
            "action_type": action,
        }
        for menu, api, action in CONSOLE_BINDINGS
    ]


def _upgrade_release_id(workspace_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"ai-platform:p311-menu:{workspace_id}")
