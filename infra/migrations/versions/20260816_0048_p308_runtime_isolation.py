"""建立 P3-08 Run 唯一路由绑定与数据库防改绑门禁。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.config import Config
from sqlalchemy.dialects import postgresql

revision: str = "20260816_0048"
down_revision: str | None = "20260816_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    config = op.get_context().config
    if not isinstance(config, Config):
        raise RuntimeError("Alembic Migration 缺少有效配置")
    return config.get_main_option("ai_platform_schema", "public")


def upgrade() -> None:
    schema = _schema()
    _add_run_binding_columns(schema)
    _backfill_unique_historical_bindings(schema)
    _protect_run_bindings(schema)


def downgrade() -> None:
    schema = _schema()
    _reject_unsafe_downgrade(schema)
    _drop_run_binding_protection(schema)
    _drop_run_binding_columns(schema)


def _add_run_binding_columns(schema: str) -> None:
    # 列保持可空只为容纳无法证明 Route 的升级前历史 Run；Trigger 会拒绝任何新空绑定。
    op.add_column(
        "assistant_runs",
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "assistant_runs",
        sa.Column("service_route_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "assistant_runs",
        sa.Column("service_route_version", sa.Integer(), nullable=True),
        schema=schema,
    )
    op.create_unique_constraint(
        "uq_service_routes_run_binding",
        "service_routes",
        ["route_id", "service_id", "workspace_id", "route_version"],
        schema=schema,
    )
    op.create_foreign_key(
        "fk_assistant_runs_service",
        "assistant_runs",
        "services",
        ["service_id", "workspace_id"],
        ["service_id", "workspace_id"],
        source_schema=schema,
        referent_schema=schema,
    )
    op.create_foreign_key(
        "fk_assistant_runs_service_route",
        "assistant_runs",
        "service_routes",
        ["service_route_id", "service_id", "workspace_id", "service_route_version"],
        ["route_id", "service_id", "workspace_id", "route_version"],
        source_schema=schema,
        referent_schema=schema,
    )
    op.create_check_constraint(
        "ck_assistant_runs_service_binding",
        "assistant_runs",
        "(service_id IS NULL AND service_route_id IS NULL AND service_route_version IS NULL) OR "
        "(service_id IS NOT NULL AND service_route_id IS NOT NULL "
        "AND service_route_version >= 1)",
        schema=schema,
    )
    op.create_index(
        "ix_assistant_runs_service_release_time",
        "assistant_runs",
        ["workspace_id", "service_id", "agent_release_id", "created_at"],
        schema=schema,
    )


def _backfill_unique_historical_bindings(schema: str) -> None:
    # 仅唯一匹配的历史 Release 可以回填；回滚复用导致的歧义不能靠时间猜测 Route。
    op.execute(
        sa.text(
            f"""
            WITH candidates AS (
              SELECT run.run_id,
                     route.service_id,
                     route.route_id,
                     route.route_version,
                     count(*) OVER (PARTITION BY run.run_id) AS candidate_count
                FROM "{schema}".assistant_runs run
                JOIN "{schema}".services service
                  ON service.workspace_id = run.workspace_id
                JOIN "{schema}".service_routes route
                  ON route.service_id = service.service_id
                 AND route.workspace_id = service.workspace_id
                 AND (
                   route.primary_release_id = run.agent_release_id
                   OR route.canary_release_id = run.agent_release_id
                 )
                JOIN "{schema}".agent_releases release
                  ON release.release_id = run.agent_release_id
                 AND release.workspace_id = run.workspace_id
                 AND release.agent_id = service.agent_id
            ), unique_candidates AS (
              SELECT run_id, service_id, route_id, route_version
                FROM candidates
               WHERE candidate_count = 1
            )
            UPDATE "{schema}".assistant_runs run
               SET service_id = candidate.service_id,
                   service_route_id = candidate.route_id,
                   service_route_version = candidate.route_version
              FROM unique_candidates candidate
             WHERE run.run_id = candidate.run_id
            """
        )
    )


def _protect_run_bindings(schema: str) -> None:
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION "{schema}".validate_assistant_run_service_binding()
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
                   AND route.route_mode = 'active'
                   AND route.primary_release_id = NEW.agent_release_id
                   AND route.canary_release_id IS NULL
                   AND route.canary_percent = 0
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
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_assistant_runs_service_binding
            BEFORE INSERT OR UPDATE ON "{schema}".assistant_runs
            FOR EACH ROW EXECUTE FUNCTION "{schema}".validate_assistant_run_service_binding()
            """
        )
    )


def _reject_unsafe_downgrade(schema: str) -> None:
    connection = op.get_bind()
    count = connection.scalar(
        sa.text(f'SELECT count(*) FROM "{schema}".assistant_runs WHERE service_id IS NOT NULL')
    )
    if int(count or 0) > 0:
        raise RuntimeError("assistant_runs 已包含 P3-08 Route 绑定, 拒绝降级丢失追溯证据")


def _drop_run_binding_protection(schema: str) -> None:
    op.execute(
        sa.text(f'DROP TRIGGER trg_assistant_runs_service_binding ON "{schema}".assistant_runs')
    )
    op.execute(sa.text(f'DROP FUNCTION "{schema}".validate_assistant_run_service_binding()'))


def _drop_run_binding_columns(schema: str) -> None:
    op.drop_index(
        "ix_assistant_runs_service_release_time",
        table_name="assistant_runs",
        schema=schema,
    )
    op.drop_constraint(
        "ck_assistant_runs_service_binding",
        "assistant_runs",
        schema=schema,
        type_="check",
    )
    op.drop_constraint(
        "fk_assistant_runs_service_route",
        "assistant_runs",
        schema=schema,
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_assistant_runs_service",
        "assistant_runs",
        schema=schema,
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_service_routes_run_binding",
        "service_routes",
        schema=schema,
        type_="unique",
    )
    for column in ("service_route_version", "service_route_id", "service_id"):
        op.drop_column("assistant_runs", column, schema=schema)
