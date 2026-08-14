from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.entitlement_errors import (
    EntitlementConflictError,
    EntitlementFeatureDeniedError,
    EntitlementGovernanceDeniedError,
)
from ai_platform_api.modules.identity.application.entitlement_support import (
    browser_account,
    entitlement_facts,
    entitlement_snapshot,
    month_key,
    require_owner,
    workspace_entitlement_version,
)
from ai_platform_api.modules.identity.application.usage import consume_usage
from ai_platform_api.modules.identity.domain.entitlements import (
    EntitlementSnapshot,
    EntitlementUnitOfWork,
    EntitlementWriteConflictError,
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

        try:
            with self._unit_of_work as unit_of_work:
                mutation = consume_usage(
                    unit_of_work.entitlements,
                    context=context,
                    workspace_id=workspace_id,
                    metric=metric,
                    delta_value=delta_value,
                    idempotency_key=idempotency_key,
                    occurred_at=occurred_at,
                )
                if mutation.created:
                    if mutation.audit is None or mutation.event is None:
                        raise RuntimeError("用量变更事实不完整")
                    unit_of_work.audit.add(mutation.audit)
                    unit_of_work.outbox.add(mutation.event)
                    unit_of_work.commit()
                return mutation.record
        except EntitlementWriteConflictError as error:
            raise EntitlementConflictError from error
