"""创建 P6B-04 企业文档发布审批事实并登记 Registry 32。"""

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

revision: str = "20260831_0079"
down_revision: str | None = "20260831_0078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENTERPRISE_KNOWLEDGE_MENU_ID = "82000000-0000-4000-8000-000000000264"
_PERMISSION_CODES = (
    "enterprise.document.publish.request",
    "enterprise.document.publish.read",
)
_ACTION_MENUS = (
    ("275", "publish_request", "提交发布申请", _PERMISSION_CODES[0], 200),
    ("276", "publish_read", "查看发布审批", _PERMISSION_CODES[1], 210),
)
_MENU_BINDINGS = (
    (
        "82000000-0000-4000-8000-000000000275",
        "81000000-0000-4000-8000-000000000199",
        "mutation",
    ),
    (
        "82000000-0000-4000-8000-000000000276",
        "81000000-0000-4000-8000-000000000200",
        "query",
    ),
    (
        "82000000-0000-4000-8000-000000000276",
        "81000000-0000-4000-8000-000000000201",
        "query",
    ),
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """扩展分类治理，创建发布请求，并发布 Registry 32 菜单快照。"""

    schema = _schema()
    op.add_column(
        "enterprise_categories",
        sa.Column(
            "approval_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema=schema,
    )
    _create_publish_request_tables(schema)
    _grant_owner_permissions(schema)
    _register_menu_bindings(schema)
    _publish_menu_snapshots(schema)


def downgrade() -> None:
    """仅在无发布事实、自定义授权和后续菜单发布时安全降级。"""

    schema = _schema()
    _reject_business_data(schema)
    _reject_custom_grants(schema)
    _restore_menu_snapshots(schema)
    _remove_authorization_upgrade(schema)
    op.drop_table("document_publish_request_categories", schema=schema)
    op.drop_table("document_publish_requests", schema=schema)
    op.drop_column("enterprise_categories", "approval_required", schema=schema)


def _create_publish_request_tables(schema: str) -> None:
    """创建发布请求主表，所有业务引用都由复合外键限制在同一空间。"""

    op.create_table(
        "document_publish_requests",
        sa.Column("publish_request_id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("requester_account_id", sa.Uuid(), nullable=False),
        sa.Column("approval_instance_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("governance_digest", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("failure_reason_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "publish_request_id",
            name="uq_document_publish_requests_workspace_request",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "approval_instance_id",
            name="uq_document_publish_requests_approval",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "requester_account_id",
            "idempotency_key",
            name="uq_document_publish_requests_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            [f"{schema}.documents.workspace_id", f"{schema}.documents.document_id"],
            name="fk_document_publish_requests_document",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "knowledge_base_id"],
            [
                f"{schema}.knowledge_bases.workspace_id",
                f"{schema}.knowledge_bases.knowledge_base_id",
            ],
            name="fk_document_publish_requests_knowledge_base",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id", "document_version_id"],
            [
                f"{schema}.document_versions.workspace_id",
                f"{schema}.document_versions.document_id",
                f"{schema}.document_versions.document_version_id",
            ],
            name="fk_document_publish_requests_version",
        ),
        sa.ForeignKeyConstraint(
            ["approval_instance_id", "workspace_id"],
            [
                f"{schema}.approval_instances.approval_instance_id",
                f"{schema}.approval_instances.workspace_id",
            ],
            name="fk_document_publish_requests_approval",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requester_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_document_publish_requests_requester",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'published', 'rejected', 'withdrawn', "
            "'expired', 'publish_failed')",
            name="ck_document_publish_requests_status",
        ),
        sa.CheckConstraint(
            "failure_reason_code IS NULL OR failure_reason_code IN "
            "('requester_inactive', 'permission_revoked', 'policy_unavailable', "
            "'document_inactive', 'version_changed', 'governance_changed', "
            "'index_not_ready')",
            name="ck_document_publish_requests_failure_reason",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND completed_at IS NULL "
            "AND failure_reason_code IS NULL) OR "
            "(status = 'publish_failed' AND completed_at IS NOT NULL "
            "AND failure_reason_code IS NOT NULL) OR "
            "(status IN ('published', 'rejected', 'withdrawn', 'expired') "
            "AND completed_at IS NOT NULL AND failure_reason_code IS NULL)",
            name="ck_document_publish_requests_completion",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$' AND governance_digest ~ '^[0-9a-f]{64}$'",
            name="ck_document_publish_requests_digests",
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
            name="ck_document_publish_requests_idempotency",
        ),
        sa.CheckConstraint(
            "version_number >= 1",
            name="ck_document_publish_requests_version_number",
        ),
        sa.CheckConstraint("version >= 1", name="ck_document_publish_requests_version"),
        schema=schema,
    )
    op.create_index(
        "uq_document_publish_requests_active_version",
        "document_publish_requests",
        ["workspace_id", "document_version_id"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_document_publish_requests_workspace_status_time",
        "document_publish_requests",
        ["workspace_id", "status", "created_at"],
        schema=schema,
    )
    _create_category_snapshots(schema)


def _create_category_snapshots(schema: str) -> None:
    """冻结申请时分类版本，批准时据此识别治理漂移。"""

    op.create_table(
        "document_publish_request_categories",
        sa.Column("workspace_id", sa.Uuid(), primary_key=True),
        sa.Column("publish_request_id", sa.Uuid(), primary_key=True),
        sa.Column("category_id", sa.Uuid(), primary_key=True),
        sa.Column("category_version", sa.Integer(), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "publish_request_id"],
            [
                f"{schema}.document_publish_requests.workspace_id",
                f"{schema}.document_publish_requests.publish_request_id",
            ],
            name="fk_document_publish_request_categories_request",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "category_id"],
            [
                f"{schema}.enterprise_categories.workspace_id",
                f"{schema}.enterprise_categories.category_id",
            ],
            name="fk_document_publish_request_categories_category",
        ),
        sa.CheckConstraint(
            "category_version >= 1",
            name="ck_document_publish_request_categories_version",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_document_publish_request_categories_category",
        "document_publish_request_categories",
        ["workspace_id", "category_id"],
        schema=schema,
    )


def _grant_owner_permissions(schema: str) -> None:
    """只为存量企业系统 Owner 回填权限，不扩大个人或自定义角色。"""

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
    """幂等登记发布申请、台账列表和详情三条接口。"""

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


def _publish_menu_snapshots(schema: str) -> None:
    """保留每个空间当前菜单事实，追加 Registry 32 发布审批动作。"""

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
        _insert_menu_release(connection, schema, dict(row))


def _insert_menu_release(
    connection: sa.engine.Connection, schema: str, row: dict[str, Any]
) -> None:
    """生成确定性发布，同时保留管理员已有菜单定制。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 32
    menus = cast(list[dict[str, Any]], snapshot["menus"])
    menus.extend(_snapshot_menus())
    snapshot["menus"] = sorted(
        {item["menu_id"]: item for item in menus}.values(),
        key=lambda item: item["menu_id"],
    )
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    bindings.extend(_snapshot_bindings())
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
                :occurred_at, 14
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


def _snapshot_menus() -> list[dict[str, object]]:
    """构造企业知识页面下的两个 Registry 32 动作菜单。"""

    return [
        {
            "menu_id": f"82000000-0000-4000-8000-000000000{suffix}",
            "menu_key": f"navigation.workspace.enterprise_knowledge.{key}",
            "parent_menu_id": _ENTERPRISE_KNOWLEDGE_MENU_ID,
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
        for suffix, key, name, permission_code, sort_order in _ACTION_MENUS
    ]


def _snapshot_bindings() -> list[dict[str, str]]:
    """构造三条发布审批接口的 Registry 32 快照绑定。"""

    return [
        {
            "menu_id": menu_id,
            "api_resource_id": api_resource_id,
            "action_type": action_type,
        }
        for menu_id, api_resource_id, action_type in _MENU_BINDINGS
    ]


def _upgrade_release_id(workspace_id: UUID) -> UUID:
    """为每个工作空间生成跨重试稳定的 P6B-04 发布 ID。"""

    return uuid5(NAMESPACE_URL, f"ai-platform:p6b04-menu:{workspace_id}")


def _reject_business_data(schema: str) -> None:
    """发布请求或已启用审批分类存在时拒绝删除治理事实。"""

    connection = op.get_bind()
    request_count = connection.scalar(
        sa.text(f'SELECT count(*) FROM "{schema}".document_publish_requests')
    )
    approval_count = connection.scalar(
        sa.text(
            f'SELECT count(*) FROM "{schema}".enterprise_categories WHERE approval_required = true'
        )
    )
    if int(request_count or 0) > 0 or int(approval_count or 0) > 0:
        raise RuntimeError("存在企业文档发布审批事实, 拒绝破坏性降级")


def _reject_custom_grants(schema: str) -> None:
    """存在非系统 Owner 的发布审批授权时拒绝降级。"""

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
        raise RuntimeError("存在自定义文档发布审批授权, 拒绝降级以避免权限事实丢失")


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
            raise RuntimeError("当前菜单发布已在 P6B-04 后变化, 拒绝破坏性降级")
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
    """移除三组接口绑定及本 Revision 注入的企业系统 Owner 授权。"""

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
