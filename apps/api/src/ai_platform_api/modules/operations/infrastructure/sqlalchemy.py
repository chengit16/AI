"""实现运营工作台聚合查询与索引维护请求 PostgreSQL Adapter。"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from types import TracebackType
from typing import Any, cast
from uuid import UUID

from ai_platform_backend.indexing.persistence import (
    index_maintenance_requests,
    index_maintenance_runs,
    index_versions,
)
from ai_platform_backend.ingestion.persistence import ingestion_jobs
from ai_platform_backend.integration.persistence import outbox_events
from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import func, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ai_platform_api.modules.operations.contracts import (
    IndexMaintenanceCommand,
    IndexMaintenanceRequest,
    IndexMaintenanceRequestStatus,
    IndexMaintenanceRun,
    LifecycleOperation,
    OperationsIngestionJob,
    OperationsOverview,
    OperationsStatusCount,
)
from ai_platform_api.modules.operations.domain.ports import (
    OperationsWorkbenchWriteConflictError,
)
from ai_platform_api.persistence.tables import (
    lifecycle_export_records,
    lifecycle_purge_requests,
    lifecycle_retention_runs,
)

SessionFactory = sessionmaker[Session]


class SqlAlchemyOperationsWorkbenchRepository:
    """读取跨模块安全摘要，并只写入索引维护请求事实。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def overview(self, workspace_id: UUID, *, now: datetime) -> OperationsOverview:
        """在同一数据库快照中聚合固定状态维度，不读取正文或高基数标识。"""

        # 四组状态只按当前工作空间聚合；索引版本与任务共享相同事实库但不复制明细。
        ingestion = _status_counts(self._session, ingestion_jobs, workspace_id)
        index_statuses = _status_counts(self._session, index_versions, workspace_id)
        requests = _status_counts(self._session, index_maintenance_requests, workspace_id)
        outbox = _status_counts(self._session, outbox_events, workspace_id)
        lifecycle_active = sum(
            _count_where(
                self._session,
                table,
                workspace_id,
                statuses,
            )
            for table, statuses in (
                (lifecycle_export_records, ("running",)),
                (lifecycle_purge_requests, ("pending", "retryable")),
                (lifecycle_retention_runs, ("running",)),
            )
        )
        return OperationsOverview(
            checked_at=now,
            ingestion=ingestion,
            index_versions=index_statuses,
            index_requests=requests,
            outbox=outbox,
            lifecycle_active_count=lifecycle_active,
        )

    def list_ingestion_jobs(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[OperationsIngestionJob, ...]:
        rows = self._session.execute(
            select(
                ingestion_jobs.c.ingestion_job_id,
                ingestion_jobs.c.knowledge_base_id,
                ingestion_jobs.c.document_id,
                ingestion_jobs.c.source_name,
                ingestion_jobs.c.processing_lane,
                ingestion_jobs.c.status,
                ingestion_jobs.c.attempt_count,
                ingestion_jobs.c.max_attempts,
                ingestion_jobs.c.manual_retry_count,
                ingestion_jobs.c.error_code,
                ingestion_jobs.c.created_at,
                ingestion_jobs.c.updated_at,
            )
            .where(ingestion_jobs.c.workspace_id == workspace_id)
            .order_by(ingestion_jobs.c.updated_at.desc(), ingestion_jobs.c.ingestion_job_id.desc())
            .limit(limit)
        ).mappings()
        return tuple(_ingestion_job(row) for row in rows)

    def list_index_requests(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[IndexMaintenanceRequest, ...]:
        rows = self._session.execute(
            select(index_maintenance_requests)
            .where(index_maintenance_requests.c.workspace_id == workspace_id)
            .order_by(
                index_maintenance_requests.c.created_at.desc(),
                index_maintenance_requests.c.maintenance_request_id.desc(),
            )
            .limit(limit)
        ).mappings()
        return tuple(_index_request(row) for row in rows)

    def list_index_runs(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[IndexMaintenanceRun, ...]:
        # 周期性全局巡检的 workspace_id 为空，不能借任一工作空间权限读取其全局结果。
        rows = self._session.execute(
            select(index_maintenance_runs)
            .where(index_maintenance_runs.c.workspace_id == workspace_id)
            .order_by(
                index_maintenance_runs.c.completed_at.desc(),
                index_maintenance_runs.c.maintenance_run_id.desc(),
            )
            .limit(limit)
        ).mappings()
        return tuple(_index_run(row) for row in rows)

    def list_lifecycle_operations(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[LifecycleOperation, ...]:
        # 1. 三类运行保持各自事实模型，聚合层只读取当前空间并投影共同字段。
        exports = [
            LifecycleOperation(
                operation_id=row.export_id,
                operation_kind="export",
                status=row.status,
                created_at=row.created_at,
                completed_at=row.completed_at,
                error_code=row.error_code,
                result_count=row.object_count,
            )
            for row in self._session.execute(
                select(lifecycle_export_records)
                .where(lifecycle_export_records.c.workspace_id == workspace_id)
                .order_by(lifecycle_export_records.c.created_at.desc())
                .limit(limit)
            )
        ]
        purges = [
            LifecycleOperation(
                operation_id=row.purge_request_id,
                operation_kind="purge",
                status=row.status,
                created_at=row.created_at,
                completed_at=row.completed_at,
                error_code=row.last_error_code,
                result_count=row.deleted_object_count + row.deleted_cache_key_count,
            )
            for row in self._session.execute(
                select(lifecycle_purge_requests)
                .where(lifecycle_purge_requests.c.workspace_id == workspace_id)
                .order_by(lifecycle_purge_requests.c.created_at.desc())
                .limit(limit)
            )
        ]
        retentions = [
            LifecycleOperation(
                operation_id=row.retention_run_id,
                operation_kind="retention",
                status=row.status,
                created_at=row.created_at,
                completed_at=row.completed_at,
                error_code=row.error_code,
                result_count=None,
            )
            for row in self._session.execute(
                select(lifecycle_retention_runs)
                .where(lifecycle_retention_runs.c.workspace_id == workspace_id)
                .order_by(lifecycle_retention_runs.c.created_at.desc())
                .limit(limit)
            )
        ]
        # 2. 统一历史按时间和稳定标识排序，再应用调用方约定的总条数上限。
        combined = sorted(
            (*exports, *purges, *retentions),
            key=lambda item: (item.created_at, str(item.operation_id)),
            reverse=True,
        )
        return tuple(combined[:limit])

    def get_index_request(
        self,
        workspace_id: UUID,
        idempotency_key: str,
    ) -> IndexMaintenanceRequest | None:
        row = (
            self._session.execute(
                select(index_maintenance_requests).where(
                    index_maintenance_requests.c.workspace_id == workspace_id,
                    index_maintenance_requests.c.idempotency_key == idempotency_key,
                )
            )
            .mappings()
            .one_or_none()
        )
        return _index_request(row) if row is not None else None

    def add_index_request(self, request: IndexMaintenanceRequest) -> None:
        self._session.execute(
            insert(index_maintenance_requests).values(
                maintenance_request_id=request.maintenance_request_id,
                workspace_id=request.workspace_id,
                command=request.command,
                idempotency_key=request.idempotency_key,
                request_hash=request.request_hash,
                reason_code=request.reason_code,
                status=request.status,
                attempt_count=request.attempt_count,
                claimed_by=None,
                claim_until=None,
                last_error_code=request.last_error_code,
                requested_by_actor_id=request.requested_by_actor_id,
                requested_by_user_id=request.requested_by_user_id,
                request_id=request.request_id,
                trace_id=request.trace_id,
                traceparent=request.traceparent,
                created_at=request.created_at,
                updated_at=request.updated_at,
                completed_at=request.completed_at,
            )
        )


class SqlAlchemyOperationsWorkbenchUnitOfWork:
    """为运营查询提供短 Session，并原子提交请求、审计和 Outbox。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyOperationsWorkbenchRepository,
                SqlAlchemyAuditWriter,
                SqlAlchemyOutboxWriter,
            ]
            | None
        ] = ContextVar("operations_workbench_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyOperationsWorkbenchUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Operations Workbench Unit of Work 不允许重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyOperationsWorkbenchRepository(session),
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
        state = self._state.get()
        if state is not None:
            if exc_type is not None:
                state[0].rollback()
            state[0].close()
            self._state.set(None)

    @property
    def operations(self) -> SqlAlchemyOperationsWorkbenchRepository:
        return self._current()[1]

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        return self._current()[2]

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        return self._current()[3]

    def commit(self) -> None:
        try:
            self._current()[0].commit()
        except IntegrityError as error:
            self._current()[0].rollback()
            raise OperationsWorkbenchWriteConflictError from error

    def _current(
        self,
    ) -> tuple[
        Session,
        SqlAlchemyOperationsWorkbenchRepository,
        SqlAlchemyAuditWriter,
        SqlAlchemyOutboxWriter,
    ]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Operations Workbench Unit of Work 尚未进入事务范围")
        return state


def _status_counts(
    session: Session, table: Any, workspace_id: UUID
) -> tuple[OperationsStatusCount, ...]:
    rows = session.execute(
        select(table.c.status, func.count())
        .where(table.c.workspace_id == workspace_id)
        .group_by(table.c.status)
        .order_by(table.c.status)
    )
    return tuple(
        OperationsStatusCount(status=str(status), count=int(count)) for status, count in rows
    )


def _count_where(
    session: Session,
    table: Any,
    workspace_id: UUID,
    statuses: tuple[str, ...],
) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(table)
            .where(
                table.c.workspace_id == workspace_id,
                table.c.status.in_(statuses),
            )
        )
        or 0
    )


def _ingestion_job(row: RowMapping) -> OperationsIngestionJob:
    return OperationsIngestionJob(
        ingestion_job_id=row["ingestion_job_id"],
        knowledge_base_id=row["knowledge_base_id"],
        document_id=row["document_id"],
        source_name=row["source_name"],
        processing_lane=cast(Any, row["processing_lane"]),
        status=row["status"],
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        manual_retry_count=row["manual_retry_count"],
        error_code=row["error_code"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _index_request(row: RowMapping) -> IndexMaintenanceRequest:
    return IndexMaintenanceRequest(
        maintenance_request_id=row["maintenance_request_id"],
        workspace_id=row["workspace_id"],
        command=cast(IndexMaintenanceCommand, row["command"]),
        idempotency_key=row["idempotency_key"],
        request_hash=row["request_hash"],
        reason_code=row["reason_code"],
        status=cast(IndexMaintenanceRequestStatus, row["status"]),
        attempt_count=row["attempt_count"],
        last_error_code=row["last_error_code"],
        requested_by_actor_id=row["requested_by_actor_id"],
        requested_by_user_id=row["requested_by_user_id"],
        request_id=row["request_id"],
        trace_id=row["trace_id"],
        traceparent=row["traceparent"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def _index_run(row: RowMapping) -> IndexMaintenanceRun:
    return IndexMaintenanceRun(
        maintenance_run_id=row["maintenance_run_id"],
        run_kind=cast(IndexMaintenanceCommand, row["run_kind"]),
        requested_by_actor_id=row["requested_by_actor_id"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        scanned_document_count=row["scanned_document_count"],
        inconsistency_count=row["inconsistency_count"],
        repaired_count=row["repaired_count"],
        rebuild_queued_count=row["rebuild_queued_count"],
        cleaned_chunk_count=row["cleaned_chunk_count"],
        result_digest=row["result_digest"],
    )
