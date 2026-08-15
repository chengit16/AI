"""定义审计、用量、Outbox 和幂等巡检的稳定 HTTP Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ai_platform_api.modules.identity.application.entitlements import (
    UsageReconciliation,
    UsageRecord,
)
from ai_platform_api.modules.integration.application.operations import (
    AuditOperationsRecord,
    IntegrationInspection,
    OutboxOperationsRecord,
    OutboxReplayRequest,
)


class AuditRecordResponse(BaseModel):
    """返回可关联主体、资源、授权策略和 Trace 的审计事实。"""

    audit_id: UUID
    workspace_id: UUID
    actor_id: UUID
    user_id: UUID | None
    action: str
    resource_type: str
    resource_id: UUID
    outcome: Literal["succeeded", "denied", "failed"]
    occurred_at: datetime
    request_id: UUID
    trace_id: str
    permission_code: str | None
    policy_decision_id: UUID | None
    policy_version: int | None

    @classmethod
    def from_domain(cls, record: AuditOperationsRecord) -> AuditRecordResponse:
        """省略自由属性，避免运营列表意外扩大敏感业务字段的可见面。"""

        return cls(
            audit_id=record.audit_id,
            workspace_id=record.workspace_id,
            actor_id=record.actor_id,
            user_id=record.user_id,
            action=record.action,
            resource_type=record.resource_type,
            resource_id=record.resource_id,
            outcome=record.outcome,
            occurred_at=record.occurred_at,
            request_id=record.request_id,
            trace_id=record.trace_id,
            permission_code=record.permission_code,
            policy_decision_id=record.policy_decision_id,
            policy_version=record.policy_version,
        )


class AuditRecordPageResponse(BaseModel):
    """返回一页审计事实及下一页稳定游标。"""

    items: list[AuditRecordResponse]
    next_cursor: UUID | None


class UsageRecordResponse(BaseModel):
    """返回单条不可变用量增量与计数结果。"""

    usage_record_id: UUID
    workspace_id: UUID
    metric: Literal[
        "storage_bytes",
        "knowledge_bases",
        "published_agents",
        "questions_monthly",
    ]
    period_key: str
    idempotency_key: str
    delta_value: int
    resulting_value: int
    occurred_at: datetime

    @classmethod
    def from_domain(cls, record: UsageRecord) -> UsageRecordResponse:
        return cls(**record.__dict__)


class UsageRecordPageResponse(BaseModel):
    """返回一页用量明细及下一页稳定游标。"""

    items: list[UsageRecordResponse]
    next_cursor: UUID | None


class UsageReconciliationResponse(BaseModel):
    """返回计数器、明细累计和最后结果的逐项对账状态。"""

    metric: Literal[
        "storage_bytes",
        "knowledge_bases",
        "published_agents",
        "questions_monthly",
    ]
    period_key: str
    counter_value: int | None
    counter_version: int | None
    record_count: int
    record_delta_total: int
    latest_resulting_value: int | None
    consistent: bool

    @classmethod
    def from_domain(
        cls,
        record: UsageReconciliation,
    ) -> UsageReconciliationResponse:
        return cls(**record.__dict__)


class UsageReconciliationListResponse(BaseModel):
    """返回工作空间全部计量项和周期的对账摘要。"""

    items: list[UsageReconciliationResponse]


class OutboxEventResponse(BaseModel):
    """返回 Outbox 运营元数据；协议结构刻意不定义 Payload。"""

    event_id: UUID
    workspace_id: UUID
    event_type: str
    schema_version: int
    aggregate_id: UUID
    aggregate_version: int
    occurred_at: datetime
    status: Literal["pending", "publishing", "published", "dead_letter"]
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

    @classmethod
    def from_domain(cls, record: OutboxOperationsRecord) -> OutboxEventResponse:
        return cls(**record.__dict__)


class OutboxEventPageResponse(BaseModel):
    """返回一页不含 Payload 的 Outbox 元数据。"""

    items: list[OutboxEventResponse]
    next_cursor: UUID | None


class OutboxStatusCountResponse(BaseModel):
    """返回一种 Outbox 状态的事件数量。"""

    status: Literal["pending", "publishing", "published", "dead_letter"]
    count: int


class IntegrationInspectionResponse(BaseModel):
    """返回 Outbox 积压、Schema 与消费者幂等巡检摘要。"""

    checked_at: datetime
    status_counts: list[OutboxStatusCountResponse]
    oldest_pending_age_seconds: float
    expired_claim_count: int
    incompatible_schema_count: int
    replay_request_count: int
    consumer_receipt_count: int
    duplicate_delivery_count: int
    idempotency_issue_count: int

    @classmethod
    def from_domain(cls, inspection: IntegrationInspection) -> IntegrationInspectionResponse:
        return cls(
            checked_at=inspection.checked_at,
            status_counts=[
                OutboxStatusCountResponse(status=item.status, count=item.count)
                for item in inspection.status_counts
            ],
            oldest_pending_age_seconds=inspection.oldest_pending_age_seconds,
            expired_claim_count=inspection.expired_claim_count,
            incompatible_schema_count=inspection.incompatible_schema_count,
            replay_request_count=inspection.replay_request_count,
            consumer_receipt_count=inspection.consumer_receipt_count,
            duplicate_delivery_count=inspection.duplicate_delivery_count,
            idempotency_issue_count=inspection.idempotency_issue_count,
        )


class OutboxReplayBody(BaseModel):
    """接收人工重放所需的客户端幂等键和结构化原因码。"""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$",
    )
    reason_code: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]{2,63}$",
    )


class OutboxReplayResponse(BaseModel):
    """返回首次重放请求的不可变事实，重复幂等调用复用同一记录。"""

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
    requested_at: datetime

    @classmethod
    def from_domain(cls, request: OutboxReplayRequest) -> OutboxReplayResponse:
        """省略内部传播用 traceparent，仅返回稳定关联字段。"""

        return cls(
            replay_request_id=request.replay_request_id,
            workspace_id=request.workspace_id,
            event_id=request.event_id,
            idempotency_key=request.idempotency_key,
            reason_code=request.reason_code,
            source_status=request.source_status,
            source_attempt_count=request.source_attempt_count,
            source_published_at=request.source_published_at,
            source_error_code=request.source_error_code,
            requested_by_actor_id=request.requested_by_actor_id,
            requested_by_user_id=request.requested_by_user_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
            requested_at=request.requested_at,
        )
