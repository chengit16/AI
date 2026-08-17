"""编排隔离资格、迁移状态和 L2 服务端路由切换。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.isolation.application.errors import (
    IsolationConflictError,
    IsolationDeniedError,
    IsolationNotConfiguredError,
    IsolationNotFoundError,
    IsolationValidationError,
)
from ai_platform_api.modules.isolation.application.policy import (
    ISOLATION_REASON_CODES,
    MIGRATION_TRANSITIONS,
    evaluate_isolation_policy,
    level_index,
)
from ai_platform_api.modules.isolation.domain.models import (
    ComplianceStatus,
    InvalidIsolationMigrationTransitionError,
    IsolationComplianceSource,
    IsolationDecision,
    IsolationLevel,
    IsolationRepository,
    IsolationUnitOfWork,
    IsolationUpgradeResult,
    IsolationWriteConflictError,
    MigrationStatus,
    WorkspaceIsolationEntitlement,
    WorkspaceIsolationMigrationPlan,
    WorkspaceIsolationPolicyVersion,
    WorkspaceIsolationRoute,
)

ISOLATION_NAMESPACE = UUID("57000000-0000-4000-8000-000000000507")
MANAGE_PERMISSION = "workspace.isolation.manage"
READ_PERMISSIONS = frozenset({MANAGE_PERMISSION, "workspace.entitlement.read"})


class IsolationGovernanceService:
    """只接受目标等级请求，资格、状态和路由均由服务端权威事实决定。"""

    def __init__(
        self,
        unit_of_work: IsolationUnitOfWork,
        compliance_source: IsolationComplianceSource,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._compliance_source = compliance_source

    def request_upgrade(
        self,
        context: RequestContext,
        target_level: IsolationLevel,
    ) -> IsolationUpgradeResult:
        """记录资格版本，并且仅为允许的单调升级创建待批准计划。"""

        _require_manage(context)
        now = datetime.now(UTC)
        # 1. 锁定空间后读取 Identity 权威套餐和当前已完成路由，调用方不能提供这些事实。
        with self._unit_of_work as unit_of_work:
            unit_of_work.isolation.lock_workspace(context.workspace_id)
            entitlement = unit_of_work.isolation.get_entitlement(
                context.workspace_id,
                for_update=True,
            )
            if entitlement is None:
                raise IsolationDeniedError
            current_route = unit_of_work.isolation.get_current_route(context.workspace_id)
            current_level = current_route.isolation_level if current_route is not None else "L1"
            if level_index(target_level) <= level_index(current_level):
                raise IsolationValidationError
            if unit_of_work.isolation.get_active_migration_plan(context.workspace_id) is not None:
                raise IsolationConflictError

            # 2. 合规结果只能由受信策略端口提供；拒绝和未配置也形成不可变决策事实。
            compliance = self._compliance_source.assess(entitlement, target_level)
            evaluation = evaluate_isolation_policy(entitlement, target_level, compliance)
            if not set(evaluation.reason_codes).issubset(ISOLATION_REASON_CODES):
                raise IsolationValidationError
            policy = _policy(
                context,
                entitlement,
                target_level,
                current_level,
                evaluation.maximum_eligible_level,
                evaluation.compliance_status,
                evaluation.compliance_policy_digest,
                evaluation.decision,
                evaluation.reason_codes,
                policy_version=unit_of_work.isolation.next_policy_version(context.workspace_id),
                occurred_at=now,
            )
            plan = _migration_plan(context, policy, now) if policy.decision == "allowed" else None
            try:
                unit_of_work.isolation.add_policy(policy)
                if plan is not None:
                    unit_of_work.isolation.add_migration_plan(plan)
            except IsolationWriteConflictError as error:
                raise IsolationConflictError from error

            # 3. 决策、可选计划、审计和 Outbox 同事务提交，拒绝不能留下可执行计划。
            event, audit = _facts(
                context,
                aggregate_id=policy.isolation_policy_id,
                event_type="workspace.isolation.evaluated",
                action="workspace.isolation.evaluate",
                occurred_at=now,
                attributes={
                    "requested_level": target_level,
                    "current_level": current_level,
                    "decision": policy.decision,
                    "reason_codes": list(policy.reason_codes),
                    "migration_plan_id": str(plan.migration_plan_id) if plan else None,
                },
            )
            unit_of_work.audit.add(audit)
            unit_of_work.outbox.add(event)
            unit_of_work.commit()
            return IsolationUpgradeResult(policy, plan)

    def transition_migration(
        self,
        context: RequestContext,
        migration_plan_id: UUID,
        target_status: MigrationStatus,
    ) -> WorkspaceIsolationMigrationPlan:
        """重验批准资格并按固定状态机推进迁移，不执行物理数据复制。"""

        _require_manage(context)
        if target_status == "completed":
            raise IsolationValidationError
        now = datetime.now(UTC)
        # 1. 在空间锁内读取本空间计划，批准动作还必须重验套餐与当前路由身份。
        with self._unit_of_work as unit_of_work:
            unit_of_work.isolation.lock_workspace(context.workspace_id)
            plan = unit_of_work.isolation.get_migration_plan(
                context.workspace_id,
                migration_plan_id,
                for_update=True,
            )
            if plan is None:
                raise IsolationNotFoundError
            if target_status == "approved":
                _revalidate_approval(unit_of_work.isolation, plan)
            # 2. Domain 状态机和数据库 Trigger 双重约束转换与乐观版本递增。
            try:
                updated = plan.transition(
                    target_status,
                    actor_id=context.actor_id,
                    occurred_at=now,
                    allowed=MIGRATION_TRANSITIONS,
                )
            except InvalidIsolationMigrationTransitionError as error:
                raise IsolationConflictError from error
            try:
                saved = unit_of_work.isolation.save_migration_plan(
                    updated,
                    expected_version=plan.version,
                )
            except IsolationWriteConflictError as error:
                raise IsolationConflictError from error
            if not saved:
                raise IsolationConflictError
            # 3. 状态、审计与递增聚合版本事件在同一事务提交。
            event, audit = _facts(
                context,
                aggregate_id=plan.migration_plan_id,
                event_type="workspace.isolation.migration_transitioned",
                action="workspace.isolation.migration.transition",
                occurred_at=now,
                aggregate_version=updated.version,
                attributes={"from_status": plan.status, "to_status": target_status},
            )
            unit_of_work.audit.add(audit)
            unit_of_work.outbox.add(event)
            unit_of_work.commit()
            return updated

    def activate_l2_route(
        self,
        context: RequestContext,
        migration_plan_id: UUID,
    ) -> WorkspaceIsolationRoute:
        """为 `switch_ready` 的 L2 计划生成路由，并与完成状态原子提交。"""

        _require_manage(context)
        now = datetime.now(UTC)
        # 1. 锁定并核对本空间 L2 计划、就绪状态和当前权威路由。
        with self._unit_of_work as unit_of_work:
            unit_of_work.isolation.lock_workspace(context.workspace_id)
            plan = unit_of_work.isolation.get_migration_plan(
                context.workspace_id,
                migration_plan_id,
                for_update=True,
            )
            if plan is None:
                raise IsolationNotFoundError
            if plan.target_level in {"L3", "L4"}:
                raise IsolationNotConfiguredError
            if plan.target_level != "L2" or plan.status != "switch_ready":
                raise IsolationConflictError
            current = unit_of_work.isolation.get_current_route(context.workspace_id)
            current_level = current.isolation_level if current is not None else "L1"
            if current_level != plan.from_level:
                raise IsolationConflictError
            # 2. 路由键完全由服务端生成，搜索命名空间与可信工作空间精确绑定。
            route = _l2_route(
                context,
                plan,
                route_version=unit_of_work.isolation.next_route_version(context.workspace_id),
                occurred_at=now,
            )
            completed = plan.transition(
                "completed",
                actor_id=context.actor_id,
                occurred_at=now,
                allowed=MIGRATION_TRANSITIONS,
            )
            # 路由插入 Trigger 要求计划仍为 switch_ready，随后同事务推进 completed 后才可被读取。
            try:
                unit_of_work.isolation.add_route(route)
                saved = unit_of_work.isolation.save_migration_plan(
                    completed,
                    expected_version=plan.version,
                )
            except IsolationWriteConflictError as error:
                raise IsolationConflictError from error
            if not saved:
                raise IsolationConflictError
            # 3. 新路由、完成状态、审计和事件原子提交，避免出现两个权威位置。
            event, audit = _facts(
                context,
                aggregate_id=route.route_id,
                event_type="workspace.isolation.route_activated",
                action="workspace.isolation.route.activate",
                occurred_at=now,
                attributes={
                    "isolation_level": route.isolation_level,
                    "route_version": route.route_version,
                    "migration_plan_id": str(plan.migration_plan_id),
                },
            )
            unit_of_work.audit.add(audit)
            unit_of_work.outbox.add(event)
            unit_of_work.commit()
            return route

    def resolve_route(self, context: RequestContext) -> WorkspaceIsolationRoute:
        """仅按可信工作空间解析当前路由，不接收等级或路由覆盖参数。"""

        _require_read(context)
        with self._unit_of_work as unit_of_work:
            entitlement = unit_of_work.isolation.get_entitlement(context.workspace_id)
            if entitlement is None or entitlement.workspace_status != "active":
                raise IsolationDeniedError
            route = unit_of_work.isolation.get_current_route(context.workspace_id)
            return route or _default_l1_route(context.workspace_id)


def _policy(
    context: RequestContext,
    entitlement: WorkspaceIsolationEntitlement,
    target_level: IsolationLevel,
    current_level: IsolationLevel,
    maximum_level: IsolationLevel,
    compliance_status: ComplianceStatus,
    compliance_policy_digest: str | None,
    decision: IsolationDecision,
    reason_codes: tuple[str, ...],
    *,
    policy_version: int,
    occurred_at: datetime,
) -> WorkspaceIsolationPolicyVersion:
    document = {
        "workspace_id": str(context.workspace_id),
        "policy_version": policy_version,
        "requested_level": target_level,
        "current_level": current_level,
        "maximum_eligible_level": maximum_level,
        "plan_code": entitlement.plan_code,
        "entitlement_version": entitlement.entitlement_version,
        "compliance_status": compliance_status,
        "compliance_policy_digest": compliance_policy_digest,
        "decision": decision,
        "reason_codes": list(reason_codes),
    }
    digest = _digest(document)
    return WorkspaceIsolationPolicyVersion(
        isolation_policy_id=uuid5(ISOLATION_NAMESPACE, digest),
        workspace_id=context.workspace_id,
        policy_version=policy_version,
        requested_level=target_level,
        current_level=current_level,
        maximum_eligible_level=maximum_level,
        plan_code=entitlement.plan_code,
        entitlement_version=entitlement.entitlement_version,
        compliance_status=compliance_status,
        compliance_policy_digest=compliance_policy_digest,
        decision=decision,
        reason_codes=reason_codes,
        decision_digest=digest,
        created_by_actor_id=context.actor_id,
        created_at=occurred_at,
    )


def _migration_plan(
    context: RequestContext,
    policy: WorkspaceIsolationPolicyVersion,
    occurred_at: datetime,
) -> WorkspaceIsolationMigrationPlan:
    requirements = {
        "workspace_id": str(context.workspace_id),
        "from_level": policy.current_level,
        "target_level": policy.requested_level,
        "single_writer": True,
        "long_term_dual_write": False,
        "required_resources": _required_resources(policy.requested_level),
    }
    digest = _digest(requirements)
    return WorkspaceIsolationMigrationPlan(
        migration_plan_id=uuid5(ISOLATION_NAMESPACE, f"plan:{policy.decision_digest}"),
        workspace_id=context.workspace_id,
        isolation_policy_id=policy.isolation_policy_id,
        from_level=policy.current_level,
        target_level=policy.requested_level,
        status="planned",
        route_requirement_digest=digest,
        created_by_actor_id=context.actor_id,
        created_at=occurred_at,
        updated_by_actor_id=context.actor_id,
        updated_at=occurred_at,
        version=1,
    )


def _l2_route(
    context: RequestContext,
    plan: WorkspaceIsolationMigrationPlan,
    *,
    route_version: int,
    occurred_at: datetime,
) -> WorkspaceIsolationRoute:
    document = {
        "workspace_id": str(context.workspace_id),
        "route_version": route_version,
        "isolation_level": "L2",
        "database_route_key": "shared.primary",
        "object_storage_route_key": "shared.objects",
        "encryption_key_route_key": "shared.workspace",
        "search_namespace": f"workspace.{context.workspace_id.hex}",
        "deployment_route_key": "shared.runtime",
        "migration_plan_id": str(plan.migration_plan_id),
    }
    digest = _digest(document)
    return WorkspaceIsolationRoute(
        route_id=uuid5(ISOLATION_NAMESPACE, f"route:{digest}"),
        workspace_id=context.workspace_id,
        route_version=route_version,
        isolation_level="L2",
        database_route_key="shared.primary",
        object_storage_route_key="shared.objects",
        encryption_key_route_key="shared.workspace",
        search_namespace=f"workspace.{context.workspace_id.hex}",
        deployment_route_key="shared.runtime",
        migration_plan_id=plan.migration_plan_id,
        route_digest=digest,
        activated_by_actor_id=context.actor_id,
        activated_at=occurred_at,
    )


def _default_l1_route(workspace_id: UUID) -> WorkspaceIsolationRoute:
    document = {
        "workspace_id": str(workspace_id),
        "route_version": 0,
        "isolation_level": "L1",
        "database_route_key": "shared.primary",
        "object_storage_route_key": "shared.objects",
        "encryption_key_route_key": "shared.workspace",
        "search_namespace": "shared.search",
        "deployment_route_key": "shared.runtime",
        "migration_plan_id": None,
    }
    digest = _digest(document)
    return WorkspaceIsolationRoute(
        route_id=uuid5(ISOLATION_NAMESPACE, f"route:{digest}"),
        workspace_id=workspace_id,
        route_version=0,
        isolation_level="L1",
        database_route_key="shared.primary",
        object_storage_route_key="shared.objects",
        encryption_key_route_key="shared.workspace",
        search_namespace="shared.search",
        deployment_route_key="shared.runtime",
        migration_plan_id=None,
        route_digest=digest,
        activated_by_actor_id=workspace_id,
        activated_at=datetime(1970, 1, 1, tzinfo=UTC),
    )


def _revalidate_approval(
    repository: IsolationRepository,
    plan: WorkspaceIsolationMigrationPlan,
) -> None:
    entitlement = repository.get_entitlement(plan.workspace_id, for_update=True)
    policy = repository.get_policy(plan.workspace_id, plan.isolation_policy_id)
    route = repository.get_current_route(plan.workspace_id)
    current_level = route.isolation_level if route is not None else "L1"
    if (
        entitlement is None
        or policy is None
        or entitlement.workspace_status != "active"
        or entitlement.plan_code != policy.plan_code
        or entitlement.entitlement_version != policy.entitlement_version
        or policy.decision != "allowed"
        or current_level != plan.from_level
    ):
        raise IsolationConflictError


def _required_resources(level: IsolationLevel) -> tuple[str, ...]:
    return {
        "L1": ("workspace_filter",),
        "L2": ("vector_namespace", "keyword_namespace"),
        "L3": ("database", "object_bucket", "encryption_key"),
        "L4": ("independent_deployment",),
    }[level]


def _facts(
    context: RequestContext,
    *,
    aggregate_id: UUID,
    event_type: str,
    action: str,
    occurred_at: datetime,
    aggregate_version: int = 1,
    attributes: dict[str, object],
) -> tuple[IntegrationEvent, AuditRecord]:
    return (
        IntegrationEvent(
            event_id=uuid4(),
            event_type=event_type,
            workspace_id=context.workspace_id,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=attributes,
        ),
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type="workspace_isolation",
            resource_id=aggregate_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes=attributes,
        ),
    )


def _require_manage(context: RequestContext) -> None:
    if (
        not context.authorized_workspace
        or context.authorized_permission_code != MANAGE_PERMISSION
        or context.audit_authorization is None
    ):
        raise IsolationDeniedError


def _require_read(context: RequestContext) -> None:
    if (
        not context.authorized_workspace
        or context.authorized_permission_code not in READ_PERMISSIONS
        or context.audit_authorization is None
    ):
        raise IsolationDeniedError


def _digest(document: object) -> str:
    try:
        payload = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    except (TypeError, ValueError) as error:
        raise IsolationValidationError from error
    return hashlib.sha256(payload).hexdigest()
