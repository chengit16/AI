"""使用 PostgreSQL 持久化隔离策略、迁移计划和已完成路由。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import Any, cast
from uuid import UUID

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import CursorResult, func, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.isolation.domain.models import (
    ACTIVE_MIGRATION_STATUSES,
    ComplianceStatus,
    IsolationDecision,
    IsolationLevel,
    IsolationWriteConflictError,
    MigrationStatus,
    WorkspaceIsolationEntitlement,
    WorkspaceIsolationMigrationPlan,
    WorkspaceIsolationPolicyVersion,
    WorkspaceIsolationRoute,
    WorkspaceStatus,
    WorkspaceType,
)
from ai_platform_api.persistence.tables import (
    workspace_entitlements,
    workspace_isolation_migration_plans,
    workspace_isolation_policy_versions,
    workspace_isolation_route_versions,
    workspaces,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyIsolationRepository:
    """读取 Identity 最小套餐事实，并维护 Isolation 模块私有表。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_workspace(self, workspace_id: UUID) -> None:
        """串行化同一工作空间的策略版本、计划和路由切换。"""

        self._session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(str(workspace_id), 0)))
        )

    def get_entitlement(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceIsolationEntitlement | None:
        statement = (
            select(
                workspaces.c.workspace_id,
                workspaces.c.workspace_type,
                workspaces.c.status.label("workspace_status"),
                workspaces.c.entitlement_version,
                workspace_entitlements.c.plan_code,
            )
            .join(
                workspace_entitlements,
                workspace_entitlements.c.workspace_id == workspaces.c.workspace_id,
            )
            .where(workspaces.c.workspace_id == workspace_id)
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).mappings().one_or_none()
        if row is None:
            return None
        return WorkspaceIsolationEntitlement(
            workspace_id=cast(UUID, row["workspace_id"]),
            workspace_type=cast(WorkspaceType, row["workspace_type"]),
            workspace_status=cast(WorkspaceStatus, row["workspace_status"]),
            plan_code=cast(str, row["plan_code"]),
            entitlement_version=cast(int, row["entitlement_version"]),
        )

    def next_policy_version(self, workspace_id: UUID) -> int:
        current = self._session.scalar(
            select(func.max(workspace_isolation_policy_versions.c.policy_version)).where(
                workspace_isolation_policy_versions.c.workspace_id == workspace_id
            )
        )
        return int(current or 0) + 1

    def add_policy(self, policy: WorkspaceIsolationPolicyVersion) -> None:
        try:
            self._session.execute(
                insert(workspace_isolation_policy_versions).values(
                    isolation_policy_id=policy.isolation_policy_id,
                    workspace_id=policy.workspace_id,
                    policy_version=policy.policy_version,
                    requested_level=policy.requested_level,
                    current_level=policy.current_level,
                    maximum_eligible_level=policy.maximum_eligible_level,
                    plan_code=policy.plan_code,
                    entitlement_version=policy.entitlement_version,
                    compliance_status=policy.compliance_status,
                    compliance_policy_digest=policy.compliance_policy_digest,
                    decision=policy.decision,
                    reason_codes=list(policy.reason_codes),
                    decision_digest=policy.decision_digest,
                    created_by_actor_id=policy.created_by_actor_id,
                    created_at=policy.created_at,
                )
            )
        except IntegrityError as error:
            raise IsolationWriteConflictError from error

    def get_policy(
        self,
        workspace_id: UUID,
        isolation_policy_id: UUID,
    ) -> WorkspaceIsolationPolicyVersion | None:
        row = (
            self._session.execute(
                select(workspace_isolation_policy_versions).where(
                    workspace_isolation_policy_versions.c.workspace_id == workspace_id,
                    workspace_isolation_policy_versions.c.isolation_policy_id
                    == isolation_policy_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        return _policy(row) if row is not None else None

    def add_migration_plan(self, plan: WorkspaceIsolationMigrationPlan) -> None:
        try:
            self._session.execute(
                insert(workspace_isolation_migration_plans).values(
                    migration_plan_id=plan.migration_plan_id,
                    workspace_id=plan.workspace_id,
                    isolation_policy_id=plan.isolation_policy_id,
                    from_level=plan.from_level,
                    target_level=plan.target_level,
                    status=plan.status,
                    route_requirement_digest=plan.route_requirement_digest,
                    created_by_actor_id=plan.created_by_actor_id,
                    created_at=plan.created_at,
                    updated_by_actor_id=plan.updated_by_actor_id,
                    updated_at=plan.updated_at,
                    version=plan.version,
                )
            )
        except IntegrityError as error:
            raise IsolationWriteConflictError from error

    def get_active_migration_plan(
        self,
        workspace_id: UUID,
    ) -> WorkspaceIsolationMigrationPlan | None:
        row = (
            self._session.execute(
                select(workspace_isolation_migration_plans).where(
                    workspace_isolation_migration_plans.c.workspace_id == workspace_id,
                    workspace_isolation_migration_plans.c.status.in_(ACTIVE_MIGRATION_STATUSES),
                )
            )
            .mappings()
            .one_or_none()
        )
        return _plan(row) if row is not None else None

    def get_migration_plan(
        self,
        workspace_id: UUID,
        migration_plan_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceIsolationMigrationPlan | None:
        statement = select(workspace_isolation_migration_plans).where(
            workspace_isolation_migration_plans.c.workspace_id == workspace_id,
            workspace_isolation_migration_plans.c.migration_plan_id == migration_plan_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).mappings().one_or_none()
        return _plan(row) if row is not None else None

    def save_migration_plan(
        self,
        plan: WorkspaceIsolationMigrationPlan,
        *,
        expected_version: int,
    ) -> bool:
        try:
            result = cast(
                CursorResult[Any],
                self._session.execute(
                    update(workspace_isolation_migration_plans)
                    .where(
                        workspace_isolation_migration_plans.c.workspace_id == plan.workspace_id,
                        workspace_isolation_migration_plans.c.migration_plan_id
                        == plan.migration_plan_id,
                        workspace_isolation_migration_plans.c.version == expected_version,
                    )
                    .values(
                        status=plan.status,
                        updated_by_actor_id=plan.updated_by_actor_id,
                        updated_at=plan.updated_at,
                        version=plan.version,
                    )
                ),
            )
        except IntegrityError as error:
            raise IsolationWriteConflictError from error
        return result.rowcount == 1

    def get_current_route(self, workspace_id: UUID) -> WorkspaceIsolationRoute | None:
        row = (
            self._session.execute(
                select(workspace_isolation_route_versions)
                .join(
                    workspace_isolation_migration_plans,
                    (
                        workspace_isolation_migration_plans.c.migration_plan_id
                        == workspace_isolation_route_versions.c.migration_plan_id
                    )
                    & (
                        workspace_isolation_migration_plans.c.workspace_id
                        == workspace_isolation_route_versions.c.workspace_id
                    ),
                )
                .where(
                    workspace_isolation_route_versions.c.workspace_id == workspace_id,
                    workspace_isolation_migration_plans.c.status == "completed",
                )
                .order_by(workspace_isolation_route_versions.c.route_version.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        return _route(row) if row is not None else None

    def next_route_version(self, workspace_id: UUID) -> int:
        current = self._session.scalar(
            select(func.max(workspace_isolation_route_versions.c.route_version)).where(
                workspace_isolation_route_versions.c.workspace_id == workspace_id
            )
        )
        return int(current or 0) + 1

    def add_route(self, route: WorkspaceIsolationRoute) -> None:
        try:
            self._session.execute(
                insert(workspace_isolation_route_versions).values(
                    route_id=route.route_id,
                    workspace_id=route.workspace_id,
                    route_version=route.route_version,
                    isolation_level=route.isolation_level,
                    database_route_key=route.database_route_key,
                    object_storage_route_key=route.object_storage_route_key,
                    encryption_key_route_key=route.encryption_key_route_key,
                    search_namespace=route.search_namespace,
                    deployment_route_key=route.deployment_route_key,
                    migration_plan_id=route.migration_plan_id,
                    route_digest=route.route_digest,
                    activated_by_actor_id=route.activated_by_actor_id,
                    activated_at=route.activated_at,
                )
            )
        except IntegrityError as error:
            raise IsolationWriteConflictError from error


class SqlAlchemyIsolationUnitOfWork:
    """为隔离控制面提供短事务及审计、Outbox 写入器。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._isolation: SqlAlchemyIsolationRepository | None = None
        self._audit: SqlAlchemyAuditWriter | None = None
        self._outbox: SqlAlchemyOutboxWriter | None = None

    @property
    def isolation(self) -> SqlAlchemyIsolationRepository:
        if self._isolation is None:
            raise RuntimeError("Isolation Unit of Work 尚未进入事务范围")
        return self._isolation

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        if self._audit is None:
            raise RuntimeError("Isolation Unit of Work 尚未进入事务范围")
        return self._audit

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        if self._outbox is None:
            raise RuntimeError("Isolation Unit of Work 尚未进入事务范围")
        return self._outbox

    def __enter__(self) -> SqlAlchemyIsolationUnitOfWork:
        if self._session is not None:
            raise RuntimeError("Isolation Unit of Work 不允许嵌套事务")
        self._session = self._session_factory()
        self._isolation = SqlAlchemyIsolationRepository(self._session)
        self._audit = SqlAlchemyAuditWriter(self._session)
        self._outbox = SqlAlchemyOutboxWriter(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is not None:
            if exc_type is not None:
                self._session.rollback()
            self._session.close()
        self._session = None
        self._isolation = None
        self._audit = None
        self._outbox = None

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Isolation Unit of Work 尚未进入事务范围")
        self._session.commit()


def _policy(row: RowMapping) -> WorkspaceIsolationPolicyVersion:
    return WorkspaceIsolationPolicyVersion(
        isolation_policy_id=cast(UUID, row["isolation_policy_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        policy_version=cast(int, row["policy_version"]),
        requested_level=cast(IsolationLevel, row["requested_level"]),
        current_level=cast(IsolationLevel, row["current_level"]),
        maximum_eligible_level=cast(IsolationLevel, row["maximum_eligible_level"]),
        plan_code=cast(str, row["plan_code"]),
        entitlement_version=cast(int, row["entitlement_version"]),
        compliance_status=cast(ComplianceStatus, row["compliance_status"]),
        compliance_policy_digest=cast(str | None, row["compliance_policy_digest"]),
        decision=cast(IsolationDecision, row["decision"]),
        reason_codes=tuple(cast(list[str], row["reason_codes"])),
        decision_digest=cast(str, row["decision_digest"]),
        created_by_actor_id=cast(UUID, row["created_by_actor_id"]),
        created_at=cast(datetime, row["created_at"]),
    )


def _plan(row: RowMapping) -> WorkspaceIsolationMigrationPlan:
    return WorkspaceIsolationMigrationPlan(
        migration_plan_id=cast(UUID, row["migration_plan_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        isolation_policy_id=cast(UUID, row["isolation_policy_id"]),
        from_level=cast(IsolationLevel, row["from_level"]),
        target_level=cast(IsolationLevel, row["target_level"]),
        status=cast(MigrationStatus, row["status"]),
        route_requirement_digest=cast(str, row["route_requirement_digest"]),
        created_by_actor_id=cast(UUID, row["created_by_actor_id"]),
        created_at=cast(datetime, row["created_at"]),
        updated_by_actor_id=cast(UUID, row["updated_by_actor_id"]),
        updated_at=cast(datetime, row["updated_at"]),
        version=cast(int, row["version"]),
    )


def _route(row: RowMapping) -> WorkspaceIsolationRoute:
    return WorkspaceIsolationRoute(
        route_id=cast(UUID, row["route_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        route_version=cast(int, row["route_version"]),
        isolation_level=cast(IsolationLevel, row["isolation_level"]),
        database_route_key=cast(str, row["database_route_key"]),
        object_storage_route_key=cast(str, row["object_storage_route_key"]),
        encryption_key_route_key=cast(str, row["encryption_key_route_key"]),
        search_namespace=cast(str, row["search_namespace"]),
        deployment_route_key=cast(str, row["deployment_route_key"]),
        migration_plan_id=cast(UUID, row["migration_plan_id"]),
        route_digest=cast(str, row["route_digest"]),
        activated_by_actor_id=cast(UUID, row["activated_by_actor_id"]),
        activated_at=cast(datetime, row["activated_at"]),
    )
