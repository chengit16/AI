"""验证阶段 1 Migration 从空库升级、逐步降级和再次升级。"""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, text

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)


@pytest.fixture
def migration_database() -> Iterator[tuple[Config, Connection, str]]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1a03_test_{uuid4().hex}"
    engine = create_engine(database_url)
    with engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.commit()
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "infra/migrations"))
        config.set_main_option(
            "prepend_sys_path",
            f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
        )
        config.set_main_option("sqlalchemy.url", database_url)
        config.set_main_option("ai_platform_schema", schema)
        try:
            yield config, connection, schema
        finally:
            # 测试断言或迁移失败后连接可能处于 aborted 状态，必须先回滚才能清理临时 Schema。
            connection.rollback()
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            connection.commit()
    engine.dispose()


def schema_snapshot(connection: Connection, schema: str) -> tuple[tuple[object, ...], ...]:
    rows = connection.execute(
        text(
            """
            SELECT 'column', table_name, column_name, ordinal_position::text,
                   data_type, udt_name, is_nullable,
                   COALESCE(column_default, ''), COALESCE(generation_expression, '')
              FROM information_schema.columns
             WHERE table_schema = :schema
            UNION ALL
            SELECT 'constraint', conrelid::regclass::text, conname, contype::text,
                   pg_get_constraintdef(oid), '', '', '', ''
              FROM pg_constraint
             WHERE connamespace = CAST(:schema AS regnamespace)
            UNION ALL
            SELECT 'index', tablename, indexname, '',
                   REPLACE(indexdef, quote_ident(:schema) || '.', ''), '', '', '', ''
              FROM pg_indexes
             WHERE schemaname = :schema
             ORDER BY 1, 2, 3, 4, 5
            """
        ),
        {"schema": schema},
    )
    return tuple(tuple(row) for row in rows)


def business_tables(connection: Connection, schema: str) -> set[str]:
    return set(
        connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name <> 'alembic_version'"
            ),
            {"schema": schema},
        ).scalars()
    )


def current_revision(connection: Connection, schema: str) -> str | None:
    revision = connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version'))
    return revision if isinstance(revision, str) else None


def test_empty_schema_can_upgrade_downgrade_and_reupgrade_identically(
    migration_database: tuple[Config, Connection, str],
) -> None:
    config, connection, schema = migration_database

    command.upgrade(config, "head")
    connection.commit()
    first_head = schema_snapshot(connection, schema)

    assert current_revision(connection, schema) == "20260815_0035"
    assert business_tables(connection, schema) == {
        "accounts",
        "approval_policies",
        "approval_policy_versions",
        "approval_instances",
        "approval_instance_levels",
        "approval_assignments",
        "approval_actions",
        "agent_publications",
        "agent_releases",
        "agents",
        "ai_runtime_config_publication",
        "ai_runtime_config_versions",
        "ai_runtime_model_routes",
        "audit_records",
        "consumer_receipts",
        "department_closure",
        "departments",
        "document_index_publications",
        "document_publications",
        "document_sources",
        "document_versions",
        "documents",
        "ingestion_jobs",
        "index_versions",
        "knowledge_bases",
        "membership_departments",
        "membership_positions",
        "menu_releases",
        "model_provider_configurations",
        "model_provider_credentials",
        "model_invocation_attempts",
        "model_invocations",
        "message_feedbacks",
        "open_api_keys",
        "outbox_events",
        "positions",
        "platform_administrators",
        "platform_audit_records",
        "role_bindings",
        "role_permission_grants",
        "roles",
        "resource_projections",
        "registered_menu_api_bindings",
        "retrieval_chunks",
        "stream_events",
        "stream_runs",
        "role_menus",
        "workspace_entitlements",
        "workspace_feature_settings",
        "workspace_memberships",
        "workspace_menu_overrides",
        "workspace_menu_publications",
        "workspace_invitations",
        "workspace_resources",
        "workspace_usage_counters",
        "workspace_usage_records",
        "workspaces",
        "workflow_drafts",
        "workflow_node_attempts",
        "workflow_publications",
        "workflow_run_steps",
        "workflow_runs",
        "workflow_versions",
        "workflows",
        "assistant_runs",
        "conversations",
        "message_parts",
        "messages",
        "retrieval_plans",
        "retrieval_query_variants",
        "retrieval_candidate_snapshots",
        "retrieval_evidence_items",
        "retrieval_evidence_sets",
    }

    command.downgrade(config, "base")
    connection.commit()

    assert business_tables(connection, schema) == set()
    assert current_revision(connection, schema) is None

    command.upgrade(config, "head")
    connection.commit()

    assert current_revision(connection, schema) == "20260815_0035"
    assert schema_snapshot(connection, schema) == first_head


def test_owner_knowledge_permissions_are_backfilled_for_existing_spaces(
    migration_database: tuple[Config, Connection, str],
) -> None:
    config, connection, schema = migration_database
    command.upgrade(config, "20260814_0023")
    connection.commit()
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".workspaces (
              workspace_id, workspace_type, name, owner_account_id, entitlement_version,
              role_version, menu_version, status, created_at, created_by_actor_id,
              updated_at, updated_by_actor_id, version
            ) VALUES (
              '20000000-0000-4000-8000-000000000424', 'enterprise', '合成历史企业空间', NULL, 1,
              1, 1, 'active', '2026-08-14T00:00:00Z',
              '10000000-0000-4000-8000-000000000424', '2026-08-14T00:00:00Z',
              '10000000-0000-4000-8000-000000000424', 1
            );
            INSERT INTO "{schema}".roles (
              role_id, workspace_id, role_key, name, status, system_managed,
              created_at, updated_at, version
            ) VALUES (
              '70000000-0000-4000-8000-000000000424',
              '20000000-0000-4000-8000-000000000424', 'workspace_owner', '空间所有者',
              'active', true, '2026-08-14T00:00:00Z', '2026-08-14T00:00:00Z', 1
            );
            """
        )
    )
    connection.commit()

    command.upgrade(config, "head")
    connection.commit()
    permission_codes = set(
        connection.execute(
            text(
                f"""
                SELECT permission_code
                FROM "{schema}".role_permission_grants
                WHERE workspace_id = '20000000-0000-4000-8000-000000000424'::uuid
                  AND role_id = '70000000-0000-4000-8000-000000000424'::uuid
                """
            )
        ).scalars()
    )
    assert permission_codes >= {
        "knowledge.base.read",
        "knowledge.document.read",
        "knowledge.ingestion.read",
        "knowledge.ingestion.retry",
        "knowledge.production.access",
    }
    command.downgrade(config, "20260814_0023")
    connection.commit()
    assert (
        connection.scalar(
            text(
                f"""
                SELECT count(*)
                FROM "{schema}".role_permission_grants
                WHERE workspace_id = '20000000-0000-4000-8000-000000000424'::uuid
                  AND role_id = '70000000-0000-4000-8000-000000000424'::uuid
                  AND permission_code IN (
                    'knowledge.base.read', 'knowledge.document.read',
                    'knowledge.ingestion.read', 'knowledge.ingestion.retry',
                    'knowledge.production.access'
                  )
                """
            )
        )
        == 5
    )


def test_existing_clean_upload_is_backfilled_as_queued_ingestion_job(
    migration_database: tuple[Config, Connection, str],
) -> None:
    config, connection, schema = migration_database
    command.upgrade(config, "20260814_0017")
    connection.commit()
    content_hash = "1" * 64

    # Schema 名称由测试生成且只含字母、数字和下划线；固定合成事实模拟 P1D-02 升级现场。
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".accounts (
              account_id, login_name, display_name, password_hash, status, auth_version,
              created_at, created_by_actor_id, updated_at, updated_by_actor_id, version
            ) VALUES (
              '10000000-0000-4000-8000-000000000319', 'synthetic.backfill@example.com',
              '合成回填用户', 'synthetic-password-hash', 'active', 1,
              '2026-08-14T00:00:00Z', '10000000-0000-4000-8000-000000000319',
              '2026-08-14T00:00:00Z', '10000000-0000-4000-8000-000000000319', 1
            );
            INSERT INTO "{schema}".workspaces (
              workspace_id, workspace_type, name, owner_account_id, entitlement_version,
              role_version, menu_version, status, created_at, created_by_actor_id,
              updated_at, updated_by_actor_id, version
            ) VALUES (
              '20000000-0000-4000-8000-000000000319', 'personal', '合成回填空间',
              '10000000-0000-4000-8000-000000000319', 1, 1, 1, 'active',
              '2026-08-14T00:00:00Z', '10000000-0000-4000-8000-000000000319',
              '2026-08-14T00:00:00Z', '10000000-0000-4000-8000-000000000319', 1
            );
            INSERT INTO "{schema}".knowledge_bases (
              knowledge_base_id, workspace_id, name, description, default_visibility,
              department_ids, default_security_level, status, created_by_account_id,
              created_at, updated_at, deleted_at, version
            ) VALUES (
              '30000000-0000-4000-8000-000000000319',
              '20000000-0000-4000-8000-000000000319', '合成回填知识库', NULL,
              'workspace', '{{}}', 'INTERNAL', 'active',
              '10000000-0000-4000-8000-000000000319',
              '2026-08-14T00:00:00Z', '2026-08-14T00:00:00Z', NULL, 1
            );
            INSERT INTO "{schema}".documents (
              document_id, workspace_id, knowledge_base_id, title, visibility,
              department_ids, security_level, permission_labels, status,
              created_by_account_id, created_at, updated_at, deleted_at, version
            ) VALUES (
              '40000000-0000-4000-8000-000000000319',
              '20000000-0000-4000-8000-000000000319',
              '30000000-0000-4000-8000-000000000319', '合成回填文档',
              'workspace', '{{}}', 'INTERNAL', '{{}}', 'active',
              '10000000-0000-4000-8000-000000000319',
              '2026-08-14T00:00:00Z', '2026-08-14T00:00:00Z', NULL, 1
            );
            INSERT INTO "{schema}".document_versions (
              document_version_id, workspace_id, document_id, version_number, status,
              content_hash, created_by_account_id, created_at, published_at, record_version
            ) VALUES (
              '41000000-0000-4000-8000-000000000319',
              '20000000-0000-4000-8000-000000000319',
              '40000000-0000-4000-8000-000000000319', 1, 'draft', NULL,
              '10000000-0000-4000-8000-000000000319',
              '2026-08-14T00:00:00Z', NULL, 1
            );
            INSERT INTO "{schema}".document_sources (
              source_id, workspace_id, document_version_id, source_kind, source_name,
              original_object_key, source_path, source_url, external_source_id, captured_at,
              created_at, media_type, size_bytes, content_hash, scan_status,
              scanner_version, scanned_at
            ) VALUES (
              '42000000-0000-4000-8000-000000000319',
              '20000000-0000-4000-8000-000000000319',
              '41000000-0000-4000-8000-000000000319', 'upload', 'synthetic.txt',
              'workspaces/20000000-0000-4000-8000-000000000319/uploads/synthetic.txt',
              NULL, NULL, NULL, NULL, '2026-08-14T00:00:00Z', 'text/plain', 32,
              '{content_hash}', 'clean', 'synthetic-scanner-v1', '2026-08-14T00:00:00Z'
            );
            """
        )
    )
    connection.commit()

    command.upgrade(config, "head")
    connection.commit()

    stored = connection.execute(
        text(
            f"""
            SELECT ingestion_job_id, status, attempt_count, max_attempts,
                   requested_by_actor_id, length(trace_id), length(traceparent)
              FROM "{schema}".ingestion_jobs
             WHERE document_version_id = '41000000-0000-4000-8000-000000000319'
            """
        )
    ).one()
    assert tuple(stored) == (
        UUID("42000000-0000-4000-8000-000000000319"),
        "queued",
        0,
        3,
        UUID("10000000-0000-4000-8000-000000000319"),
        32,
        55,
    )


def test_existing_menu_release_is_copied_and_restored_without_mutation(
    migration_database: tuple[Config, Connection, str],
) -> None:
    config, connection, schema = migration_database
    command.upgrade(config, "20260815_0034")
    connection.commit()
    workspace_id = UUID("20000000-0000-4000-8000-000000000505")
    account_id = UUID("10000000-0000-4000-8000-000000000505")
    role_id = UUID("70000000-0000-4000-8000-000000000505")
    source_release_id = UUID("90000000-0000-4000-8000-000000000505")
    source_digest = "a" * 64
    source_snapshot = {
        "schema_version": 1,
        "registry_version": 3,
        "workspace_id": str(workspace_id),
        "menu_version": 1,
        "menus": [
            {
                "menu_id": "82000000-0000-4000-8000-000000000171",
                "menu_key": "navigation.workspace.workflow",
                "parent_menu_id": "82000000-0000-4000-8000-000000000001",
                "name": "工作流",
                "menu_type": "page",
                "page_resource_id": "80000000-0000-4000-8000-000000000009",
                "permission_code": "workflow.page.access",
                "icon_key": None,
                "sort_order": 370,
                "source": "system",
                "status": "disabled",
                "visible": False,
            }
        ],
        "role_menus": [],
        "menu_api_bindings": [],
    }

    # 1. 固定历史账号、空间、系统角色和发布指针，复现真实升级前的非空菜单数据。
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".accounts (
              account_id, login_name, display_name, password_hash, status, auth_version,
              created_at, created_by_actor_id, updated_at, updated_by_actor_id, version
            ) VALUES (
              :account_id, 'synthetic.p1f05.migration@example.com', '合成迁移用户',
              'synthetic-password-hash', 'active', 1, '2026-08-15T00:00:00Z', :account_id,
              '2026-08-15T00:00:00Z', :account_id, 1
            )
            """
        ),
        {"account_id": account_id},
    )
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".workspaces (
              workspace_id, workspace_type, name, owner_account_id, entitlement_version,
              role_version, menu_version, status, created_at, created_by_actor_id,
              updated_at, updated_by_actor_id, version
            ) VALUES (
              :workspace_id, 'enterprise', '合成历史菜单空间', NULL, 1, 1, 1,
              'active', '2026-08-15T00:00:00Z', :account_id,
              '2026-08-15T00:00:00Z', :account_id, 1
            )
            """
        ),
        {"workspace_id": workspace_id, "account_id": account_id},
    )
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".roles (
              role_id, workspace_id, role_key, name, status, system_managed,
              created_at, updated_at, version
            ) VALUES (
              :role_id, :workspace_id, 'workspace_owner', '空间所有者', 'active', true,
              '2026-08-15T00:00:00Z', '2026-08-15T00:00:00Z', 1
            )
            """
        ),
        {"role_id": role_id, "workspace_id": workspace_id},
    )
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".menu_releases (
              release_id, workspace_id, release_number, release_kind, source_release_id,
              status, snapshot, snapshot_digest, validation_errors, rejection_reason,
              created_by_account_id, decided_by_account_id, created_at, validated_at,
              decided_at, published_at, version
            ) VALUES (
              :release_id, :workspace_id, 1, 'standard', NULL, 'published',
              CAST(:snapshot AS jsonb), :digest, ARRAY[]::varchar[], NULL,
              :account_id, :account_id, '2026-08-15T00:00:00Z', '2026-08-15T00:00:00Z',
              '2026-08-15T00:00:00Z', '2026-08-15T00:00:00Z', 1
            )
            """
        ),
        {
            "release_id": source_release_id,
            "workspace_id": workspace_id,
            "snapshot": json.dumps(source_snapshot, ensure_ascii=False),
            "digest": source_digest,
            "account_id": account_id,
        },
    )
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".workspace_menu_publications (
              workspace_id, current_release_id, published_at
            ) VALUES (:workspace_id, :release_id, '2026-08-15T00:00:00Z')
            """
        ),
        {"workspace_id": workspace_id, "release_id": source_release_id},
    )
    connection.commit()

    # 2. 升级必须复制新事实并切换指针，原快照、原摘要和来源发布保持不变。
    command.upgrade(config, "head")
    connection.commit()
    releases = (
        connection.execute(
            text(
                f"""
            SELECT release_id, release_number, source_release_id, snapshot, snapshot_digest
            FROM "{schema}".menu_releases
            WHERE workspace_id = :workspace_id
            ORDER BY release_number
            """
            ),
            {"workspace_id": workspace_id},
        )
        .mappings()
        .all()
    )
    assert len(releases) == 2
    assert releases[0]["snapshot"] == source_snapshot
    assert releases[0]["snapshot_digest"] == source_digest
    assert releases[1]["source_release_id"] == source_release_id
    assert releases[1]["snapshot"]["registry_version"] == 15
    assert releases[1]["snapshot"]["menus"][0]["status"] == "active"
    assert releases[1]["snapshot"]["menus"][0]["icon_key"] == "network"
    assert releases[1]["snapshot"]["menus"][0]["visible"] is True
    assert releases[1]["snapshot"]["menu_api_bindings"] == [
        {
            "menu_id": "82000000-0000-4000-8000-000000000177",
            "api_resource_id": "81000000-0000-4000-8000-000000000101",
            "action_type": "query",
        }
    ]
    assert (
        connection.scalar(
            text(
                f"""
            SELECT current_release_id FROM "{schema}".workspace_menu_publications
            WHERE workspace_id = :workspace_id
            """
            ),
            {"workspace_id": workspace_id},
        )
        == releases[1]["release_id"]
    )

    # 3. 降级只删除本 Revision 的复制版本，并把当前指针恢复到原发布事实。
    command.downgrade(config, "20260815_0034")
    connection.commit()
    assert (
        connection.scalar(
            text(
                f"""
            SELECT current_release_id FROM "{schema}".workspace_menu_publications
            WHERE workspace_id = :workspace_id
            """
            ),
            {"workspace_id": workspace_id},
        )
        == source_release_id
    )
    assert (
        connection.scalar(
            text(
                f'SELECT count(*) FROM "{schema}".menu_releases WHERE workspace_id = :workspace_id'
            ),
            {"workspace_id": workspace_id},
        )
        == 1
    )
