"""验证 P2-10 运营命令权限、确认、幂等、审计和 Worker 有限重试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Literal, cast
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.operations.application.service import (
    OperationsWorkbenchDeniedError,
    OperationsWorkbenchIdempotencyConflictError,
    OperationsWorkbenchService,
    OperationsWorkbenchValidationError,
)
from ai_platform_api.modules.operations.contracts import (
    IndexMaintenanceRequest,
    IndexMaintenanceRun,
    LifecycleOperation,
    OperationsIngestionJob,
    OperationsOverview,
)
from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent
from ai_platform_worker.modules.indexing.application.commands import (
    IndexMaintenanceCommandProcessor,
)
from ai_platform_worker.modules.indexing.application.maintenance import IndexMaintenanceProcessor
from ai_platform_worker.modules.indexing.domain.commands import (
    ClaimedIndexMaintenanceRequest,
    IndexMaintenanceRequestStore,
)

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000210")
ACTOR_ID = UUID("10000000-0000-4000-8000-000000000210")
NOW = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)
TRACE = TraceContext.continue_from("00-b123456789abcdef0123456789abcdef-b123456789abcdef-01")


class MemoryOperationsRepository:
    """保存最小索引请求事实，使测试聚焦应用层安全不变量。"""

    def __init__(self) -> None:
        self.requests: dict[tuple[UUID, str], IndexMaintenanceRequest] = {}

    def overview(self, workspace_id: UUID, *, now: datetime) -> OperationsOverview:
        raise AssertionError("本测试不应读取运营概览")

    def list_ingestion_jobs(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[OperationsIngestionJob, ...]:
        return ()

    def list_index_requests(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[IndexMaintenanceRequest, ...]:
        return tuple(
            request
            for (request_workspace_id, _), request in self.requests.items()
            if request_workspace_id == workspace_id
        )[:limit]

    def list_index_runs(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[IndexMaintenanceRun, ...]:
        return ()

    def list_lifecycle_operations(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[LifecycleOperation, ...]:
        return ()

    def get_index_request(
        self,
        workspace_id: UUID,
        idempotency_key: str,
    ) -> IndexMaintenanceRequest | None:
        return self.requests.get((workspace_id, idempotency_key))

    def add_index_request(self, request: IndexMaintenanceRequest) -> None:
        self.requests[(request.workspace_id, request.idempotency_key)] = request


class MemoryAuditWriter:
    def __init__(self) -> None:
        self.items: list[AuditRecord] = []

    def add(self, record: AuditRecord) -> None:
        self.items.append(record)


class MemoryOutboxWriter:
    def __init__(self) -> None:
        self.items: list[IntegrationEvent] = []

    def add(self, event: IntegrationEvent) -> None:
        self.items.append(event)


class MemoryOperationsUnitOfWork:
    """共享内存事实，并记录应用服务是否执行了原子提交入口。"""

    def __init__(self) -> None:
        self.operations = MemoryOperationsRepository()
        self.audit = MemoryAuditWriter()
        self.outbox = MemoryOutboxWriter()
        self.commit_count = 0

    def __enter__(self) -> MemoryOperationsUnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def commit(self) -> None:
        self.commit_count += 1


def _context(
    permission: str,
    *,
    workspace_id: UUID = WORKSPACE_ID,
    authentication_method: str = "browser_session",
) -> RequestContext:
    return replace(
        RequestContext.trusted(
            actor_id=ACTOR_ID,
            user_id=ACTOR_ID,
            workspace_id=workspace_id,
            trace=TRACE,
            authentication_method=authentication_method,
        ),
        authorized_permission_code=permission,
        authorized_policy_decision_id=uuid4(),
        authorized_policy_version=18,
        authorized_workspace=True,
    )


def _request_cleanup(
    service: OperationsWorkbenchService,
    context: RequestContext,
    *,
    reason_code: str = "OPERATOR_REQUEST",
    confirmation: str = "DELETE_UNRECOVERABLE_INDEX_CHUNKS",
) -> IndexMaintenanceRequest:
    """使用显式类型参数登记固定清理命令，避免测试辅助层弱化命令契约。"""

    return service.request_index_maintenance(
        context,
        workspace_id=WORKSPACE_ID,
        command="cleanup",
        idempotency_key="synthetic:p210:cleanup",
        reason_code=reason_code,
        confirmation=confirmation,
        requested_at=NOW,
    )


def test_index_command_is_atomic_audited_and_idempotent() -> None:
    unit_of_work = MemoryOperationsUnitOfWork()
    service = OperationsWorkbenchService(unit_of_work)
    context = _context("operations.index.rebuild")

    first = service.request_index_maintenance(
        context,
        workspace_id=WORKSPACE_ID,
        command="full_rebuild",
        idempotency_key="synthetic:p210:rebuild",
        reason_code="OPERATOR_REQUEST",
        confirmation="REBUILD_WORKSPACE_INDEX",
        requested_at=NOW,
    )
    repeated = service.request_index_maintenance(
        context,
        workspace_id=WORKSPACE_ID,
        command="full_rebuild",
        idempotency_key="synthetic:p210:rebuild",
        reason_code="OPERATOR_REQUEST",
        confirmation="REBUILD_WORKSPACE_INDEX",
        requested_at=NOW,
    )

    assert repeated == first
    assert first.status == "pending"
    assert unit_of_work.commit_count == 1
    assert len(unit_of_work.audit.items) == len(unit_of_work.outbox.items) == 1
    assert unit_of_work.audit.items[0].action == "operations.index.rebuild"
    assert unit_of_work.audit.items[0].authorization is not None
    assert unit_of_work.outbox.items[0].event_type == "index.maintenance.requested"
    assert unit_of_work.outbox.items[0].payload == {
        "maintenance_request_id": str(first.maintenance_request_id),
        "command": "full_rebuild",
    }


def test_index_command_rejects_wrong_scope_subject_confirmation_and_key_reuse() -> None:
    unit_of_work = MemoryOperationsUnitOfWork()
    service = OperationsWorkbenchService(unit_of_work)
    valid_context = _context("operations.index.cleanup")

    with pytest.raises(OperationsWorkbenchDeniedError):
        _request_cleanup(
            service,
            _context("operations.index.cleanup", workspace_id=uuid4()),
        )
    with pytest.raises(OperationsWorkbenchDeniedError):
        _request_cleanup(
            service,
            _context("operations.index.cleanup", authentication_method="open_api_key"),
        )
    with pytest.raises(OperationsWorkbenchValidationError):
        _request_cleanup(service, valid_context, confirmation="cleanup")

    _request_cleanup(service, valid_context)
    with pytest.raises(OperationsWorkbenchIdempotencyConflictError):
        _request_cleanup(service, valid_context, reason_code="SECURITY_REVIEW")


class MemoryRequestStore:
    """返回预设租约并记录 Worker 的完成、重试和死信回写。"""

    def __init__(self, requests: tuple[ClaimedIndexMaintenanceRequest, ...]) -> None:
        self.requests = requests
        self.completed: list[UUID] = []
        self.failed: list[tuple[UUID, str]] = []

    def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
        max_attempts: int,
    ) -> tuple[ClaimedIndexMaintenanceRequest, ...]:
        return self.requests[:limit]

    def mark_completed(
        self,
        request: ClaimedIndexMaintenanceRequest,
        *,
        completed_at: datetime,
    ) -> bool:
        self.completed.append(request.maintenance_request_id)
        return True

    def mark_failed(
        self,
        request: ClaimedIndexMaintenanceRequest,
        *,
        failed_at: datetime,
        error_code: str,
        max_attempts: int,
    ) -> Literal["retry_wait", "dead_letter", "lost_claim"]:
        self.failed.append((request.maintenance_request_id, error_code))
        return "dead_letter" if request.attempt_count >= max_attempts else "retry_wait"


class RecordingMaintenance:
    """记录稳定运行 ID；清理命令用于模拟单条执行失败。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, UUID]] = []

    def inspect_and_repair(self, **values: object) -> None:
        self.calls.append(("inspection", cast(UUID, values["maintenance_run_id"])))

    def enqueue_full_rebuild(self, maintenance_run_id: UUID, **_: object) -> None:
        self.calls.append(("full_rebuild", maintenance_run_id))

    def cleanup_unrecoverable_chunks(self, maintenance_run_id: UUID, **_: object) -> None:
        self.calls.append(("cleanup", maintenance_run_id))
        raise RuntimeError("synthetic cleanup failure")


@pytest.mark.parametrize(("attempt_count", "expected"), [(1, "retry_wait"), (3, "dead_letter")])
def test_worker_continues_batch_and_uses_request_id_as_idempotent_run_id(
    attempt_count: int,
    expected: Literal["retry_wait", "dead_letter"],
) -> None:
    inspection_id, cleanup_id = uuid4(), uuid4()
    requests = (
        ClaimedIndexMaintenanceRequest(
            inspection_id,
            WORKSPACE_ID,
            "inspection",
            ACTOR_ID,
            1,
            "synthetic-worker",
        ),
        ClaimedIndexMaintenanceRequest(
            cleanup_id,
            WORKSPACE_ID,
            "cleanup",
            ACTOR_ID,
            attempt_count,
            "synthetic-worker",
        ),
    )
    store = MemoryRequestStore(requests)
    maintenance = RecordingMaintenance()
    processor = IndexMaintenanceCommandProcessor(
        cast(IndexMaintenanceRequestStore, store),
        cast(IndexMaintenanceProcessor, maintenance),
        worker_id="synthetic-worker",
        lease_seconds=30,
        max_attempts=3,
    )

    result = processor.run_batch(limit=10, now=NOW)

    assert maintenance.calls == [("inspection", inspection_id), ("cleanup", cleanup_id)]
    assert store.completed == [inspection_id]
    assert store.failed[0][0] == cleanup_id
    assert store.failed[0][1] == "INDEX_MAINTENANCE_RUNTIMEERROR"
    assert result.claimed == 2 and result.completed == 1
    assert (result.retried, result.dead_lettered) == (
        (1, 0) if expected == "retry_wait" else (0, 1)
    )
