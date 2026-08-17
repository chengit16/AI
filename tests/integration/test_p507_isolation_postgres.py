"""验证 P5-07 隔离资格、迁移状态、路由激活和数据库失败关闭。"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.enterprise import EnterpriseWorkspaceService
from ai_platform_api.modules.identity.infrastructure.enterprise_sqlalchemy import (
    SqlAlchemyEnterpriseUnitOfWork,
)
from ai_platform_api.modules.isolation.application.errors import (
    IsolationConflictError,
    IsolationNotConfiguredError,
    IsolationNotFoundError,
)
from ai_platform_api.modules.isolation.application.service import (
    MANAGE_PERMISSION,
    IsolationGovernanceService,
)
from ai_platform_api.persistence.tables import (
    workspace_entitlements,
    workspace_isolation_migration_plans,
    workspace_isolation_policy_versions,
    workspace_isolation_route_versions,
    workspaces,
)
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import DBAPIError

from tests.support.p502_quality import TRACE, RegisteredAccount, authorized_context
from tests.support.p503_quality import QualityEvaluationHarness
from tests.support.p507_isolation import (
    StaticIsolationComplianceSource,
    isolation_service,
)

pytest_plugins = ("tests.support.p503_quality_plugin",)


def _register(harness: QualityEvaluationHarness, identity: str) -> RegisteredAccount:
    result = harness.registration.register(
        login_name=f"synthetic.isolation.{identity}.{uuid4().hex}@example.com",
        display_name=f"合成隔离治理用户 {identity}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return RegisteredAccount(result.account_id, result.personal_workspace_id)


def _enterprise(
    harness: QualityEvaluationHarness,
    owner: RegisteredAccount,
) -> RegisteredAccount:
    summary = EnterpriseWorkspaceService(SqlAlchemyEnterpriseUnitOfWork(harness.sessions)).create(
        RequestContext.trusted(
            actor_id=owner.account_id,
            user_id=owner.account_id,
            workspace_id=owner.workspace_id,
            trace=TRACE,
            authentication_method="browser_session",
        ),
        name=f"合成隔离企业 {uuid4().hex[:8]}",
    )
    return RegisteredAccount(owner.account_id, summary.workspace_id)


def _advance_to_switch_ready(
    service: IsolationGovernanceService,
    context: RequestContext,
    migration_plan_id: UUID,
) -> None:
    for status in ("approved", "executing", "verifying", "switch_ready"):
        service.transition_migration(context, migration_plan_id, status)


def test_p507_personal_upgrade_is_denied_without_executable_plan(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = _register(quality_evaluation_database, "personal-denied")
    compliance = StaticIsolationComplianceSource()
    service = isolation_service(quality_evaluation_database.sessions, compliance)
    context = authorized_context(owner, MANAGE_PERMISSION)

    result = service.request_upgrade(context, "L2")

    assert result.policy.decision == "denied"
    assert result.migration_plan is None
    assert service.resolve_route(context).isolation_level == "L1"
    with quality_evaluation_database.sessions() as session:
        assert (
            session.scalar(
                select(workspace_isolation_policy_versions.c.decision).where(
                    workspace_isolation_policy_versions.c.isolation_policy_id
                    == result.policy.isolation_policy_id
                )
            )
            == "denied"
        )
        assert (
            session.scalar(
                select(workspace_isolation_migration_plans.c.migration_plan_id).where(
                    workspace_isolation_migration_plans.c.workspace_id == owner.workspace_id
                )
            )
            is None
        )


def test_p507_enterprise_l2_requires_approval_and_activates_server_route(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = _register(quality_evaluation_database, "enterprise-l2")
    enterprise = _enterprise(quality_evaluation_database, owner)
    compliance = StaticIsolationComplianceSource()
    service = isolation_service(quality_evaluation_database.sessions, compliance)
    context = authorized_context(enterprise, MANAGE_PERMISSION)
    result = service.request_upgrade(context, "L2")
    assert result.policy.decision == "allowed"
    assert result.migration_plan is not None
    plan = result.migration_plan

    invalid_route = {
        "route_id": uuid4(),
        "workspace_id": enterprise.workspace_id,
        "route_version": 1,
        "isolation_level": "L2",
        "database_route_key": "shared.primary",
        "object_storage_route_key": "shared.objects",
        "encryption_key_route_key": "shared.workspace",
        "search_namespace": f"workspace.{enterprise.workspace_id.hex}",
        "deployment_route_key": "shared.runtime",
        "migration_plan_id": plan.migration_plan_id,
        "route_digest": "d" * 64,
        "activated_by_actor_id": owner.account_id,
        "activated_at": plan.updated_at,
    }
    with pytest.raises(DBAPIError), quality_evaluation_database.sessions.begin() as session:
        session.execute(insert(workspace_isolation_route_versions).values(**invalid_route))
    with pytest.raises(DBAPIError), quality_evaluation_database.sessions.begin() as session:
        session.execute(
            delete(workspace_isolation_migration_plans).where(
                workspace_isolation_migration_plans.c.migration_plan_id == plan.migration_plan_id
            )
        )
    with pytest.raises(DBAPIError), quality_evaluation_database.sessions.begin() as session:
        session.execute(
            update(workspace_isolation_migration_plans)
            .where(
                workspace_isolation_migration_plans.c.migration_plan_id == plan.migration_plan_id
            )
            .values(status="switch_ready", version=2)
        )

    _advance_to_switch_ready(service, context, plan.migration_plan_id)
    wrong_workspace_route = {
        **invalid_route,
        "route_id": uuid4(),
        "search_namespace": f"workspace.{uuid4().hex}",
    }
    with pytest.raises(DBAPIError), quality_evaluation_database.sessions.begin() as session:
        session.execute(insert(workspace_isolation_route_versions).values(**wrong_workspace_route))
    route = service.activate_l2_route(context, plan.migration_plan_id)
    resolved = service.resolve_route(context)

    assert route.isolation_level == "L2"
    assert resolved == route
    assert route.database_route_key == "shared.primary"
    assert route.object_storage_route_key == "shared.objects"
    assert route.search_namespace == f"workspace.{enterprise.workspace_id.hex}"
    with quality_evaluation_database.sessions() as session:
        status = session.scalar(
            select(workspace_isolation_migration_plans.c.status).where(
                workspace_isolation_migration_plans.c.migration_plan_id == plan.migration_plan_id
            )
        )
    assert status == "completed"

    statements = (
        update(workspace_isolation_policy_versions)
        .where(
            workspace_isolation_policy_versions.c.isolation_policy_id
            == result.policy.isolation_policy_id
        )
        .values(decision="denied"),
        delete(workspace_isolation_route_versions).where(
            workspace_isolation_route_versions.c.route_id == route.route_id
        ),
    )
    for statement in statements:
        with pytest.raises(DBAPIError), quality_evaluation_database.sessions.begin() as session:
            session.execute(statement)


def test_p507_entitlement_drift_and_cross_workspace_plan_access_fail_closed(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = _register(quality_evaluation_database, "drift-owner")
    outsider = _register(quality_evaluation_database, "drift-outsider")
    enterprise = _enterprise(quality_evaluation_database, owner)
    compliance = StaticIsolationComplianceSource()
    service = isolation_service(quality_evaluation_database.sessions, compliance)
    context = authorized_context(enterprise, MANAGE_PERMISSION)
    result = service.request_upgrade(context, "L2")
    assert result.migration_plan is not None

    with quality_evaluation_database.sessions.begin() as session:
        session.execute(
            update(workspace_entitlements)
            .where(workspace_entitlements.c.workspace_id == enterprise.workspace_id)
            .values(plan_code="enterprise_isolated", version=2)
        )
        session.execute(
            update(workspaces)
            .where(workspaces.c.workspace_id == enterprise.workspace_id)
            .values(entitlement_version=2)
        )
    with pytest.raises(IsolationConflictError):
        service.transition_migration(
            context,
            result.migration_plan.migration_plan_id,
            "approved",
        )
    with pytest.raises(IsolationNotFoundError):
        service.transition_migration(
            authorized_context(outsider, MANAGE_PERMISSION),
            result.migration_plan.migration_plan_id,
            "approved",
        )


def test_p507_l3_requires_real_compliance_and_p508_target_resources(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = _register(quality_evaluation_database, "l3-not-configured")
    enterprise = _enterprise(quality_evaluation_database, owner)
    with quality_evaluation_database.sessions.begin() as session:
        session.execute(
            update(workspace_entitlements)
            .where(workspace_entitlements.c.workspace_id == enterprise.workspace_id)
            .values(plan_code="enterprise_isolated", version=2)
        )
        session.execute(
            update(workspaces)
            .where(workspaces.c.workspace_id == enterprise.workspace_id)
            .values(entitlement_version=2)
        )
    compliance = StaticIsolationComplianceSource()
    service = isolation_service(quality_evaluation_database.sessions, compliance)
    context = authorized_context(enterprise, MANAGE_PERMISSION)

    blocked = service.request_upgrade(context, "L3")
    assert blocked.policy.decision == "not_configured"
    assert blocked.migration_plan is None

    compliance.statuses["L3"] = "approved"
    allowed = service.request_upgrade(context, "L3")
    assert allowed.migration_plan is not None
    _advance_to_switch_ready(service, context, allowed.migration_plan.migration_plan_id)
    premature_route = {
        "route_id": uuid4(),
        "workspace_id": enterprise.workspace_id,
        "route_version": 1,
        "isolation_level": "L3",
        "database_route_key": f"database.{enterprise.workspace_id.hex}",
        "object_storage_route_key": f"objects.{enterprise.workspace_id.hex}",
        "encryption_key_route_key": f"key.{enterprise.workspace_id.hex}",
        "search_namespace": f"workspace.{enterprise.workspace_id.hex}",
        "deployment_route_key": "shared.runtime",
        "migration_plan_id": allowed.migration_plan.migration_plan_id,
        "route_digest": "e" * 64,
        "activated_by_actor_id": owner.account_id,
        "activated_at": allowed.migration_plan.updated_at,
    }
    with pytest.raises(DBAPIError), quality_evaluation_database.sessions.begin() as session:
        session.execute(insert(workspace_isolation_route_versions).values(**premature_route))
    with pytest.raises(IsolationNotConfiguredError):
        service.activate_l2_route(context, allowed.migration_plan.migration_plan_id)


def test_p507_rollback_required_keeps_single_active_plan(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = _register(quality_evaluation_database, "rollback-single-plan")
    enterprise = _enterprise(quality_evaluation_database, owner)
    service = isolation_service(
        quality_evaluation_database.sessions,
        StaticIsolationComplianceSource(),
    )
    context = authorized_context(enterprise, MANAGE_PERMISSION)
    result = service.request_upgrade(context, "L2")
    assert result.migration_plan is not None
    plan_id = result.migration_plan.migration_plan_id

    for status in ("approved", "executing", "rollback_required"):
        service.transition_migration(context, plan_id, status)
    with pytest.raises(IsolationConflictError):
        service.request_upgrade(context, "L2")

    service.transition_migration(context, plan_id, "rolled_back")
    retried = service.request_upgrade(context, "L2")
    assert retried.migration_plan is not None
    assert retried.migration_plan.migration_plan_id != plan_id
