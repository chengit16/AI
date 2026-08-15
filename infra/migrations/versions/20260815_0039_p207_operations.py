"""建立 P2-07 审计、用量和 Transactional Outbox 运营事实。"""

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

revision: str = "20260815_0039"
down_revision: str | None = "20260815_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPERATIONS_PERMISSIONS = (
    "operations.records.read",
    "operations.outbox.replay",
)
OPERATIONS_BINDINGS = (
    (189, 102, "query"),
    (189, 103, "query"),
    (189, 104, "query"),
    (189, 105, "query"),
    (189, 106, "query"),
    (190, 107, "mutation"),
)
STATUS_MENU_ID = "82000000-0000-4000-8000-000000000005"


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """按事实依赖顺序扩展表、授权、绑定和菜单发布快照。"""

    schema = _schema()
    _extend_audit_records(schema)
    _extend_consumer_receipts(schema)
    _create_replay_requests(schema)
    _grant_permissions(schema)
    _register_bindings(schema)
    _publish_upgraded_snapshots(schema)


def downgrade() -> None:
    """恢复本 Revision 复制的菜单指针，再按反向依赖移除运营事实。"""

    schema = _schema()
    _restore_menu_snapshots(schema)
    api_ids = ", ".join(
        f"'81000000-0000-4000-8000-{api_number:012d}'::uuid"
        for _, api_number, _ in OPERATIONS_BINDINGS
    )
    permissions = ", ".join(f"'{code}'" for code in OPERATIONS_PERMISSIONS)
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".registered_menu_api_bindings
            WHERE api_resource_id IN ({api_ids})
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            DELETE FROM "{schema}".role_permission_grants
            WHERE permission_code IN ({permissions})
            """
        )
    )
    op.execute(
        sa.text(
            f'DROP TRIGGER reject_outbox_replay_request_mutation ON "{schema}".'
            "outbox_replay_requests"
        )
    )
    op.execute(sa.text(f'DROP FUNCTION "{schema}".reject_outbox_replay_request_mutation()'))
    op.drop_index(
        "ix_outbox_replay_requests_event_time",
        table_name="outbox_replay_requests",
        schema=schema,
    )
    op.drop_table("outbox_replay_requests", schema=schema)
    op.drop_constraint(
        "uq_outbox_events_event_workspace",
        "outbox_events",
        type_="unique",
        schema=schema,
    )
    op.drop_index(
        "ix_outbox_events_workspace_status_occurred",
        table_name="outbox_events",
        schema=schema,
    )
    op.drop_index(
        "ix_consumer_receipts_event_received",
        table_name="consumer_receipts",
        schema=schema,
    )
    op.drop_constraint(
        "ck_consumer_receipts_delivery_count",
        "consumer_receipts",
        type_="check",
        schema=schema,
    )
    op.drop_column("consumer_receipts", "last_received_at", schema=schema)
    op.drop_column("consumer_receipts", "delivery_count", schema=schema)
    op.drop_index(
        "ix_audit_records_workspace_actor_occurred",
        table_name="audit_records",
        schema=schema,
    )
    op.drop_constraint(
        "ck_audit_authorization",
        "audit_records",
        type_="check",
        schema=schema,
    )
    op.drop_column("audit_records", "policy_version", schema=schema)
    op.drop_column("audit_records", "policy_decision_id", schema=schema)
    op.drop_column("audit_records", "permission_code", schema=schema)


def _extend_audit_records(schema: str) -> None:
    # 历史记录没有可证明的授权决策，因此保持三列同时为空，禁止迁移时反向猜测。
    op.add_column(
        "audit_records",
        sa.Column("permission_code", sa.String(160), nullable=True),
        schema=schema,
    )
    op.add_column(
        "audit_records",
        sa.Column("policy_decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "audit_records",
        sa.Column("policy_version", sa.Integer(), nullable=True),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_audit_authorization",
        "audit_records",
        "(permission_code IS NULL AND policy_decision_id IS NULL AND policy_version IS NULL) "
        "OR (permission_code IS NOT NULL AND policy_decision_id IS NOT NULL "
        "AND policy_version >= 1)",
        schema=schema,
    )
    op.create_index(
        "ix_audit_records_workspace_actor_occurred",
        "audit_records",
        ["workspace_id", "actor_id", "occurred_at", "audit_id"],
        schema=schema,
    )


def _extend_consumer_receipts(schema: str) -> None:
    # 旧回执只证明至少成功接收一次，首次接收时间以原处理时间作为可验证下界。
    op.add_column(
        "consumer_receipts",
        sa.Column("delivery_count", sa.Integer(), nullable=True),
        schema=schema,
    )
    op.add_column(
        "consumer_receipts",
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    op.execute(
        sa.text(
            f"""
            UPDATE "{schema}".consumer_receipts
            SET delivery_count = 1, last_received_at = processed_at
            WHERE delivery_count IS NULL OR last_received_at IS NULL
            """
        )
    )
    op.alter_column(
        "consumer_receipts",
        "delivery_count",
        existing_type=sa.Integer(),
        nullable=False,
        schema=schema,
    )
    op.alter_column(
        "consumer_receipts",
        "last_received_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        schema=schema,
    )
    op.create_check_constraint(
        "ck_consumer_receipts_delivery_count",
        "consumer_receipts",
        "delivery_count >= 1",
        schema=schema,
    )
    op.create_index(
        "ix_consumer_receipts_event_received",
        "consumer_receipts",
        ["event_id", "last_received_at"],
        schema=schema,
    )
    op.create_index(
        "ix_outbox_events_workspace_status_occurred",
        "outbox_events",
        ["workspace_id", "status", "occurred_at", "event_id"],
        schema=schema,
    )


def _create_replay_requests(schema: str) -> None:
    # 复合唯一键让重放请求的 workspace_id 必须与源事件一致，不能只依赖服务层检查。
    op.create_unique_constraint(
        "uq_outbox_events_event_workspace",
        "outbox_events",
        ["event_id", "workspace_id"],
        schema=schema,
    )
    op.create_table(
        "outbox_replay_requests",
        sa.Column("replay_request_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("source_status", sa.String(32), nullable=False),
        sa.Column("source_attempt_count", sa.Integer(), nullable=False),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_error_code", sa.String(128), nullable=True),
        sa.Column("requested_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("traceparent", sa.String(55), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_outbox_replay_requests_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["event_id", "workspace_id"],
            [f"{schema}.outbox_events.event_id", f"{schema}.outbox_events.workspace_id"],
            name="fk_outbox_replay_requests_event_workspace",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "reason_code ~ '^[A-Z][A-Z0-9_]{2,63}$'",
            name="ck_outbox_replay_requests_reason",
        ),
        sa.CheckConstraint(
            "source_status IN ('published', 'dead_letter')",
            name="ck_outbox_replay_requests_source_status",
        ),
        sa.CheckConstraint(
            "source_attempt_count >= 0",
            name="ck_outbox_replay_requests_attempt_count",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_outbox_replay_requests_event_time",
        "outbox_replay_requests",
        ["workspace_id", "event_id", "requested_at"],
        schema=schema,
    )
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".reject_outbox_replay_request_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'outbox replay requests are immutable' USING ERRCODE = '55000';
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER reject_outbox_replay_request_mutation
            BEFORE UPDATE OR DELETE ON "{schema}".outbox_replay_requests
            FOR EACH ROW EXECUTE FUNCTION "{schema}".reject_outbox_replay_request_mutation()
            """
        )
    )


def _grant_permissions(schema: str) -> None:
    permissions = ", ".join(f"('{code}')" for code in OPERATIONS_PERMISSIONS)
    # 个人空间和企业空间的系统 Owner 都可运营；普通成员不会获得审计与重放权限。
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, permission_code, 'workspace',
                   ARRAY[]::uuid[], ARRAY[]::uuid[], 'RESTRICTED', ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            CROSS JOIN (VALUES {permissions}) AS permission_codes(permission_code)
            WHERE roles.role_key = 'workspace_owner' AND roles.system_managed = true
            ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
            """
        )
    )


def _register_bindings(schema: str) -> None:
    values = ", ".join(
        "("
        + ", ".join(
            (
                f"'82000000-0000-4000-8000-{menu_number:012d}'::uuid",
                f"'81000000-0000-4000-8000-{api_number:012d}'::uuid",
                f"'{action_type}'",
            )
        )
        + ")"
        for menu_number, api_number, action_type in OPERATIONS_BINDINGS
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


def _publish_upgraded_snapshots(schema: str) -> None:
    """为每个已发布空间复制注册表 16 快照，不原地修改历史发布事实。"""

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
        _insert_upgraded_release(connection, schema, dict(row))


def _insert_upgraded_release(
    connection: sa.engine.Connection,
    schema: str,
    row: dict[str, Any],
) -> None:
    """追加运营动作菜单和绑定，并原子切换工作空间当前发布指针。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 16
    menus = cast(list[dict[str, Any]], snapshot["menus"])
    menus.extend(_operations_snapshot_menus())
    unique_menus = {menu["menu_id"]: menu for menu in menus}
    snapshot["menus"] = sorted(unique_menus.values(), key=lambda item: item["menu_id"])
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    bindings.extend(_operations_snapshot_bindings())
    unique_bindings = {(item["menu_id"], item["api_resource_id"]): item for item in bindings}
    snapshot["menu_api_bindings"] = sorted(
        unique_bindings.values(),
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
    # 只恢复仍指向本迁移发布的空间，避免覆盖升级后的管理员发布。
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
                f"""
                DELETE FROM "{schema}".menu_releases
                WHERE workspace_id = :workspace_id AND release_id = :release_id
                """
            ),
            parameters,
        )


def _operations_snapshot_menus() -> list[dict[str, object]]:
    return [
        {
            "menu_id": "82000000-0000-4000-8000-000000000189",
            "menu_key": "navigation.workspace.status.operations_read",
            "parent_menu_id": STATUS_MENU_ID,
            "name": "查看运营记录",
            "menu_type": "action",
            "page_resource_id": None,
            "permission_code": "operations.records.read",
            "icon_key": None,
            "sort_order": 100,
            "source": "system",
            "status": "active",
            "visible": True,
        },
        {
            "menu_id": "82000000-0000-4000-8000-000000000190",
            "menu_key": "navigation.workspace.status.outbox_replay",
            "parent_menu_id": STATUS_MENU_ID,
            "name": "重放集成事件",
            "menu_type": "action",
            "page_resource_id": None,
            "permission_code": "operations.outbox.replay",
            "icon_key": None,
            "sort_order": 110,
            "source": "system",
            "status": "active",
            "visible": True,
        },
    ]


def _operations_snapshot_bindings() -> list[dict[str, str]]:
    return [
        {
            "menu_id": f"82000000-0000-4000-8000-{menu_number:012d}",
            "api_resource_id": f"81000000-0000-4000-8000-{api_number:012d}",
            "action_type": action_type,
        }
        for menu_number, api_number, action_type in OPERATIONS_BINDINGS
    ]


def _upgrade_release_id(workspace_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"ai-platform:p207-menu:{workspace_id}")
