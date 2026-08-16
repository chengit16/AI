"""实现权益、功能开关和用量账本的 PostgreSQL Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime
from types import TracebackType
from typing import Any, cast
from uuid import UUID

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import func, insert, select, tuple_, update
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
    UsageCursorError,
    UsageMetric,
    UsageReconciliation,
    UsageRecord,
    UsageRecordPage,
    WorkspaceEntitlement,
    WorkspaceFeatureSettings,
    WorkspacePlanEntitlement,
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

    def get_workspace_plan_entitlement(
        self,
        workspace_id: UUID,
    ) -> WorkspacePlanEntitlement | None:
        """读取套餐与空间状态，避免工具模块直接依赖身份模块私有 Repository。"""

        with self._session_factory() as session:
            row = session.execute(
                select(
                    workspace_entitlements.c.workspace_id,
                    workspace_entitlements.c.plan_code,
                    workspaces.c.status,
                )
                .join(
                    workspaces,
                    workspaces.c.workspace_id == workspace_entitlements.c.workspace_id,
                )
                .where(workspace_entitlements.c.workspace_id == workspace_id)
            ).one_or_none()
        if row is None:
            return None
        return WorkspacePlanEntitlement(
            row.workspace_id,
            row.plan_code,
            cast("WorkspaceStatus", row.status),
        )


class SqlAlchemyEntitlementRepository:
    """在工作空间隔离下维护套餐、功能设置、用量和幂等用量记录。"""

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

    def list_usage_records(
        self,
        workspace_id: UUID,
        *,
        limit: int,
        cursor: UUID | None,
        metric: UsageMetric | None,
        period_key: str | None,
    ) -> UsageRecordPage:
        """使用稳定复合游标返回工作空间内的用量明细。"""

        statement = select(workspace_usage_records).where(
            workspace_usage_records.c.workspace_id == workspace_id
        )
        if cursor is not None:
            cursor_position = self._usage_cursor(workspace_id, cursor)
            statement = statement.where(
                tuple_(
                    workspace_usage_records.c.occurred_at,
                    workspace_usage_records.c.usage_record_id,
                )
                < cursor_position
            )
        if metric is not None:
            statement = statement.where(workspace_usage_records.c.metric == metric)
        if period_key is not None:
            statement = statement.where(workspace_usage_records.c.period_key == period_key)
        rows = self._session.execute(
            statement.order_by(
                workspace_usage_records.c.occurred_at.desc(),
                workspace_usage_records.c.usage_record_id.desc(),
            ).limit(limit + 1)
        )
        items = tuple(self._usage_record(row) for row in rows)
        return UsageRecordPage(
            items=items[:limit],
            next_cursor=(items[limit - 1].usage_record_id if len(items) > limit else None),
        )

    def list_usage_reconciliation(
        self,
        workspace_id: UUID,
    ) -> tuple[UsageReconciliation, ...]:
        """在数据库内聚合明细，并与当前计数器和最后结果逐项核对。"""

        # 1. 聚合只返回每个计量项和周期的摘要，避免运营查询把整本用量明细载入内存。
        summaries = self._session.execute(
            select(
                workspace_usage_records.c.metric,
                workspace_usage_records.c.period_key,
                func.count().label("record_count"),
                func.sum(workspace_usage_records.c.delta_value).label("record_delta_total"),
            )
            .where(workspace_usage_records.c.workspace_id == workspace_id)
            .group_by(
                workspace_usage_records.c.metric,
                workspace_usage_records.c.period_key,
            )
        ).all()
        summary_by_key = {
            (cast("UsageMetric", row.metric), row.period_key): (
                int(row.record_count),
                int(row.record_delta_total),
            )
            for row in summaries
        }

        # 2. 窗口排名选出每项最后结果；相同时间再按 UUID 排序，保证结果可重复。
        rank = (
            func.row_number()
            .over(
                partition_by=(
                    workspace_usage_records.c.metric,
                    workspace_usage_records.c.period_key,
                ),
                order_by=(
                    workspace_usage_records.c.occurred_at.desc(),
                    workspace_usage_records.c.usage_record_id.desc(),
                ),
            )
            .label("record_rank")
        )
        ranked_records = (
            select(
                workspace_usage_records.c.metric,
                workspace_usage_records.c.period_key,
                workspace_usage_records.c.resulting_value,
                rank,
            )
            .where(workspace_usage_records.c.workspace_id == workspace_id)
            .subquery()
        )
        latest_by_key = {
            (cast("UsageMetric", row.metric), row.period_key): int(row.resulting_value)
            for row in self._session.execute(
                select(ranked_records).where(ranked_records.c.record_rank == 1)
            )
        }
        counters = {
            (counter.metric, counter.period_key): counter
            for counter in self.list_usage_counters(workspace_id)
        }
        keys = sorted(counters.keys() | summary_by_key.keys())
        return tuple(
            self._reconciliation(
                metric=metric_key,
                period_key=period_key,
                counter=counters.get((metric_key, period_key)),
                summary=summary_by_key.get((metric_key, period_key)),
                latest_resulting_value=latest_by_key.get((metric_key, period_key)),
            )
            for metric_key, period_key in keys
        )

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
        return self._usage_record(row)

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

    @staticmethod
    def _usage_record(row: Any) -> UsageRecord:
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

    def _usage_cursor(
        self,
        workspace_id: UUID,
        usage_record_id: UUID,
    ) -> tuple[datetime, UUID]:
        row = self._session.execute(
            select(
                workspace_usage_records.c.occurred_at,
                workspace_usage_records.c.usage_record_id,
            ).where(
                workspace_usage_records.c.workspace_id == workspace_id,
                workspace_usage_records.c.usage_record_id == usage_record_id,
            )
        ).one_or_none()
        if row is None:
            raise UsageCursorError
        return row.occurred_at, row.usage_record_id

    @staticmethod
    def _reconciliation(
        *,
        metric: UsageMetric,
        period_key: str,
        counter: UsageCounter | None,
        summary: tuple[int, int] | None,
        latest_resulting_value: int | None,
    ) -> UsageReconciliation:
        record_count, record_delta_total = summary or (0, 0)
        counter_value = counter.used_value if counter is not None else None
        # 明细累计值和最后结果都必须等于计数器；缺任一侧即保留为不一致供运营排查。
        consistent = (
            counter_value is not None
            and latest_resulting_value is not None
            and counter_value == latest_resulting_value == record_delta_total
        )
        return UsageReconciliation(
            metric=metric,
            period_key=period_key,
            counter_value=counter_value,
            counter_version=counter.version if counter is not None else None,
            record_count=record_count,
            record_delta_total=record_delta_total,
            latest_resulting_value=latest_resulting_value,
            consistent=consistent,
        )


class SqlAlchemyEntitlementUnitOfWork:
    """保证权益、用量、审计和 Outbox 使用同一 Session 提交。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyEntitlementRepository,
                SqlAlchemyAuditWriter,
                SqlAlchemyOutboxWriter,
            ]
            | None
        ] = ContextVar("entitlement_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyEntitlementUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Entitlement Unit of Work 不允许在同一上下文重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyEntitlementRepository(session),
                SqlAlchemyAuditWriter(session),
                SqlAlchemyOutboxWriter(session),
            )
        )
        return self

    def _current(
        self,
    ) -> tuple[
        Session,
        SqlAlchemyEntitlementRepository,
        SqlAlchemyAuditWriter,
        SqlAlchemyOutboxWriter,
    ]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Entitlement Unit of Work 尚未进入事务范围")
        return state

    @property
    def entitlements(self) -> SqlAlchemyEntitlementRepository:
        return self._current()[1]

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        return self._current()[2]

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        return self._current()[3]

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        state = self._state.get()
        if state is not None:
            session = state[0]
            if exc_type is not None:
                session.rollback()
            session.close()
            self._state.set(None)

    def commit(self) -> None:
        session = self._current()[0]
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise EntitlementWriteConflictError from error
