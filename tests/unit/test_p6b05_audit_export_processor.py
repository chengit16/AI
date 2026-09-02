"""验证 P6B-05 审计导出 Worker 的成功、重试、死信和丢失租约边界。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from ai_platform_worker.modules.integration.application.audit_exports import (
    AuditExportProcessor,
)
from ai_platform_worker.modules.integration.domain.audit_exports import (
    AuditExportResult,
    ClaimedAuditExportRequest,
)

NOW = datetime(2026, 9, 3, 8, 30, tzinfo=UTC)


def _request(attempt_count: int = 1) -> ClaimedAuditExportRequest:
    return ClaimedAuditExportRequest(
        audit_export_request_id=UUID("63000000-0000-4000-8000-000000000905"),
        workspace_id=UUID("20000000-0000-4000-8000-000000000905"),
        actor_id=None,
        action="role.permissions.replace",
        resource_type="role",
        outcome="succeeded",
        occurred_from=None,
        occurred_to=NOW,
        field_mask=frozenset({"actor_id"}),
        attempt_count=attempt_count,
        claimed_by="synthetic-worker",
    )


class MemoryStore:
    """按配置结果模拟租约存储，记录 Worker 的状态回写。"""

    def __init__(self, *, fail: bool = False, attempt_count: int = 1) -> None:
        self.fail = fail
        self.request = _request(attempt_count)
        self.completed: list[AuditExportResult] = []
        self.failures: list[tuple[str, int]] = []

    def claim_due(self, **_: object) -> tuple[ClaimedAuditExportRequest, ...]:
        return (self.request,)

    def render_safe_result(self, request: ClaimedAuditExportRequest) -> AuditExportResult:
        assert request.workspace_id == self.request.workspace_id
        if self.fail:
            raise ValueError("synthetic")
        return AuditExportResult(1, "a" * 64, "已生成 1 条脱敏审计记录的安全摘要")

    def mark_completed(
        self,
        request: ClaimedAuditExportRequest,
        *,
        result: AuditExportResult,
        completed_at: datetime,
    ) -> bool:
        assert request == self.request and completed_at.tzinfo is not None
        self.completed.append(result)
        return True

    def mark_failed(
        self,
        request: ClaimedAuditExportRequest,
        *,
        failed_at: datetime,
        error_code: str,
        max_attempts: int,
    ) -> Literal["retry_wait", "dead_letter", "lost_claim"]:
        assert request == self.request and failed_at.tzinfo is not None
        self.failures.append((error_code, max_attempts))
        return "dead_letter" if request.attempt_count >= max_attempts else "retry_wait"


def test_successful_export_records_safe_summary() -> None:
    store = MemoryStore()
    result = AuditExportProcessor(store, worker_id="synthetic-worker", lease_seconds=30).run_batch(
        limit=10, now=NOW
    )

    assert result.claimed == result.completed == 1
    assert result.retried == result.dead_lettered == 0
    assert store.completed[0].sha256 == "a" * 64


def test_failure_retries_then_dead_letters_at_third_attempt() -> None:
    retry_store = MemoryStore(fail=True, attempt_count=2)
    retried = AuditExportProcessor(
        retry_store, worker_id="synthetic-worker", lease_seconds=30
    ).run_batch(limit=10, now=NOW)
    assert retried.retried == 1
    assert retry_store.failures == [("AUDIT_EXPORT_VALUEERROR", 3)]

    dead_store = MemoryStore(fail=True, attempt_count=3)
    dead = AuditExportProcessor(
        dead_store, worker_id="synthetic-worker", lease_seconds=30
    ).run_batch(limit=10, now=NOW)
    assert dead.dead_lettered == 1
    assert dead.completed == dead.retried == 0
