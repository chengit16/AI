"""定义索引维护命令的租约事实、批次结果和持久化端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

IndexMaintenanceCommand = Literal["inspection", "full_rebuild", "cleanup"]


@dataclass(frozen=True)
class ClaimedIndexMaintenanceRequest:
    """携带 Worker 已持有租约的最小索引维护命令。"""

    maintenance_request_id: UUID
    workspace_id: UUID
    command: IndexMaintenanceCommand
    requested_by_actor_id: UUID
    attempt_count: int
    claimed_by: str


@dataclass(frozen=True)
class IndexMaintenanceCommandBatchResult:
    """汇总单轮人工维护请求处理结果。"""

    claimed: int
    completed: int
    retried: int
    dead_lettered: int


class IndexMaintenanceRequestStore(Protocol):
    """约束人工维护请求租约和状态回写操作。"""

    def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
        max_attempts: int,
    ) -> tuple[ClaimedIndexMaintenanceRequest, ...]: ...

    def mark_completed(
        self,
        request: ClaimedIndexMaintenanceRequest,
        *,
        completed_at: datetime,
    ) -> bool: ...

    def mark_failed(
        self,
        request: ClaimedIndexMaintenanceRequest,
        *,
        failed_at: datetime,
        error_code: str,
        max_attempts: int,
    ) -> Literal["retry_wait", "dead_letter", "lost_claim"]: ...
