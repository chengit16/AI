"""验证审计导出冻结、幂等和事务边界。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import TracebackType
from typing import cast
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.integration.application.operations import (
    IntegrationOperationsService,
    OperationsDeniedError,
    OperationsIdempotencyConflictError,
)
from ai_platform_api.modules.integration.domain.operations import (
    AuditExportRequest,
    IntegrationOperationsRepository,
    IntegrationOperationsUnitOfWork,
)
from ai_platform_backend.integration.domain import IntegrationEvent

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000805")
OTHER_WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000806")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000805")
NOW = datetime(2026, 9, 3, 8, 30, tzinfo=UTC)


class MemoryWriter:
    """收集事务内审计或 Outbox 事实。"""

    def __init__(self) -> None:
        self.items: list[object] = []

    def add(self, item: object) -> None:
        self.items.append(item)


class MemoryOperations:
    """只实现审计导出用例需要的空间隔离仓储。"""

    def __init__(self) -> None:
        self.requests: dict[tuple[UUID, str], AuditExportRequest] = {}

    def get_audit_export_request_by_key(
        self, workspace_id: UUID, idempotency_key: str
    ) -> AuditExportRequest | None:
        return self.requests.get((workspace_id, idempotency_key))

    def get_audit_export_request(
        self, workspace_id: UUID, audit_export_request_id: UUID
    ) -> AuditExportRequest | None:
        return next(
            (
                item
                for (item_workspace_id, _), item in self.requests.items()
                if item_workspace_id == workspace_id
                and item.audit_export_request_id == audit_export_request_id
            ),
            None,
        )

    def list_audit_export_requests(
        self, workspace_id: UUID, *, limit: int
    ) -> tuple[AuditExportRequest, ...]:
        return tuple(
            item
            for (item_workspace_id, _), item in self.requests.items()
            if item_workspace_id == workspace_id
        )[:limit]

    def add_audit_export_request(self, request: AuditExportRequest) -> None:
        self.requests[(request.workspace_id, request.idempotency_key)] = request


class MemoryOperationsUnitOfWork:
    """模拟同一事务内的请求、审计、Outbox 与提交标记。"""

    def __init__(self) -> None:
        self.operations = cast(IntegrationOperationsRepository, MemoryOperations())
        self.audit = MemoryWriter()
        self.outbox = MemoryWriter()
        self.commit_count = 0

    def __enter__(self) -> IntegrationOperationsUnitOfWork:
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
    *,
    workspace_id: UUID = WORKSPACE_ID,
    authentication_method: str = "browser_session",
) -> RequestContext:
    base = RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=workspace_id,
        trace=TraceContext("a" * 32, "b" * 16),
        authentication_method=authentication_method,
    )
    return replace(
        base,
        authorized_permission_code="operations.records.read",
        authorized_policy_decision_id=UUID("90000000-0000-4000-8000-000000000805"),
        authorized_policy_version=7,
        authorized_workspace=True,
        authorized_field_mask=frozenset({"actor_id"}),
    )


def test_create_freezes_time_and_commits_request_audit_and_outbox() -> None:
    unit_of_work = MemoryOperationsUnitOfWork()
    service = IntegrationOperationsService(unit_of_work)

    request = service.create_audit_export_request(
        _context(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="synthetic-export-0001",
        action=" role.permissions.replace ",
        requested_at=NOW,
    )

    assert request.occurred_to == NOW
    assert request.action == "role.permissions.replace"
    assert request.field_mask == frozenset({"actor_id"})
    assert unit_of_work.commit_count == 1
    assert len(unit_of_work.audit.items) == len(unit_of_work.outbox.items) == 1
    event = unit_of_work.outbox.items[0]
    assert isinstance(event, IntegrationEvent)
    assert event.event_type == "operations.audit.export_requested"
    assert event.payload == {"audit_export_request_id": str(request.audit_export_request_id)}


def test_same_idempotency_returns_first_fact_and_changed_filter_conflicts() -> None:
    unit_of_work = MemoryOperationsUnitOfWork()
    service = IntegrationOperationsService(unit_of_work)
    first = service.create_audit_export_request(
        _context(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="synthetic-export-0002",
        outcome="succeeded",
        requested_at=NOW,
    )

    repeated = service.create_audit_export_request(
        _context(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="synthetic-export-0002",
        outcome="succeeded",
        requested_at=NOW,
    )
    assert repeated == first
    assert unit_of_work.commit_count == 1

    with pytest.raises(OperationsIdempotencyConflictError):
        service.create_audit_export_request(
            _context(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="synthetic-export-0002",
            outcome="failed",
            requested_at=NOW,
        )


def test_non_browser_and_cross_workspace_are_denied() -> None:
    service = IntegrationOperationsService(MemoryOperationsUnitOfWork())
    with pytest.raises(OperationsDeniedError):
        service.create_audit_export_request(
            _context(authentication_method="api_key"),
            workspace_id=WORKSPACE_ID,
            idempotency_key="synthetic-export-0003",
            requested_at=NOW,
        )
    with pytest.raises(OperationsDeniedError):
        service.create_audit_export_request(
            _context(workspace_id=OTHER_WORKSPACE_ID),
            workspace_id=WORKSPACE_ID,
            idempotency_key="synthetic-export-0004",
            requested_at=NOW,
        )
