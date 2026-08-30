"""建立 P6A-05 会话范围、临时附件和冻结检索范围。"""

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

revision: str = "20260830_0075"
down_revision: str | None = "20260825_0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PERMISSIONS = (
    "assistant.attachment.manage",
    "assistant.conversation.scope.manage",
)
_MENU_DEFINITIONS = (
    (
        "82000000-0000-4000-8000-000000000256",
        "navigation.workspace.assistant.scope_manage",
        "配置会话知识范围",
        "assistant.conversation.scope.manage",
        275,
    ),
    (
        "82000000-0000-4000-8000-000000000257",
        "navigation.workspace.assistant.attachment_manage",
        "管理会话临时附件",
        "assistant.attachment.manage",
        278,
    ),
)
_BINDINGS = (
    (
        "82000000-0000-4000-8000-000000000256",
        "81000000-0000-4000-8000-000000000179",
        "mutation",
    ),
    (
        "82000000-0000-4000-8000-000000000257",
        "81000000-0000-4000-8000-000000000180",
        "query",
    ),
    (
        "82000000-0000-4000-8000-000000000257",
        "81000000-0000-4000-8000-000000000181",
        "mutation",
    ),
    (
        "82000000-0000-4000-8000-000000000257",
        "81000000-0000-4000-8000-000000000182",
        "mutation",
    ),
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """追加范围和附件事实，并同步 Registry 28 的授权与菜单发布。"""

    schema = _schema()
    op.add_column(
        "conversations",
        sa.Column("scope_mode", sa.String(32), nullable=False, server_default="workspace"),
        schema=schema,
    )
    op.add_column(
        "conversations",
        sa.Column(
            "knowledge_base_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        schema=schema,
    )
    op.add_column(
        "conversations",
        sa.Column(
            "tag_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_conversations_scope_mode",
        "conversations",
        "scope_mode IN ('workspace', 'selected')",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_conversations_scope_shape",
        "conversations",
        "(scope_mode = 'workspace' AND cardinality(knowledge_base_ids) = 0 "
        "AND cardinality(tag_ids) = 0) OR "
        "(scope_mode = 'selected' AND "
        "cardinality(knowledge_base_ids) + cardinality(tag_ids) BETWEEN 1 AND 40)",
        schema=schema,
    )
    _create_attachments(schema)
    _add_run_scope_columns(schema)
    _protect_run_scope(schema)
    _grant_default_permissions(schema)
    _register_menu_bindings(schema)
    _publish_upgraded_menu_snapshots(schema)


def downgrade() -> None:
    """仅在没有 P6A-05 用户事实和后续菜单发布时恢复 Registry 27。"""

    schema = _schema()
    _reject_destructive_downgrade(schema)
    _restore_menu_snapshots(schema)
    _remove_authorization_upgrade(schema)
    op.execute(sa.text(f'DROP TRIGGER assistant_runs_scope_immutable ON "{schema}".assistant_runs'))
    op.execute(sa.text(f'DROP FUNCTION "{schema}".prevent_assistant_run_scope_mutation()'))
    op.drop_column("assistant_runs", "attachment_ids", schema=schema)
    op.drop_column("assistant_runs", "document_ids", schema=schema)
    op.drop_column("assistant_runs", "knowledge_base_ids", schema=schema)
    op.drop_index(
        "ix_conversation_attachments_conversation_time",
        table_name="conversation_attachments",
        schema=schema,
    )
    op.drop_table("conversation_attachments", schema=schema)
    op.drop_constraint("ck_conversations_scope_shape", "conversations", schema=schema)
    op.drop_constraint("ck_conversations_scope_mode", "conversations", schema=schema)
    op.drop_column("conversations", "tag_ids", schema=schema)
    op.drop_column("conversations", "knowledge_base_ids", schema=schema)
    op.drop_column("conversations", "scope_mode", schema=schema)


def _create_attachments(schema: str) -> None:
    op.create_table(
        "conversation_attachments",
        sa.Column("attachment_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(120), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "attachment_id",
            "workspace_id",
            "conversation_id",
            name="uq_conversation_attachments_identity",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            [f"{schema}.conversations.conversation_id", f"{schema}.conversations.workspace_id"],
            name="fk_conversation_attachments_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_conversation_attachments_creator",
        ),
        sa.CheckConstraint(
            "media_type IN ('text/plain', 'text/markdown', 'text/csv', 'application/json')",
            name="ck_conversation_attachments_media_type",
        ),
        sa.CheckConstraint(
            "size_bytes BETWEEN 1 AND 20000 AND char_length(content) BETWEEN 1 AND 20000",
            name="ck_conversation_attachments_size",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_conversation_attachments_hash",
        ),
        sa.CheckConstraint(
            "char_length(btrim(file_name)) BETWEEN 1 AND 255",
            name="ck_conversation_attachments_name",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_conversation_attachments_conversation_time",
        "conversation_attachments",
        ["workspace_id", "conversation_id", "created_at"],
        schema=schema,
    )


def _add_run_scope_columns(schema: str) -> None:
    op.add_column(
        "assistant_runs",
        sa.Column(
            "knowledge_base_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
        schema=schema,
    )
    op.add_column(
        "assistant_runs",
        sa.Column(
            "document_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
        schema=schema,
    )
    op.add_column(
        "assistant_runs",
        sa.Column(
            "attachment_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        schema=schema,
    )


def _protect_run_scope(schema: str) -> None:
    """冻结 Run 范围和附件标识，状态推进不得改绑模型输入。"""

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".prevent_assistant_run_scope_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.knowledge_base_ids IS DISTINCT FROM OLD.knowledge_base_ids
                   OR NEW.document_ids IS DISTINCT FROM OLD.document_ids
                   OR NEW.attachment_ids IS DISTINCT FROM OLD.attachment_ids THEN
                    RAISE EXCEPTION 'assistant run scope is immutable';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER assistant_runs_scope_immutable
            BEFORE UPDATE ON "{schema}".assistant_runs
            FOR EACH ROW EXECUTE FUNCTION "{schema}".prevent_assistant_run_scope_mutation()
            """
        )
    )


def _grant_default_permissions(schema: str) -> None:
    permissions = ", ".join(f"('{code}')" for code in _PERMISSIONS)
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, codes.permission_code,
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[],
                   CASE WHEN roles.role_key = 'workspace_owner'
                        THEN 'RESTRICTED' ELSE 'INTERNAL' END,
                   ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            CROSS JOIN (VALUES {permissions}) AS codes(permission_code)
            WHERE roles.role_key IN ('workspace_owner', 'workspace_member')
              AND roles.system_managed = true
            ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
            """
        )
    )


def _register_menu_bindings(schema: str) -> None:
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
            for menu_id, api_id, action_type in _BINDINGS
        ],
    )


def _publish_upgraded_menu_snapshots(schema: str) -> None:
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
    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 28
    parent_menu_id = "82000000-0000-4000-8000-000000000167"
    menus = cast(list[dict[str, Any]], snapshot["menus"])
    menus.extend(
        {
            "menu_id": menu_id,
            "menu_key": menu_key,
            "parent_menu_id": parent_menu_id,
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
        for menu_id, menu_key, name, permission, sort_order in _MENU_DEFINITIONS
    )
    snapshot["menus"] = sorted(
        {item["menu_id"]: item for item in menus}.values(), key=lambda item: item["menu_id"]
    )
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    bindings.extend(
        {"menu_id": menu_id, "api_resource_id": api_id, "action_type": action_type}
        for menu_id, api_id, action_type in _BINDINGS
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
                :occurred_at, 10
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
    return uuid5(NAMESPACE_URL, f"ai-platform:p6a05-menu:{workspace_id}")


def _reject_destructive_downgrade(schema: str) -> None:
    connection = op.get_bind()
    facts = connection.scalar(
        sa.text(
            f"""
            SELECT
              (SELECT count(*) FROM "{schema}".conversation_attachments) +
              (SELECT count(*) FROM "{schema}".conversations WHERE scope_mode = 'selected') +
              (SELECT count(*) FROM "{schema}".assistant_runs
               WHERE knowledge_base_ids IS NOT NULL OR document_ids IS NOT NULL
                  OR cardinality(attachment_ids) > 0)
            """
        )
    )
    if int(facts or 0) > 0:
        raise RuntimeError("存在会话范围、临时附件或冻结 Run 范围, 拒绝破坏性降级")


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
            raise RuntimeError("当前菜单发布已在 P6A-05 后变化, 拒绝破坏性降级")
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
                "release_id": row["release_id"],
            },
        )
        connection.execute(
            sa.text(
                f"""
                DELETE FROM "{schema}".menu_releases
                WHERE workspace_id = :workspace_id AND release_id = :release_id
                """
            ),
            {"workspace_id": workspace_id, "release_id": row["release_id"]},
        )


def _remove_authorization_upgrade(schema: str) -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE api_resource_id = ANY(CAST(:api_resource_ids AS uuid[]))
            """
        ),
        {"api_resource_ids": [api_id for _, api_id, _ in _BINDINGS]},
    )
    connection.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".role_permission_grants AS grants
            USING "{schema}".roles AS roles
            WHERE grants.workspace_id = roles.workspace_id
              AND grants.role_id = roles.role_id
              AND roles.role_key IN ('workspace_owner', 'workspace_member')
              AND roles.system_managed = true
              AND grants.permission_code = ANY(CAST(:permissions AS varchar[]))
            """
        ),
        {"permissions": list(_PERMISSIONS)},
    )
