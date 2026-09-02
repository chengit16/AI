"""定义工作空间审计、Outbox 与消费者幂等运营事实和持久化端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter

AuditOutcome = Literal["succeeded", "denied", "failed"]
OutboxStatus = Literal["pending", "publishing", "published", "dead_letter"]
AuditExportStatus = Literal["pending", "running", "retry_wait", "completed", "dead_letter"]


@dataclass(frozen=True)
class AuditOperationsRecord:
    """提供已授权运营查询需要的完整审计关联字段。"""

    audit_id: UUID
    workspace_id: UUID
    actor_id: UUID | None
    user_id: UUID | None
    action: str
    resource_type: str
    resource_id: UUID
    outcome: AuditOutcome
    occurred_at: datetime
    request_id: UUID
    trace_id: str
    permission_code: str | None
    policy_decision_id: UUID | None
    policy_version: int | None
    attributes: dict[str, object]


@dataclass(frozen=True)
class AuditOperationsPage:
    """返回按发生时间倒序排列的一页审计事实。"""

    items: tuple[AuditOperationsRecord, ...]
    next_cursor: UUID | None


@dataclass(frozen=True)
class AuditExportRequest:
    """冻结审计筛选、字段遮罩和异步处理结果，不暴露内部导出载荷。"""

    audit_export_request_id: UUID
    workspace_id: UUID
    idempotency_key: str
    request_hash: str
    actor_id: UUID | None
    action: str | None
    resource_type: str | None
    outcome: AuditOutcome | None
    occurred_from: datetime | None
    occurred_to: datetime
    field_mask: frozenset[str]
    requested_by_actor_id: UUID
    requested_by_user_id: UUID | None
    request_id: UUID
    trace_id: str
    traceparent: str
    status: AuditExportStatus
    attempt_count: int
    last_error_code: str | None
    row_count: int | None
    result_sha256: str | None
    result_summary: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class OutboxOperationsRecord:
    """提供不含 Payload 的 Outbox 运营元数据。"""

    event_id: UUID
    workspace_id: UUID
    event_type: str
    schema_version: int
    aggregate_id: UUID
    aggregate_version: int
    occurred_at: datetime
    status: OutboxStatus
    attempt_count: int
    available_at: datetime
    claim_until: datetime | None
    last_error_code: str | None
    published_at: datetime | None
    actor_id: UUID | None
    user_id: UUID | None
    request_id: UUID | None
    trace_id: str
    replay_count: int


@dataclass(frozen=True)
class OutboxOperationsPage:
    """返回按发生时间倒序排列的一页 Outbox 元数据。"""

    items: tuple[OutboxOperationsRecord, ...]
    next_cursor: UUID | None


@dataclass(frozen=True)
class OutboxStatusCount:
    """记录一种 Outbox 状态的事件数量。"""

    status: OutboxStatus
    count: int


@dataclass(frozen=True)
class IntegrationInspection:
    """汇总积压、Schema 兼容和消费者幂等巡检结果。"""

    checked_at: datetime
    status_counts: tuple[OutboxStatusCount, ...]
    oldest_pending_age_seconds: float
    expired_claim_count: int
    incompatible_schema_count: int
    replay_request_count: int
    consumer_receipt_count: int
    duplicate_delivery_count: int
    idempotency_issue_count: int


@dataclass(frozen=True)
class OutboxReplayRequest:
    """不可变记录一次人工重放请求及重放前的原事件状态。"""

    replay_request_id: UUID
    workspace_id: UUID
    event_id: UUID
    idempotency_key: str
    reason_code: str
    source_status: Literal["published", "dead_letter"]
    source_attempt_count: int
    source_published_at: datetime | None
    source_error_code: str | None
    requested_by_actor_id: UUID
    requested_by_user_id: UUID | None
    request_id: UUID
    trace_id: str
    traceparent: str
    requested_at: datetime


class IntegrationOperationsRepository(Protocol):
    """在工作空间隔离下查询运营事实并原子重排同一 Outbox 事件。"""

    def list_audit_records(
        self,
        workspace_id: UUID,
        *,
        limit: int,
        cursor: UUID | None,
        actor_id: UUID | None,
        action: str | None,
        resource_type: str | None,
        outcome: AuditOutcome | None,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
    ) -> AuditOperationsPage: ...

    def get_audit_record(
        self, workspace_id: UUID, audit_id: UUID
    ) -> AuditOperationsRecord | None: ...

    def list_audit_export_requests(
        self, workspace_id: UUID, *, limit: int
    ) -> tuple[AuditExportRequest, ...]: ...

    def get_audit_export_request(
        self, workspace_id: UUID, audit_export_request_id: UUID
    ) -> AuditExportRequest | None: ...

    def get_audit_export_request_by_key(
        self, workspace_id: UUID, idempotency_key: str
    ) -> AuditExportRequest | None: ...

    def add_audit_export_request(self, request: AuditExportRequest) -> None: ...

    def list_outbox_events(
        self,
        workspace_id: UUID,
        *,
        limit: int,
        cursor: UUID | None,
        status: OutboxStatus | None,
        event_type: str | None,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
    ) -> OutboxOperationsPage: ...

    def inspect(self, workspace_id: UUID, *, now: datetime) -> IntegrationInspection: ...

    def get_replay_request(
        self,
        workspace_id: UUID,
        idempotency_key: str,
    ) -> OutboxReplayRequest | None: ...

    def get_replay_source(
        self,
        workspace_id: UUID,
        event_id: UUID,
    ) -> OutboxOperationsRecord | None: ...

    def add_replay_request(self, request: OutboxReplayRequest) -> None: ...

    def requeue_event(
        self,
        *,
        workspace_id: UUID,
        event_id: UUID,
        source_status: Literal["published", "dead_letter"],
        available_at: datetime,
    ) -> bool: ...


class IntegrationOperationsUnitOfWork(Protocol):
    """保证重放请求、原事件重排和审计记录使用一个数据库事务。"""

    @property
    def operations(self) -> IntegrationOperationsRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> IntegrationOperationsUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class IntegrationOperationsWriteConflictError(Exception):
    """表示重放幂等键或事件状态在提交前发生并发变化。"""


class IntegrationOperationsCursorError(Exception):
    """表示分页游标不存在或不属于当前工作空间。"""
