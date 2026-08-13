from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.entitlement_errors import (
    EntitlementConflictError,
    EntitlementFeatureDeniedError,
    EntitlementGovernanceDeniedError,
    EntitlementValidationError,
    QuotaExceededError,
)
from ai_platform_api.modules.identity.application.entitlement_support import (
    IDEMPOTENCY_PATTERN,
    browser_account,
    entitlement_facts,
    entitlement_snapshot,
    month_key,
    period_key,
    require_owner,
    require_trusted_actor,
    workspace_entitlement_version,
)
from ai_platform_api.modules.identity.domain.entitlements import (
    EntitlementSnapshot,
    EntitlementUnitOfWork,
    EntitlementWriteConflictError,
    UsageCounter,
    UsageMetric,
    UsageRecord,
)

__all__ = ["EntitlementService", "EntitlementSnapshot"]


class EntitlementService:
    """集中提供套餐读取、功能开关和后续业务模块使用的原子配额入口。"""

    def __init__(self, unit_of_work: EntitlementUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def get_snapshot(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        now: datetime | None = None,
    ) -> EntitlementSnapshot:
        account_id = browser_account(context, workspace_id)
        current_time = now or datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            workspace = unit_of_work.entitlements.get_workspace(workspace_id)
            membership = unit_of_work.entitlements.get_membership(workspace_id, account_id)
            entitlement = unit_of_work.entitlements.get_entitlement(workspace_id)
            settings = unit_of_work.entitlements.get_feature_settings(workspace_id)
            if (
                workspace is None
                or membership is None
                or membership.status != "active"
                or entitlement is None
                or settings is None
            ):
                raise EntitlementGovernanceDeniedError
            counters = unit_of_work.entitlements.list_usage_counters(workspace_id)
            member_count = unit_of_work.entitlements.active_member_count(workspace_id)
            return entitlement_snapshot(
                workspace_status=workspace.status,
                entitlement_version=workspace_entitlement_version(
                    unit_of_work.entitlements, workspace_id
                ),
                entitlement=entitlement,
                settings=settings,
                counters=counters,
                member_count=member_count,
                current_month=month_key(current_time),
            )

    def set_open_api_enabled(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        enabled: bool,
    ) -> EntitlementSnapshot:
        account_id = browser_account(context, workspace_id)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                workspace, _, entitlement, settings = require_owner(
                    unit_of_work.entitlements,
                    workspace_id=workspace_id,
                    account_id=account_id,
                )
                if enabled and not entitlement.open_api_allowed:
                    raise EntitlementFeatureDeniedError
                if settings.open_api_enabled != enabled:
                    updated = replace(
                        settings,
                        open_api_enabled=enabled,
                        updated_at=now,
                        version=settings.version + 1,
                    )
                    unit_of_work.entitlements.set_open_api_enabled(
                        updated,
                        expected_version=settings.version,
                    )
                    entitlement_version = unit_of_work.entitlements.bump_entitlement_version(
                        workspace_id
                    )
                    event, audit = entitlement_facts(
                        context=context,
                        workspace_id=workspace_id,
                        aggregate_id=workspace_id,
                        aggregate_version=entitlement_version,
                        event_type="workspace.feature.open_api.updated",
                        action="workspace.feature.open_api.update",
                        resource_type="workspace_entitlement",
                        occurred_at=now,
                        payload={"open_api_enabled": enabled},
                    )
                    unit_of_work.audit.add(audit)
                    unit_of_work.outbox.add(event)
                    unit_of_work.commit()
                    settings = updated
                else:
                    entitlement_version = workspace_entitlement_version(
                        unit_of_work.entitlements, workspace_id
                    )
                return entitlement_snapshot(
                    workspace_status=workspace.status,
                    entitlement_version=entitlement_version,
                    entitlement=entitlement,
                    settings=settings,
                    counters=unit_of_work.entitlements.list_usage_counters(workspace_id),
                    member_count=unit_of_work.entitlements.active_member_count(workspace_id),
                    current_month=month_key(now),
                )
        except EntitlementWriteConflictError as error:
            raise EntitlementConflictError from error

    def consume(
        self,
        *,
        context: RequestContext,
        workspace_id: UUID,
        metric: UsageMetric,
        delta_value: int,
        idempotency_key: str,
        occurred_at: datetime | None = None,
    ) -> UsageRecord:
        """业务模块只能通过该入口调整用量，不能先检查再异步写计数器。"""

        require_trusted_actor(context, workspace_id)
        now = occurred_at or datetime.now(UTC)
        usage_period_key = period_key(metric, now)
        if (
            delta_value == 0
            or (metric == "questions_monthly" and delta_value < 0)
            or IDEMPOTENCY_PATTERN.fullmatch(idempotency_key) is None
        ):
            raise EntitlementValidationError
        try:
            with self._unit_of_work as unit_of_work:
                workspace = unit_of_work.entitlements.get_workspace(workspace_id, for_update=True)
                entitlement = unit_of_work.entitlements.get_entitlement(
                    workspace_id, for_update=True
                )
                if workspace is None or workspace.status != "active" or entitlement is None:
                    raise EntitlementGovernanceDeniedError
                previous = unit_of_work.entitlements.get_usage_record(workspace_id, idempotency_key)
                if previous is not None:
                    if (
                        previous.metric != metric
                        or previous.period_key != usage_period_key
                        or previous.delta_value != delta_value
                    ):
                        raise EntitlementConflictError
                    return previous
                current = unit_of_work.entitlements.get_usage_counter(
                    workspace_id,
                    metric,
                    usage_period_key,
                    for_update=True,
                )
                current_value = current.used_value if current is not None else 0
                resulting_value = current_value + delta_value
                if resulting_value < 0 or resulting_value > entitlement.limit_for(metric):
                    raise QuotaExceededError
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
                unit_of_work.entitlements.save_usage_counter(
                    counter,
                    expected_version=current.version if current is not None else None,
                )
                unit_of_work.entitlements.add_usage_record(record)
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
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
                return record
        except EntitlementWriteConflictError as error:
            raise EntitlementConflictError from error
