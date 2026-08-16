"""建立 P3-10 服务调用会话类型和独立请求 Actor 归属。"""

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

revision: str = "20260816_0050"
down_revision: str | None = "20260816_0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SERVICE_INVOCATION_PERMISSION = "service.definition.read"
SERVICE_INVOCATION_BINDINGS = (
    (198, 120, "mutation"),
    (199, 121, "query"),
    (200, 122, "query"),
)
ASSISTANT_MENU_ID = "82000000-0000-4000-8000-000000000167"


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    # 1. 既有会话全部属于私有页面；新服务调用使用隐藏类型，不污染普通会话列表。
    op.add_column(
        "conversations",
        sa.Column(
            "conversation_kind",
            sa.String(length=32),
            nullable=False,
            server_default="private",
        ),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_conversations_kind",
        "conversations",
        "conversation_kind IN ('private', 'service_invocation')",
        schema=schema,
    )

    # 2. 历史浏览器 Run 的 Actor 等于账号；新 API Key Run 可保留不同的凭证主体。
    op.add_column(
        "assistant_runs",
        sa.Column("requested_by_actor_id", sa.Uuid(), nullable=True),
        schema=schema,
    )
    op.execute(
        sa.text(
            f'UPDATE "{schema}".assistant_runs '
            "SET requested_by_actor_id = requested_by_account_id "
            "WHERE requested_by_actor_id IS NULL"
        )
    )
    op.alter_column(
        "assistant_runs",
        "requested_by_actor_id",
        nullable=False,
        schema=schema,
    )
    op.drop_constraint(
        "uq_assistant_runs_idempotency",
        "assistant_runs",
        schema=schema,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_assistant_runs_idempotency",
        "assistant_runs",
        ["workspace_id", "requested_by_actor_id", "idempotency_key"],
        schema=schema,
    )
    op.create_index(
        "ix_assistant_runs_service_actor_time",
        "assistant_runs",
        ["workspace_id", "service_id", "requested_by_actor_id", "created_at"],
        schema=schema,
    )
    _create_actor_immutability_trigger(schema)
    # 3. 既有角色、菜单绑定和当前发布快照同步升级，不能只让新建空间获得服务入口。
    _grant_service_invocation_permission(schema)
    _register_service_invocation_bindings(schema)
    _publish_upgraded_menu_snapshots(schema)


def downgrade() -> None:
    schema = _schema()
    _reject_unsafe_downgrade(schema)
    _restore_menu_snapshots(schema)
    _remove_service_invocation_authorization(schema)
    op.execute(
        sa.text(
            f'DROP TRIGGER IF EXISTS assistant_runs_actor_immutable ON "{schema}".assistant_runs'
        )
    )
    op.execute(sa.text(f'DROP FUNCTION IF EXISTS "{schema}".prevent_assistant_run_actor_change()'))
    op.drop_index(
        "ix_assistant_runs_service_actor_time",
        table_name="assistant_runs",
        schema=schema,
    )
    op.drop_constraint(
        "uq_assistant_runs_idempotency",
        "assistant_runs",
        schema=schema,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_assistant_runs_idempotency",
        "assistant_runs",
        ["workspace_id", "requested_by_account_id", "idempotency_key"],
        schema=schema,
    )
    op.drop_column("assistant_runs", "requested_by_actor_id", schema=schema)
    op.drop_constraint(
        "ck_conversations_kind",
        "conversations",
        schema=schema,
        type_="check",
    )
    op.drop_column("conversations", "conversation_kind", schema=schema)


def _create_actor_immutability_trigger(schema: str) -> None:
    """防止直接 SQL 把历史 Run 改绑到其他账号、API Key 或幂等请求。"""

    op.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION "{schema}".prevent_assistant_run_actor_change()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF NEW.requested_by_account_id IS DISTINCT FROM OLD.requested_by_account_id
                 OR NEW.requested_by_actor_id IS DISTINCT FROM OLD.requested_by_actor_id
                 OR NEW.conversation_id IS DISTINCT FROM OLD.conversation_id
                 OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
                 OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
              THEN
                RAISE EXCEPTION 'assistant run requester binding is immutable'
                  USING ERRCODE = '55000';
              END IF;
              RETURN NEW;
            END;
            $$;

            CREATE TRIGGER assistant_runs_actor_immutable
            BEFORE UPDATE ON "{schema}".assistant_runs
            FOR EACH ROW EXECUTE FUNCTION "{schema}".prevent_assistant_run_actor_change();
            """
        )
    )


def _grant_service_invocation_permission(schema: str) -> None:
    """让个人 Owner 与企业 Owner/Member 获得入口权限，访问策略继续约束具体服务。"""

    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, '{SERVICE_INVOCATION_PERMISSION}',
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[],
                   'RESTRICTED', ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            WHERE roles.role_key IN ('workspace_owner', 'workspace_member')
              AND roles.system_managed = true
            ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
            """
        )
    )


def _register_service_invocation_bindings(schema: str) -> None:
    values = ", ".join(
        f"('82000000-0000-4000-8000-{menu:012d}'::uuid, "
        f"'81000000-0000-4000-8000-{api:012d}'::uuid, '{action}')"
        for menu, api, action in SERVICE_INVOCATION_BINDINGS
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
    """为已有当前菜单复制注册表 19 快照，保留历史发布与管理员自定义结果。"""

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
    """追加服务调用动作与绑定，并原子切换到不可变的新菜单发布。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 19
    menus = cast(list[dict[str, Any]], snapshot["menus"])
    menus.extend(_service_invocation_snapshot_menus())
    snapshot["menus"] = sorted(
        {menu["menu_id"]: menu for menu in menus}.values(),
        key=lambda item: item["menu_id"],
    )
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    bindings.extend(_service_invocation_snapshot_bindings())
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


def _remove_service_invocation_authorization(schema: str) -> None:
    api_ids = ", ".join(
        f"'81000000-0000-4000-8000-{api:012d}'::uuid" for _, api, _ in SERVICE_INVOCATION_BINDINGS
    )
    op.execute(
        sa.text(
            f'DELETE FROM "{schema}".registered_menu_api_bindings '
            f"WHERE api_resource_id IN ({api_ids})"
        )
    )
    op.execute(
        sa.text(
            f'DELETE FROM "{schema}".role_permission_grants '
            f"WHERE permission_code = '{SERVICE_INVOCATION_PERMISSION}'"
        )
    )


def _service_invocation_snapshot_menus() -> list[dict[str, object]]:
    definitions = (
        (198, "invoke", "调用已发布服务", 280),
        (199, "invocation_read", "查看服务调用", 290),
        (200, "invocation_stream", "订阅服务调用事件", 300),
    )
    return [
        {
            "menu_id": f"82000000-0000-4000-8000-{menu_number:012d}",
            "menu_key": f"navigation.workspace.assistant.{suffix}",
            "parent_menu_id": ASSISTANT_MENU_ID,
            "name": name,
            "menu_type": "action",
            "page_resource_id": None,
            "permission_code": SERVICE_INVOCATION_PERMISSION,
            "icon_key": None,
            "sort_order": sort_order,
            "source": "system",
            "status": "active",
            "visible": True,
        }
        for menu_number, suffix, name, sort_order in definitions
    ]


def _service_invocation_snapshot_bindings() -> list[dict[str, str]]:
    return [
        {
            "menu_id": f"82000000-0000-4000-8000-{menu:012d}",
            "api_resource_id": f"81000000-0000-4000-8000-{api:012d}",
            "action_type": action,
        }
        for menu, api, action in SERVICE_INVOCATION_BINDINGS
    ]


def _upgrade_release_id(workspace_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"ai-platform:p310-menu:{workspace_id}")


def _reject_unsafe_downgrade(schema: str) -> None:
    """旧版本无法表达隐藏服务会话和 API Key Actor，存在相关事实时拒绝降级。"""

    connection = op.get_bind()
    hidden_count = connection.scalar(
        sa.text(
            f'SELECT count(*) FROM "{schema}".conversations '
            "WHERE conversation_kind = 'service_invocation'"
        )
    )
    api_actor_count = connection.scalar(
        sa.text(
            f'SELECT count(*) FROM "{schema}".assistant_runs '
            "WHERE requested_by_actor_id <> requested_by_account_id"
        )
    )
    if int(hidden_count or 0) > 0 or int(api_actor_count or 0) > 0:
        raise RuntimeError("数据库已包含 P3-10 服务调用事实, 拒绝降级到账号归属模型")
