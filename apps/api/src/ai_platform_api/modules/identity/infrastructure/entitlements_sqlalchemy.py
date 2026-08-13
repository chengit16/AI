from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Any, cast
from uuid import UUID

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.identity.domain.enterprise import (
    MembershipType,
    WorkspaceMembership,
    WorkspaceRecord,
)
from ai_platform_api.modules.identity.domain.entitlements import (
    EntitlementWriteConflictError,
    OpenApiEntitlement,
    UsageCounter,
    UsageMetric,
    UsageRecord,
    WorkspaceEntitlement,
    WorkspaceFeatureSettings,
)
from ai_platform_api.modules.identity.domain.models import (
    MembershipStatus,
    WorkspaceStatus,
    WorkspaceType,
)
from ai_platform_api.persistence.tables import (
    workspace_entitlements,
    workspace_feature_settings,
    workspace_memberships,
    workspace_usage_counters,
    workspace_usage_records,
    workspaces,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyEntitlementAccessReader:
    """认证链使用独立短 Session，使功能开关关闭后现有 API Key 立即失效。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def get_open_api_entitlement(self, workspace_id: UUID) -> OpenApiEntitlement | None:
        with self._session_factory() as session:
            row = session.execute(
                select(
                    workspace_entitlements.c.open_api_allowed,
                    workspace_feature_settings.c.open_api_enabled,
                )
                .join(
                    workspace_feature_settings,
                    workspace_feature_settings.c.workspace_id
                    == workspace_entitlements.c.workspace_id,
                )
                .where(workspace_entitlements.c.workspace_id == workspace_id)
            ).one_or_none()
        if row is None:
            return None
        return OpenApiEntitlement(row.open_api_allowed, row.open_api_enabled)


class SqlAlchemyEntitlementRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_workspace(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> WorkspaceRecord | None:
        statement = select(
            workspaces.c.workspace_id,
            workspaces.c.workspace_type,
            workspaces.c.name,
            workspaces.c.status,
        ).where(workspaces.c.workspace_id == workspace_id)
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return WorkspaceRecord(
            row.workspace_id,
            cast("WorkspaceType", row.workspace_type),
            row.name,
            cast("WorkspaceStatus", row.status),
        )

    def get_membership(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceMembership | None:
        statement = select(workspace_memberships).where(
            workspace_memberships.c.workspace_id == workspace_id,
            workspace_memberships.c.account_id == account_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return WorkspaceMembership(
            row.membership_id,
            row.workspace_id,
            row.account_id,
            cast("MembershipType", row.membership_type),
            cast("MembershipStatus", row.status),
            row.created_at,
            row.updated_at,
            row.version,
        )

    def get_entitlement(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> WorkspaceEntitlement | None:
        statement = select(workspace_entitlements).where(
            workspace_entitlements.c.workspace_id == workspace_id
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return WorkspaceEntitlement(
            row.workspace_id,
            row.plan_code,
            row.max_storage_bytes,
            row.max_members,
            row.max_knowledge_bases,
            row.max_published_agents,
            row.max_monthly_questions,
            row.open_api_allowed,
            row.public_publish_allowed,
            row.created_at,
            row.updated_at,
            row.version,
        )

    def get_feature_settings(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> WorkspaceFeatureSettings | None:
        statement = select(workspace_feature_settings).where(
            workspace_feature_settings.c.workspace_id == workspace_id
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return WorkspaceFeatureSettings(
            row.workspace_id,
            row.open_api_enabled,
            row.updated_at,
            row.version,
        )

    def get_entitlement_version(self, workspace_id: UUID) -> int | None:
        return self._session.scalar(
            select(workspaces.c.entitlement_version).where(
                workspaces.c.workspace_id == workspace_id
            )
        )

    def active_member_count(self, workspace_id: UUID) -> int:
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(workspace_memberships)
                .where(
                    workspace_memberships.c.workspace_id == workspace_id,
                    workspace_memberships.c.status == "active",
                )
            )
            or 0
        )

    def list_usage_counters(self, workspace_id: UUID) -> tuple[UsageCounter, ...]:
        rows = self._session.execute(
            select(workspace_usage_counters)
            .where(workspace_usage_counters.c.workspace_id == workspace_id)
            .order_by(workspace_usage_counters.c.metric, workspace_usage_counters.c.period_key)
        )
        return tuple(self._counter(row) for row in rows)

    def get_usage_counter(
        self,
        workspace_id: UUID,
        metric: UsageMetric,
        period_key: str,
        *,
        for_update: bool = False,
    ) -> UsageCounter | None:
        statement = select(workspace_usage_counters).where(
            workspace_usage_counters.c.workspace_id == workspace_id,
            workspace_usage_counters.c.metric == metric,
            workspace_usage_counters.c.period_key == period_key,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return self._counter(row) if row is not None else None

    def get_usage_record(self, workspace_id: UUID, idempotency_key: str) -> UsageRecord | None:
        row = self._session.execute(
            select(workspace_usage_records).where(
                workspace_usage_records.c.workspace_id == workspace_id,
                workspace_usage_records.c.idempotency_key == idempotency_key,
            )
        ).one_or_none()
        if row is None:
            return None
        return UsageRecord(
            row.usage_record_id,
            row.workspace_id,
            cast("UsageMetric", row.metric),
            row.period_key,
            row.idempotency_key,
            row.delta_value,
            row.resulting_value,
            row.occurred_at,
        )

    def set_open_api_enabled(
        self,
        settings: WorkspaceFeatureSettings,
        *,
        expected_version: int,
    ) -> None:
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(workspace_feature_settings)
                .where(
                    workspace_feature_settings.c.workspace_id == settings.workspace_id,
                    workspace_feature_settings.c.version == expected_version,
                )
                .values(
                    open_api_enabled=settings.open_api_enabled,
                    updated_at=settings.updated_at,
                    version=settings.version,
                )
            ),
        )
        if result.rowcount != 1:
            raise EntitlementWriteConflictError

    def save_usage_counter(self, counter: UsageCounter, *, expected_version: int | None) -> None:
        try:
            if expected_version is None:
                self._session.execute(
                    insert(workspace_usage_counters).values(
                        workspace_id=counter.workspace_id,
                        metric=counter.metric,
                        period_key=counter.period_key,
                        used_value=counter.used_value,
                        updated_at=counter.updated_at,
                        version=counter.version,
                    )
                )
                return
            result = cast(
                "CursorResult[Any]",
                self._session.execute(
                    update(workspace_usage_counters)
                    .where(
                        workspace_usage_counters.c.workspace_id == counter.workspace_id,
                        workspace_usage_counters.c.metric == counter.metric,
                        workspace_usage_counters.c.period_key == counter.period_key,
                        workspace_usage_counters.c.version == expected_version,
                    )
                    .values(
                        used_value=counter.used_value,
                        updated_at=counter.updated_at,
                        version=counter.version,
                    )
                ),
            )
            if result.rowcount != 1:
                raise EntitlementWriteConflictError
        except IntegrityError as error:
            raise EntitlementWriteConflictError from error

    def add_usage_record(self, record: UsageRecord) -> None:
        try:
            self._session.execute(
                insert(workspace_usage_records).values(
                    usage_record_id=record.usage_record_id,
                    workspace_id=record.workspace_id,
                    metric=record.metric,
                    period_key=record.period_key,
                    idempotency_key=record.idempotency_key,
                    delta_value=record.delta_value,
                    resulting_value=record.resulting_value,
                    occurred_at=record.occurred_at,
                )
            )
        except IntegrityError as error:
            raise EntitlementWriteConflictError from error

    def bump_entitlement_version(self, workspace_id: UUID) -> int:
        version = self._session.scalar(
            update(workspaces)
            .where(workspaces.c.workspace_id == workspace_id)
            .values(entitlement_version=workspaces.c.entitlement_version + 1)
            .returning(workspaces.c.entitlement_version)
        )
        if version is None:
            raise EntitlementWriteConflictError
        return int(version)

    @staticmethod
    def _counter(row: Any) -> UsageCounter:
        return UsageCounter(
            row.workspace_id,
            cast("UsageMetric", row.metric),
            row.period_key,
            row.used_value,
            row.updated_at,
            row.version,
        )


class SqlAlchemyEntitlementUnitOfWork:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._entitlements: SqlAlchemyEntitlementRepository | None = None
        self._audit: SqlAlchemyAuditWriter | None = None
        self._outbox: SqlAlchemyOutboxWriter | None = None

    def __enter__(self) -> SqlAlchemyEntitlementUnitOfWork:
        self._session = self._session_factory()
        self._entitlements = SqlAlchemyEntitlementRepository(self._session)
        self._audit = SqlAlchemyAuditWriter(self._session)
        self._outbox = SqlAlchemyOutboxWriter(self._session)
        return self

    @property
    def entitlements(self) -> SqlAlchemyEntitlementRepository:
        if self._entitlements is None:
            raise RuntimeError("Entitlement Unit of Work 尚未进入事务范围")
        return self._entitlements

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        if self._audit is None:
            raise RuntimeError("Entitlement Unit of Work 尚未进入事务范围")
        return self._audit

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        if self._outbox is None:
            raise RuntimeError("Entitlement Unit of Work 尚未进入事务范围")
        return self._outbox

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
            self._entitlements = None
            self._audit = None
            self._outbox = None

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Entitlement Unit of Work 尚未进入事务范围")
        try:
            self._session.commit()
        except IntegrityError as error:
            self._session.rollback()
            raise EntitlementWriteConflictError from error
