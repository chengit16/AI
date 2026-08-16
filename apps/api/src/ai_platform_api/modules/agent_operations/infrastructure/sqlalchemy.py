"""从既有运行事实聚合 AgentRelease 运营指标，不复制业务正文。"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from types import TracebackType
from typing import Any, cast
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session, sessionmaker

from ai_platform_api.modules.agent_operations.domain.models import (
    OperationsRouteContext,
    ReleaseOperationsFacts,
)
from ai_platform_api.persistence.tables import (
    agent_evaluation_runs,
    agent_releases,
    ai_runtime_config_versions,
    assistant_runs,
    message_feedbacks,
    model_invocations,
    service_route_publications,
    service_routes,
    services,
)

SessionFactory = sessionmaker[Session]
TERMINAL_RUN_STATUSES = ("completed", "failed", "cancelled")


class SqlAlchemyAgentOperationsRepository:
    """执行受工作空间和服务约束的低基数聚合查询。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_route_context(
        self,
        workspace_id: UUID,
        service_id: UUID,
    ) -> OperationsRouteContext | None:
        """读取当前 Route 与最近前序版本，比较身份始终来自同一查询快照。"""

        previous_routes = service_routes.alias("previous_routes")
        row = (
            self._session.execute(
                select(
                    services.c.service_id,
                    services.c.name.label("service_name"),
                    services.c.status.label("service_status"),
                    service_routes.c.route_id,
                    service_routes.c.route_version,
                    service_routes.c.route_mode,
                    service_routes.c.created_at.label("route_created_at"),
                    service_routes.c.primary_release_id,
                    service_routes.c.canary_release_id,
                    previous_routes.c.primary_release_id.label("previous_release_id"),
                    service_routes.c.canary_percent,
                )
                .select_from(service_route_publications)
                .join(
                    services,
                    (services.c.workspace_id == service_route_publications.c.workspace_id)
                    & (services.c.service_id == service_route_publications.c.service_id),
                )
                .join(
                    service_routes,
                    (service_routes.c.workspace_id == service_route_publications.c.workspace_id)
                    & (service_routes.c.service_id == service_route_publications.c.service_id)
                    & (service_routes.c.route_id == service_route_publications.c.route_id),
                )
                .outerjoin(
                    previous_routes,
                    (previous_routes.c.workspace_id == service_routes.c.workspace_id)
                    & (previous_routes.c.service_id == service_routes.c.service_id)
                    & (previous_routes.c.route_id == service_routes.c.previous_route_id),
                )
                .where(
                    service_route_publications.c.workspace_id == workspace_id,
                    service_route_publications.c.service_id == service_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        return _route_context(row) if row is not None else None

    def aggregate_releases(
        self,
        workspace_id: UUID,
        service_id: UUID,
        release_ids: tuple[UUID, ...],
        *,
        started_at: datetime,
        ended_at: datetime,
    ) -> tuple[ReleaseOperationsFacts, ...]:
        """用独立子聚合避免 Run、调用和反馈多对多连接造成重复计数。"""

        if not release_ids:
            return ()
        releases = self._release_metadata(workspace_id, release_ids)
        runs = self._run_statistics(
            workspace_id,
            service_id,
            release_ids,
            started_at=started_at,
            ended_at=ended_at,
        )
        invocations = self._invocation_statistics(
            workspace_id,
            service_id,
            release_ids,
            started_at=started_at,
            ended_at=ended_at,
        )
        feedback = self._feedback_statistics(
            workspace_id,
            service_id,
            release_ids,
            started_at=started_at,
            ended_at=ended_at,
        )
        return tuple(
            _release_facts(
                row,
                runs.get(release_id),
                invocations.get(release_id),
                feedback.get(release_id),
            )
            for release_id in release_ids
            if (row := releases.get(release_id)) is not None
        )

    def _release_metadata(
        self,
        workspace_id: UUID,
        release_ids: tuple[UUID, ...],
    ) -> dict[UUID, RowMapping]:
        rows = self._session.execute(
            select(
                agent_releases.c.release_id,
                agent_releases.c.version.label("release_version"),
                agent_releases.c.snapshot,
                ai_runtime_config_versions.c.max_estimated_cost_microunits,
                agent_evaluation_runs.c.status.label("evaluation_status"),
                agent_evaluation_runs.c.total_cases,
                agent_evaluation_runs.c.passed_cases,
            )
            .join(
                ai_runtime_config_versions,
                ai_runtime_config_versions.c.runtime_config_version_id
                == agent_releases.c.runtime_config_version_id,
            )
            .outerjoin(
                agent_evaluation_runs,
                (agent_evaluation_runs.c.workspace_id == agent_releases.c.workspace_id)
                & (agent_evaluation_runs.c.evaluation_run_id == agent_releases.c.evaluation_run_id),
            )
            .where(
                agent_releases.c.workspace_id == workspace_id,
                agent_releases.c.release_id.in_(release_ids),
            )
        ).mappings()
        return {cast(UUID, row["release_id"]): row for row in rows}

    def _run_statistics(
        self,
        workspace_id: UUID,
        service_id: UUID,
        release_ids: tuple[UUID, ...],
        *,
        started_at: datetime,
        ended_at: datetime,
    ) -> dict[UUID, RowMapping]:
        """终态延迟以完成时间减创建时间计算，未完成 Run 不进入 P95。"""

        latency_ms = (
            func.extract(
                "epoch",
                assistant_runs.c.completed_at - assistant_runs.c.created_at,
            )
            * 1_000
        )
        rows = self._session.execute(
            select(
                assistant_runs.c.agent_release_id.label("release_id"),
                func.count().label("run_count"),
                func.count()
                .filter(assistant_runs.c.status.in_(TERMINAL_RUN_STATUSES))
                .label("terminal_count"),
                func.count()
                .filter(assistant_runs.c.status == "completed")
                .label("completed_count"),
                func.count().filter(assistant_runs.c.status == "failed").label("failed_count"),
                func.percentile_cont(0.95)
                .within_group(latency_ms.asc())
                .filter(assistant_runs.c.status.in_(TERMINAL_RUN_STATUSES))
                .label("latency_p95_ms"),
            )
            .where(*_run_scope(workspace_id, service_id, release_ids, started_at, ended_at))
            .group_by(assistant_runs.c.agent_release_id)
        ).mappings()
        return {cast(UUID, row["release_id"]): row for row in rows}

    def _invocation_statistics(
        self,
        workspace_id: UUID,
        service_id: UUID,
        release_ids: tuple[UUID, ...],
        *,
        started_at: datetime,
        ended_at: datetime,
    ) -> dict[UUID, RowMapping]:
        """先按 Run 汇总成本和降级，再按 Release 聚合，避免多模型工作流放大样本数。"""

        scoped_runs = (
            select(
                assistant_runs.c.run_id,
                assistant_runs.c.agent_release_id.label("release_id"),
                assistant_runs.c.trace_id,
            )
            .where(*_run_scope(workspace_id, service_id, release_ids, started_at, ended_at))
            .cte("agent_operations_runs")
        )
        per_run = (
            select(
                scoped_runs.c.run_id,
                scoped_runs.c.release_id,
                func.coalesce(func.sum(model_invocations.c.estimated_cost_microunits), 0).label(
                    "run_cost"
                ),
                func.bool_or(
                    or_(
                        model_invocations.c.status == "degraded",
                        model_invocations.c.degradation_reason.is_not(None),
                    )
                ).label("degraded"),
            )
            .select_from(scoped_runs)
            .join(
                model_invocations,
                (model_invocations.c.workspace_id == workspace_id)
                & (model_invocations.c.trace_id == scoped_runs.c.trace_id),
            )
            .group_by(scoped_runs.c.run_id, scoped_runs.c.release_id)
            .cte("agent_operations_invocations_per_run")
        )
        rows = self._session.execute(
            select(
                per_run.c.release_id,
                func.count().filter(per_run.c.degraded.is_(True)).label("degraded_count"),
                func.coalesce(func.sum(per_run.c.run_cost), 0).label("total_cost_microunits"),
                func.coalesce(func.max(per_run.c.run_cost), 0).label("max_run_cost_microunits"),
            ).group_by(per_run.c.release_id)
        ).mappings()
        return {cast(UUID, row["release_id"]): row for row in rows}

    def _feedback_statistics(
        self,
        workspace_id: UUID,
        service_id: UUID,
        release_ids: tuple[UUID, ...],
        *,
        started_at: datetime,
        ended_at: datetime,
    ) -> dict[UUID, RowMapping]:
        rows = self._session.execute(
            select(
                assistant_runs.c.agent_release_id.label("release_id"),
                func.count(message_feedbacks.c.feedback_id).label("feedback_count"),
                func.sum(case((message_feedbacks.c.rating == "helpful", 1), else_=0)).label(
                    "helpful_feedback_count"
                ),
            )
            .select_from(assistant_runs)
            .join(
                message_feedbacks,
                (message_feedbacks.c.workspace_id == assistant_runs.c.workspace_id)
                & (message_feedbacks.c.run_id == assistant_runs.c.run_id),
            )
            .where(*_run_scope(workspace_id, service_id, release_ids, started_at, ended_at))
            .group_by(assistant_runs.c.agent_release_id)
        ).mappings()
        return {cast(UUID, row["release_id"]): row for row in rows}


class SqlAlchemyAgentOperationsUnitOfWork:
    """为一次运营报告提供短生命周期的一致只读 Session。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[tuple[Session, SqlAlchemyAgentOperationsRepository] | None] = (
            ContextVar("agent_operations_unit_of_work", default=None)
        )

    def __enter__(self) -> SqlAlchemyAgentOperationsUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Agent Operations Unit of Work 不允许重复进入")
        session = self._session_factory()
        self._state.set((session, SqlAlchemyAgentOperationsRepository(session)))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        state = self._state.get()
        if state is not None:
            state[0].rollback()
            state[0].close()
            self._state.set(None)

    @property
    def operations(self) -> SqlAlchemyAgentOperationsRepository:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Agent Operations Unit of Work 尚未进入读取范围")
        return state[1]


def _run_scope(
    workspace_id: UUID,
    service_id: UUID,
    release_ids: tuple[UUID, ...],
    started_at: datetime,
    ended_at: datetime,
) -> tuple[Any, ...]:
    """集中固定服务调用统计边界，私有助手 Run 因 service_id 为空自动排除。"""

    return (
        assistant_runs.c.workspace_id == workspace_id,
        assistant_runs.c.service_id == service_id,
        assistant_runs.c.agent_release_id.in_(release_ids),
        assistant_runs.c.created_at >= started_at,
        assistant_runs.c.created_at < ended_at,
    )


def _route_context(row: RowMapping) -> OperationsRouteContext:
    return OperationsRouteContext(
        service_id=cast(UUID, row["service_id"]),
        service_name=cast(str, row["service_name"]),
        service_status=cast(str, row["service_status"]),
        route_id=cast(UUID, row["route_id"]),
        route_version=cast(int, row["route_version"]),
        route_mode=cast(str, row["route_mode"]),
        route_created_at=cast(datetime, row["route_created_at"]),
        primary_release_id=cast(UUID, row["primary_release_id"]),
        canary_release_id=cast(UUID | None, row["canary_release_id"]),
        previous_release_id=cast(UUID | None, row["previous_release_id"]),
        canary_percent=cast(int, row["canary_percent"]),
    )


def _release_facts(
    metadata: RowMapping,
    runs: RowMapping | None,
    invocations: RowMapping | None,
    feedback: RowMapping | None,
) -> ReleaseOperationsFacts:
    """缺少在线样本时保留零计数，不能让无数据版本从结果中消失。"""

    total_cases = int(metadata["total_cases"] or 0)
    passed_cases = int(metadata["passed_cases"] or 0)
    latency = runs["latency_p95_ms"] if runs is not None else None
    return ReleaseOperationsFacts(
        release_id=cast(UUID, metadata["release_id"]),
        release_version=int(metadata["release_version"]),
        max_cost_microunits=_release_cost_budget(metadata),
        offline_evaluation_status=str(metadata["evaluation_status"] or "not_run"),
        offline_evaluation_score_bps=(
            round(passed_cases * 10_000 / total_cases) if total_cases else 0
        ),
        run_count=int(runs["run_count"] or 0) if runs is not None else 0,
        terminal_count=int(runs["terminal_count"] or 0) if runs is not None else 0,
        completed_count=int(runs["completed_count"] or 0) if runs is not None else 0,
        failed_count=int(runs["failed_count"] or 0) if runs is not None else 0,
        degraded_count=(int(invocations["degraded_count"] or 0) if invocations is not None else 0),
        latency_p95_ms=round(float(latency)) if latency is not None else None,
        total_cost_microunits=(
            int(invocations["total_cost_microunits"] or 0) if invocations is not None else 0
        ),
        max_run_cost_microunits=(
            int(invocations["max_run_cost_microunits"] or 0) if invocations is not None else 0
        ),
        feedback_count=int(feedback["feedback_count"] or 0) if feedback is not None else 0,
        helpful_feedback_count=(
            int(feedback["helpful_feedback_count"] or 0) if feedback is not None else 0
        ),
    )


def _release_cost_budget(metadata: RowMapping) -> int:
    """优先读取 Release 冻结预算；旧系统发布缺少配置时使用运行配置上限。"""

    snapshot = metadata["snapshot"]
    if isinstance(snapshot, dict):
        configuration = snapshot.get("configuration")
        if isinstance(configuration, dict):
            limits = configuration.get("limits")
            if isinstance(limits, dict):
                budget = limits.get("max_cost_microunits")
                if isinstance(budget, int) and not isinstance(budget, bool) and budget >= 0:
                    return budget
    return int(metadata["max_estimated_cost_microunits"])
