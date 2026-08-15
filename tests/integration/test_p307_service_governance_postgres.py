"""验证 P3-07 服务、访问策略、路由历史和数据库防绕过闭环。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from ai_platform_api.modules.agent_control.domain.models import AgentRelease
from ai_platform_api.modules.service_governance.application.service import (
    ServiceDeniedError,
    ServiceGovernanceService,
    ServiceNotFoundError,
)
from ai_platform_api.modules.service_governance.infrastructure.sqlalchemy import (
    SqlAlchemyServiceGovernanceUnitOfWork,
)
from ai_platform_api.persistence.tables import (
    audit_records,
    outbox_events,
    service_access_policy_versions,
    service_control_requests,
    service_route_publications,
    service_routes,
    services,
)
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import DBAPIError

from tests.integration.test_p304_agent_evaluation_postgres import RegisteredAccount, context
from tests.integration.test_p305_agent_approval_postgres import (
    ApprovalHarness,
    approval_harness,
    register,
)
from tests.integration.test_p306_agent_release_postgres import approve_candidate


@pytest.fixture(scope="module")
def service_database() -> Iterator[ApprovalHarness]:
    """复用发布审批 Harness，在同一真实 Schema 验证服务治理。"""

    yield from approval_harness()


def governance(harness: ApprovalHarness) -> ServiceGovernanceService:
    return ServiceGovernanceService(SqlAlchemyServiceGovernanceUnitOfWork(harness.sessions))


def published_release(
    harness: ApprovalHarness,
    owner: RegisteredAccount,
    suffix: str,
) -> AgentRelease:
    """创建经过测试、审批和快照校验的自定义 Release。"""

    agents, candidate_id = approve_candidate(harness, owner, suffix)
    return agents.publish_release(
        context(owner),
        candidate_id=candidate_id,
        idempotency_key=f"synthetic-service-release-{suffix}",
    )


def test_custom_service_creation_is_atomic_idempotent_and_traceable(
    service_database: ApprovalHarness,
) -> None:
    """有效 Release 原子生成活动服务、策略、首个 Route、指针和事件。"""

    owner = register(service_database, "service-create-owner")
    release = published_release(service_database, owner, "create")
    service = governance(service_database)
    first = service.create_service(
        context(owner),
        name="  合成知识服务  ",
        release_id=release.release_id,
        visibility="restricted",
        allowed_account_ids=(owner.account_id,),
        idempotency_key="synthetic-service-create",
    )
    replayed = service.create_service(
        context(owner),
        name="合成知识服务",
        release_id=release.release_id,
        visibility="restricted",
        allowed_account_ids=(owner.account_id,),
        idempotency_key="synthetic-service-create",
    )

    assert replayed == first
    assert first.service.status == "active"
    assert first.service.version == 2
    assert first.service.agent_id == release.agent_id
    assert first.access_policy.version == 1
    assert first.access_policy.allowed_account_ids == (owner.account_id,)
    assert first.route.route_version == 1
    assert first.route.primary_release_id == release.release_id
    assert first.publication.generation == 1
    assert (
        service.get_service(
            context(owner),
            service_id=first.service.service_id,
        )
        == first
    )

    with service_database.sessions() as session:
        for table in (
            services,
            service_access_policy_versions,
            service_routes,
            service_route_publications,
            service_control_requests,
        ):
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(table)
                    .where(table.c.workspace_id == owner.workspace_id)
                )
                == 1
            )
        assert (
            session.scalar(
                select(func.count())
                .select_from(outbox_events)
                .where(
                    outbox_events.c.workspace_id == owner.workspace_id,
                    outbox_events.c.event_type == "service.state.changed",
                )
            )
            == 1
        )


def test_service_policy_and_state_updates_preserve_original_replay(
    service_database: ApprovalHarness,
) -> None:
    """策略更新产生新版本，暂停和恢复复用当前 Route，旧幂等响应保持不变。"""

    owner = register(service_database, "service-update-owner")
    release = published_release(service_database, owner, "update")
    service = governance(service_database)
    created = service.create_service(
        context(owner),
        name="合成状态服务",
        release_id=release.release_id,
        visibility="workspace",
        idempotency_key="synthetic-service-update-create",
    )
    suspended = service.update_service(
        context(owner),
        service_id=created.service.service_id,
        expected_version=2,
        target_status="suspended",
        visibility="restricted",
        allowed_account_ids=(owner.account_id,),
        idempotency_key="synthetic-service-suspend",
    )
    resumed = service.update_service(
        context(owner),
        service_id=created.service.service_id,
        expected_version=3,
        target_status="active",
        idempotency_key="synthetic-service-resume",
    )
    replayed_suspension = service.update_service(
        context(owner),
        service_id=created.service.service_id,
        expected_version=2,
        target_status="suspended",
        visibility="restricted",
        allowed_account_ids=(owner.account_id,),
        idempotency_key="synthetic-service-suspend",
    )

    assert suspended.service.status == "suspended"
    assert suspended.service.version == 3
    assert suspended.access_policy.version == 2
    assert resumed.service.status == "active"
    assert resumed.service.version == 4
    assert resumed.route == created.route
    assert replayed_suspension == suspended
    assert replayed_suspension != resumed

    denied = replace(context(owner), authorized_workspace=False)
    with pytest.raises(ServiceDeniedError):
        service.get_service(denied, service_id=created.service.service_id)
    outsider = register(service_database, "service-update-outsider")
    with pytest.raises(ServiceNotFoundError):
        service.get_service(context(outsider), service_id=created.service.service_id)


def test_database_rejects_cross_agent_route_and_history_mutation(
    service_database: ApprovalHarness,
) -> None:
    """数据库拒绝跨 Agent Route、非法状态跳转和历史策略或路由篡改。"""

    owner = register(service_database, "service-database-owner")
    first_release = published_release(service_database, owner, "database-first")
    second_release = published_release(service_database, owner, "database-second")
    deployment = governance(service_database).create_service(
        context(owner),
        name="合成防绕过服务",
        release_id=first_release.release_id,
        visibility="workspace",
        idempotency_key="synthetic-service-database",
    )

    # 1. 复合外键允许同空间 Release，但 Trigger 仍拒绝不同 Agent 的伪造 Route。
    with pytest.raises(DBAPIError), service_database.sessions.begin() as session:
        session.execute(
            insert(service_routes).values(
                route_id=uuid4(),
                service_id=deployment.service.service_id,
                workspace_id=owner.workspace_id,
                route_version=2,
                route_mode="active",
                primary_release_id=second_release.release_id,
                canary_release_id=None,
                canary_percent=0,
                previous_route_id=deployment.route.route_id,
                route_hash="f" * 64,
                created_by_account_id=owner.account_id,
                created_at=datetime.now(UTC),
            )
        )

    # 2. 访问策略和历史 Route 只能追加，维护 SQL 也不能直接覆盖。
    with pytest.raises(DBAPIError), service_database.sessions.begin() as session:
        session.execute(
            update(service_access_policy_versions)
            .where(
                service_access_policy_versions.c.access_policy_version_id
                == deployment.access_policy.access_policy_version_id
            )
            .values(policy_hash="e" * 64)
        )
    with pytest.raises(DBAPIError), service_database.sessions.begin() as session:
        session.execute(
            update(service_routes)
            .where(service_routes.c.route_id == deployment.route.route_id)
            .values(route_hash="e" * 64)
        )

    # 3. Service 不能回到 draft，当前指针也不能跳过 generation。
    with pytest.raises(DBAPIError), service_database.sessions.begin() as session:
        session.execute(
            update(services)
            .where(services.c.service_id == deployment.service.service_id)
            .values(status="draft", version=3, updated_at=datetime.now(UTC))
        )
    with pytest.raises(DBAPIError), service_database.sessions.begin() as session:
        session.execute(
            update(service_route_publications)
            .where(service_route_publications.c.service_id == deployment.service.service_id)
            .values(generation=3, published_at=datetime.now(UTC))
        )

    with service_database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(audit_records)
                .where(
                    audit_records.c.workspace_id == owner.workspace_id,
                    audit_records.c.resource_type == "service_definition",
                )
            )
            == 1
        )
