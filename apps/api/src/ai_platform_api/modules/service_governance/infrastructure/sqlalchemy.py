"""实现服务、访问策略、历史路由和当前指针的 PostgreSQL Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from types import TracebackType
from typing import Any, Literal, cast
from uuid import UUID

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import CursorResult, func, insert, select, update
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.service_governance.domain.models import (
    RoutableAgentRelease,
    Service,
    ServiceAccessPolicyVersion,
    ServiceAccessVisibility,
    ServiceControlOperation,
    ServiceControlRequest,
    ServiceGovernanceUnitOfWork,
    ServiceRepository,
    ServiceRoute,
    ServiceRouteMode,
    ServiceRoutePublication,
    ServiceStatus,
    ServiceType,
    ServiceWriteConflictError,
)
from ai_platform_api.persistence.tables import (
    agent_releases,
    agents,
    departments,
    service_access_policy_versions,
    service_control_requests,
    service_route_publications,
    service_routes,
    services,
    workspace_memberships,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyServiceRepository(ServiceRepository):
    """维护服务当前态，并让访问策略和 Route 历史只执行插入。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_routable_release(
        self,
        workspace_id: UUID,
        release_id: UUID,
        *,
        for_share: bool = False,
    ) -> RoutableAgentRelease | None:
        # 1. 单次查询同时读取 Release 和 Agent 状态，避免两个时点组合出伪有效路由。
        statement = (
            select(
                agent_releases.c.release_id,
                agent_releases.c.agent_id,
                agent_releases.c.workspace_id,
                agent_releases.c.release_kind,
                agent_releases.c.version.label("release_version"),
                agent_releases.c.status.label("release_status"),
                agents.c.status.label("agent_status"),
                agent_releases.c.snapshot_hash,
            )
            .join(
                agents,
                (agents.c.agent_id == agent_releases.c.agent_id)
                & (agents.c.workspace_id == agent_releases.c.workspace_id),
            )
            .where(
                agent_releases.c.workspace_id == workspace_id,
                agent_releases.c.release_id == release_id,
            )
        )
        if for_share:
            statement = statement.with_for_update(read=True, of=agent_releases)
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        # 2. 只映射路由门禁所需字段，Prompt 和发布快照正文不进入服务治理层。
        return RoutableAgentRelease(
            release_id=row.release_id,
            agent_id=row.agent_id,
            workspace_id=row.workspace_id,
            release_kind=cast("Literal['system', 'custom']", row.release_kind),
            release_version=row.release_version,
            release_status=row.release_status,
            agent_status=row.agent_status,
            snapshot_hash=row.snapshot_hash,
        )

    def subjects_exist(
        self,
        workspace_id: UUID,
        department_ids: tuple[UUID, ...],
        account_ids: tuple[UUID, ...],
    ) -> bool:
        """确认限制策略中的全部部门和账号均为当前空间活动主体。"""

        if department_ids:
            department_count = self._session.scalar(
                select(func.count())
                .select_from(departments)
                .where(
                    departments.c.workspace_id == workspace_id,
                    departments.c.department_id.in_(department_ids),
                    departments.c.status == "active",
                )
            )
            if int(department_count or 0) != len(department_ids):
                return False
        if account_ids:
            account_count = self._session.scalar(
                select(func.count())
                .select_from(workspace_memberships)
                .where(
                    workspace_memberships.c.workspace_id == workspace_id,
                    workspace_memberships.c.account_id.in_(account_ids),
                    workspace_memberships.c.status == "active",
                )
            )
            if int(account_count or 0) != len(account_ids):
                return False
        return True

    def get_service(
        self,
        workspace_id: UUID,
        service_id: UUID,
        *,
        for_update: bool = False,
    ) -> Service | None:
        statement = select(services).where(
            services.c.workspace_id == workspace_id,
            services.c.service_id == service_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _service(row) if row is not None else None

    def get_service_by_key(
        self,
        workspace_id: UUID,
        service_key: str,
        *,
        for_update: bool = False,
    ) -> Service | None:
        statement = select(services).where(
            services.c.workspace_id == workspace_id,
            services.c.service_key == service_key,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _service(row) if row is not None else None

    def list_services(self, workspace_id: UUID, *, limit: int) -> tuple[Service, ...]:
        rows = self._session.execute(
            select(services)
            .where(services.c.workspace_id == workspace_id)
            .order_by(services.c.updated_at.desc(), services.c.service_id)
            .limit(limit)
        )
        return tuple(_service(row) for row in rows)

    def get_access_policy(
        self,
        workspace_id: UUID,
        access_policy_version_id: UUID,
    ) -> ServiceAccessPolicyVersion | None:
        row = self._session.execute(
            select(service_access_policy_versions).where(
                service_access_policy_versions.c.workspace_id == workspace_id,
                service_access_policy_versions.c.access_policy_version_id
                == access_policy_version_id,
            )
        ).one_or_none()
        return _policy(row) if row is not None else None

    def next_access_policy_version(self, workspace_id: UUID, service_id: UUID) -> int:
        current = self._session.scalar(
            select(func.max(service_access_policy_versions.c.version)).where(
                service_access_policy_versions.c.workspace_id == workspace_id,
                service_access_policy_versions.c.service_id == service_id,
            )
        )
        return int(current or 0) + 1

    def get_route(self, workspace_id: UUID, route_id: UUID) -> ServiceRoute | None:
        row = self._session.execute(
            select(service_routes).where(
                service_routes.c.workspace_id == workspace_id,
                service_routes.c.route_id == route_id,
            )
        ).one_or_none()
        return _route(row) if row is not None else None

    def get_current_route(
        self,
        workspace_id: UUID,
        service_id: UUID,
        *,
        for_update: bool = False,
    ) -> tuple[ServiceRoute, ServiceRoutePublication] | None:
        statement = select(service_route_publications).where(
            service_route_publications.c.workspace_id == workspace_id,
            service_route_publications.c.service_id == service_id,
        )
        if for_update:
            statement = statement.with_for_update()
        publication_row = self._session.execute(statement).one_or_none()
        if publication_row is None:
            return None
        route = self.get_route(workspace_id, publication_row.route_id)
        if route is None:
            return None
        return route, _publication(publication_row)

    def add_deployment(
        self,
        draft: Service,
        active: Service,
        policy: ServiceAccessPolicyVersion,
        route: ServiceRoute,
        publication: ServiceRoutePublication,
    ) -> None:
        """按 draft、策略、Route、指针、active 顺序建立完整初始服务。"""

        try:
            self._session.execute(insert(services).values(**_service_values(draft)))
            self._session.execute(
                insert(service_access_policy_versions).values(**_policy_values(policy))
            )
            self._session.execute(insert(service_routes).values(**_route_values(route)))
            self._session.execute(
                insert(service_route_publications).values(**_publication_values(publication))
            )
            result = cast(
                CursorResult[Any],
                self._session.execute(
                    update(services)
                    .where(
                        services.c.service_id == draft.service_id,
                        services.c.workspace_id == draft.workspace_id,
                        services.c.version == draft.version,
                        services.c.status == "draft",
                    )
                    .values(**_service_update_values(active))
                ),
            )
            if result.rowcount != 1:
                raise ServiceWriteConflictError("version")
        except IntegrityError as error:
            raise _write_conflict(error) from error

    def save_service(self, service: Service, *, expected_version: int) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(services)
                .where(
                    services.c.service_id == service.service_id,
                    services.c.workspace_id == service.workspace_id,
                    services.c.version == expected_version,
                )
                .values(**_service_update_values(service))
            ),
        )
        return result.rowcount == 1

    def add_access_policy(self, policy: ServiceAccessPolicyVersion) -> None:
        try:
            self._session.execute(
                insert(service_access_policy_versions).values(**_policy_values(policy))
            )
        except IntegrityError as error:
            raise _write_conflict(error) from error

    def append_route(
        self,
        route: ServiceRoute,
        publication: ServiceRoutePublication,
        *,
        expected_generation: int,
    ) -> bool:
        try:
            self._session.execute(insert(service_routes).values(**_route_values(route)))
            result = cast(
                CursorResult[Any],
                self._session.execute(
                    update(service_route_publications)
                    .where(
                        service_route_publications.c.service_id == publication.service_id,
                        service_route_publications.c.workspace_id == publication.workspace_id,
                        service_route_publications.c.generation == expected_generation,
                    )
                    .values(
                        route_id=publication.route_id,
                        generation=publication.generation,
                        published_by_account_id=publication.published_by_account_id,
                        published_at=publication.published_at,
                    )
                ),
            )
            return result.rowcount == 1
        except IntegrityError as error:
            raise _write_conflict(error) from error

    def get_request(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        operation: ServiceControlOperation,
        idempotency_key: str,
    ) -> ServiceControlRequest | None:
        row = self._session.execute(
            select(service_control_requests).where(
                service_control_requests.c.workspace_id == workspace_id,
                service_control_requests.c.actor_id == actor_id,
                service_control_requests.c.operation == operation,
                service_control_requests.c.idempotency_key == idempotency_key,
            )
        ).one_or_none()
        return _request(row) if row is not None else None

    def add_request(self, request: ServiceControlRequest) -> None:
        try:
            self._session.execute(
                insert(service_control_requests).values(
                    request_id=request.request_id,
                    workspace_id=request.workspace_id,
                    actor_id=request.actor_id,
                    operation=request.operation,
                    idempotency_key=request.idempotency_key,
                    request_hash=request.request_hash,
                    service_id=request.service_id,
                    result_snapshot=request.result_snapshot,
                    created_at=request.created_at,
                )
            )
        except IntegrityError as error:
            raise _write_conflict(error) from error


class SqlAlchemyServiceGovernanceUnitOfWork(ServiceGovernanceUnitOfWork):
    """提供服务事实、审计和 Outbox 的不可嵌套事务边界。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyServiceRepository,
                SqlAlchemyAuditWriter,
                SqlAlchemyOutboxWriter,
            ]
            | None
        ] = ContextVar("service_governance_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyServiceGovernanceUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Service Governance Unit of Work 不允许重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyServiceRepository(session),
                SqlAlchemyAuditWriter(session),
                SqlAlchemyOutboxWriter(session),
            )
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """异常时回滚并始终关闭 Session，避免失败事务被后续请求复用。"""

        del exc_value, traceback
        state = self._state.get()
        if state is not None:
            if exc_type is not None:
                state[0].rollback()
            state[0].close()
            self._state.set(None)

    @property
    def services(self) -> SqlAlchemyServiceRepository:
        return self._require_state()[1]

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        return self._require_state()[2]

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        return self._require_state()[3]

    def commit(self) -> None:
        self._require_state()[0].commit()

    def _require_state(
        self,
    ) -> tuple[
        Session,
        SqlAlchemyServiceRepository,
        SqlAlchemyAuditWriter,
        SqlAlchemyOutboxWriter,
    ]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Service Governance Unit of Work 尚未进入事务范围")
        return state


def _service_values(service: Service) -> dict[str, object]:
    return {
        "service_id": service.service_id,
        "workspace_id": service.workspace_id,
        "agent_id": service.agent_id,
        "service_key": service.service_key,
        "name": service.name,
        "service_type": service.service_type,
        "status": service.status,
        "access_policy_version_id": service.access_policy_version_id,
        "created_by_account_id": service.created_by_account_id,
        "created_at": service.created_at,
        "updated_by_account_id": service.updated_by_account_id,
        "updated_at": service.updated_at,
        "version": service.version,
    }


def _service_update_values(service: Service) -> dict[str, object]:
    return {
        "name": service.name,
        "status": service.status,
        "access_policy_version_id": service.access_policy_version_id,
        "updated_by_account_id": service.updated_by_account_id,
        "updated_at": service.updated_at,
        "version": service.version,
    }


def _policy_values(policy: ServiceAccessPolicyVersion) -> dict[str, object]:
    return {
        "access_policy_version_id": policy.access_policy_version_id,
        "service_id": policy.service_id,
        "workspace_id": policy.workspace_id,
        "version": policy.version,
        "visibility": policy.visibility,
        "allowed_department_ids": policy.allowed_department_ids,
        "allowed_account_ids": policy.allowed_account_ids,
        "policy_hash": policy.policy_hash,
        "created_by_account_id": policy.created_by_account_id,
        "created_at": policy.created_at,
    }


def _route_values(route: ServiceRoute) -> dict[str, object]:
    return {
        "route_id": route.route_id,
        "service_id": route.service_id,
        "workspace_id": route.workspace_id,
        "route_version": route.route_version,
        "route_mode": route.route_mode,
        "primary_release_id": route.primary_release_id,
        "canary_release_id": route.canary_release_id,
        "canary_percent": route.canary_percent,
        "previous_route_id": route.previous_route_id,
        "route_hash": route.route_hash,
        "created_by_account_id": route.created_by_account_id,
        "created_at": route.created_at,
    }


def _publication_values(publication: ServiceRoutePublication) -> dict[str, object]:
    return {
        "service_id": publication.service_id,
        "workspace_id": publication.workspace_id,
        "route_id": publication.route_id,
        "generation": publication.generation,
        "published_by_account_id": publication.published_by_account_id,
        "published_at": publication.published_at,
    }


def _service(row: Row[Any]) -> Service:
    return Service(
        service_id=row.service_id,
        workspace_id=row.workspace_id,
        agent_id=row.agent_id,
        service_key=row.service_key,
        name=row.name,
        service_type=cast("ServiceType", row.service_type),
        status=cast("ServiceStatus", row.status),
        access_policy_version_id=row.access_policy_version_id,
        created_by_account_id=row.created_by_account_id,
        created_at=row.created_at,
        updated_by_account_id=row.updated_by_account_id,
        updated_at=row.updated_at,
        version=row.version,
    )


def _policy(row: Row[Any]) -> ServiceAccessPolicyVersion:
    return ServiceAccessPolicyVersion(
        access_policy_version_id=row.access_policy_version_id,
        service_id=row.service_id,
        workspace_id=row.workspace_id,
        version=row.version,
        visibility=cast("ServiceAccessVisibility", row.visibility),
        allowed_department_ids=tuple(row.allowed_department_ids),
        allowed_account_ids=tuple(row.allowed_account_ids),
        policy_hash=row.policy_hash,
        created_by_account_id=row.created_by_account_id,
        created_at=row.created_at,
    )


def _route(row: Row[Any]) -> ServiceRoute:
    return ServiceRoute(
        route_id=row.route_id,
        service_id=row.service_id,
        workspace_id=row.workspace_id,
        route_version=row.route_version,
        route_mode=cast("ServiceRouteMode", row.route_mode),
        primary_release_id=row.primary_release_id,
        canary_release_id=row.canary_release_id,
        canary_percent=row.canary_percent,
        previous_route_id=row.previous_route_id,
        route_hash=row.route_hash,
        created_by_account_id=row.created_by_account_id,
        created_at=row.created_at,
    )


def _publication(row: Row[Any]) -> ServiceRoutePublication:
    return ServiceRoutePublication(
        service_id=row.service_id,
        workspace_id=row.workspace_id,
        route_id=row.route_id,
        generation=row.generation,
        published_by_account_id=row.published_by_account_id,
        published_at=row.published_at,
    )


def _request(row: Row[Any]) -> ServiceControlRequest:
    return ServiceControlRequest(
        request_id=row.request_id,
        workspace_id=row.workspace_id,
        actor_id=row.actor_id,
        operation=cast("ServiceControlOperation", row.operation),
        idempotency_key=row.idempotency_key,
        request_hash=row.request_hash,
        service_id=row.service_id,
        result_snapshot=cast("dict[str, object]", row.result_snapshot),
        created_at=row.created_at,
    )


def _write_conflict(error: IntegrityError) -> ServiceWriteConflictError:
    name = _constraint_name(error)
    reason: Literal["idempotency", "version", "route", "write"] = "write"
    if name == "uq_service_control_requests_idempotency":
        reason = "idempotency"
    elif name in {"uq_services_workspace_key", "uq_service_access_policies_version"}:
        reason = "version"
    elif name in {"uq_service_routes_version", "service_route_publications_pkey"}:
        reason = "route"
    return ServiceWriteConflictError(reason)


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    value = getattr(diagnostic, "constraint_name", None)
    return value if isinstance(value, str) else None
