from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.entitlement_errors import (
    EntitlementGovernanceDeniedError,
    EntitlementNotFoundError,
    EntitlementValidationError,
)
from ai_platform_api.modules.identity.domain.enterprise import (
    WorkspaceMembership,
    WorkspaceRecord,
)
from ai_platform_api.modules.identity.domain.entitlements import (
    EntitlementRepository,
    EntitlementSnapshot,
    QuotaSnapshot,
    UsageCounter,
    UsageMetric,
    WorkspaceEntitlement,
    WorkspaceFeatureSettings,
)
from ai_platform_api.modules.identity.domain.models import WorkspaceStatus

LIFETIME_METRICS = frozenset({"storage_bytes", "knowledge_bases", "published_agents"})
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


def browser_account(context: RequestContext, workspace_id: UUID) -> UUID:
    if (
        context.user_id is None
        or context.authentication_method != "browser_session"
        or context.workspace_id != workspace_id
    ):
        raise EntitlementGovernanceDeniedError
    return context.user_id


def require_trusted_actor(context: RequestContext, workspace_id: UUID) -> None:
    if context.workspace_id != workspace_id:
        raise EntitlementGovernanceDeniedError


def require_owner(
    repository: EntitlementRepository,
    *,
    workspace_id: UUID,
    account_id: UUID,
) -> tuple[
    WorkspaceRecord,
    WorkspaceMembership,
    WorkspaceEntitlement,
    WorkspaceFeatureSettings,
]:
    workspace = repository.get_workspace(workspace_id, for_update=True)
    membership = repository.get_membership(workspace_id, account_id, for_update=True)
    entitlement = repository.get_entitlement(workspace_id, for_update=True)
    settings = repository.get_feature_settings(workspace_id, for_update=True)
    if (
        workspace is None
        or workspace.status != "active"
        or membership is None
        or membership.status != "active"
        or membership.membership_type != "owner"
        or entitlement is None
        or settings is None
    ):
        raise EntitlementGovernanceDeniedError
    return workspace, membership, entitlement, settings


def workspace_entitlement_version(
    repository: EntitlementRepository,
    workspace_id: UUID,
) -> int:
    version = repository.get_entitlement_version(workspace_id)
    if version is None:
        raise EntitlementNotFoundError
    return version


def month_key(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m")


def period_key(metric: UsageMetric, occurred_at: datetime) -> str:
    if metric in LIFETIME_METRICS:
        return "lifetime"
    month = month_key(occurred_at)
    if MONTH_PATTERN.fullmatch(month) is None:
        raise EntitlementValidationError
    return month


def entitlement_snapshot(
    *,
    workspace_status: WorkspaceStatus,
    entitlement_version: int,
    entitlement: WorkspaceEntitlement,
    settings: WorkspaceFeatureSettings,
    counters: tuple[UsageCounter, ...],
    member_count: int,
    current_month: str,
) -> EntitlementSnapshot:
    counter_by_key = {(item.metric, item.period_key): item.used_value for item in counters}
    quotas = (
        QuotaSnapshot("members", "lifetime", member_count, entitlement.max_members),
        QuotaSnapshot(
            "storage_bytes",
            "lifetime",
            counter_by_key.get(("storage_bytes", "lifetime"), 0),
            entitlement.max_storage_bytes,
        ),
        QuotaSnapshot(
            "knowledge_bases",
            "lifetime",
            counter_by_key.get(("knowledge_bases", "lifetime"), 0),
            entitlement.max_knowledge_bases,
        ),
        QuotaSnapshot(
            "published_agents",
            "lifetime",
            counter_by_key.get(("published_agents", "lifetime"), 0),
            entitlement.max_published_agents,
        ),
        QuotaSnapshot(
            "questions_monthly",
            current_month,
            counter_by_key.get(("questions_monthly", current_month), 0),
            entitlement.max_monthly_questions,
        ),
    )
    return EntitlementSnapshot(
        entitlement.workspace_id,
        workspace_status,
        entitlement.plan_code,
        entitlement_version,
        entitlement.open_api_allowed,
        settings.open_api_enabled,
        entitlement.public_publish_allowed,
        quotas,
    )


def entitlement_facts(
    *,
    context: RequestContext,
    workspace_id: UUID,
    aggregate_id: UUID,
    aggregate_version: int,
    event_type: str,
    action: str,
    resource_type: str,
    occurred_at: datetime,
    payload: dict[str, object],
) -> tuple[IntegrationEvent, AuditRecord]:
    return (
        IntegrationEvent(
            event_id=uuid4(),
            event_type=event_type,
            workspace_id=workspace_id,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=payload,
        ),
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=aggregate_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            attributes=payload,
        ),
    )
