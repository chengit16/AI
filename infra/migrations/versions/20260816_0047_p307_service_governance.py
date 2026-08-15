"""建立 P3-07 服务、访问策略、版本化路由和系统助手兼容迁移。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from uuid import UUID, uuid5

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import RowMapping

revision: str = "20260816_0047"
down_revision: str | None = "20260816_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SERVICE_NAMESPACE = UUID("4b72e2b0-8a2b-4a46-bcc7-23442cb7a307")


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _create_access_policies(schema)
    _create_services(schema)
    _create_routes(schema)
    _create_route_publications(schema)
    _create_control_requests(schema)
    _create_history_protection(schema)
    _create_service_validation(schema)
    _create_policy_validation(schema)
    _create_route_validation(schema)
    _create_publication_validation(schema)
    _backfill_system_services(schema)


def downgrade() -> None:
    schema = _schema()
    _reject_unsafe_downgrade(schema)
    _drop_validation(schema)
    op.drop_index(
        "ix_service_control_requests_workspace_time",
        table_name="service_control_requests",
        schema=schema,
    )
    op.drop_table("service_control_requests", schema=schema)
    op.drop_table("service_route_publications", schema=schema)
    op.drop_index(
        "ix_service_routes_workspace_service_time",
        table_name="service_routes",
        schema=schema,
    )
    op.drop_table("service_routes", schema=schema)
    op.drop_constraint(
        "fk_service_access_policies_service",
        "service_access_policy_versions",
        schema=schema,
        type_="foreignkey",
    )
    op.drop_index(
        "ix_services_workspace_updated",
        table_name="services",
        schema=schema,
    )
    op.drop_table("services", schema=schema)
    op.drop_table("service_access_policy_versions", schema=schema)


def _create_access_policies(schema: str) -> None:
    op.create_table(
        "service_access_policy_versions",
        sa.Column(
            "access_policy_version_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("visibility", sa.String(32), nullable=False),
        sa.Column(
            "allowed_department_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "allowed_account_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "service_id",
            "version",
            name="uq_service_access_policies_version",
        ),
        sa.UniqueConstraint(
            "access_policy_version_id",
            "service_id",
            "workspace_id",
            name="uq_service_access_policies_identity",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_service_access_policies_creator",
        ),
        sa.CheckConstraint("version >= 1", name="ck_service_access_policies_version"),
        sa.CheckConstraint(
            "visibility IN ('workspace', 'restricted')",
            name="ck_service_access_policies_visibility",
        ),
        sa.CheckConstraint(
            "(visibility = 'workspace' AND cardinality(allowed_department_ids) = 0 "
            "AND cardinality(allowed_account_ids) = 0) OR "
            "(visibility = 'restricted' AND "
            "(cardinality(allowed_department_ids) > 0 "
            "OR cardinality(allowed_account_ids) > 0))",
            name="ck_service_access_policies_subjects",
        ),
        sa.CheckConstraint(
            "policy_hash ~ '^[0-9a-f]{64}$'",
            name="ck_service_access_policies_hash",
        ),
        schema=schema,
    )


def _create_services(schema: str) -> None:
    op.create_table(
        "services",
        sa.Column("service_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_key", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("service_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "access_policy_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("service_id", "workspace_id", name="uq_services_id_workspace"),
        sa.UniqueConstraint("workspace_id", "service_key", name="uq_services_workspace_key"),
        sa.ForeignKeyConstraint(
            ["agent_id", "workspace_id"],
            [f"{schema}.agents.agent_id", f"{schema}.agents.workspace_id"],
            name="fk_services_agent",
        ),
        sa.ForeignKeyConstraint(
            ["access_policy_version_id", "service_id", "workspace_id"],
            [
                f"{schema}.service_access_policy_versions.access_policy_version_id",
                f"{schema}.service_access_policy_versions.service_id",
                f"{schema}.service_access_policy_versions.workspace_id",
            ],
            name="fk_services_access_policy",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_services_creator",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_services_updater",
        ),
        sa.CheckConstraint(
            "service_key ~ '^[a-z][a-z0-9-]{2,79}$'",
            name="ck_services_key",
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 120",
            name="ck_services_name",
        ),
        sa.CheckConstraint(
            "service_type IN ('system_assistant', 'custom_knowledge_agent', "
            "'scenario_application', 'open_api')",
            name="ck_services_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'suspended', 'archived')",
            name="ck_services_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_services_version"),
        schema=schema,
    )
    op.create_foreign_key(
        "fk_service_access_policies_service",
        "service_access_policy_versions",
        "services",
        ["service_id", "workspace_id"],
        ["service_id", "workspace_id"],
        source_schema=schema,
        referent_schema=schema,
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_index(
        "ix_services_workspace_updated",
        "services",
        ["workspace_id", "updated_at"],
        schema=schema,
    )


def _create_routes(schema: str) -> None:
    op.create_table(
        "service_routes",
        sa.Column("route_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("route_version", sa.Integer(), nullable=False),
        sa.Column("route_mode", sa.String(32), nullable=False),
        sa.Column("primary_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canary_release_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("canary_percent", sa.Integer(), nullable=False),
        sa.Column("previous_route_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("route_hash", sa.String(64), nullable=False),
        sa.Column("created_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("service_id", "route_version", name="uq_service_routes_version"),
        sa.UniqueConstraint(
            "route_id",
            "service_id",
            "workspace_id",
            name="uq_service_routes_identity",
        ),
        sa.ForeignKeyConstraint(
            ["service_id", "workspace_id"],
            [f"{schema}.services.service_id", f"{schema}.services.workspace_id"],
            name="fk_service_routes_service",
        ),
        sa.ForeignKeyConstraint(
            ["primary_release_id", "workspace_id"],
            [f"{schema}.agent_releases.release_id", f"{schema}.agent_releases.workspace_id"],
            name="fk_service_routes_primary_release",
        ),
        sa.ForeignKeyConstraint(
            ["canary_release_id", "workspace_id"],
            [f"{schema}.agent_releases.release_id", f"{schema}.agent_releases.workspace_id"],
            name="fk_service_routes_canary_release",
        ),
        sa.ForeignKeyConstraint(
            ["previous_route_id", "service_id", "workspace_id"],
            [
                f"{schema}.service_routes.route_id",
                f"{schema}.service_routes.service_id",
                f"{schema}.service_routes.workspace_id",
            ],
            name="fk_service_routes_previous",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_service_routes_creator",
        ),
        sa.CheckConstraint("route_version >= 1", name="ck_service_routes_version"),
        sa.CheckConstraint(
            "route_mode IN ('active', 'canary', 'rollback')",
            name="ck_service_routes_mode",
        ),
        sa.CheckConstraint(
            "(route_mode = 'canary' AND canary_release_id IS NOT NULL "
            "AND canary_release_id <> primary_release_id "
            "AND canary_percent BETWEEN 1 AND 99) OR "
            "(route_mode <> 'canary' AND canary_release_id IS NULL AND canary_percent = 0)",
            name="ck_service_routes_canary",
        ),
        sa.CheckConstraint(
            "(route_version = 1 AND previous_route_id IS NULL) OR "
            "(route_version > 1 AND previous_route_id IS NOT NULL)",
            name="ck_service_routes_previous",
        ),
        sa.CheckConstraint(
            "route_hash ~ '^[0-9a-f]{64}$'",
            name="ck_service_routes_hash",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_service_routes_workspace_service_time",
        "service_routes",
        ["workspace_id", "service_id", "created_at"],
        schema=schema,
    )


def _create_route_publications(schema: str) -> None:
    op.create_table(
        "service_route_publications",
        sa.Column("service_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("route_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("published_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["service_id", "workspace_id"],
            [f"{schema}.services.service_id", f"{schema}.services.workspace_id"],
            name="fk_service_route_publications_service",
        ),
        sa.ForeignKeyConstraint(
            ["route_id", "service_id", "workspace_id"],
            [
                f"{schema}.service_routes.route_id",
                f"{schema}.service_routes.service_id",
                f"{schema}.service_routes.workspace_id",
            ],
            name="fk_service_route_publications_route",
        ),
        sa.ForeignKeyConstraint(
            ["published_by_account_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_service_route_publications_publisher",
        ),
        sa.CheckConstraint(
            "generation >= 1",
            name="ck_service_route_publications_generation",
        ),
        schema=schema,
    )


def _create_control_requests(schema: str) -> None:
    op.create_table(
        "service_control_requests",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "actor_id",
            "operation",
            "idempotency_key",
            name="uq_service_control_requests_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{schema}.workspaces.workspace_id"],
            name="fk_service_control_requests_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            [f"{schema}.accounts.account_id"],
            name="fk_service_control_requests_actor",
        ),
        sa.ForeignKeyConstraint(
            ["service_id", "workspace_id"],
            [f"{schema}.services.service_id", f"{schema}.services.workspace_id"],
            name="fk_service_control_requests_service",
        ),
        sa.CheckConstraint(
            "operation IN ('service.create', 'service.update')",
            name="ck_service_control_requests_operation",
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'",
            name="ck_service_control_requests_idempotency",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_service_control_requests_hash",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_service_control_requests_snapshot",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_service_control_requests_workspace_time",
        "service_control_requests",
        ["workspace_id", "created_at"],
        schema=schema,
    )


def _create_history_protection(schema: str) -> None:
    # 历史策略、Route 和幂等请求只有生命周期清理事务可以删除，任何更新均被拒绝。
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".protect_service_history()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'DELETE'
                   AND current_setting('ai_platform.lifecycle_purge', true) = 'on' THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION 'service history is immutable' USING ERRCODE = '55000';
            END;
            $$
            """
        )
    )
    for table in (
        "service_access_policy_versions",
        "service_routes",
        "service_control_requests",
    ):
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE "
                f'ON "{schema}"."{table}" FOR EACH ROW '
                f'EXECUTE FUNCTION "{schema}".protect_service_history()'
            )
        )


def _create_service_validation(schema: str) -> None:
    # Service 只能以 draft v1 创建，激活必须已经具备匹配当前策略和当前 Route。
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".validate_service_write()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE
                allowed_transition boolean;
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'draft' OR NEW.version <> 1 THEN
                        RAISE EXCEPTION 'service must start as draft version one'
                            USING ERRCODE = '55000';
                    END IF;
                    RETURN NEW;
                END IF;
                allowed_transition :=
                    (OLD.status = 'draft' AND NEW.status IN ('active', 'archived')) OR
                    (OLD.status = 'active' AND NEW.status IN ('active', 'suspended', 'archived')) OR
                    (OLD.status = 'suspended'
                     AND NEW.status IN ('suspended', 'active', 'archived'));
                IF NOT allowed_transition
                   OR NEW.version <> OLD.version + 1
                   OR NEW.service_id <> OLD.service_id
                   OR NEW.workspace_id <> OLD.workspace_id
                   OR NEW.agent_id <> OLD.agent_id
                   OR NEW.service_key <> OLD.service_key
                   OR NEW.service_type <> OLD.service_type
                   OR NEW.created_by_account_id <> OLD.created_by_account_id
                   OR NEW.created_at <> OLD.created_at
                   OR NEW.updated_at < OLD.updated_at THEN
                    RAISE EXCEPTION 'invalid service state transition' USING ERRCODE = '55000';
                END IF;
                IF NEW.status = 'active' AND NOT EXISTS (
                    SELECT 1
                    FROM "{schema}".service_route_publications publication
                    JOIN "{schema}".service_routes route
                      ON route.route_id = publication.route_id
                     AND route.service_id = publication.service_id
                     AND route.workspace_id = publication.workspace_id
                    JOIN "{schema}".agent_releases release
                      ON release.release_id = route.primary_release_id
                     AND release.workspace_id = route.workspace_id
                    JOIN "{schema}".agents agent
                      ON agent.agent_id = release.agent_id
                     AND agent.workspace_id = release.workspace_id
                    WHERE publication.service_id = NEW.service_id
                      AND publication.workspace_id = NEW.workspace_id
                      AND release.agent_id = NEW.agent_id
                      AND release.status = 'released'
                      AND agent.status = 'active'
                ) THEN
                    RAISE EXCEPTION 'active service requires valid current route'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"CREATE TRIGGER trg_services_validate BEFORE INSERT OR UPDATE "
            f'ON "{schema}".services FOR EACH ROW '
            f'EXECUTE FUNCTION "{schema}".validate_service_write()'
        )
    )


def _create_policy_validation(schema: str) -> None:
    # 数组主体必须全部属于当前工作空间，且版本必须在既有最大版本后单调增加。
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".validate_service_access_policy()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE
                previous_version integer;
            BEGIN
                SELECT max(version) INTO previous_version
                FROM "{schema}".service_access_policy_versions
                WHERE service_id = NEW.service_id;
                IF NEW.version <> coalesce(previous_version, 0) + 1
                   OR EXISTS (
                       SELECT 1 FROM unnest(NEW.allowed_department_ids) subject_id
                       WHERE NOT EXISTS (
                           SELECT 1 FROM "{schema}".departments department
                           WHERE department.department_id = subject_id
                             AND department.workspace_id = NEW.workspace_id
                             AND department.status = 'active'
                       )
                   )
                   OR EXISTS (
                       SELECT 1 FROM unnest(NEW.allowed_account_ids) subject_id
                       WHERE NOT EXISTS (
                           SELECT 1 FROM "{schema}".workspace_memberships membership
                           WHERE membership.account_id = subject_id
                             AND membership.workspace_id = NEW.workspace_id
                             AND membership.status = 'active'
                       )
                   ) THEN
                    RAISE EXCEPTION 'invalid service access policy subjects or version'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"CREATE TRIGGER trg_service_access_policies_validate BEFORE INSERT "
            f'ON "{schema}".service_access_policy_versions FOR EACH ROW '
            f'EXECUTE FUNCTION "{schema}".validate_service_access_policy()'
        )
    )


def _create_route_validation(schema: str) -> None:
    # 复合外键保证空间一致，Trigger 再保证 Release 属于 Service 的 Agent 和类型。
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".validate_service_route()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE
                expected_kind text;
            BEGIN
                SELECT CASE
                    WHEN service_type = 'system_assistant' THEN 'system'
                    ELSE 'custom'
                END INTO expected_kind
                FROM "{schema}".services
                WHERE service_id = NEW.service_id AND workspace_id = NEW.workspace_id;
                IF expected_kind IS NULL OR NOT EXISTS (
                    SELECT 1
                    FROM "{schema}".services service
                    JOIN "{schema}".agent_releases release
                      ON release.release_id = NEW.primary_release_id
                     AND release.workspace_id = NEW.workspace_id
                    JOIN "{schema}".agents agent
                      ON agent.agent_id = release.agent_id
                     AND agent.workspace_id = release.workspace_id
                    WHERE service.service_id = NEW.service_id
                      AND service.workspace_id = NEW.workspace_id
                      AND release.agent_id = service.agent_id
                      AND release.release_kind = expected_kind
                      AND release.status = 'released'
                      AND agent.status = 'active'
                      AND (expected_kind = 'system' OR release.snapshot_hash IS NOT NULL)
                ) OR (
                    NEW.canary_release_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM "{schema}".agent_releases release
                        WHERE release.release_id = NEW.canary_release_id
                          AND release.workspace_id = NEW.workspace_id
                          AND release.agent_id = (
                              SELECT agent_id FROM "{schema}".services
                              WHERE service_id = NEW.service_id
                                AND workspace_id = NEW.workspace_id
                          )
                          AND release.release_kind = expected_kind
                          AND release.status = 'released'
                    )
                ) OR (
                    NEW.route_version > 1 AND NOT EXISTS (
                        SELECT 1 FROM "{schema}".service_routes previous
                        WHERE previous.route_id = NEW.previous_route_id
                          AND previous.service_id = NEW.service_id
                          AND previous.workspace_id = NEW.workspace_id
                          AND previous.route_version = NEW.route_version - 1
                    )
                ) THEN
                    RAISE EXCEPTION 'invalid service route release or version chain'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"CREATE TRIGGER trg_service_routes_validate BEFORE INSERT "
            f'ON "{schema}".service_routes FOR EACH ROW '
            f'EXECUTE FUNCTION "{schema}".validate_service_route()'
        )
    )


def _create_publication_validation(schema: str) -> None:
    # 当前指针可变，但只能逐 generation 指向下一条不可变 Route。
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".validate_service_route_publication()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE
                old_route_version integer;
                new_route_version integer;
            BEGIN
                SELECT route_version INTO new_route_version
                FROM "{schema}".service_routes
                WHERE route_id = NEW.route_id
                  AND service_id = NEW.service_id
                  AND workspace_id = NEW.workspace_id;
                IF TG_OP = 'INSERT' THEN
                    IF NEW.generation <> 1 OR new_route_version <> 1 THEN
                        RAISE EXCEPTION 'initial route publication must use version one'
                            USING ERRCODE = '55000';
                    END IF;
                    RETURN NEW;
                END IF;
                SELECT route_version INTO old_route_version
                FROM "{schema}".service_routes
                WHERE route_id = OLD.route_id
                  AND service_id = OLD.service_id
                  AND workspace_id = OLD.workspace_id;
                IF NEW.service_id <> OLD.service_id
                   OR NEW.workspace_id <> OLD.workspace_id
                   OR NEW.generation <> OLD.generation + 1
                   OR new_route_version <> old_route_version + 1
                   OR NEW.published_at < OLD.published_at THEN
                    RAISE EXCEPTION 'invalid current service route transition'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"CREATE TRIGGER trg_service_route_publications_validate BEFORE INSERT OR UPDATE "
            f'ON "{schema}".service_route_publications FOR EACH ROW '
            f'EXECUTE FUNCTION "{schema}".validate_service_route_publication()'
        )
    )


def _backfill_system_services(schema: str) -> None:
    # 只迁移当前系统发布；历史 Release 和 Run 保持原身份，后续变化由兼容端口追加 Route。
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            f"""
            SELECT publication.workspace_id, publication.agent_id, publication.release_id,
                   publication.published_by_account_id, publication.published_at
            FROM "{schema}".agent_publications publication
            JOIN "{schema}".agents agent
              ON agent.agent_id = publication.agent_id
             AND agent.workspace_id = publication.workspace_id
            JOIN "{schema}".agent_releases release
              ON release.release_id = publication.release_id
             AND release.workspace_id = publication.workspace_id
            WHERE agent.agent_kind = 'system'
              AND agent.agent_key = 'system_knowledge'
              AND release.release_kind = 'system'
            """
        )
    ).mappings()
    for row in rows:
        workspace_id = UUID(str(row["workspace_id"]))
        service_id = uuid5(SERVICE_NAMESPACE, f"service:{workspace_id}")
        policy_id = uuid5(SERVICE_NAMESPACE, f"policy:{workspace_id}:1")
        route_id = uuid5(SERVICE_NAMESPACE, f"route:{workspace_id}:1")
        policy_hash = _digest(
            {
                "service_id": str(service_id),
                "version": 1,
                "visibility": "workspace",
                "allowed_department_ids": [],
                "allowed_account_ids": [],
            }
        )
        route_hash = _digest(
            {
                "service_id": str(service_id),
                "workspace_id": str(workspace_id),
                "route_version": 1,
                "route_mode": "active",
                "primary_release_id": str(row["release_id"]),
                "canary_release_id": None,
                "canary_percent": 0,
                "previous_route_id": None,
            }
        )
        _insert_system_deployment(
            schema,
            service_id=service_id,
            policy_id=policy_id,
            route_id=route_id,
            policy_hash=policy_hash,
            route_hash=route_hash,
            row=row,
        )


def _insert_system_deployment(
    schema: str,
    *,
    service_id: UUID,
    policy_id: UUID,
    route_id: UUID,
    policy_hash: str,
    route_hash: str,
    row: RowMapping,
) -> None:
    bind = op.get_bind()
    values = {
        "service_id": service_id,
        "workspace_id": row["workspace_id"],
        "agent_id": row["agent_id"],
        "policy_id": policy_id,
        "route_id": route_id,
        "release_id": row["release_id"],
        "account_id": row["published_by_account_id"],
        "occurred_at": row["published_at"],
        "policy_hash": policy_hash,
        "route_hash": route_hash,
    }
    # 先插入 draft，再在策略和路由完整后触发受保护的 active 状态迁移。
    bind.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".services (
                service_id, workspace_id, agent_id, service_key, name, service_type,
                status, access_policy_version_id, created_by_account_id, created_at,
                updated_by_account_id, updated_at, version
            ) VALUES (
                :service_id, :workspace_id, :agent_id, 'system-knowledge', '系统知识助手',
                'system_assistant', 'draft', :policy_id, :account_id, :occurred_at,
                :account_id, :occurred_at, 1
            )
            """
        ),
        values,
    )
    bind.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".service_access_policy_versions (
                access_policy_version_id, service_id, workspace_id, version, visibility,
                allowed_department_ids, allowed_account_ids, policy_hash,
                created_by_account_id, created_at
            ) VALUES (
                :policy_id, :service_id, :workspace_id, 1, 'workspace',
                '{{}}', '{{}}', :policy_hash, :account_id, :occurred_at
            )
            """
        ),
        values,
    )
    bind.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".service_routes (
                route_id, service_id, workspace_id, route_version, route_mode,
                primary_release_id, canary_release_id, canary_percent, previous_route_id,
                route_hash, created_by_account_id, created_at
            ) VALUES (
                :route_id, :service_id, :workspace_id, 1, 'active',
                :release_id, NULL, 0, NULL, :route_hash, :account_id, :occurred_at
            )
            """
        ),
        values,
    )
    bind.execute(
        sa.text(
            f"""
            INSERT INTO "{schema}".service_route_publications (
                service_id, workspace_id, route_id, generation,
                published_by_account_id, published_at
            ) VALUES (
                :service_id, :workspace_id, :route_id, 1, :account_id, :occurred_at
            )
            """
        ),
        values,
    )
    bind.execute(
        sa.text(
            f"""
            UPDATE "{schema}".services
            SET status = 'active', version = 2
            WHERE service_id = :service_id AND workspace_id = :workspace_id
            """
        ),
        values,
    )


def _digest(document: object) -> str:
    payload = json.dumps(
        document,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _drop_validation(schema: str) -> None:
    triggers = (
        ("service_route_publications", "trg_service_route_publications_validate"),
        ("service_routes", "trg_service_routes_validate"),
        ("service_access_policy_versions", "trg_service_access_policies_validate"),
        ("services", "trg_services_validate"),
        ("service_control_requests", "trg_service_control_requests_immutable"),
        ("service_routes", "trg_service_routes_immutable"),
        (
            "service_access_policy_versions",
            "trg_service_access_policy_versions_immutable",
        ),
    )
    for table, trigger in triggers:
        op.execute(sa.text(f'DROP TRIGGER "{trigger}" ON "{schema}"."{table}"'))
    for function in (
        "validate_service_route_publication",
        "validate_service_route",
        "validate_service_access_policy",
        "validate_service_write",
        "protect_service_history",
    ):
        op.execute(sa.text(f'DROP FUNCTION "{schema}"."{function}"()'))


def _reject_unsafe_downgrade(schema: str) -> None:
    count = (
        op.get_bind()
        .execute(
            sa.text(
                f"""
                SELECT count(*)
                FROM "{schema}".services service
                WHERE service.service_type <> 'system_assistant'
                   OR service.version > 2
                   OR EXISTS (
                       SELECT 1 FROM "{schema}".service_routes route
                       WHERE route.service_id = service.service_id
                         AND route.route_version > 1
                   )
                   OR EXISTS (SELECT 1 FROM "{schema}".service_control_requests)
                """
            )
        )
        .scalar_one()
    )
    if int(count) > 0:
        raise RuntimeError("存在已治理服务、历史路由或控制请求。拒绝降级并丢失事实")
