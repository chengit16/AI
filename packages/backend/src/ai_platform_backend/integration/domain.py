"""定义集成事件、审计、Outbox 租约和任务发布领域端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID


@dataclass(frozen=True)
class IntegrationEvent:
    """定义跨进程传递的版本化业务事实及可信追踪元数据。"""

    event_id: UUID
    event_type: str
    workspace_id: UUID
    aggregate_id: UUID
    aggregate_version: int
    occurred_at: datetime
    trace_id: str
    traceparent: str
    payload: dict[str, object]
    actor_id: UUID | None = None
    user_id: UUID | None = None
    request_id: UUID | None = None
    schema_version: int = 1


@dataclass(frozen=True)
class AuditRecord:
    """记录工作空间内操作者、资源、结果和请求追踪信息。"""

    audit_id: UUID
    workspace_id: UUID
    actor_id: UUID
    user_id: UUID | None
    action: str
    resource_type: str
    resource_id: UUID
    outcome: str
    occurred_at: datetime
    request_id: UUID
    trace_id: str
    traceparent: str
    attributes: dict[str, object]


@dataclass(frozen=True)
class ClaimedOutboxEvent:
    """表示由指定 Worker 持有租约的一条待发布 Outbox 事件。"""

    event: IntegrationEvent
    attempt_count: int
    claimed_by: str
    claim_until: datetime


@dataclass(frozen=True)
class OutboxClaimBatch:
    """汇总本轮认领和认领开始时的积压事实，不包含事件载荷或资源标识。"""

    events: tuple[ClaimedOutboxEvent, ...]
    dead_lettered: int
    pending_count: int = 0
    oldest_pending_age_seconds: float = 0


class OutboxWriter(Protocol):
    """把集成事件写入当前业务事务，禁止先于业务提交发布。"""

    def add(self, event: IntegrationEvent) -> None: ...


class AuditWriter(Protocol):
    """把审计记录写入当前业务事务，敏感字段必须在调用前完成投影。"""

    def add(self, record: AuditRecord) -> None: ...


class TaskPublisher(Protocol):
    """发布已签名语言无关事件，Broker 异常由调度器决定重试。"""

    def publish(self, event: IntegrationEvent) -> None: ...


class OutboxLeaseStore(Protocol):
    """以租约领取到期事件，并只允许租约持有者回写发布或失败状态。"""

    def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
        max_attempts: int,
    ) -> OutboxClaimBatch: ...

    def mark_published(
        self,
        *,
        event_id: UUID,
        worker_id: str,
        published_at: datetime,
    ) -> bool: ...

    def mark_failed(
        self,
        *,
        event_id: UUID,
        worker_id: str,
        error_code: str,
        next_attempt_at: datetime,
        max_attempts: int,
    ) -> Literal["pending", "dead_letter", "lost_claim"]: ...
