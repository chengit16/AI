"""建立 P3-09 灰度路由、晋级回滚幂等操作与 Run 绑定门禁。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config

revision: str = "20260816_0049"
down_revision: str | None = "20260816_0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _extend_control_operations(schema)
    _strengthen_route_validation(schema)
    _allow_current_canary_run_bindings(schema)


def downgrade() -> None:
    schema = _schema()
    _reject_unsafe_downgrade(schema)
    _restore_active_only_run_bindings(schema)
    _restore_route_validation(schema)
    _restore_control_operations(schema)


def _extend_control_operations(schema: str) -> None:
    op.drop_constraint(
        "ck_service_control_requests_operation",
        "service_control_requests",
        schema=schema,
        type_="check",
    )
    op.create_check_constraint(
        "ck_service_control_requests_operation",
        "service_control_requests",
        "operation IN ('service.create', 'service.update', 'service.route.canary', "
        "'service.route.promote', 'service.route.rollback')",
        schema=schema,
    )


def _restore_control_operations(schema: str) -> None:
    op.drop_constraint(
        "ck_service_control_requests_operation",
        "service_control_requests",
        schema=schema,
        type_="check",
    )
    op.create_check_constraint(
        "ck_service_control_requests_operation",
        "service_control_requests",
        "operation IN ('service.create', 'service.update')",
        schema=schema,
    )


def _strengthen_route_validation(schema: str) -> None:
    # 主版本与灰度版本执行同一 Release 完整性门禁，避免灰度列绕过 Agent 状态和快照校验。
    op.execute(sa.text(_route_validation_sql(schema, strict_canary=True)))


def _restore_route_validation(schema: str) -> None:
    op.execute(sa.text(_route_validation_sql(schema, strict_canary=False)))


def _route_validation_sql(schema: str, *, strict_canary: bool) -> str:
    canary_gate = (
        f"""
                    NEW.canary_release_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1
                        FROM "{schema}".agent_releases release
                        JOIN "{schema}".agents agent
                          ON agent.agent_id = release.agent_id
                         AND agent.workspace_id = release.workspace_id
                        WHERE release.release_id = NEW.canary_release_id
                          AND release.workspace_id = NEW.workspace_id
                          AND release.agent_id = (
                              SELECT agent_id FROM "{schema}".services
                              WHERE service_id = NEW.service_id
                                AND workspace_id = NEW.workspace_id
                          )
                          AND release.release_kind = expected_kind
                          AND release.status = 'released'
                          AND agent.status = 'active'
                          AND (expected_kind = 'system' OR release.snapshot_hash IS NOT NULL)
                    )
        """
        if strict_canary
        else f"""
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
        """
    )
    return f"""
        CREATE OR REPLACE FUNCTION "{schema}".validate_service_route()
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
                {canary_gate}
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


def _allow_current_canary_run_bindings(schema: str) -> None:
    op.execute(sa.text(_run_binding_sql(schema, allow_rollout=True)))


def _restore_active_only_run_bindings(schema: str) -> None:
    op.execute(sa.text(_run_binding_sql(schema, allow_rollout=False)))


def _run_binding_sql(schema: str, *, allow_rollout: bool) -> str:
    route_gate = (
        """
                   AND (
                     (route.route_mode IN ('active', 'rollback')
                      AND route.primary_release_id = NEW.agent_release_id
                      AND route.canary_release_id IS NULL
                      AND route.canary_percent = 0)
                     OR
                     (route.route_mode = 'canary'
                      AND NEW.agent_release_id IN (
                        route.primary_release_id, route.canary_release_id
                      )
                      AND route.canary_release_id IS NOT NULL
                      AND route.canary_release_id <> route.primary_release_id
                      AND route.canary_percent BETWEEN 1 AND 99)
                   )
        """
        if allow_rollout
        else """
                   AND route.route_mode = 'active'
                   AND route.primary_release_id = NEW.agent_release_id
                   AND route.canary_release_id IS NULL
                   AND route.canary_percent = 0
        """
    )
    return f"""
        CREATE OR REPLACE FUNCTION "{schema}".validate_assistant_run_service_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            IF NEW.service_id IS DISTINCT FROM OLD.service_id
               OR NEW.service_route_id IS DISTINCT FROM OLD.service_route_id
               OR NEW.service_route_version IS DISTINCT FROM OLD.service_route_version
               OR NEW.agent_release_id IS DISTINCT FROM OLD.agent_release_id
               OR NEW.runtime_config_version_id IS DISTINCT FROM OLD.runtime_config_version_id
            THEN
              RAISE EXCEPTION 'assistant run release binding is immutable'
                USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
          END IF;

          IF NEW.service_id IS NULL
             OR NEW.service_route_id IS NULL
             OR NEW.service_route_version IS NULL
          THEN
            RAISE EXCEPTION 'assistant run requires a complete service route binding'
              USING ERRCODE = '55000';
          END IF;

          IF NOT EXISTS (
            SELECT 1
              FROM "{schema}".service_routes route
              JOIN "{schema}".service_route_publications publication
                ON publication.route_id = route.route_id
               AND publication.service_id = route.service_id
               AND publication.workspace_id = route.workspace_id
              JOIN "{schema}".services service
                ON service.service_id = route.service_id
               AND service.workspace_id = route.workspace_id
              JOIN "{schema}".agents agent
                ON agent.agent_id = service.agent_id
               AND agent.workspace_id = service.workspace_id
              JOIN "{schema}".agent_releases release
                ON release.release_id = NEW.agent_release_id
               AND release.workspace_id = NEW.workspace_id
               AND release.agent_id = service.agent_id
             WHERE route.route_id = NEW.service_route_id
               AND route.service_id = NEW.service_id
               AND route.workspace_id = NEW.workspace_id
               AND route.route_version = NEW.service_route_version
               {route_gate}
               AND service.status = 'active'
               AND agent.status = 'active'
               AND release.status = 'released'
               AND release.runtime_config_version_id = NEW.runtime_config_version_id
          ) THEN
            RAISE EXCEPTION 'invalid assistant run service route binding'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$
    """


def _reject_unsafe_downgrade(schema: str) -> None:
    # 0048 的应用和 Trigger 都不能安全解释灰度/回滚 Route 或新增幂等操作。
    connection = op.get_bind()
    route_count = connection.scalar(
        sa.text(
            f'SELECT count(*) FROM "{schema}".service_routes '
            "WHERE route_mode IN ('canary', 'rollback')"
        )
    )
    operation_count = connection.scalar(
        sa.text(
            f'SELECT count(*) FROM "{schema}".service_control_requests '
            "WHERE operation LIKE 'service.route.%'"
        )
    )
    if int(route_count or 0) > 0 or int(operation_count or 0) > 0:
        raise RuntimeError("数据库已包含 P3-09 灰度或回滚事实, 拒绝降级到 active-only Runtime")
