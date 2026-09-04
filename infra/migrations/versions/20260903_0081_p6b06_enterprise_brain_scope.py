"""建立 P6B-06 企业大脑会话与 Run 的知识域冻结关系。"""

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

revision: str = "20260903_0081"
down_revision: str | None = "20260903_0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACE_MENU_ID = "82000000-0000-4000-8000-000000000001"
_ENTERPRISE_BRAIN_PAGE_ID = "80000000-0000-4000-8000-000000000018"
_ENTERPRISE_BRAIN_MENU_ID = "82000000-0000-4000-8000-000000000282"
_PERMISSION_CODES = (
    "enterprise.brain.access",
    "enterprise.brain.read",
    "enterprise.brain.conversation.create",
    "enterprise.brain.message.create",
    "enterprise.brain.conversation.archive",
    "enterprise.brain.run.cancel",
    "enterprise.brain.source.read",
    "enterprise.brain.feedback.manage",
    "enterprise.brain.report.create",
    "enterprise.brain.report.read",
)
_MENU_BINDINGS = (
    ("82000000-0000-4000-8000-000000000283", "81000000-0000-4000-8000-000000000222", "query"),
    ("82000000-0000-4000-8000-000000000284", "81000000-0000-4000-8000-000000000207", "mutation"),
    ("82000000-0000-4000-8000-000000000283", "81000000-0000-4000-8000-000000000208", "query"),
    ("82000000-0000-4000-8000-000000000283", "81000000-0000-4000-8000-000000000209", "query"),
    ("82000000-0000-4000-8000-000000000283", "81000000-0000-4000-8000-000000000210", "query"),
    ("82000000-0000-4000-8000-000000000285", "81000000-0000-4000-8000-000000000211", "mutation"),
    ("82000000-0000-4000-8000-000000000283", "81000000-0000-4000-8000-000000000212", "query"),
    ("82000000-0000-4000-8000-000000000286", "81000000-0000-4000-8000-000000000213", "mutation"),
    ("82000000-0000-4000-8000-000000000287", "81000000-0000-4000-8000-000000000214", "query"),
    ("82000000-0000-4000-8000-000000000288", "81000000-0000-4000-8000-000000000215", "query"),
    ("82000000-0000-4000-8000-000000000288", "81000000-0000-4000-8000-000000000216", "mutation"),
    ("82000000-0000-4000-8000-000000000289", "81000000-0000-4000-8000-000000000217", "mutation"),
    ("82000000-0000-4000-8000-000000000290", "81000000-0000-4000-8000-000000000218", "mutation"),
    ("82000000-0000-4000-8000-000000000293", "81000000-0000-4000-8000-000000000219", "query"),
    ("82000000-0000-4000-8000-000000000291", "81000000-0000-4000-8000-000000000220", "query"),
    ("82000000-0000-4000-8000-000000000292", "81000000-0000-4000-8000-000000000221", "query"),
)


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    """扩展会话类型，并冻结会话、Run 与团队知识域的空间关系。"""

    schema = _schema()
    op.drop_constraint("ck_conversations_kind", "conversations", schema=schema)
    op.add_column(
        "conversations",
        sa.Column("knowledge_domain_id", sa.Uuid(), nullable=True),
        schema=schema,
    )
    op.add_column(
        "conversations",
        sa.Column("knowledge_domain_policy_version", sa.Integer(), nullable=True),
        schema=schema,
    )
    op.create_foreign_key(
        "fk_conversations_knowledge_domain",
        "conversations",
        "team_knowledge_domains",
        ["workspace_id", "knowledge_domain_id"],
        ["workspace_id", "domain_id"],
        source_schema=schema,
        referent_schema=schema,
    )
    op.create_check_constraint(
        "ck_conversations_kind",
        "conversations",
        "conversation_kind IN ('private', 'service_invocation', 'enterprise_brain')",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_conversations_knowledge_domain",
        "conversations",
        "(conversation_kind = 'enterprise_brain' AND knowledge_domain_id IS NOT NULL "
        "AND knowledge_domain_policy_version >= 1) OR "
        "(conversation_kind <> 'enterprise_brain' AND knowledge_domain_id IS NULL "
        "AND knowledge_domain_policy_version IS NULL)",
        schema=schema,
    )
    _add_run_domain(schema)
    _replace_scope_trigger(schema, include_domain=True)
    _create_report_table(schema)
    _grant_enterprise_brain_member_permissions(schema)
    _register_menu_bindings(schema)
    _publish_menu_snapshots(schema)


def downgrade() -> None:
    """仅在不存在企业大脑事实时移除知识域关系。"""

    schema = _schema()
    count = op.get_bind().scalar(
        sa.text(
            f'SELECT count(*) FROM "{schema}".conversations '
            "WHERE conversation_kind = 'enterprise_brain'"
        )
    )
    if count:
        raise RuntimeError("存在企业大脑会话，不能执行破坏性降级")  # noqa: RUF001
    report_count = op.get_bind().scalar(
        sa.text(f'SELECT count(*) FROM "{schema}".enterprise_brain_reports')
    )
    if report_count:
        raise RuntimeError("存在企业大脑报告，不能执行破坏性降级")  # noqa: RUF001
    _assert_safe_authorization_downgrade(schema)
    op.drop_table("enterprise_brain_reports", schema=schema)
    _drop_scope_trigger(schema)
    _restore_menu_snapshots(schema)
    _remove_authorization_upgrade(schema)
    op.drop_constraint("fk_assistant_runs_knowledge_domain", "assistant_runs", schema=schema)
    op.drop_constraint("ck_assistant_runs_knowledge_domain", "assistant_runs", schema=schema)
    op.drop_column("assistant_runs", "knowledge_domain_policy_version", schema=schema)
    op.drop_column("assistant_runs", "knowledge_domain_id", schema=schema)
    op.drop_constraint("ck_conversations_knowledge_domain", "conversations", schema=schema)
    op.drop_constraint("fk_conversations_knowledge_domain", "conversations", schema=schema)
    op.drop_constraint("ck_conversations_kind", "conversations", schema=schema)
    op.create_check_constraint(
        "ck_conversations_kind",
        "conversations",
        "conversation_kind IN ('private', 'service_invocation')",
        schema=schema,
    )
    op.drop_column("conversations", "knowledge_domain_policy_version", schema=schema)
    op.drop_column("conversations", "knowledge_domain_id", schema=schema)
    _create_scope_trigger(schema, include_domain=False)


def _assert_safe_authorization_downgrade(schema: str) -> None:
    """拒绝删除并非本 Revision 注入的企业大脑授权事实。"""

    count = op.get_bind().scalar(
        sa.text(
            f"""
            SELECT count(*)
            FROM "{schema}".role_permission_grants AS grants
            JOIN "{schema}".roles AS roles
              ON roles.workspace_id = grants.workspace_id
             AND roles.role_id = grants.role_id
            JOIN "{schema}".workspaces AS workspaces
              ON workspaces.workspace_id = grants.workspace_id
            WHERE grants.permission_code = ANY(:permission_codes)
              AND NOT (
                  workspaces.workspace_type = 'enterprise'
                  AND roles.role_key IN ('workspace_owner', 'workspace_member')
                  AND roles.system_managed = true
              )
            """
        ),
        {"permission_codes": list(_PERMISSION_CODES)},
    )
    if count:
        raise RuntimeError("存在自定义企业大脑授权，拒绝降级以避免权限事实丢失")  # noqa: RUF001


def _create_report_table(schema: str) -> None:
    """创建不可变报告事实表，所有关系均带工作空间边界。"""

    op.create_table(
        "enterprise_brain_reports",
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_account_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("template", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("citation_count", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("report_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "created_by_account_id",
            "idempotency_key",
            name="uq_enterprise_brain_reports_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_enterprise_brain_reports_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            [f"{schema}.conversations.conversation_id", f"{schema}.conversations.workspace_id"],
            name="fk_enterprise_brain_reports_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{schema}.assistant_runs.run_id"],
            name="fk_enterprise_brain_reports_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id", "workspace_id"],
            [f"{schema}.messages.message_id", f"{schema}.messages.workspace_id"],
            name="fk_enterprise_brain_reports_message",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_enterprise_brain_reports_creator",
        ),
        sa.CheckConstraint(
            "template IN ('briefing', 'risk_review', 'comparison')",
            name="ck_enterprise_brain_reports_template",
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 200",
            name="ck_enterprise_brain_reports_title",
        ),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 200000",
            name="ck_enterprise_brain_reports_content",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$' AND citation_count >= 0",
            name="ck_enterprise_brain_reports_integrity",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_enterprise_brain_reports_workspace_time",
        "enterprise_brain_reports",
        ["workspace_id", "created_at"],
        schema=schema,
    )


def _grant_enterprise_brain_member_permissions(schema: str) -> None:
    """为存量企业系统成员角色回填入口权限，个人空间不注入该能力。"""

    op.get_bind().execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".role_permission_grants (
                workspace_id, role_id, permission_code, scope_type,
                department_ids, resource_ids, maximum_security_level, field_mask
            )
            SELECT roles.workspace_id, roles.role_id, permission_codes.permission_code,
                   'workspace', ARRAY[]::uuid[], ARRAY[]::uuid[], 'RESTRICTED', ARRAY[]::varchar[]
            FROM "{schema}".roles AS roles
            JOIN "{schema}".workspaces AS workspaces
              ON workspaces.workspace_id = roles.workspace_id
            CROSS JOIN unnest(CAST(:permission_codes AS varchar[]))
                AS permission_codes(permission_code)
            WHERE workspaces.workspace_type = 'enterprise'
              AND roles.role_key IN ('workspace_owner', 'workspace_member')
              AND roles.system_managed = true
            ON CONFLICT (workspace_id, role_id, permission_code) DO NOTHING
            """
        ),
        {"permission_codes": list(_PERMISSION_CODES)},
    )


def _register_menu_bindings(schema: str) -> None:
    """登记企业大脑动作与 API 的独立权限绑定，避免普通助手权限旁路。"""

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
            for menu_id, api_id, action_type in _MENU_BINDINGS
        ],
    )


def _publish_menu_snapshots(schema: str) -> None:
    """仅向已有企业空间追加企业大脑入口并冻结 Registry 37。"""

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            f"""
            SELECT releases.*, numbers.next_release_number
            FROM "{schema}".workspace_menu_publications AS publications
            JOIN "{schema}".menu_releases AS releases
              ON releases.workspace_id = publications.workspace_id
             AND releases.release_id = publications.current_release_id
            JOIN "{schema}".workspaces AS workspaces
              ON workspaces.workspace_id = publications.workspace_id
            JOIN LATERAL (
                SELECT COALESCE(MAX(history.release_number), 0) + 1 AS next_release_number
                FROM "{schema}".menu_releases AS history
                WHERE history.workspace_id = releases.workspace_id
            ) AS numbers ON true
            WHERE workspaces.workspace_type = 'enterprise'
            """
        )
    ).mappings()
    for row in rows:
        _insert_menu_release(connection, schema, dict(row))


def _insert_menu_release(
    connection: sa.engine.Connection, schema: str, row: dict[str, Any]
) -> None:
    """保留管理员现有菜单定制，并以空间稳定 UUID 生成一次新发布。"""

    workspace_id = cast(UUID, row["workspace_id"])
    snapshot = copy.deepcopy(cast(dict[str, Any], row["snapshot"]))
    snapshot["registry_version"] = 37
    menus = cast(list[dict[str, Any]], snapshot["menus"])
    menus.extend(_snapshot_menus())
    snapshot["menus"] = sorted(
        {item["menu_id"]: item for item in menus}.values(), key=lambda item: item["menu_id"]
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
                :occurred_at, 16
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
    """构造企业大脑页面和动作菜单的不可变发布快照。"""

    page: dict[str, object] = {
        "menu_id": _ENTERPRISE_BRAIN_MENU_ID,
        "menu_key": "navigation.workspace.enterprise_brain",
        "parent_menu_id": _WORKSPACE_MENU_ID,
        "name": "AI 企业大脑",
        "menu_type": "page",
        "page_resource_id": _ENTERPRISE_BRAIN_PAGE_ID,
        "permission_code": "enterprise.brain.access",
        "icon_key": "brain-circuit",
        "sort_order": 365,
        "source": "system",
        "status": "active",
        "visible": True,
    }
    action_names = (
        ("283", "read", "查看企业大脑", "enterprise.brain.read", 100),
        (
            "284",
            "conversation_create",
            "创建企业大脑会话",
            "enterprise.brain.conversation.create",
            110,
        ),
        ("285", "message_create", "发起企业知识问答", "enterprise.brain.message.create", 120),
        ("286", "run_cancel", "取消企业大脑运行", "enterprise.brain.run.cancel", 130),
        ("287", "source_read", "查看企业大脑来源", "enterprise.brain.source.read", 140),
        ("288", "feedback_manage", "管理企业大脑反馈", "enterprise.brain.feedback.manage", 150),
        (
            "289",
            "conversation_archive",
            "归档企业大脑会话",
            "enterprise.brain.conversation.archive",
            160,
        ),
        ("290", "report_create", "生成企业大脑报告", "enterprise.brain.report.create", 170),
        ("291", "report_read", "查看企业大脑报告", "enterprise.brain.report.read", 180),
        ("292", "report_download", "下载企业大脑报告", "enterprise.brain.report.read", 190),
        ("293", "report_list", "列出企业大脑报告", "enterprise.brain.report.read", 200),
    )
    actions: list[dict[str, object]] = [
        {
            "menu_id": f"82000000-0000-4000-8000-000000000{suffix}",
            "menu_key": f"navigation.workspace.enterprise_brain.{key}",
            "parent_menu_id": _ENTERPRISE_BRAIN_MENU_ID,
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
        for suffix, key, name, permission_code, sort_order in action_names
    ]
    return [page, *actions]


def _snapshot_bindings() -> list[dict[str, str]]:
    """构造企业大脑 API 与动作菜单绑定。"""

    return [
        {"menu_id": menu_id, "api_resource_id": api_id, "action_type": action_type}
        for menu_id, api_id, action_type in _MENU_BINDINGS
    ]


def _upgrade_release_id(workspace_id: UUID) -> UUID:
    """为每个空间生成可重试复用的 P6B-06 菜单发布 ID。"""

    return uuid5(NAMESPACE_URL, f"ai-platform:p6b06-menu:{workspace_id}")


def _restore_menu_snapshots(schema: str) -> None:
    """仅撤销本 Revision 生成的快照，发现后续发布时拒绝破坏性回滚。"""

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            f"""
            SELECT publications.workspace_id, releases.release_id, releases.source_release_id
            FROM "{schema}".workspace_menu_publications AS publications
            JOIN "{schema}".menu_releases AS releases
              ON releases.workspace_id = publications.workspace_id
             AND releases.release_id = publications.current_release_id
            JOIN "{schema}".workspaces AS workspaces
              ON workspaces.workspace_id = publications.workspace_id
            WHERE workspaces.workspace_type = 'enterprise'
            """
        )
    ).mappings()
    for row in rows:
        workspace_id = cast(UUID, row["workspace_id"])
        if row["release_id"] != _upgrade_release_id(workspace_id):
            raise RuntimeError("当前菜单发布已在 P6B-06 后变化, 拒绝破坏性降级")
        connection.execute(
            sa.text(
                f"""
                UPDATE "{schema}".workspace_menu_publications
                SET current_release_id = :source_release_id, published_at = now()
                WHERE workspace_id = :workspace_id
                """
            ),
            {"workspace_id": workspace_id, "source_release_id": row["source_release_id"]},
        )
        connection.execute(
            sa.text(
                f'DELETE FROM "{schema}".menu_releases '
                "WHERE workspace_id = :workspace_id AND release_id = :release_id"
            ),
            {"workspace_id": workspace_id, "release_id": row["release_id"]},
        )


def _remove_authorization_upgrade(schema: str) -> None:
    """降级时移除本 Revision 注入的企业大脑绑定与 Owner 权限。"""

    connection = op.get_bind()
    connection.execute(
        sa.text(
            f'DELETE FROM "{schema}".registered_menu_api_bindings '
            "WHERE api_resource_id = ANY(CAST(:api_resource_ids AS uuid[]))"
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
              AND roles.role_key IN ('workspace_owner', 'workspace_member')
              AND roles.system_managed = true
              AND grants.permission_code = ANY(:permission_codes)
            """
        ),
        {"permission_codes": list(_PERMISSION_CODES)},
    )


def _add_run_domain(schema: str) -> None:
    """为 Run 增加知识域身份，并以复合外键阻断跨空间引用。"""

    op.add_column(
        "assistant_runs",
        sa.Column("knowledge_domain_id", sa.Uuid(), nullable=True),
        schema=schema,
    )
    op.add_column(
        "assistant_runs",
        sa.Column("knowledge_domain_policy_version", sa.Integer(), nullable=True),
        schema=schema,
    )
    op.create_foreign_key(
        "fk_assistant_runs_knowledge_domain",
        "assistant_runs",
        "team_knowledge_domains",
        ["workspace_id", "knowledge_domain_id"],
        ["workspace_id", "domain_id"],
        source_schema=schema,
        referent_schema=schema,
    )
    op.create_check_constraint(
        "ck_assistant_runs_knowledge_domain",
        "assistant_runs",
        "(knowledge_domain_id IS NULL AND knowledge_domain_policy_version IS NULL) OR "
        "(knowledge_domain_id IS NOT NULL AND knowledge_domain_policy_version >= 1)",
        schema=schema,
    )


def _replace_scope_trigger(schema: str, *, include_domain: bool) -> None:
    _drop_scope_trigger(schema)
    _create_scope_trigger(schema, include_domain=include_domain)


def _drop_scope_trigger(schema: str) -> None:
    op.execute(sa.text(f'DROP TRIGGER assistant_runs_scope_immutable ON "{schema}".assistant_runs'))
    op.execute(sa.text(f'DROP FUNCTION "{schema}".prevent_assistant_run_scope_mutation()'))


def _create_scope_trigger(schema: str, *, include_domain: bool) -> None:
    """重建 Run 输入冻结触发器，升级后额外保护知识域身份。"""

    domain_conditions = ""
    if include_domain:
        domain_conditions = (
            " OR NEW.knowledge_domain_id IS DISTINCT FROM OLD.knowledge_domain_id"
            " OR NEW.knowledge_domain_policy_version IS DISTINCT FROM "
            "OLD.knowledge_domain_policy_version"
        )
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".prevent_assistant_run_scope_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.knowledge_base_ids IS DISTINCT FROM OLD.knowledge_base_ids
                   OR NEW.document_ids IS DISTINCT FROM OLD.document_ids
                   OR NEW.attachment_ids IS DISTINCT FROM OLD.attachment_ids
                   {domain_conditions} THEN
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
            f"CREATE TRIGGER assistant_runs_scope_immutable BEFORE UPDATE ON "
            f'"{schema}".assistant_runs FOR EACH ROW EXECUTE FUNCTION '
            f'"{schema}".prevent_assistant_run_scope_mutation()'
        )
    )
