"""建立 P6A-04 最近访问投影和工作台搜索接口绑定。"""

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

revision: str = "20260825_0074"
down_revision: str | None = "20260825_0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOCUMENT_MENU_ID = "82000000-0000-4000-8000-000000000147"
_WORKBENCH_BINDINGS = (
    (_DOCUMENT_MENU_ID, "81000000-0000-4000-8000-000000000176", "query"),
    (_DOCUMENT_MENU_ID, "81000000-0000-4000-8000-000000000177", "mutation"),
    (_DOCUMENT_MENU_ID, "81000000-0000-4000-8000-000000000178", "query"),
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """创建最近访问表，并把工作台接口加入不可变 Registry 27 快照。"""

    schema = _schema()
    op.create_table(
        "document_accesses",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            [f"{schema}.documents.workspace_id", f"{schema}.documents.document_id"],
            name="fk_document_accesses_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_document_accesses_account",
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "account_id",
            "document_id",
            name="pk_document_accesses",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_document_accesses_workspace_account_time",
        "document_accesses",
        ["workspace_id", "account_id", "last_accessed_at"],
        schema=schema,
    )
    _register_menu_bindings(schema)
    _publish_upgraded_menu_snapshots(schema)


def downgrade() -> None:
    """仅在没有用户访问数据和后续菜单发布时恢复 Registry 26。"""

    schema = _schema()
    _reject_destructive_downgrade(schema)
    _restore_menu_snapshots(schema)
    _remove_menu_bindings(schema)
    op.drop_index(
        "ix_document_accesses_workspace_account_time",
        table_name="document_accesses",
        schema=schema,
    )
    op.drop_table("document_accesses", schema=schema)


def _register_menu_bindings(schema: str) -> None:
    """登记工作台聚合、访问和搜索接口与文档读取动作的关系。"""

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
            for menu_id, api_id, action_type in _WORKBENCH_BINDINGS
        ],
    )


def _publish_upgraded_menu_snapshots(schema: str) -> None:
    """为每个已有当前发布追加确定性的 Registry 27 不可变快照。"""

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
    """保留管理员既有菜单，仅追加本节点接口绑定并切换当前发布。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 27
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    bindings.extend(
        {
            "menu_id": menu_id,
            "api_resource_id": api_resource_id,
            "action_type": action_type,
        }
        for menu_id, api_resource_id, action_type in _WORKBENCH_BINDINGS
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
                :occurred_at, 9
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
    """生成可复核且跨重试稳定的 P6A-04 菜单发布 ID。"""

    return uuid5(NAMESPACE_URL, f"ai-platform:p6a04-menu:{workspace_id}")


def _reject_destructive_downgrade(schema: str) -> None:
    """已有最近访问事实时拒绝删除表，避免静默丢失用户活动。"""

    count = op.get_bind().scalar(sa.text(f'SELECT count(*) FROM "{schema}".document_accesses'))
    if int(count or 0) > 0:
        raise RuntimeError("存在个人工作台最近访问数据, 拒绝破坏性降级")


def _restore_menu_snapshots(schema: str) -> None:
    """只恢复本 Revision 创建且尚未被后续发布替换的菜单快照。"""

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
            raise RuntimeError("当前菜单发布已在 P6A-04 后变化, 拒绝破坏性降级")
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
    """移除只属于 Registry 27 的三条数据库接口绑定。"""

    op.get_bind().execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE api_resource_id = ANY(CAST(:api_resource_ids AS uuid[]))
            """
        ),
        {"api_resource_ids": [api_id for _, api_id, _ in _WORKBENCH_BINDINGS]},
    )
