"""定义工作空间生命周期请求、结果与不可变证明。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

LifecycleExportStatus = Literal["running", "completed", "failed"]
LifecyclePurgeStatus = Literal["pending", "retryable", "completed"]
RetentionRunStatus = Literal["running", "completed", "failed"]


@dataclass(frozen=True)
class ExportObject:
    """携带一个工作空间对象及其已复算摘要。"""

    object_key: str
    content: bytes
    sha256: str


@dataclass(frozen=True)
class LifecycleExport:
    """记录一次幂等工作空间导出及其可校验包摘要。"""

    export_id: UUID
    workspace_id: UUID
    idempotency_key: str
    request_hash: str
    status: LifecycleExportStatus
    registry_version: int
    object_key: str | None
    bundle_size_bytes: int | None
    bundle_sha256: str | None
    object_manifest_sha256: str | None
    table_count: int | None
    object_count: int | None
    requested_by_account_id: UUID
    request_id: UUID
    trace_id: str
    traceparent: str
    created_at: datetime
    completed_at: datetime | None
    error_code: str | None


@dataclass(frozen=True)
class LifecyclePurge:
    """记录跨数据库、对象存储和缓存推进的数据清除状态。"""

    purge_request_id: UUID
    workspace_id: UUID
    idempotency_key: str
    request_hash: str
    reason_code: str
    confirmed_workspace_name: str
    status: LifecyclePurgeStatus
    database_cleared: bool
    objects_cleared: bool
    cache_cleared: bool
    deleted_table_counts: dict[str, int]
    deleted_object_count: int
    deleted_cache_key_count: int
    last_error_code: str | None
    requested_by_account_id: UUID
    request_id: UUID
    trace_id: str
    traceparent: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class DeletionCertificate:
    """保存不含业务正文的不可变跨存储删除证明。"""

    certificate_id: UUID
    workspace_id: UUID
    purge_request_id: UUID
    registry_version: int
    deleted_table_counts: dict[str, int]
    deleted_object_count: int
    deleted_cache_key_count: int
    result_sha256: str
    completed_at: datetime


@dataclass(frozen=True)
class RetentionRun:
    """记录一次保留期边界、删除计数和结果摘要。"""

    retention_run_id: UUID
    workspace_id: UUID
    idempotency_key: str
    status: RetentionRunStatus
    cutoffs: dict[str, str]
    deleted_table_counts: dict[str, int]
    result_sha256: str | None
    requested_by_account_id: UUID
    created_at: datetime
    completed_at: datetime | None
    error_code: str | None
