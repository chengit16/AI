"""从隔离的服务 Route 与 AgentRelease 发布事实装载 Runtime 快照。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ai_platform_api.modules.service_runtime.domain.models import (
    RuntimeReleaseKind,
    RuntimeReleaseSnapshot,
    RuntimeSourceUnavailableError,
)
from ai_platform_api.persistence.tables import (
    agent_releases,
    agents,
    service_route_publications,
    service_routes,
    services,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyRuntimeSnapshotSource:
    """使用独立短事务查询发布表，不暴露服务治理或 Agent 控制面 Repository。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def get_current(
        self,
        workspace_id: UUID,
        service_id: UUID,
    ) -> RuntimeReleaseSnapshot | None:
        """读取当前指针选择的主 Release；P3-09 前不执行灰度分配。"""

        statement = (
            _snapshot_select()
            .join(
                service_route_publications,
                service_route_publications.c.service_id == services.c.service_id,
            )
            .join(
                service_routes,
                (service_routes.c.route_id == service_route_publications.c.route_id)
                & (service_routes.c.service_id == services.c.service_id)
                & (service_routes.c.workspace_id == services.c.workspace_id),
            )
            .join(
                agent_releases,
                (agent_releases.c.release_id == service_routes.c.primary_release_id)
                & (agent_releases.c.workspace_id == services.c.workspace_id),
            )
            .join(
                agents,
                (agents.c.agent_id == services.c.agent_id)
                & (agents.c.workspace_id == services.c.workspace_id),
            )
            .where(
                services.c.workspace_id == workspace_id,
                services.c.service_id == service_id,
            )
        )
        return self._one(statement)

    def get_bound(
        self,
        workspace_id: UUID,
        service_id: UUID,
        route_id: UUID,
        service_route_version: int,
        agent_release_id: UUID,
    ) -> RuntimeReleaseSnapshot | None:
        """按 Run 冻结的 Route 和 Release 查询历史发布，不读取当前指针。"""

        statement = (
            _snapshot_select()
            .join(
                service_routes,
                (service_routes.c.service_id == services.c.service_id)
                & (service_routes.c.workspace_id == services.c.workspace_id),
            )
            .join(
                agent_releases,
                (agent_releases.c.release_id == agent_release_id)
                & (agent_releases.c.workspace_id == services.c.workspace_id),
            )
            .join(
                agents,
                (agents.c.agent_id == services.c.agent_id)
                & (agents.c.workspace_id == services.c.workspace_id),
            )
            .where(
                services.c.workspace_id == workspace_id,
                services.c.service_id == service_id,
                service_routes.c.route_id == route_id,
                service_routes.c.route_version == service_route_version,
                or_(
                    service_routes.c.primary_release_id == agent_release_id,
                    service_routes.c.canary_release_id == agent_release_id,
                ),
            )
        )
        return self._one(statement)

    def _one(self, statement: Select[tuple[Any, ...]]) -> RuntimeReleaseSnapshot | None:
        try:
            with self._session_factory() as session:
                row = session.execute(statement).one_or_none()
        except SQLAlchemyError as error:
            raise RuntimeSourceUnavailableError from error
        return _snapshot(row) if row is not None else None


def _snapshot_select() -> Select[tuple[Any, ...]]:
    """固定 Runtime 所需发布列，查询中没有任何草稿、候选或审批当前态。"""

    return select(
        services.c.workspace_id,
        services.c.service_id,
        services.c.service_key,
        services.c.status.label("service_status"),
        services.c.access_policy_version_id,
        service_routes.c.route_id,
        service_routes.c.route_version,
        service_routes.c.route_mode,
        service_routes.c.primary_release_id,
        service_routes.c.canary_release_id,
        service_routes.c.canary_percent,
        service_routes.c.previous_route_id,
        service_routes.c.route_hash,
        agents.c.agent_id,
        agents.c.status.label("agent_status"),
        agent_releases.c.release_id,
        agent_releases.c.release_kind,
        agent_releases.c.version.label("release_version"),
        agent_releases.c.status.label("release_status"),
        agent_releases.c.runtime_config_version_id,
        agent_releases.c.config_hash,
        agent_releases.c.snapshot,
        agent_releases.c.snapshot_hash,
        agent_releases.c.released_at,
    ).select_from(services)


def _snapshot(row: Row[Any]) -> RuntimeReleaseSnapshot:
    release_snapshot = row.snapshot
    return RuntimeReleaseSnapshot(
        workspace_id=row.workspace_id,
        service_id=row.service_id,
        service_key=row.service_key,
        service_status=row.service_status,
        access_policy_version_id=row.access_policy_version_id,
        route_id=row.route_id,
        service_route_version=row.route_version,
        route_mode=row.route_mode,
        primary_release_id=row.primary_release_id,
        canary_release_id=row.canary_release_id,
        canary_percent=row.canary_percent,
        previous_route_id=row.previous_route_id,
        route_hash=row.route_hash,
        agent_id=row.agent_id,
        agent_status=row.agent_status,
        agent_release_id=row.release_id,
        release_kind=cast("RuntimeReleaseKind", row.release_kind),
        release_version=row.release_version,
        release_status=row.release_status,
        runtime_config_version_id=row.runtime_config_version_id,
        config_hash=row.config_hash,
        release_snapshot=(
            cast("dict[str, object]", release_snapshot) if release_snapshot is not None else None
        ),
        release_snapshot_hash=row.snapshot_hash,
        released_at=row.released_at,
    )
