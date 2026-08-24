"""建立知识目录、标签、文档关系和收藏事实。"""

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

revision: str = "20260823_0072"
down_revision: str | None = "20260820_0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_DEFAULT_FOLDER_NAME = "全部文件"
_DEFAULT_FOLDER_NAMESPACE = UUID("3c7db2a5-9f72-4a72-8e0b-8d4db53ce5f0")
_KNOWLEDGE_MENU_ID = "82000000-0000-4000-8000-000000000145"
_ORGANIZATION_PERMISSIONS = (
    "knowledge.document.favorite",
    "knowledge.document.folder.bind",
    "knowledge.document.tag.bind",
    "knowledge.folder.create",
    "knowledge.folder.delete",
    "knowledge.folder.purge",
    "knowledge.folder.read",
    "knowledge.folder.restore",
    "knowledge.folder.update",
    "knowledge.tag.create",
    "knowledge.tag.delete",
    "knowledge.tag.read",
    "knowledge.tag.restore",
    "knowledge.tag.update",
    "knowledge.trash.purge",
    "knowledge.trash.read",
    "knowledge.trash.restore",
)
_ORGANIZATION_MENU_DEFINITIONS = (
    ("234", "folder_list", "查看文件夹", "knowledge.folder.read", 300),
    ("235", "folder_create", "创建文件夹", "knowledge.folder.create", 310),
    ("236", "folder_rename", "重命名文件夹", "knowledge.folder.update", 320),
    ("237", "folder_move", "移动文件夹", "knowledge.folder.update", 330),
    ("238", "folder_delete", "删除文件夹", "knowledge.folder.delete", 340),
    ("239", "folder_restore", "恢复文件夹", "knowledge.folder.restore", 350),
    ("240", "folder_purge", "永久删除文件夹", "knowledge.folder.purge", 360),
    ("241", "tag_list", "查看标签", "knowledge.tag.read", 370),
    ("242", "tag_create", "创建标签", "knowledge.tag.create", 380),
    ("243", "tag_update", "编辑标签", "knowledge.tag.update", 390),
    ("244", "tag_delete", "删除标签", "knowledge.tag.delete", 400),
    ("245", "tag_restore", "恢复标签", "knowledge.tag.restore", 410),
    (
        "246",
        "document_folder_bind",
        "绑定文档文件夹",
        "knowledge.document.folder.bind",
        420,
    ),
    (
        "247",
        "document_folder_unbind",
        "解绑文档文件夹",
        "knowledge.document.folder.bind",
        430,
    ),
    (
        "248",
        "document_tag_bind",
        "绑定文档标签",
        "knowledge.document.tag.bind",
        440,
    ),
    (
        "249",
        "document_tag_unbind",
        "解绑文档标签",
        "knowledge.document.tag.bind",
        450,
    ),
    (
        "250",
        "document_favorite_set",
        "设置文档收藏",
        "knowledge.document.favorite",
        460,
    ),
    (
        "251",
        "document_favorite_list",
        "查看文档收藏",
        "knowledge.document.favorite",
        470,
    ),
    ("252", "trash_list", "查看回收站", "knowledge.trash.read", 480),
    ("253", "document_restore", "恢复回收站文档", "knowledge.trash.restore", 490),
    ("254", "document_purge", "永久清理回收站文档", "knowledge.trash.purge", 500),
)
_ORGANIZATION_MENU_BINDINGS = tuple(
    (
        f"82000000-0000-4000-8000-000000000{menu_suffix}",
        f"81000000-0000-4000-8000-000000000{int(menu_suffix) - 81:03d}",
        "query" if menu_suffix in {"234", "241", "251", "252"} else "mutation",
    )
    for menu_suffix, *_ in _ORGANIZATION_MENU_DEFINITIONS
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """创建第一版个人文件型知识管理的组织关系事实。"""

    schema = _schema()
    # Worker 可能已写入解析元数据但尚未回写产物键。
    # 清理链会按任务身份推导确定性键，不能让数据库约束阻断这条收敛路径。
    op.drop_constraint("ck_ingestion_jobs_result", "ingestion_jobs", type_="check", schema=schema)
    op.create_check_constraint(
        "ck_ingestion_jobs_result",
        "ingestion_jobs",
        "(status = 'succeeded' AND completed_at IS NOT NULL "
        "AND (artifact_object_key IS NULL OR char_length(btrim(artifact_object_key)) > 0) "
        "AND parsed_content_hash ~ '^[0-9a-f]{64}$' AND parser_name IS NOT NULL "
        "AND ocr_used IS NOT NULL AND page_count >= 1 AND block_count >= 1) OR "
        "(status <> 'succeeded' AND artifact_object_key IS NULL "
        "AND parsed_content_hash IS NULL AND parser_name IS NULL AND ocr_used IS NULL "
        "AND page_count IS NULL AND block_count IS NULL)",
        schema=schema,
    )
    op.create_table(
        "knowledge_folders",
        sa.Column("folder_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("parent_folder_id", sa.UUID(), nullable=True),
        sa.Column("created_by_account_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("folder_id"),
        sa.UniqueConstraint(
            "workspace_id", "folder_id", name="uq_knowledge_folders_workspace_folder"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "parent_folder_id",
            "folder_id",
            name="uq_knowledge_folders_parent_folder",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "parent_folder_id"],
            [f"{schema}.knowledge_folders.workspace_id", f"{schema}.knowledge_folders.folder_id"],
            name="fk_knowledge_folders_parent",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_knowledge_folders_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_knowledge_folders_creator",
        ),
        sa.CheckConstraint("status IN ('active', 'deleted')", name="ck_knowledge_folders_status"),
        sa.CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL) OR "
            "(status = 'active' AND deleted_at IS NULL)",
            name="ck_knowledge_folders_deleted_at",
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120", name="ck_knowledge_folders_name"
        ),
        sa.CheckConstraint("version >= 1", name="ck_knowledge_folders_version"),
        sa.CheckConstraint(
            "NOT is_default OR (name = '全部文件' AND parent_folder_id IS NULL "
            "AND status = 'active' AND deleted_at IS NULL)",
            name="ck_knowledge_folders_default_shape",
        ),
        schema=schema,
    )
    op.create_index(
        "uq_knowledge_folders_active_name",
        "knowledge_folders",
        [
            "workspace_id",
            sa.text("coalesce(parent_folder_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            sa.text("lower(name)"),
        ],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        schema=schema,
    )
    op.create_index(
        "ix_knowledge_folders_workspace_parent_status",
        "knowledge_folders",
        ["workspace_id", "parent_folder_id", "status"],
        schema=schema,
    )
    op.create_index(
        "uq_knowledge_folders_default",
        "knowledge_folders",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
        schema=schema,
    )

    op.create_table(
        "knowledge_tags",
        sa.Column("tag_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("color", sa.String(length=32), nullable=True),
        sa.Column("created_by_account_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("tag_id"),
        sa.UniqueConstraint("workspace_id", "tag_id", name="uq_knowledge_tags_workspace_tag"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_knowledge_tags_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_knowledge_tags_creator",
        ),
        sa.CheckConstraint("status IN ('active', 'deleted')", name="ck_knowledge_tags_status"),
        sa.CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL) OR "
            "(status = 'active' AND deleted_at IS NULL)",
            name="ck_knowledge_tags_deleted_at",
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 80", name="ck_knowledge_tags_name"
        ),
        sa.CheckConstraint("version >= 1", name="ck_knowledge_tags_version"),
        schema=schema,
    )
    op.create_index(
        "uq_knowledge_tags_active_name",
        "knowledge_tags",
        ["workspace_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        schema=schema,
    )

    op.create_table(
        "document_folder_bindings",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("folder_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "document_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            [f"{schema}.documents.workspace_id", f"{schema}.documents.document_id"],
            name="fk_document_folder_bindings_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "folder_id"],
            [f"{schema}.knowledge_folders.workspace_id", f"{schema}.knowledge_folders.folder_id"],
            name="fk_document_folder_bindings_folder",
            ondelete="CASCADE",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_document_folder_bindings_workspace_folder",
        "document_folder_bindings",
        ["workspace_id", "folder_id"],
        schema=schema,
    )

    # 为存量空间建立确定性根目录，并将已有活动/回收站文档补入默认目录。
    bind = op.get_bind()
    workspace_rows = bind.execute(
        sa.text(
            f"SELECT workspace_id, workspace_type, owner_account_id, created_at "
            f'FROM "{schema}".workspaces ORDER BY workspace_id'
        )
    ).mappings()
    for workspace in workspace_rows:
        workspace_id = workspace["workspace_id"]
        creator_id = workspace["owner_account_id"]
        if creator_id is None:
            creator_id = bind.scalar(
                sa.text(
                    f'SELECT account_id FROM "{schema}".workspace_memberships '
                    "WHERE workspace_id = :workspace_id "
                    "AND membership_type = 'owner' AND status = 'active' "
                    "ORDER BY account_id LIMIT 1"
                ),
                {"workspace_id": workspace_id},
            )
        if creator_id is None:
            legacy_document_count = bind.scalar(
                sa.text(
                    f'SELECT count(*) FROM "{schema}".documents '
                    "WHERE workspace_id = :workspace_id AND status IN ('active', 'deleted')"
                ),
                {"workspace_id": workspace_id},
            )
            if int(legacy_document_count or 0) > 0:
                # 没有可验证账号时不能伪造 created_by_account_id。
                # 已有文档若跳过目录会破坏组织不变量，必须让迁移失败并人工修复。
                raise RuntimeError(
                    f"工作空间 {workspace_id} 缺少可验证的活动 owner, 且仍有文档待回填"
                )
            # 历史测试和早期导入可能留下没有 Owner 成员的空空间。
            # 空空间没有待迁移事实，保留它比伪造账号更安全。
            continue
        root_id = uuid5(_DEFAULT_FOLDER_NAMESPACE, str(workspace_id))
        created_at = workspace["created_at"]
        bind.execute(
            sa.text(
                f'INSERT INTO "{schema}".knowledge_folders '
                "(folder_id, workspace_id, name, parent_folder_id, created_by_account_id, "
                "created_at, updated_at, status, deleted_at, version, is_default) "
                "VALUES (:folder_id, :workspace_id, :name, NULL, :creator_id, "
                ":created_at, :updated_at, 'active', NULL, 1, true) "
                "ON CONFLICT (folder_id) DO NOTHING"
            ),
            {
                "folder_id": root_id,
                "workspace_id": workspace_id,
                "name": _DEFAULT_FOLDER_NAME,
                "creator_id": creator_id,
                "created_at": created_at,
                "updated_at": created_at,
            },
        )
        bind.execute(
            sa.text(
                f'INSERT INTO "{schema}".document_folder_bindings '
                "(workspace_id, document_id, folder_id, created_at) "
                f"SELECT d.workspace_id, d.document_id, :folder_id, d.created_at "
                f'FROM "{schema}".documents d '
                "WHERE d.workspace_id = :workspace_id "
                "AND d.status IN ('active', 'deleted') "
                "ON CONFLICT (workspace_id, document_id) DO NOTHING"
            ),
            {"folder_id": root_id, "workspace_id": workspace_id},
        )

    op.create_table(
        "document_tag_bindings",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("tag_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "document_id", "tag_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            [f"{schema}.documents.workspace_id", f"{schema}.documents.document_id"],
            name="fk_document_tag_bindings_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tag_id"],
            [f"{schema}.knowledge_tags.workspace_id", f"{schema}.knowledge_tags.tag_id"],
            name="fk_document_tag_bindings_tag",
            ondelete="CASCADE",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_document_tag_bindings_workspace_tag",
        "document_tag_bindings",
        ["workspace_id", "tag_id"],
        schema=schema,
    )

    op.create_table(
        "document_favorites",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "account_id", "document_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            [f"{schema}.documents.workspace_id", f"{schema}.documents.document_id"],
            name="fk_document_favorites_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], [f"{schema}.accounts.account_id"], name="fk_document_favorites_account"
        ),
        schema=schema,
    )
    op.create_index(
        "ix_document_favorites_workspace_account_created",
        "document_favorites",
        ["workspace_id", "account_id", "created_at"],
        schema=schema,
    )
    # 组织 API 必须与默认 Owner 授权和当前菜单快照同时生效，避免页面存在但动作全部被裁剪。
    _grant_owner_permissions(schema)
    _register_menu_bindings(schema)
    _publish_upgraded_menu_snapshots(schema)


def downgrade() -> None:
    """仅在没有用户组织事实和后续授权配置时恢复 Registry 24。"""

    schema = _schema()
    _reject_destructive_downgrade(schema)
    op.drop_constraint("ck_ingestion_jobs_result", "ingestion_jobs", type_="check", schema=schema)
    op.create_check_constraint(
        "ck_ingestion_jobs_result",
        "ingestion_jobs",
        "(status = 'succeeded' AND completed_at IS NOT NULL "
        "AND artifact_object_key IS NOT NULL "
        "AND parsed_content_hash ~ '^[0-9a-f]{64}$' AND parser_name IS NOT NULL "
        "AND ocr_used IS NOT NULL AND page_count >= 1 AND block_count >= 1) OR "
        "(status <> 'succeeded' AND artifact_object_key IS NULL "
        "AND parsed_content_hash IS NULL AND parser_name IS NULL AND ocr_used IS NULL "
        "AND page_count IS NULL AND block_count IS NULL)",
        schema=schema,
    )
    _restore_menu_snapshots(schema)
    _remove_authorization_upgrade(schema)
    op.drop_index(
        "ix_document_favorites_workspace_account_created",
        table_name="document_favorites",
        schema=schema,
    )
    op.drop_table("document_favorites", schema=schema)
    op.drop_index(
        "ix_document_tag_bindings_workspace_tag", table_name="document_tag_bindings", schema=schema
    )
    op.drop_table("document_tag_bindings", schema=schema)
    op.drop_index(
        "ix_document_folder_bindings_workspace_folder",
        table_name="document_folder_bindings",
        schema=schema,
    )
    op.drop_table("document_folder_bindings", schema=schema)
    op.drop_index("uq_knowledge_tags_active_name", table_name="knowledge_tags", schema=schema)
    op.drop_table("knowledge_tags", schema=schema)
    op.drop_index(
        "ix_knowledge_folders_workspace_parent_status",
        table_name="knowledge_folders",
        schema=schema,
    )
    op.drop_index("uq_knowledge_folders_default", table_name="knowledge_folders", schema=schema)
    op.drop_index("uq_knowledge_folders_active_name", table_name="knowledge_folders", schema=schema)
    op.drop_table("knowledge_folders", schema=schema)


def _grant_owner_permissions(schema: str) -> None:
    """为存量个人和企业 Owner 回填知识组织动作权限。"""

    permissions = ", ".join(f"('{code}')" for code in _ORGANIZATION_PERMISSIONS)
    op.get_bind().execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, codes.permission_code,
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[],
                   'RESTRICTED', ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            CROSS JOIN (VALUES {permissions}) AS codes(permission_code)
            WHERE roles.role_key = 'workspace_owner' AND roles.system_managed = true
            ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
            """
        )
    )


def _register_menu_bindings(schema: str) -> None:
    """登记 Registry 25 的知识组织菜单/API 绑定。"""

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
            for menu_id, api_id, action_type in _ORGANIZATION_MENU_BINDINGS
        ],
    )


def _publish_upgraded_menu_snapshots(schema: str) -> None:
    """为已有当前菜单发布追加不可变 Registry 25 快照。"""

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
    """复制当前快照并原子切换到确定性的 Registry 25 发布。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 25
    # 菜单和绑定按稳定标识去重，兼容已包含部分系统动作的历史自定义发布。
    menus = cast(list[dict[str, Any]], snapshot["menus"])
    menus.extend(_organization_snapshot_menus())
    snapshot["menus"] = sorted(
        {item["menu_id"]: item for item in menus}.values(),
        key=lambda item: item["menu_id"],
    )
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    bindings.extend(_organization_snapshot_bindings())
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
    # 发布事实保持不可变，当前指针只在新事实成功写入后切换。
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


def _organization_snapshot_menus() -> list[dict[str, object]]:
    """构造知识页面下的 Registry 25 动作菜单。"""

    return [
        {
            "menu_id": f"82000000-0000-4000-8000-000000000{suffix}",
            "menu_key": f"navigation.workspace.knowledge.{key}",
            "parent_menu_id": _KNOWLEDGE_MENU_ID,
            "name": name,
            "menu_type": "action",
            "page_resource_id": None,
            "permission_code": permission,
            "icon_key": None,
            "sort_order": sort_order,
            "source": "system",
            "status": "active",
            "visible": True,
        }
        for suffix, key, name, permission, sort_order in _ORGANIZATION_MENU_DEFINITIONS
    ]


def _organization_snapshot_bindings() -> list[dict[str, str]]:
    """构造知识组织动作与 API 的 Registry 25 快照关系。"""

    return [
        {"menu_id": menu_id, "api_resource_id": api_id, "action_type": action_type}
        for menu_id, api_id, action_type in _ORGANIZATION_MENU_BINDINGS
    ]


def _upgrade_release_id(workspace_id: UUID) -> UUID:
    """为每个工作空间生成可复核的 P6A-02 菜单发布 ID。"""

    return uuid5(NAMESPACE_URL, f"ai-platform:p6a02-menu:{workspace_id}")


def _reject_destructive_downgrade(schema: str) -> None:
    """用户组织事实或自定义授权存在时拒绝丢数据降级。"""

    connection = op.get_bind()
    missing_artifact_count = connection.scalar(
        sa.text(
            f'SELECT count(*) FROM "{schema}".ingestion_jobs '
            "WHERE status = 'succeeded' AND artifact_object_key IS NULL"
        )
    )
    if int(missing_artifact_count or 0) > 0:
        raise RuntimeError("存在尚未回写解析产物键的成功任务, 拒绝恢复旧约束")
    organization_count = connection.scalar(
        sa.text(
            f"""
            SELECT
              (SELECT count(*) FROM "{schema}".knowledge_folders WHERE NOT is_default) +
              (SELECT count(*) FROM "{schema}".knowledge_tags) +
              (SELECT count(*) FROM "{schema}".document_tag_bindings) +
              (SELECT count(*) FROM "{schema}".document_favorites)
            """
        )
    )
    if int(organization_count or 0) > 0:
        raise RuntimeError("存在用户创建的知识组织事实, 拒绝破坏性降级")

    permissions = ", ".join(f"'{code}'" for code in _ORGANIZATION_PERMISSIONS)
    custom_grants = connection.scalar(
        sa.text(
            f"""
            SELECT count(*)
            FROM "{schema}".role_permission_grants AS grants
            LEFT JOIN "{schema}".roles AS roles
              ON roles.workspace_id = grants.workspace_id AND roles.role_id = grants.role_id
            WHERE grants.permission_code IN ({permissions})
              AND NOT (roles.role_key = 'workspace_owner' AND roles.system_managed = true)
            """
        )
    )
    if int(custom_grants or 0) > 0:
        raise RuntimeError("存在自定义知识组织授权, 拒绝降级以避免权限事实丢失")


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
            raise RuntimeError("当前菜单发布已在 P6A-02 后变化, 拒绝破坏性降级")
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
    """移除 Registry 25 绑定和本 Revision 注入的系统 Owner 授权。"""

    permissions = ", ".join(f"'{code}'" for code in _ORGANIZATION_PERMISSIONS)
    connection = op.get_bind()
    connection.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE menu_id BETWEEN
                  '82000000-0000-4000-8000-000000000234'::uuid AND
                  '82000000-0000-4000-8000-000000000254'::uuid
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
              AND grants.permission_code IN ({permissions})
            """
        )
    )
