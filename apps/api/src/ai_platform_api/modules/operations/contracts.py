"""定义运营模块对应用层、HTTP 层和 PostgreSQL Adapter 暴露的稳定类型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

IndexMaintenanceCommand = Literal["inspection", "full_rebuild", "cleanup"]
IndexMaintenanceRequestStatus = Literal[
    "pending",
    "running",
    "retry_wait",
    "completed",
    "dead_letter",
]


@dataclass(frozen=True)
class OperationsStatusCount:
    """返回一个低基数状态及其当前数量。"""

    status: str
    count: int


@dataclass(frozen=True)
class OperationsOverview:
    """汇总工作空间任务、索引、Outbox 和生命周期的安全计数。"""

    checked_at: datetime
    ingestion: tuple[OperationsStatusCount, ...]
    index_versions: tuple[OperationsStatusCount, ...]
    index_requests: tuple[OperationsStatusCount, ...]
    outbox: tuple[OperationsStatusCount, ...]
    lifecycle_active_count: int


@dataclass(frozen=True)
class OperationsIngestionJob:
    """返回不含对象键、正文和内部租约的入库任务运营摘要。"""

    ingestion_job_id: UUID
    knowledge_base_id: UUID
    document_id: UUID
    source_name: str
    processing_lane: Literal["parsing", "ocr"]
    status: str
    attempt_count: int
    max_attempts: int
    manual_retry_count: int
    error_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class IndexMaintenanceRequest:
    """记录索引维护命令的权限、幂等、执行和失败状态。"""

    maintenance_request_id: UUID
    workspace_id: UUID
    command: IndexMaintenanceCommand
    idempotency_key: str
    request_hash: str
    reason_code: str
    status: IndexMaintenanceRequestStatus
    attempt_count: int
    last_error_code: str | None
    requested_by_actor_id: UUID
    requested_by_user_id: UUID
    request_id: UUID
    trace_id: str
    traceparent: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class IndexMaintenanceRun:
    """返回一次已完成索引维护的最小结果摘要。"""

    maintenance_run_id: UUID
    run_kind: IndexMaintenanceCommand
    requested_by_actor_id: UUID | None
    started_at: datetime
    completed_at: datetime
    scanned_document_count: int
    inconsistency_count: int
    repaired_count: int
    rebuild_queued_count: int
    cleaned_chunk_count: int
    result_digest: str


@dataclass(frozen=True)
class LifecycleOperation:
    """把导出、清除和保留期运行投影为统一历史列表。"""

    operation_id: UUID
    operation_kind: Literal["export", "purge", "retention"]
    status: str
    created_at: datetime
    completed_at: datetime | None
    error_code: str | None
    result_count: int | None
