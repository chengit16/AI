"""定义审计导出请求的租约事实、批次结果和持久化端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID


@dataclass(frozen=True)
class ClaimedAuditExportRequest:
    """携带 Worker 已持有租约的审计筛选和可信字段遮罩。"""

    audit_export_request_id: UUID
    workspace_id: UUID
    actor_id: UUID | None
    action: str | None
    resource_type: str | None
    outcome: Literal["succeeded", "denied", "failed"] | None
    occurred_from: datetime | None
    occurred_to: datetime
    field_mask: frozenset[str]
    attempt_count: int
    claimed_by: str


@dataclass(frozen=True)
class AuditExportResult:
    """记录安全投影的行数、摘要和可校验哈希。"""

    row_count: int
    sha256: str
    summary: str


@dataclass(frozen=True)
class AuditExportBatchResult:
    """汇总单轮审计导出处理结果。"""

    claimed: int
    completed: int
    retried: int
    dead_lettered: int


class AuditExportRequestStore(Protocol):
    """约束审计导出请求的租约、安全投影和状态回写。"""

    def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
        max_attempts: int,
    ) -> tuple[ClaimedAuditExportRequest, ...]: ...

    def render_safe_result(self, request: ClaimedAuditExportRequest) -> AuditExportResult: ...

    def mark_completed(
        self,
        request: ClaimedAuditExportRequest,
        *,
        result: AuditExportResult,
        completed_at: datetime,
    ) -> bool: ...

    def mark_failed(
        self,
        request: ClaimedAuditExportRequest,
        *,
        failed_at: datetime,
        error_code: str,
        max_attempts: int,
    ) -> Literal["retry_wait", "dead_letter", "lost_claim"]: ...
