"""编排索引维护请求的有限重试、幂等执行和结果回写。"""

from __future__ import annotations

from datetime import UTC, datetime

from ai_platform_worker.modules.indexing.application.maintenance import IndexMaintenanceProcessor
from ai_platform_worker.modules.indexing.domain.commands import (
    ClaimedIndexMaintenanceRequest,
    IndexMaintenanceCommandBatchResult,
    IndexMaintenanceRequestStore,
)


class IndexMaintenanceCommandProcessor:
    """扫描公开请求事实，并使用请求 ID 幂等调用既有索引维护能力。"""

    def __init__(
        self,
        store: IndexMaintenanceRequestStore,
        maintenance: IndexMaintenanceProcessor,
        *,
        worker_id: str,
        lease_seconds: int,
        max_attempts: int = 3,
    ) -> None:
        self._store = store
        self._maintenance = maintenance
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts

    def run_batch(
        self,
        *,
        limit: int,
        now: datetime | None = None,
    ) -> IndexMaintenanceCommandBatchResult:
        """领取并执行一批请求；单条失败不会阻断同批其他维护命令。"""

        started_at = now or datetime.now(UTC)
        requests = self._store.claim_due(
            worker_id=self._worker_id,
            now=started_at,
            limit=limit,
            lease_seconds=self._lease_seconds,
            max_attempts=self._max_attempts,
        )
        completed = retried = dead_lettered = 0
        # 每个命令使用请求 ID 作为维护运行 ID，崩溃后的租约恢复不会复制派生索引副作用。
        for request in requests:
            try:
                self._execute(request, started_at)
            except Exception as error:
                outcome = self._store.mark_failed(
                    request,
                    failed_at=datetime.now(UTC),
                    error_code=f"INDEX_MAINTENANCE_{type(error).__name__.upper()}"[:128],
                    max_attempts=self._max_attempts,
                )
                retried += int(outcome == "retry_wait")
                dead_lettered += int(outcome == "dead_letter")
                continue
            completed += int(self._store.mark_completed(request, completed_at=datetime.now(UTC)))
        return IndexMaintenanceCommandBatchResult(
            claimed=len(requests),
            completed=completed,
            retried=retried,
            dead_lettered=dead_lettered,
        )

    def _execute(self, request: ClaimedIndexMaintenanceRequest, now: datetime) -> None:
        if request.command == "inspection":
            self._maintenance.inspect_and_repair(
                now=now,
                workspace_id=request.workspace_id,
                maintenance_run_id=request.maintenance_request_id,
                requested_by_actor_id=request.requested_by_actor_id,
            )
            return
        if request.command == "full_rebuild":
            self._maintenance.enqueue_full_rebuild(
                request.maintenance_request_id,
                requested_by_actor_id=request.requested_by_actor_id,
                workspace_id=request.workspace_id,
                now=now,
            )
            return
        self._maintenance.cleanup_unrecoverable_chunks(
            request.maintenance_request_id,
            requested_by_actor_id=request.requested_by_actor_id,
            workspace_id=request.workspace_id,
            now=now,
        )
