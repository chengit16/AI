"""创建 P6B-03 企业分类、团队知识域并登记 Registry 31。"""

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

revision: str = "20260831_0078"
down_revision: str | None = "20260831_0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE_MENU_ID = "82000000-0000-4000-8000-000000000001"
_ENTERPRISE_KNOWLEDGE_MENU_ID = "82000000-0000-4000-8000-000000000264"
_ENTERPRISE_KNOWLEDGE_PAGE_ID = "80000000-0000-4000-8000-000000000016"
_PERMISSION_CODES = (
    "enterprise.knowledge.access",
    "enterprise.knowledge.read",
    "enterprise.category.create",
    "enterprise.category.update",
    "enterprise.category.archive",
    "enterprise.category.bind",
    "enterprise.domain.create",
    "enterprise.domain.update",
    "enterprise.domain.archive",
    "enterprise.domain.scope",
    "enterprise.domain.resolve",
)
_ACTION_MENU_DEFINITIONS = (
    ("265", "query", "查看企业知识门户", "enterprise.knowledge.read", 100),
    ("266", "category_create", "创建企业分类", "enterprise.category.create", 110),
    ("267", "category_update", "编辑企业分类", "enterprise.category.update", 120),
    ("268", "category_archive", "归档企业分类", "enterprise.category.archive", 130),
    ("269", "category_bind", "绑定分类文档", "enterprise.category.bind", 140),
    ("270", "domain_create", "创建团队知识域", "enterprise.domain.create", 150),
    ("271", "domain_update", "编辑团队知识域", "enterprise.domain.update", 160),
    ("272", "domain_archive", "归档团队知识域", "enterprise.domain.archive", 170),
    ("273", "domain_scope", "配置知识域范围", "enterprise.domain.scope", 180),
    ("274", "domain_resolve", "解释知识域范围", "enterprise.domain.resolve", 190),
)
_MENU_BINDINGS = tuple(
    (
        f"82000000-0000-4000-8000-000000000{menu_suffix}",
        f"81000000-0000-4000-8000-000000000{api_suffix}",
        action_type,
    )
    for menu_suffix, api_suffix, action_type in (
        ("265", "189", "query"),
        ("266", "190", "mutation"),
        ("267", "191", "mutation"),
        ("268", "192", "mutation"),
        ("269", "193", "mutation"),
        ("270", "194", "mutation"),
        ("271", "195", "mutation"),
        ("272", "196", "mutation"),
        ("273", "197", "mutation"),
        ("274", "198", "query"),
    )
)
_BUSINESS_TABLES = (
    "enterprise_category_documents",
    "team_knowledge_domain_rag_policies",
    "team_knowledge_domain_members",
    "team_knowledge_domain_departments",
    "team_knowledge_domain_bases",
    "enterprise_categories",
    "team_knowledge_domains",
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """创建治理事实，为企业 Owner 回填权限并发布 Registry 31。"""

    schema = _schema()
    _create_enterprise_knowledge_tables(schema)
    _grant_enterprise_owner_permissions(schema)
    _register_menu_bindings(schema)
    _publish_upgraded_menu_snapshots(schema)


def downgrade() -> None:
    """仅在无业务事实、自定义授权和后续菜单发布时安全降级。"""

    schema = _schema()
    _reject_business_data(schema)
    _reject_custom_grants(schema)
    _restore_menu_snapshots(schema)
    _remove_authorization_upgrade(schema)
    _drop_enterprise_knowledge_tables(schema)


def _create_enterprise_knowledge_tables(schema: str) -> None:
    """按父表到关系表顺序创建七张工作空间隔离表。"""

    op.create_table(
        "enterprise_categories",
        sa.Column("category_id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("parent_category_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("visibility", sa.String(32), nullable=False),
        sa.Column(
            "department_ids",
            sa.ARRAY(sa.Uuid()),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by_account_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "category_id",
            name="uq_enterprise_categories_workspace_category",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_enterprise_categories_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "parent_category_id"],
            [
                f"{schema}.enterprise_categories.workspace_id",
                f"{schema}.enterprise_categories.category_id",
            ],
            name="fk_enterprise_categories_parent",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_enterprise_categories_creator",
        ),
        sa.CheckConstraint(
            "visibility IN ('public', 'departments', 'private')",
            name="ck_enterprise_categories_visibility",
        ),
        sa.CheckConstraint(
            "(visibility = 'departments' AND cardinality(department_ids) > 0) OR "
            "(visibility <> 'departments' AND cardinality(department_ids) = 0)",
            name="ck_enterprise_categories_department_scope",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name="ck_enterprise_categories_status"
        ),
        sa.CheckConstraint(
            "parent_category_id IS NULL OR parent_category_id <> category_id",
            name="ck_enterprise_categories_not_self_parent",
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120",
            name="ck_enterprise_categories_name",
        ),
        sa.CheckConstraint("version >= 1", name="ck_enterprise_categories_version"),
        schema=schema,
    )
    op.create_index(
        "uq_enterprise_categories_active_sibling_name",
        "enterprise_categories",
        [
            "workspace_id",
            sa.text("COALESCE(parent_category_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            sa.text("lower(name)"),
        ],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "team_knowledge_domains",
        sa.Column("domain_id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("current_rag_policy_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by_account_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "domain_id",
            name="uq_team_knowledge_domains_workspace_domain",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_team_knowledge_domains_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_team_knowledge_domains_creator",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name="ck_team_knowledge_domains_status"
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120",
            name="ck_team_knowledge_domains_name",
        ),
        sa.CheckConstraint(
            "current_rag_policy_version >= 1",
            name="ck_team_knowledge_domains_policy_version",
        ),
        sa.CheckConstraint("version >= 1", name="ck_team_knowledge_domains_version"),
        schema=schema,
    )
    op.create_index(
        "uq_team_knowledge_domains_active_name",
        "team_knowledge_domains",
        ["workspace_id", sa.text("lower(name)")],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("status = 'active'"),
    )
    _create_enterprise_category_documents(schema)
    _create_team_knowledge_domain_relations(schema)


def _create_enterprise_category_documents(schema: str) -> None:
    """创建分类到原文档的显式关系，不复制任何内容或解析产物。"""

    op.create_table(
        "enterprise_category_documents",
        sa.Column("workspace_id", sa.Uuid(), primary_key=True),
        sa.Column("category_id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "category_id"],
            [
                f"{schema}.enterprise_categories.workspace_id",
                f"{schema}.enterprise_categories.category_id",
            ],
            name="fk_enterprise_category_documents_category",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            [f"{schema}.documents.workspace_id", f"{schema}.documents.document_id"],
            name="fk_enterprise_category_documents_document",
            ondelete="CASCADE",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_enterprise_category_documents_document",
        "enterprise_category_documents",
        ["workspace_id", "document_id"],
        schema=schema,
    )


def _create_team_knowledge_domain_relations(schema: str) -> None:
    """创建不可变 RAG 策略与三类原子替换范围关系。"""

    op.create_table(
        "team_knowledge_domain_rag_policies",
        sa.Column("workspace_id", sa.Uuid(), primary_key=True),
        sa.Column("domain_id", sa.Uuid(), primary_key=True),
        sa.Column("policy_version", sa.Integer(), primary_key=True),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("minimum_score", sa.Float(), nullable=False),
        sa.Column("created_by_account_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "domain_id"],
            [
                f"{schema}.team_knowledge_domains.workspace_id",
                f"{schema}.team_knowledge_domains.domain_id",
            ],
            name="fk_team_knowledge_domain_policies_domain",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_team_knowledge_domain_policies_creator",
        ),
        sa.CheckConstraint(
            "mode IN ('balanced', 'precision', 'recall')",
            name="ck_team_knowledge_domain_policies_mode",
        ),
        sa.CheckConstraint("policy_version >= 1", name="ck_team_knowledge_domain_policies_version"),
        sa.CheckConstraint(
            "top_k BETWEEN 1 AND 50", name="ck_team_knowledge_domain_policies_top_k"
        ),
        sa.CheckConstraint(
            "minimum_score BETWEEN 0.0 AND 1.0",
            name="ck_team_knowledge_domain_policies_score",
        ),
        schema=schema,
    )
    _create_domain_scope_table(
        schema,
        table="team_knowledge_domain_members",
        target_column="membership_id",
        target_table="workspace_memberships",
        target_key="membership_id",
        foreign_key_name="fk_team_knowledge_domain_members_membership",
        domain_foreign_key_name="fk_team_knowledge_domain_members_domain",
        index_name="ix_team_knowledge_domain_members_membership",
    )
    _create_domain_scope_table(
        schema,
        table="team_knowledge_domain_departments",
        target_column="department_id",
        target_table="departments",
        target_key="department_id",
        foreign_key_name="fk_team_knowledge_domain_departments_department",
        domain_foreign_key_name="fk_team_knowledge_domain_departments_domain",
    )
    _create_domain_scope_table(
        schema,
        table="team_knowledge_domain_bases",
        target_column="knowledge_base_id",
        target_table="knowledge_bases",
        target_key="knowledge_base_id",
        foreign_key_name="fk_team_knowledge_domain_bases_base",
        domain_foreign_key_name="fk_team_knowledge_domain_bases_domain",
        index_name="ix_team_knowledge_domain_bases_base",
    )


def _create_domain_scope_table(
    schema: str,
    *,
    table: str,
    target_column: str,
    target_table: str,
    target_key: str,
    foreign_key_name: str,
    domain_foreign_key_name: str,
    index_name: str | None = None,
) -> None:
    """用复合外键确保知识域范围引用无法跨工作空间。"""

    op.create_table(
        table,
        sa.Column("workspace_id", sa.Uuid(), primary_key=True),
        sa.Column("domain_id", sa.Uuid(), primary_key=True),
        sa.Column(target_column, sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "domain_id"],
            [
                f"{schema}.team_knowledge_domains.workspace_id",
                f"{schema}.team_knowledge_domains.domain_id",
            ],
            name=domain_foreign_key_name,
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", target_column],
            [f"{schema}.{target_table}.workspace_id", f"{schema}.{target_table}.{target_key}"],
            name=foreign_key_name,
            ondelete="CASCADE",
        ),
        schema=schema,
    )
    if index_name is not None:
        op.create_index(index_name, table, ["workspace_id", target_column], schema=schema)


def _grant_enterprise_owner_permissions(schema: str) -> None:
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
    """幂等登记十个企业知识动作与受保护接口的关系。"""

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
    """保留每个空间当前菜单事实，追加 Registry 31 企业知识入口。"""

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
    connection: sa.engine.Connection, schema: str, row: dict[str, Any]
) -> None:
    """生成确定性发布，同时保留管理员已有的菜单定制。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 31
    menus = cast(list[dict[str, Any]], snapshot["menus"])
    menus.extend(_enterprise_knowledge_snapshot_menus())
    snapshot["menus"] = sorted(
        {item["menu_id"]: item for item in menus}.values(),
        key=lambda item: item["menu_id"],
    )
    bindings = cast(list[dict[str, Any]], snapshot["menu_api_bindings"])
    bindings.extend(_enterprise_knowledge_snapshot_bindings())
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
                :occurred_at, 13
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


def _enterprise_knowledge_snapshot_menus() -> list[dict[str, object]]:
    """构造企业知识页面和十个治理动作的 Registry 31 快照。"""

    page: dict[str, object] = {
        "menu_id": _ENTERPRISE_KNOWLEDGE_MENU_ID,
        "menu_key": "navigation.workspace.enterprise_knowledge",
        "parent_menu_id": _WORKSPACE_MENU_ID,
        "name": "企业知识库",
        "menu_type": "page",
        "page_resource_id": _ENTERPRISE_KNOWLEDGE_PAGE_ID,
        "permission_code": "enterprise.knowledge.access",
        "icon_key": "network",
        "sort_order": 360,
        "source": "system",
        "status": "active",
        "visible": True,
    }
    actions: list[dict[str, object]] = [
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
        for suffix, key, name, permission_code, sort_order in _ACTION_MENU_DEFINITIONS
    ]
    return [page, *actions]


def _enterprise_knowledge_snapshot_bindings() -> list[dict[str, str]]:
    """构造十个企业知识治理动作的 Registry 31 快照绑定。"""

    return [
        {
            "menu_id": menu_id,
            "api_resource_id": api_resource_id,
            "action_type": action_type,
        }
        for menu_id, api_resource_id, action_type in _MENU_BINDINGS
    ]


def _upgrade_release_id(workspace_id: UUID) -> UUID:
    """为每个工作空间生成跨重试稳定的 P6B-03 发布 ID。"""

    return uuid5(NAMESPACE_URL, f"ai-platform:p6b03-menu:{workspace_id}")


def _reject_business_data(schema: str) -> None:
    """任一治理表已有事实时拒绝删除，避免静默丢失业务数据。"""

    connection = op.get_bind()
    for table in _BUSINESS_TABLES:
        count = connection.scalar(sa.text(f'SELECT count(*) FROM "{schema}"."{table}"'))
        if int(count or 0) > 0:
            raise RuntimeError("存在企业分类或团队知识域数据, 拒绝破坏性降级")


def _reject_custom_grants(schema: str) -> None:
    """存在非系统 Owner 的企业知识授权时拒绝降级。"""

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
        raise RuntimeError("存在自定义企业知识治理授权, 拒绝降级以避免权限事实丢失")


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
            raise RuntimeError("当前菜单发布已在 P6B-03 后变化, 拒绝破坏性降级")
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
    """移除接口绑定及本 Revision 注入的企业系统 Owner 授权。"""

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


def _drop_enterprise_knowledge_tables(schema: str) -> None:
    """按关系表到父表顺序移除空的企业知识治理结构。"""

    for table in _BUSINESS_TABLES:
        op.drop_table(table, schema=schema)
