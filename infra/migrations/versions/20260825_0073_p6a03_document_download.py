"""回填 P6A-03 文档详情与原文件下载授权事实。"""

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

revision: str = "20260825_0073"
down_revision: str | None = "20260823_0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOWNLOAD_PERMISSION = "knowledge.document.download"
_KNOWLEDGE_MENU_ID = "82000000-0000-4000-8000-000000000145"
_DOWNLOAD_MENU_ID = "82000000-0000-4000-8000-000000000255"
_DOCUMENT_BINDINGS = (
    (
        "82000000-0000-4000-8000-000000000147",
        "81000000-0000-4000-8000-000000000174",
        "query",
    ),
    (
        _DOWNLOAD_MENU_ID,
        "81000000-0000-4000-8000-000000000175",
        "query",
    ),
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """同步 Registry 26 绑定、菜单快照和既有系统 Owner 下载权限。"""

    schema = _schema()
    _grant_owner_permission(schema)
    _register_menu_bindings(schema)
    _publish_upgraded_menu_snapshots(schema)


def downgrade() -> None:
    """仅在没有后续管理员事实时恢复 Registry 25。"""

    schema = _schema()
    _reject_destructive_downgrade(schema)
    _restore_menu_snapshots(schema)
    _remove_authorization_upgrade(schema)


def _grant_owner_permission(schema: str) -> None:
    """既有个人与企业 Owner 获得下载权，自定义角色仍由管理员显式配置。"""

    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, :permission_code,
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[], 'RESTRICTED', ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            WHERE roles.role_key = 'workspace_owner' AND roles.system_managed = true
            ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
            """
        ).bindparams(permission_code=_DOWNLOAD_PERMISSION)
    )


def _register_menu_bindings(schema: str) -> None:
    """登记文档详情与下载动作，保持数据库镜像和静态 Registry 一致。"""

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
            {"menu_id": menu_id, "api_resource_id": api_id, "action_type": action_type}
            for menu_id, api_id, action_type in _DOCUMENT_BINDINGS
        ],
    )


def _publish_upgraded_menu_snapshots(schema: str) -> None:
    """为已有当前菜单发布追加不可变 Registry 26 快照。"""

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
    """保留已有定制项，并原子切换到确定性的 Registry 26 发布。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 26
    menus = cast(list[dict[str, Any]], snapshot["menus"])
    menus.append(_download_snapshot_menu())
    snapshot["menus"] = sorted(
        {item["menu_id"]: item for item in menus}.values(),
        key=lambda item: item["menu_id"],
    )
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    bindings.extend(_document_snapshot_bindings())
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
                :occurred_at, 8
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


def _download_snapshot_menu() -> dict[str, object]:
    """构造知识页面下的 Registry 26 下载动作菜单。"""

    return {
        "menu_id": _DOWNLOAD_MENU_ID,
        "menu_key": "navigation.workspace.knowledge.document_download",
        "parent_menu_id": _KNOWLEDGE_MENU_ID,
        "name": "下载文档原文件",
        "menu_type": "action",
        "page_resource_id": None,
        "permission_code": _DOWNLOAD_PERMISSION,
        "icon_key": None,
        "sort_order": 510,
        "source": "system",
        "status": "active",
        "visible": True,
    }


def _document_snapshot_bindings() -> list[dict[str, str]]:
    """构造文档详情与下载 API 的 Registry 26 快照关系。"""

    return [
        {"menu_id": menu_id, "api_resource_id": api_id, "action_type": action_type}
        for menu_id, api_id, action_type in _DOCUMENT_BINDINGS
    ]


def _upgrade_release_id(workspace_id: UUID) -> UUID:
    """为每个工作空间生成可复核的 P6A-03 菜单发布 ID。"""

    return uuid5(NAMESPACE_URL, f"ai-platform:p6a03-menu:{workspace_id}")


def _reject_destructive_downgrade(schema: str) -> None:
    """存在自定义下载授权时拒绝降级，避免静默丢失管理员配置。"""

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
        {"permission_code": _DOWNLOAD_PERMISSION},
    )
    if int(count or 0) > 0:
        raise RuntimeError("存在自定义文档下载授权, 拒绝降级以避免权限事实丢失")


def _restore_menu_snapshots(schema: str) -> None:
    """仅恢复本 Revision 创建且未被后续发布替换的菜单快照。"""

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
            raise RuntimeError("当前菜单发布已在 P6A-03 后变化, 拒绝破坏性降级")
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
    """移除 Registry 26 绑定和本 Revision 注入的系统 Owner 授权。"""

    connection = op.get_bind()
    connection.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE (menu_id, api_resource_id) IN (
                ('82000000-0000-4000-8000-000000000147'::uuid,
                 '81000000-0000-4000-8000-000000000174'::uuid),
                ('82000000-0000-4000-8000-000000000255'::uuid,
                 '81000000-0000-4000-8000-000000000175'::uuid)
            )
            """
        )
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
        {"permission_code": _DOWNLOAD_PERMISSION},
    )
