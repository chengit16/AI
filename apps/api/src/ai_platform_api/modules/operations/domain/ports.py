"""定义运营工作台 PostgreSQL 读写端口与事务边界。"""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter

from ai_platform_api.modules.operations.contracts import (
    IndexMaintenanceRequest,
    IndexMaintenanceRun,
    LifecycleOperation,
    OperationsIngestionJob,
    OperationsOverview,
)


class OperationsWorkbenchWriteConflictError(Exception):
    """运营请求唯一键或并发状态写入发生冲突。"""


class OperationsWorkbenchRepository(Protocol):
    """约束运营聚合读取与索引维护请求写入能力。"""

    def overview(self, workspace_id: UUID, *, now: datetime) -> OperationsOverview: ...

    def list_ingestion_jobs(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[OperationsIngestionJob, ...]: ...

    def list_index_requests(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[IndexMaintenanceRequest, ...]: ...

    def list_index_runs(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[IndexMaintenanceRun, ...]: ...

    def list_lifecycle_operations(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[LifecycleOperation, ...]: ...

    def get_index_request(
        self,
        workspace_id: UUID,
        idempotency_key: str,
    ) -> IndexMaintenanceRequest | None: ...

    def add_index_request(self, request: IndexMaintenanceRequest) -> None: ...


class OperationsWorkbenchUnitOfWork(Protocol):
    """保证索引维护请求、审计和 Outbox 使用同一事务。"""

    @property
    def operations(self) -> OperationsWorkbenchRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> OperationsWorkbenchUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
