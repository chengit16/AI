"""在业务事务内执行套餐用量预留、确认和回滚。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.entitlement_errors import (
    EntitlementConflictError,
    EntitlementGovernanceDeniedError,
    EntitlementValidationError,
    QuotaExceededError,
)
from ai_platform_api.modules.identity.application.entitlement_support import (
    IDEMPOTENCY_PATTERN,
    entitlement_facts,
    period_key,
    require_trusted_actor,
)
from ai_platform_api.modules.identity.domain.entitlements import (
    UsageCounter,
    UsageMetric,
    UsageRecord,
    UsageRepository,
)


@dataclass(frozen=True)
class UsageMutation:
    """描述一次用量变更及其待同事务写入的审计和事件事实。"""

    record: UsageRecord
    event: IntegrationEvent | None
    audit: AuditRecord | None

    @property
    def created(self) -> bool:
        return self.event is not None and self.audit is not None


def consume_usage(
    repository: UsageRepository,
    *,
    context: RequestContext,
    workspace_id: UUID,
    metric: UsageMetric,
    delta_value: int,
    idempotency_key: str,
    occurred_at: datetime | None = None,
) -> UsageMutation:
    """只修改当前事务中的用量事实，提交和 Outbox 写入仍由调用用例负责。"""

    # 1. 先验证可信主体、计费周期和幂等键，非法扣减不能进入锁定计数器阶段。
    require_trusted_actor(context, workspace_id)
    now = occurred_at or datetime.now(UTC)
    usage_period_key = period_key(metric, now)
    if (
        delta_value == 0
        or (metric == "questions_monthly" and delta_value < 0)
        or IDEMPOTENCY_PATTERN.fullmatch(idempotency_key) is None
    ):
        raise EntitlementValidationError

    workspace = repository.get_workspace(workspace_id, for_update=True)
    entitlement = repository.get_entitlement(workspace_id, for_update=True)
    if workspace is None or workspace.status != "active" or entitlement is None:
        raise EntitlementGovernanceDeniedError
    # 2. 相同幂等键只允许完全相同的计量事实；首次写入再锁定周期计数器计算额度。
    previous = repository.get_usage_record(workspace_id, idempotency_key)
    if previous is not None:
        if (
            previous.metric != metric
            or previous.period_key != usage_period_key
            or previous.delta_value != delta_value
        ):
            raise EntitlementConflictError
        return UsageMutation(previous, None, None)

    current = repository.get_usage_counter(
        workspace_id,
        metric,
        usage_period_key,
        for_update=True,
    )
    current_value = current.used_value if current is not None else 0
    resulting_value = current_value + delta_value
    if resulting_value < 0 or resulting_value > entitlement.limit_for(metric):
        raise QuotaExceededError
    # 3. 计数器、明细、审计和 Outbox 事实由调用方在同一事务中持久化。
    counter = UsageCounter(
        workspace_id,
        metric,
        usage_period_key,
        resulting_value,
        now,
        (current.version + 1) if current is not None else 1,
    )
    record = UsageRecord(
        uuid4(),
        workspace_id,
        metric,
        usage_period_key,
        idempotency_key,
        delta_value,
        resulting_value,
        now,
    )
    repository.save_usage_counter(
        counter,
        expected_version=current.version if current is not None else None,
    )
    repository.add_usage_record(record)
    event, audit = entitlement_facts(
        context=context,
        workspace_id=workspace_id,
        aggregate_id=record.usage_record_id,
        aggregate_version=counter.version,
        event_type="workspace.usage.recorded",
        action="workspace.usage.record",
        resource_type="workspace_usage",
        occurred_at=now,
        payload={
            "metric": metric,
            "period_key": usage_period_key,
            "delta_value": delta_value,
            "resulting_value": resulting_value,
        },
    )
    return UsageMutation(record, event, audit)
