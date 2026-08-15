"""启用 P1F-05 工作流页面，并为已有空间生成新的菜单发布快照。"""

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

revision: str = "20260815_0035"
down_revision: str | None = "20260815_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKFLOW_MENU_ID = "82000000-0000-4000-8000-000000000171"
WORKFLOW_RUN_MENU_ID = "82000000-0000-4000-8000-000000000177"
WORKFLOW_RUN_LIST_API_ID = "81000000-0000-4000-8000-000000000101"


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """补齐角色权限和运行列表绑定，再发布不改写历史的新菜单版本。"""

    schema = _schema()
    _grant_page_access(schema)
    _register_run_list_binding(schema)
    _publish_upgraded_snapshots(schema)


def downgrade() -> None:
    """仅删除本 Revision 创建的新快照，并将当前指针恢复到其来源版本。"""

    schema = _schema()
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
    # 只回退确定性 ID 匹配的升级快照，避免误删管理员在升级后发布的新版本。
    for row in rows:
        workspace_id = cast(UUID, row["workspace_id"])
        if row["release_id"] != _upgrade_release_id(workspace_id):
            continue
        connection.execute(
            sa.text(
                f"""
                UPDATE "{schema}".workspace_menu_publications
                SET current_release_id = :source_release_id, published_at = now()
                WHERE workspace_id = :workspace_id
                """
            ),
            {
                "workspace_id": workspace_id,
                "source_release_id": row["source_release_id"],
            },
        )
        connection.execute(
            sa.text(
                f"""
                DELETE FROM "{schema}".menu_releases
                WHERE workspace_id = :workspace_id AND release_id = :release_id
                """
            ),
            {
                "workspace_id": workspace_id,
                "release_id": row["release_id"],
            },
        )
    connection.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE menu_id = '{WORKFLOW_RUN_MENU_ID}'::uuid
              AND api_resource_id = '{WORKFLOW_RUN_LIST_API_ID}'::uuid
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".role_permission_grants
            WHERE permission_code = 'workflow.page.access'
            """
        )
    )


def _grant_page_access(schema: str) -> None:
    # 个人与企业空间都显示页面；动作权限仍由各系统角色既有授权集合独立裁剪。
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, 'workflow.page.access',
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[],
                   CASE WHEN roles.role_key = 'workspace_owner'
                        THEN 'RESTRICTED' ELSE 'INTERNAL' END,
                   ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            WHERE roles.role_key IN ('workspace_owner', 'workspace_member')
              AND roles.system_managed = true
            ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
            """
        )
    )


def _register_run_list_binding(schema: str) -> None:
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".registered_menu_api_bindings (
                menu_id, api_resource_id, action_type
            ) VALUES (
                '{WORKFLOW_RUN_MENU_ID}'::uuid,
                '{WORKFLOW_RUN_LIST_API_ID}'::uuid,
                'query'
            )
            ON CONFLICT (menu_id, api_resource_id) DO UPDATE
            SET action_type = EXCLUDED.action_type
            """
        )
    )


def _publish_upgraded_snapshots(schema: str) -> None:
    """复制每个当前发布快照并启用工作流入口，原发布记录保持字节级不变。"""

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            f"""
            SELECT releases.*, overrides.visible AS workflow_override_visible,
                   numbers.next_release_number
            FROM "{schema}".workspace_menu_publications AS publications
            JOIN "{schema}".menu_releases AS releases
              ON releases.workspace_id = publications.workspace_id
             AND releases.release_id = publications.current_release_id
            LEFT JOIN "{schema}".workspace_menu_overrides AS overrides
              ON overrides.workspace_id = releases.workspace_id
             AND overrides.menu_id = '{WORKFLOW_MENU_ID}'::uuid
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
    """构造一个可回滚的新发布事实，并原子切换工作空间当前指针。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 15
    for menu in cast(list[dict[str, Any]], snapshot["menus"]):
        if menu["menu_id"] != WORKFLOW_MENU_ID:
            continue
        menu["status"] = "active"
        menu["icon_key"] = "network"
        menu["visible"] = row["workflow_override_visible"] is not False
        break
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    binding = {
        "menu_id": WORKFLOW_RUN_MENU_ID,
        "api_resource_id": WORKFLOW_RUN_LIST_API_ID,
        "action_type": "query",
    }
    if binding not in bindings:
        bindings.append(binding)
        bindings.sort(
            key=lambda item: (item["menu_id"], item["api_resource_id"], item["action_type"])
        )
    digest = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    release_id = _upgrade_release_id(workspace_id)
    now = datetime.now(UTC)
    # 新发布记录引用旧发布作为来源，升级和降级都不触碰旧快照或其摘要。
    parameters = {
        "release_id": release_id,
        "workspace_id": workspace_id,
        "release_number": row["next_release_number"],
        "source_release_id": row["release_id"],
        "snapshot": json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        "snapshot_digest": digest,
        "created_by_account_id": row["created_by_account_id"],
        "occurred_at": now,
    }
    # psycopg 的 prepared statement 每次只接受一条命令；事务原子性由 Alembic 外层保证。
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


def _upgrade_release_id(workspace_id: UUID) -> UUID:
    """由工作空间稳定生成迁移发布 ID，使降级能精确识别本节点事实。"""

    return uuid5(NAMESPACE_URL, f"ai-platform:p1f05-menu:{workspace_id}")
