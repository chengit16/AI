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
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
P311_OWNER_PERMISSIONS = frozenset(
    {
        "agent.definition.archive",
        "agent.definition.create",
        "agent.definition.read",
        "agent.definition.update",
        "agent.page.access",
        "agent.release.approve",
        "agent.release.publish",
        "agent.release.read",
        "agent.release.request",
        "agent.test.execute",
        "agent.test.read",
        "service.definition.create",
        "service.definition.update",
        "service.page.access",
        "service.route.canary",
        "service.route.promote",
        "service.route.rollback",
    }
)
P311_MEMBER_PERMISSIONS = frozenset(
    {
        "agent.definition.read",
        "agent.page.access",
        "agent.release.read",
        "agent.test.read",
        "service.page.access",
    }
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


def seed_existing_system_agent_publication(connection: Connection, schema: str) -> None:
    """在 0046 结构中写入可被服务治理迁移接管的系统助手发布事实。"""

    # 1. 先建立系统 Release 依赖的账号、个人空间和运行配置。
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".accounts (
              account_id, login_name, display_name, password_hash, status, auth_version,
              created_at, created_by_actor_id, updated_at, updated_by_actor_id, version
            ) VALUES (
              '10000000-0000-4000-8000-000000000307',
              'synthetic.service.backfill@example.com', '合成服务回填用户',
              'synthetic-password-hash', 'active', 1, '2026-08-16T00:00:00Z',
              '10000000-0000-4000-8000-000000000307', '2026-08-16T00:00:00Z',
              '10000000-0000-4000-8000-000000000307', 1
            );
            INSERT INTO "{schema}".workspaces (
              workspace_id, workspace_type, name, owner_account_id, entitlement_version,
              role_version, menu_version, status, created_at, created_by_actor_id,
              updated_at, updated_by_actor_id, version
            ) VALUES (
              '20000000-0000-4000-8000-000000000307', 'personal', '合成服务回填空间',
              '10000000-0000-4000-8000-000000000307', 1, 1, 1, 'active',
              '2026-08-16T00:00:00Z', '10000000-0000-4000-8000-000000000307',
              '2026-08-16T00:00:00Z', '10000000-0000-4000-8000-000000000307', 1
            );
            INSERT INTO "{schema}".ai_runtime_config_versions (
              runtime_config_version_id, version_number, display_name, content_hash,
              system_prompt_template, system_prompt_hash, component_versions,
              attempt_timeout_ms, total_timeout_ms, max_attempts_per_route,
              max_prompt_characters, max_output_tokens, max_response_characters,
              circuit_failure_threshold, circuit_recovery_ms, rule_degradation_message,
              max_estimated_cost_microunits, created_by_account_id, created_at
            ) VALUES (
              '30000000-0000-4000-8000-000000000307', 307, '合成服务回填配置',
              '{"a" * 64}', '只使用合成授权证据回答。', '{"b" * 64}',
              '{{"retrieval": "synthetic-p307"}}', 500, 2000, 1, 4000, 256, 8000,
              3, 30000, NULL, 5000000,
              '10000000-0000-4000-8000-000000000307', '2026-08-16T00:00:00Z'
            );
            """
        )
    )

    # 2. 模拟已投入使用的知识助手，并保留一个不应被本次 Migration 接管的系统 Agent。
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".agents (
              agent_id, workspace_id, agent_key, agent_kind, name, description, status,
              created_by_account_id, created_at, updated_at, version
            ) VALUES
            (
              '40000000-0000-4000-8000-000000000307',
              '20000000-0000-4000-8000-000000000307', 'system_knowledge', 'system',
              '合成系统知识助手', NULL, 'active',
              '10000000-0000-4000-8000-000000000307',
              '2026-08-16T00:00:00Z', '2026-08-16T00:00:00Z', 1
            ),
            (
              '41000000-0000-4000-8000-000000000307',
              '20000000-0000-4000-8000-000000000307', 'system-other', 'system',
              '合成无关系统 Agent', NULL, 'active',
              '10000000-0000-4000-8000-000000000307',
              '2026-08-16T00:00:00Z', '2026-08-16T00:00:00Z', 1
            );
            INSERT INTO "{schema}".agent_releases (
              release_id, agent_id, workspace_id, release_kind, version, status,
              runtime_config_version_id, config_hash, candidate_id, candidate_hash,
              source_draft_id, source_draft_revision, evaluation_run_id,
              approval_binding_id, snapshot, snapshot_hash, released_by_account_id,
              released_at
            ) VALUES
            (
              '50000000-0000-4000-8000-000000000307',
              '40000000-0000-4000-8000-000000000307',
              '20000000-0000-4000-8000-000000000307', 'system', 1, 'released',
              '30000000-0000-4000-8000-000000000307', '{"c" * 64}',
              NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
              '10000000-0000-4000-8000-000000000307', '2026-08-16T00:01:00Z'
            ),
            (
              '51000000-0000-4000-8000-000000000307',
              '41000000-0000-4000-8000-000000000307',
              '20000000-0000-4000-8000-000000000307', 'system', 1, 'released',
              '30000000-0000-4000-8000-000000000307', '{"d" * 64}',
              NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
              '10000000-0000-4000-8000-000000000307', '2026-08-16T00:01:00Z'
            );
            INSERT INTO "{schema}".agent_publications (
              agent_id, workspace_id, release_id, generation,
              published_by_account_id, published_at
            ) VALUES
            (
              '40000000-0000-4000-8000-000000000307',
              '20000000-0000-4000-8000-000000000307',
              '50000000-0000-4000-8000-000000000307', 1,
              '10000000-0000-4000-8000-000000000307', '2026-08-16T00:01:00Z'
            ),
            (
              '41000000-0000-4000-8000-000000000307',
              '20000000-0000-4000-8000-000000000307',
              '51000000-0000-4000-8000-000000000307', 1,
              '10000000-0000-4000-8000-000000000307', '2026-08-16T00:01:00Z'
            );
            """
        )
    )
    connection.commit()


def seed_runtime_isolation_runs(connection: Connection, schema: str) -> None:
    """在 0047 结构中写入一条可证明 Route 和一条无 Route 证据的历史 Run。"""

    # 两条已完成 Run 不触发活动会话唯一约束；Release 分别对应已接管和未接管的系统 Agent。
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".conversations (
              conversation_id, workspace_id, created_by_account_id, title, status,
              created_at, updated_at, version
            ) VALUES
            (
              '60000000-0000-4000-8000-000000000308',
              '20000000-0000-4000-8000-000000000307',
              '10000000-0000-4000-8000-000000000307', '合成可证明历史会话', 'active',
              '2026-08-16T00:02:00Z', '2026-08-16T00:03:00Z', 1
            ),
            (
              '61000000-0000-4000-8000-000000000308',
              '20000000-0000-4000-8000-000000000307',
              '10000000-0000-4000-8000-000000000307', '合成无路由历史会话', 'active',
              '2026-08-16T00:02:00Z', '2026-08-16T00:03:00Z', 1
            );
            INSERT INTO "{schema}".messages (
              message_id, workspace_id, conversation_id, role, status,
              created_by_account_id, created_at, updated_at, version
            ) VALUES
            (
              '70000000-0000-4000-8000-000000000308',
              '20000000-0000-4000-8000-000000000307',
              '60000000-0000-4000-8000-000000000308', 'user', 'completed',
              '10000000-0000-4000-8000-000000000307',
              '2026-08-16T00:02:00Z', '2026-08-16T00:02:00Z', 1
            ),
            (
              '71000000-0000-4000-8000-000000000308',
              '20000000-0000-4000-8000-000000000307',
              '61000000-0000-4000-8000-000000000308', 'user', 'completed',
              '10000000-0000-4000-8000-000000000307',
              '2026-08-16T00:02:00Z', '2026-08-16T00:02:00Z', 1
            );
            INSERT INTO "{schema}".assistant_runs (
              run_id, workspace_id, conversation_id, user_message_id,
              assistant_message_id, agent_release_id, runtime_config_version_id,
              requested_by_account_id, status, idempotency_key, request_hash,
              trace_id, traceparent, created_at, updated_at, completed_at, error_code
            ) VALUES
            (
              '80000000-0000-4000-8000-000000000308',
              '20000000-0000-4000-8000-000000000307',
              '60000000-0000-4000-8000-000000000308',
              '70000000-0000-4000-8000-000000000308', NULL,
              '50000000-0000-4000-8000-000000000307',
              '30000000-0000-4000-8000-000000000307',
              '10000000-0000-4000-8000-000000000307', 'completed',
              'synthetic-runtime-backfill-unique', '{"e" * 64}', '{"f" * 32}',
              '00-{"f" * 32}-{"a" * 16}-01', '2026-08-16T00:02:00Z',
              '2026-08-16T00:03:00Z', '2026-08-16T00:03:00Z', NULL
            ),
            (
              '81000000-0000-4000-8000-000000000308',
              '20000000-0000-4000-8000-000000000307',
              '61000000-0000-4000-8000-000000000308',
              '71000000-0000-4000-8000-000000000308', NULL,
              '51000000-0000-4000-8000-000000000307',
              '30000000-0000-4000-8000-000000000307',
              '10000000-0000-4000-8000-000000000307', 'completed',
              'synthetic-runtime-backfill-unproven', '{"d" * 64}', '{"c" * 32}',
              '00-{"c" * 32}-{"b" * 16}-01', '2026-08-16T00:02:00Z',
              '2026-08-16T00:03:00Z', '2026-08-16T00:03:00Z', NULL
            );
            """
        )
    )
    connection.commit()


def seed_service_invocation_upgrade_facts(connection: Connection, schema: str) -> None:
    """在 0049 结构中建立既有系统角色和当前菜单发布，验证 P3-10 非空升级。"""

    workspace_id = UUID("20000000-0000-4000-8000-000000000307")
    account_id = UUID("10000000-0000-4000-8000-000000000307")
    snapshot = {
        "schema_version": 1,
        "registry_version": 18,
        "workspace_id": str(workspace_id),
        "menu_version": 1,
        "menus": [
            {
                "menu_id": "82000000-0000-4000-8000-000000000167",
                "menu_key": "navigation.workspace.assistant",
                "parent_menu_id": "82000000-0000-4000-8000-000000000001",
                "name": "知识问答",
                "menu_type": "page",
                "page_resource_id": "80000000-0000-4000-8000-000000000007",
                "permission_code": "assistant.page.access",
                "icon_key": "message-circle",
                "sort_order": 300,
                "source": "system",
                "status": "active",
                "visible": True,
            }
        ],
        "role_menus": [],
        "menu_api_bindings": [],
    }
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".roles (
              role_id, workspace_id, role_key, name, status, system_managed,
              created_at, updated_at, version
            ) VALUES (
              '70000000-0000-4000-8000-000000000310', :workspace_id,
              'workspace_owner', '空间所有者', 'active', true,
              '2026-08-16T00:05:00Z', '2026-08-16T00:05:00Z', 1
            ), (
              '70000000-0000-4000-8000-000000000311', :workspace_id,
              'workspace_member', '空间成员', 'active', true,
              '2026-08-16T00:05:00Z', '2026-08-16T00:05:00Z', 1
            )
            """
        ),
        {"workspace_id": workspace_id},
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
              '90000000-0000-4000-8000-000000000310', :workspace_id, 1,
              'standard', NULL, 'published', CAST(:snapshot AS jsonb), :digest,
              ARRAY[]::varchar[], NULL, :account_id, :account_id,
              '2026-08-16T00:05:00Z', '2026-08-16T00:05:00Z',
              '2026-08-16T00:05:00Z', '2026-08-16T00:05:00Z', 1
            )
            """
        ),
        {
            "workspace_id": workspace_id,
            "account_id": account_id,
            "snapshot": json.dumps(snapshot, ensure_ascii=False),
            "digest": "9" * 64,
        },
    )
    connection.execute(
        text(
            f"""
            INSERT INTO "{schema}".workspace_menu_publications (
              workspace_id, current_release_id, published_at
            ) VALUES (
              :workspace_id, '90000000-0000-4000-8000-000000000310',
              '2026-08-16T00:05:00Z'
            )
            """
        ),
        {"workspace_id": workspace_id},
    )
    connection.commit()


def test_empty_schema_can_upgrade_downgrade_and_reupgrade_identically(
    migration_database: tuple[Config, Connection, str],
) -> None:
    config, connection, schema = migration_database

    command.upgrade(config, "head")
    connection.commit()
    first_head = schema_snapshot(connection, schema)

    assert current_revision(connection, schema) == "20260903_0081"
    assert business_tables(connection, schema) == {
        "accounts",
        "approval_policies",
        "approval_policy_versions",
        "approval_instances",
        "approval_instance_levels",
        "approval_assignments",
        "approval_actions",
        "agent_control_requests",
        "agent_approval_bindings",
        "agent_evaluation_case_results",
        "agent_evaluation_check_results",
        "agent_evaluation_dataset_versions",
        "agent_evaluation_policy_versions",
        "agent_evaluation_runs",
        "agent_evaluation_test_cases",
        "agent_knowledge_scope_items",
        "agent_knowledge_scope_versions",
        "agent_output_schema_versions",
        "agent_prompt_versions",
        "agent_safety_policy_versions",
        "agent_tool_definitions",
        "tool_plan_availability",
        "tool_attempts",
        "tool_calls",
        "tool_policy_decisions",
        "tool_confirmations",
        "tool_confirmation_invalidations",
        "tool_credentials",
        "tool_idempotency_records",
        "synthetic_tool_side_effects",
        "tool_safe_results",
        "tool_usage_records",
        "tool_progress_events",
        "tool_runs",
        "tool_steps",
        "agent_draft_revisions",
        "agent_drafts",
        "agent_publications",
        "agent_release_candidates",
        "agent_releases",
        "agents",
        "service_access_policy_versions",
        "service_control_requests",
        "service_route_publications",
        "service_routes",
        "services",
        "ai_runtime_config_publication",
        "ai_runtime_config_versions",
        "ai_runtime_model_routes",
        "audit_records",
        "audit_export_requests",
        "consumer_receipts",
        "cost_attribution_lines",
        "cost_attribution_windows",
        "cost_ledger_entries",
        "conversation_attachments",
        "department_closure",
        "departments",
        "document_accesses",
        "document_index_publications",
        "document_publications",
        "document_publish_requests",
        "document_publish_request_categories",
        "document_sources",
        "document_versions",
        "documents",
        "ingestion_job_attempts",
        "ingestion_job_stages",
        "ingestion_jobs",
        "index_maintenance_requests",
        "index_versions",
        "index_maintenance_runs",
        "index_inspection_findings",
        "knowledge_bases",
        "knowledge_folders",
        "knowledge_tags",
        "document_folder_bindings",
        "document_tag_bindings",
        "document_favorites",
        "enterprise_categories",
        "enterprise_category_documents",
        "enterprise_brain_reports",
        "lifecycle_deletion_certificates",
        "lifecycle_compliance_proofs",
        "lifecycle_export_records",
        "lifecycle_legal_hold_releases",
        "lifecycle_legal_holds",
        "lifecycle_purge_requests",
        "lifecycle_regulatory_policy_versions",
        "lifecycle_retention_runs",
        "l3_isolation_migration_checkpoints",
        "l3_isolation_recovery_records",
        "l3_isolation_resource_profiles",
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
        "outbox_replay_requests",
        "positions",
        "platform_administrators",
        "platform_audit_records",
        "quality_dataset_members",
        "quality_dataset_versions",
        "quality_evaluation_layer_results",
        "quality_evaluation_runs",
        "quality_evaluation_sample_results",
        "quality_operation_source_results",
        "quality_operation_windows",
        "quality_sample_versions",
        "role_bindings",
        "role_permission_grants",
        "roles",
        "resource_projections",
        "registered_menu_api_bindings",
        "retrieval_chunks",
        "stream_events",
        "stream_runs",
        "team_knowledge_domain_bases",
        "team_knowledge_domain_departments",
        "team_knowledge_domain_members",
        "team_knowledge_domain_rag_policies",
        "team_knowledge_domains",
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
        "workspace_isolation_migration_plans",
        "workspace_isolation_policy_versions",
        "workspace_isolation_route_versions",
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

    assert current_revision(connection, schema) == "20260903_0081"
    assert schema_snapshot(connection, schema) == first_head


def test_runtime_binding_upgrade_backfills_only_proven_history_and_blocks_downgrade(
    migration_database: tuple[Config, Connection, str],
) -> None:
    """0048 不猜测历史 Route，并在产生追溯证据后拒绝破坏性降级。"""

    config, connection, schema = migration_database
    command.upgrade(config, "20260816_0046")
    connection.commit()
    seed_existing_system_agent_publication(connection, schema)
    command.upgrade(config, "20260816_0047")
    connection.commit()
    seed_runtime_isolation_runs(connection, schema)

    # 1. 唯一匹配当前系统 Route 的历史 Run 被回填，无 Route 的历史 Run 保持完整空绑定。
    command.upgrade(config, "20260816_0049")
    connection.commit()
    rows = (
        connection.execute(
            text(
                f"""
            SELECT run.run_id, run.service_id, run.service_route_id,
                   run.service_route_version, run.agent_release_id
              FROM "{schema}".assistant_runs run
             ORDER BY run.run_id
            """
            )
        )
        .mappings()
        .all()
    )
    route = (
        connection.execute(
            text(
                f"""
            SELECT service.service_id, route.route_id, route.route_version
              FROM "{schema}".services service
              JOIN "{schema}".service_route_publications publication
                ON publication.service_id = service.service_id
              JOIN "{schema}".service_routes route
                ON route.route_id = publication.route_id
            """
            )
        )
        .mappings()
        .one()
    )
    assert rows[0] == {
        "run_id": UUID("80000000-0000-4000-8000-000000000308"),
        "service_id": route["service_id"],
        "service_route_id": route["route_id"],
        "service_route_version": route["route_version"],
        "agent_release_id": UUID("50000000-0000-4000-8000-000000000307"),
    }
    assert rows[1] == {
        "run_id": UUID("81000000-0000-4000-8000-000000000308"),
        "service_id": None,
        "service_route_id": None,
        "service_route_version": None,
        "agent_release_id": UUID("51000000-0000-4000-8000-000000000307"),
    }
    connection.commit()

    # 2. 兼容空值只属于升级前历史，新 INSERT 即使其他引用有效也必须完整绑定。
    with pytest.raises(DBAPIError):
        connection.execute(
            text(
                f"""
                INSERT INTO "{schema}".assistant_runs (
                  run_id, workspace_id, conversation_id, user_message_id,
                  assistant_message_id, service_id, service_route_id,
                  service_route_version, agent_release_id, runtime_config_version_id,
                  requested_by_account_id, status, idempotency_key, request_hash,
                  trace_id, traceparent, created_at, updated_at, completed_at, error_code
                ) VALUES (
                  '82000000-0000-4000-8000-000000000308',
                  '20000000-0000-4000-8000-000000000307',
                  '61000000-0000-4000-8000-000000000308',
                  '71000000-0000-4000-8000-000000000308', NULL,
                  NULL, NULL, NULL,
                  '51000000-0000-4000-8000-000000000307',
                  '30000000-0000-4000-8000-000000000307',
                  '10000000-0000-4000-8000-000000000307', 'completed',
                  'synthetic-runtime-new-unbound', '{"1" * 64}', '{"2" * 32}',
                  '00-{"2" * 32}-{"3" * 16}-01', '2026-08-16T00:04:00Z',
                  '2026-08-16T00:04:00Z', '2026-08-16T00:04:00Z', NULL
                )
                """
            )
        )
    connection.rollback()

    # 3. 已回填完整绑定时降级会丢失追溯证据，Migration 必须显式拒绝。
    with pytest.raises(RuntimeError, match="拒绝降级"):
        command.downgrade(config, "20260816_0047")
    connection.rollback()
    assert current_revision(connection, schema) == "20260816_0049"


def test_service_invocation_upgrade_preserves_history_and_rejects_unsafe_downgrade(
    migration_database: tuple[Config, Connection, str],
) -> None:
    """0050 回填账号 Actor、复制菜单授权，并拒绝丢失隐藏调用身份的降级。"""

    # 长函数保留原因: 同一临时 Schema 必须连续证明非空升级、数据库约束和安全降级边界。
    config, connection, schema = migration_database
    command.upgrade(config, "20260816_0046")
    connection.commit()
    seed_existing_system_agent_publication(connection, schema)
    command.upgrade(config, "20260816_0047")
    connection.commit()
    seed_runtime_isolation_runs(connection, schema)
    command.upgrade(config, "20260816_0049")
    connection.commit()
    seed_service_invocation_upgrade_facts(connection, schema)

    # 1. 历史事实保持私有会话语义，Run Actor 精确回填为原请求账号。
    command.upgrade(config, "20260816_0050")
    connection.commit()
    history = connection.execute(
        text(
            f"""
            SELECT run.run_id, run.requested_by_account_id, run.requested_by_actor_id,
                   conversation.conversation_kind
              FROM "{schema}".assistant_runs AS run
              JOIN "{schema}".conversations AS conversation
                ON conversation.conversation_id = run.conversation_id
             ORDER BY run.run_id
            """
        )
    ).mappings()
    assert len(history.all()) == 2
    actor_preserved = connection.execute(
        text(
            f"""
            SELECT requested_by_account_id = requested_by_actor_id AS actor_preserved
              FROM "{schema}".assistant_runs
            """
        )
    ).scalars()
    assert all(actor_preserved)
    assert (
        connection.scalar(
            text(
                f'SELECT count(*) FROM "{schema}".conversations '
                "WHERE conversation_kind = 'private'"
            )
        )
        == 2
    )

    # 2. 既有 Owner 获得服务读取权限，当前菜单复制到注册表 19 且新增三个调用动作。
    permission_count = connection.scalar(
        text(
            f"""
            SELECT count(*) FROM "{schema}".role_permission_grants
             WHERE permission_code = 'service.definition.read'
            """
        )
    )
    current_snapshot = connection.scalar(
        text(
            f"""
            SELECT releases.snapshot
              FROM "{schema}".workspace_menu_publications AS publications
              JOIN "{schema}".menu_releases AS releases
                ON releases.release_id = publications.current_release_id
            """
        )
    )
    assert permission_count == 2
    assert current_snapshot["registry_version"] == 19
    assert {item["menu_id"] for item in current_snapshot["menus"]}.issuperset(
        {
            "82000000-0000-4000-8000-000000000198",
            "82000000-0000-4000-8000-000000000199",
            "82000000-0000-4000-8000-000000000200",
        }
    )
    assert len(current_snapshot["menu_api_bindings"]) == 3

    # 3. 数据库拒绝 Run Actor 改绑；出现隐藏服务会话后 Migration 不允许破坏性降级。
    with pytest.raises(DBAPIError):
        connection.execute(
            text(
                f"""
                UPDATE "{schema}".assistant_runs
                   SET requested_by_actor_id = '10000000-0000-4000-8000-000000000999'
                 WHERE run_id = '80000000-0000-4000-8000-000000000308'
                """
            )
        )
    connection.rollback()
    connection.execute(
        text(
            f"""
            UPDATE "{schema}".conversations
               SET conversation_kind = 'service_invocation'
             WHERE conversation_id = '60000000-0000-4000-8000-000000000308'
            """
        )
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="拒绝降级"):
        command.downgrade(config, "20260816_0049")
    connection.rollback()
    assert current_revision(connection, schema) == "20260816_0050"


def test_agent_console_upgrade_restores_roles_bindings_and_menu_publication(
    migration_database: tuple[Config, Connection, str],
) -> None:
    """0051 只追加控制台事实，降级后可恢复 0050 菜单并再次升级。"""

    config, connection, schema = migration_database
    command.upgrade(config, "20260816_0046")
    connection.commit()
    seed_existing_system_agent_publication(connection, schema)
    command.upgrade(config, "20260816_0047")
    connection.commit()
    seed_runtime_isolation_runs(connection, schema)
    command.upgrade(config, "20260816_0049")
    connection.commit()
    seed_service_invocation_upgrade_facts(connection, schema)
    command.upgrade(config, "20260816_0050")
    connection.commit()
    source_release = (
        connection.execute(
            text(
                f"""
            SELECT releases.release_id, releases.snapshot, releases.snapshot_digest
              FROM "{schema}".workspace_menu_publications AS publications
              JOIN "{schema}".menu_releases AS releases
                ON releases.workspace_id = publications.workspace_id
               AND releases.release_id = publications.current_release_id
            """
            )
        )
        .mappings()
        .one()
    )

    # 1. 非空升级为系统角色补齐最小权限，并追加 Registry 20 菜单和接口绑定。
    command.upgrade(config, "20260816_0051")
    connection.commit()
    grants = connection.execute(
        text(
            f"""
            SELECT roles.role_key, grants.permission_code
              FROM "{schema}".role_permission_grants AS grants
              JOIN "{schema}".roles AS roles
                ON roles.workspace_id = grants.workspace_id
               AND roles.role_id = grants.role_id
             WHERE roles.role_key IN ('workspace_owner', 'workspace_member')
            """
        )
    ).all()
    permissions_by_role = {
        role_key: {permission for key, permission in grants if key == role_key}
        for role_key in ("workspace_owner", "workspace_member")
    }
    current_release = (
        connection.execute(
            text(
                f"""
            SELECT releases.release_id, releases.source_release_id, releases.snapshot
              FROM "{schema}".workspace_menu_publications AS publications
              JOIN "{schema}".menu_releases AS releases
                ON releases.workspace_id = publications.workspace_id
               AND releases.release_id = publications.current_release_id
            """
            )
        )
        .mappings()
        .one()
    )
    snapshot = current_release["snapshot"]
    assert current_revision(connection, schema) == "20260816_0051"
    assert permissions_by_role["workspace_owner"] >= P311_OWNER_PERMISSIONS
    assert permissions_by_role["workspace_member"] >= P311_MEMBER_PERMISSIONS
    assert "service.definition.read" in permissions_by_role["workspace_member"]
    assert current_release["source_release_id"] == source_release["release_id"]
    assert snapshot["registry_version"] == 20
    assert {
        item["menu_id"]
        for item in snapshot["menus"]
        if item["menu_id"].endswith(tuple(f"{number:012d}" for number in range(201, 219)))
    } == {f"82000000-0000-4000-8000-{number:012d}" for number in range(201, 219)}
    assert {
        item["api_resource_id"]
        for item in snapshot["menu_api_bindings"]
        if item["api_resource_id"].endswith(tuple(f"{number:012d}" for number in range(123, 139)))
    } == {f"81000000-0000-4000-8000-{number:012d}" for number in range(123, 139)}

    # 2. 降级恢复原发布且不改原快照，只回收系统角色和本节点绑定。
    command.downgrade(config, "20260816_0050")
    connection.commit()
    restored_release = (
        connection.execute(
            text(
                f"""
            SELECT releases.release_id, releases.snapshot, releases.snapshot_digest
              FROM "{schema}".workspace_menu_publications AS publications
              JOIN "{schema}".menu_releases AS releases
                ON releases.workspace_id = publications.workspace_id
               AND releases.release_id = publications.current_release_id
            """
            )
        )
        .mappings()
        .one()
    )
    remaining_member_permissions = set(
        connection.execute(
            text(
                f"""
                SELECT grants.permission_code
                  FROM "{schema}".role_permission_grants AS grants
                  JOIN "{schema}".roles AS roles
                    ON roles.workspace_id = grants.workspace_id
                   AND roles.role_id = grants.role_id
                 WHERE roles.role_key = 'workspace_member'
                """
            )
        ).scalars()
    )
    assert current_revision(connection, schema) == "20260816_0050"
    assert dict(restored_release) == dict(source_release)
    assert remaining_member_permissions.isdisjoint(P311_MEMBER_PERMISSIONS)
    assert "service.definition.read" in remaining_member_permissions
    assert (
        connection.scalar(
            text(
                f"""
            SELECT count(*) FROM "{schema}".registered_menu_api_bindings
             WHERE api_resource_id::text >= '81000000-0000-4000-8000-000000000123'
               AND api_resource_id::text <= '81000000-0000-4000-8000-000000000138'
            """
            )
        )
        == 0
    )

    # 3. 同一非空事实再次升级仍只生成一个确定性控制台发布。
    command.upgrade(config, "head")
    connection.commit()
    assert current_revision(connection, schema) == "20260903_0081"
    assert (
        connection.scalar(
            text(
                f"""
            SELECT count(*) FROM "{schema}".menu_releases
             WHERE source_release_id = :source_release_id
               AND snapshot ->> 'registry_version' = '20'
            """
            ),
            {"source_release_id": source_release["release_id"]},
        )
        == 1
    )
    upgraded = (
        connection.execute(
            text(
                f"""
                SELECT releases.snapshot
                FROM "{schema}".workspace_menu_publications AS publications
                JOIN "{schema}".menu_releases AS releases
                  ON releases.workspace_id = publications.workspace_id
                 AND releases.release_id = publications.current_release_id
                """
            )
        )
        .mappings()
        .one()["snapshot"]
    )
    assert upgraded["registry_version"] == 35
    assert {item["menu_id"] for item in upgraded["menus"]} >= {
        "82000000-0000-4000-8000-000000000219",
        "82000000-0000-4000-8000-000000000220",
    }
    assert any(
        item["api_resource_id"] == "81000000-0000-4000-8000-000000000139"
        for item in upgraded["menu_api_bindings"]
    )
    assert (
        connection.scalar(
            text(
                f"""
            SELECT count(*)
            FROM "{schema}".role_permission_grants AS grants
            JOIN "{schema}".roles AS roles
              ON roles.workspace_id = grants.workspace_id AND roles.role_id = grants.role_id
            WHERE roles.role_key = 'workspace_owner'
              AND grants.permission_code = 'agent.operations.read'
            """
            )
        )
        == 1
    )
    assert (
        connection.scalar(
            text(
                f"""
            SELECT count(*) FROM pg_indexes
            WHERE schemaname = '{schema}'
              AND indexname = 'ix_model_invocations_workspace_trace'
            """
            )
        )
        == 1
    )


def test_existing_system_publication_is_backfilled_as_current_service_route(
    migration_database: tuple[Config, Connection, str],
) -> None:
    """0046 的系统发布升级后成为可追溯服务，且不会改变原发布身份。"""

    config, connection, schema = migration_database
    command.upgrade(config, "20260816_0046")
    connection.commit()
    seed_existing_system_agent_publication(connection, schema)

    # 1. 升级只接管当前发布，生成单一系统 Service 及其首版策略和 Route。
    command.upgrade(config, "20260816_0047")
    connection.commit()
    deployment = (
        connection.execute(
            text(
                f"""
            SELECT service.workspace_id, service.agent_id, service.service_key,
                   service.service_type, service.status, service.version,
                   policy.visibility, policy.version AS policy_version,
                   route.primary_release_id, route.route_version,
                   publication.generation
            FROM "{schema}".services service
            JOIN "{schema}".service_access_policy_versions policy
              ON policy.access_policy_version_id = service.access_policy_version_id
             AND policy.service_id = service.service_id
             AND policy.workspace_id = service.workspace_id
            JOIN "{schema}".service_route_publications publication
              ON publication.service_id = service.service_id
             AND publication.workspace_id = service.workspace_id
            JOIN "{schema}".service_routes route
              ON route.route_id = publication.route_id
             AND route.service_id = publication.service_id
             AND route.workspace_id = publication.workspace_id
            """
            )
        )
        .mappings()
        .one()
    )
    assert deployment == {
        "workspace_id": UUID("20000000-0000-4000-8000-000000000307"),
        "agent_id": UUID("40000000-0000-4000-8000-000000000307"),
        "service_key": "system-knowledge",
        "service_type": "system_assistant",
        "status": "active",
        "version": 2,
        "visibility": "workspace",
        "policy_version": 1,
        "primary_release_id": UUID("50000000-0000-4000-8000-000000000307"),
        "route_version": 1,
        "generation": 1,
    }
    # Alembic 使用独立连接执行 DDL；先结束本连接的隐式只读事务，避免持有表锁。
    connection.commit()

    # 2. 纯回填的系统服务允许安全降级，原有 Agent 发布事实仍完整保留。
    command.downgrade(config, "20260816_0046")
    connection.commit()
    assert current_revision(connection, schema) == "20260816_0046"
    assert "services" not in business_tables(connection, schema)
    publications: list[tuple[UUID, UUID, int]] = [
        (row.agent_id, row.release_id, row.generation)
        for row in connection.execute(
            text(
                f"""
                SELECT agent_id, release_id, generation
                FROM "{schema}".agent_publications
                ORDER BY agent_id
                """
            )
        )
    ]
    assert publications == [
        (
            UUID("40000000-0000-4000-8000-000000000307"),
            UUID("50000000-0000-4000-8000-000000000307"),
            1,
        ),
        (
            UUID("41000000-0000-4000-8000-000000000307"),
            UUID("51000000-0000-4000-8000-000000000307"),
            1,
        ),
    ]


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


def test_existing_clean_upload_is_backfilled_and_cancelled_job_can_downgrade(
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

    # P2-02 的取消终态降级为阶段 1 可理解的失败态，同时清除阶段 2 专属取消元数据。
    connection.execute(
        text(
            f"""
            UPDATE "{schema}".ingestion_jobs
               SET status = 'cancelled',
                   completed_at = '2026-08-15T00:00:00Z',
                   cancelled_by_actor_id = '10000000-0000-4000-8000-000000000319',
                   cancelled_at = '2026-08-15T00:00:00Z',
                   updated_at = '2026-08-15T00:00:00Z'
             WHERE ingestion_job_id = '42000000-0000-4000-8000-000000000319'
            """
        )
    )
    connection.commit()

    command.downgrade(config, "20260815_0035")
    connection.commit()
    downgraded = connection.execute(
        text(
            f"""
            SELECT status, failure_stage, error_code, error_message
              FROM "{schema}".ingestion_jobs
             WHERE ingestion_job_id = '42000000-0000-4000-8000-000000000319'
            """
        )
    ).one()
    assert tuple(downgraded) == (
        "failed",
        "worker",
        "INGESTION_JOB_CANCELLED",
        "任务在降级前已取消",
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
    command.upgrade(config, "20260815_0035")
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
