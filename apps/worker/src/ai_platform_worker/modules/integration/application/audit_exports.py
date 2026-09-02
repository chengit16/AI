"""编排审计导出请求的安全投影、有限重试和结果回写。"""

from __future__ import annotations

from datetime import UTC, datetime

from ai_platform_worker.modules.integration.domain.audit_exports import (
    AuditExportBatchResult,
    AuditExportRequestStore,
)


class AuditExportProcessor:
    """扫描公开请求事实，并生成不含自由属性和内部对象定位的安全摘要。"""

    def __init__(
        self,
        store: AuditExportRequestStore,
        *,
        worker_id: str,
        lease_seconds: int,
        max_attempts: int = 3,
    ) -> None:
        self._store = store
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts

    def run_batch(self, *, limit: int, now: datetime | None = None) -> AuditExportBatchResult:
        """处理一批请求；单条失败不会阻断同批其他导出。"""

        # 1. 通过数据库租约领取有界批次，过期租约与重试上限由存储层统一裁决。
        started_at = now or datetime.now(UTC)
        requests = self._store.claim_due(
            worker_id=self._worker_id,
            now=started_at,
            limit=limit,
            lease_seconds=self._lease_seconds,
            max_attempts=self._max_attempts,
        )
        completed = retried = dead_lettered = 0
        # 2. 每条请求独立投影和回写，单条异常只进入有限重试或死信，不阻断同批任务。
        for request in requests:
            try:
                result = self._store.render_safe_result(request)
            except Exception as error:
                outcome = self._store.mark_failed(
                    request,
                    failed_at=datetime.now(UTC),
                    error_code=f"AUDIT_EXPORT_{type(error).__name__.upper()}"[:128],
                    max_attempts=self._max_attempts,
                )
                retried += int(outcome == "retry_wait")
                dead_lettered += int(outcome == "dead_letter")
                continue
            completed += int(
                self._store.mark_completed(
                    request,
                    result=result,
                    completed_at=datetime.now(UTC),
                )
            )
        return AuditExportBatchResult(
            claimed=len(requests),
            completed=completed,
            retried=retried,
            dead_lettered=dead_lettered,
        )
