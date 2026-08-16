"""验证 P3-10 三类服务出口在 PostgreSQL、Valkey、HTTP 和 SSE 上的完整边界。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.config import Settings, get_settings
from ai_platform_api.modules.assistant.api.routes import assistant_run_executor
from ai_platform_api.modules.assistant.application.errors import AssistantNotFoundError
from ai_platform_api.modules.assistant.application.runner import AssistantRunExecutor
from ai_platform_api.modules.assistant.application.service import AssistantConversationService
from ai_platform_api.modules.assistant.infrastructure.sqlalchemy import (
    SqlAlchemyAssistantUnitOfWork,
)
from ai_platform_api.modules.identity.api.dependencies import trusted_request_context
from ai_platform_api.modules.identity.application.entitlement_errors import QuotaExceededError
from ai_platform_api.modules.identity.application.organization import OrganizationService
from ai_platform_api.modules.identity.infrastructure.entitlements_sqlalchemy import (
    SqlAlchemyEntitlementRepository,
)
from ai_platform_api.modules.identity.infrastructure.organization_sqlalchemy import (
    SqlAlchemyOrganizationUnitOfWork,
)
from ai_platform_api.modules.service_delivery.api.routes import router as service_delivery_router
from ai_platform_api.modules.service_delivery.application.errors import (
    ServiceInvocationDeniedError,
    ServiceInvocationRateLimitedError,
)
from ai_platform_api.modules.service_delivery.application.service import ServiceInvocationService
from ai_platform_api.modules.service_delivery.infrastructure.valkey import (
    ValkeyInvocationRateLimiter,
)
from ai_platform_api.modules.service_governance.application.service import (
    ServiceGovernanceService,
)
from ai_platform_api.modules.service_governance.domain.models import (
    ServiceDeployment,
    ServiceType,
)
from ai_platform_api.modules.service_governance.infrastructure.sqlalchemy import (
    SqlAlchemyServiceGovernanceUnitOfWork,
    SqlAlchemyServiceRepository,
)
from ai_platform_api.modules.service_runtime.application.loader import RuntimeReleaseLoader
from ai_platform_api.modules.service_runtime.domain.models import (
    RuntimeReleaseSnapshot,
    RuntimeSnapshotCache,
)
from ai_platform_api.modules.service_runtime.infrastructure.sqlalchemy import (
    SqlAlchemyRuntimeSnapshotSource,
)
from ai_platform_api.modules.streaming.application.service import TransactionalStreamService
from ai_platform_api.modules.streaming.domain.models import StreamPolicy
from ai_platform_api.modules.streaming.infrastructure.sqlalchemy import (
    SqlAlchemyStreamUnitOfWork,
)
from ai_platform_api.modules.workflow.domain.approval_runtime import ApprovalRuntimeCommand
from ai_platform_api.modules.workflow.domain.approvals import (
    ApprovalApproverSource,
    ApprovalLevelDefinition,
    ApprovalPolicyDefinition,
)
from ai_platform_api.persistence.tables import (
    assistant_runs,
    conversations,
    workspace_entitlements,
    workspace_usage_records,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from tests.integration.test_p304_agent_evaluation_postgres import RegisteredAccount, context
from tests.integration.test_p305_agent_approval_postgres import (
    ApprovalHarness,
    add_enterprise_member,
    approval_harness,
    prepare_candidate,
    register,
)
from tests.integration.test_p307_service_governance_postgres import published_release

DEFAULT_VALKEY_URL = "redis://127.0.0.1:6379/10"
SERVICE_PERMISSION = "service.definition.read"


class NoopRuntimeCache:
    """让集成测试始终从 PostgreSQL 发布事实读取，缓存行为已由 P3-08/P3-09 专项覆盖。"""

    def get_current(
        self,
        workspace_id: UUID,
        service_id: UUID,
        assignment_bucket: int,
    ) -> RuntimeReleaseSnapshot | None:
        del workspace_id, service_id, assignment_bucket
        return None

    def get_bound(
        self,
        workspace_id: UUID,
        service_id: UUID,
        route_id: UUID,
        service_route_version: int,
        agent_release_id: UUID,
    ) -> RuntimeReleaseSnapshot | None:
        del workspace_id, service_id, route_id, service_route_version, agent_release_id
        return None

    def put_current(self, snapshot: RuntimeReleaseSnapshot, assignment_bucket: int) -> None:
        del snapshot, assignment_bucket

    def put_bound(self, snapshot: RuntimeReleaseSnapshot) -> None:
        del snapshot

    def delete_current(self, workspace_id: UUID, service_id: UUID) -> None:
        del workspace_id, service_id

    def delete_bound(
        self,
        workspace_id: UUID,
        service_id: UUID,
        route_id: UUID,
        service_route_version: int,
        agent_release_id: UUID,
    ) -> None:
        del workspace_id, service_id, route_id, service_route_version, agent_release_id

    def close(self) -> None:
        pass


class AllowRateLimiter:
    """只隔离非限流测试的 Valkey 状态；真实原子限流由独立用例验证。"""

    def consume(
        self,
        workspace_id: UUID,
        service_id: UUID,
        actor_id: UUID,
        idempotency_key: str,
    ) -> None:
        del workspace_id, service_id, actor_id, idempotency_key

    def close(self) -> None:
        pass


class RecordingExecutor:
    """记录 HTTP 后台调度，不调用模型或检索，避免把供应商能力混入出口验收。"""

    def __init__(self) -> None:
        self.run_ids: list[UUID] = []

    def execute(self, context: RequestContext, run_id: UUID) -> None:
        del context
        self.run_ids.append(run_id)


@dataclass(frozen=True)
class ServiceDeliveryHarness:
    """集中持有 P3-10 所需的发布、调用、助手、流式和组织服务。"""

    approval: ApprovalHarness
    unit_of_work: SqlAlchemyAssistantUnitOfWork
    runtime: RuntimeReleaseLoader
    invocations: ServiceInvocationService
    assistant: AssistantConversationService
    streams: TransactionalStreamService
    organization: OrganizationService


@pytest.fixture(scope="module")
def service_delivery_database() -> Iterator[ServiceDeliveryHarness]:
    """在独立 Schema 装配统一调用链，结束后由既有审批 Harness 删除全部事实。"""

    for approval in approval_harness():
        unit_of_work = SqlAlchemyAssistantUnitOfWork(
            approval.sessions,
            SqlAlchemyServiceRepository,
            SqlAlchemyEntitlementRepository,
        )
        runtime = RuntimeReleaseLoader(
            SqlAlchemyRuntimeSnapshotSource(approval.sessions),
            cast(RuntimeSnapshotCache, NoopRuntimeCache()),
        )
        streams = TransactionalStreamService(
            SqlAlchemyStreamUnitOfWork(approval.sessions),
            StreamPolicy(),
        )
        try:
            yield ServiceDeliveryHarness(
                approval=approval,
                unit_of_work=unit_of_work,
                runtime=runtime,
                invocations=ServiceInvocationService(
                    unit_of_work,
                    runtime,
                    AllowRateLimiter(),
                ),
                assistant=AssistantConversationService(unit_of_work),
                streams=streams,
                organization=OrganizationService(
                    SqlAlchemyOrganizationUnitOfWork(approval.sessions)
                ),
            )
        finally:
            streams.close()
            runtime.close()


def test_three_surfaces_share_postgres_route_policy_and_quota_chain(
    service_delivery_database: ServiceDeliveryHarness,
) -> None:
    """三类服务只接受对应认证 surface，成功调用共享同一组冻结事实与用量账本。"""

    harness = service_delivery_database
    owner = register(harness.approval, "delivery-surfaces")
    release = published_release(harness.approval, owner, "delivery-surfaces")
    governance = _governance(harness)
    service_types: tuple[ServiceType, ...] = (
        "custom_knowledge_agent",
        "scenario_application",
        "open_api",
    )
    services = {
        service_type: governance.create_service(
            context(owner),
            name=f"合成 {service_type} 服务",
            release_id=release.release_id,
            service_type=service_type,
            visibility="workspace",
            idempotency_key=f"synthetic-p310-create-{service_type}",
        )
        for service_type in service_types
    }
    browser = _browser_context(owner)
    api = _api_context(owner, services["open_api"].service.service_id)

    custom = harness.invocations.invoke(
        browser,
        service_id=services["custom_knowledge_agent"].service.service_id,
        texts=("合成知识 Agent 问题",),
        idempotency_key="synthetic-p310-custom-invoke",
    )
    scenario = harness.invocations.invoke(
        browser,
        service_id=services["scenario_application"].service.service_id,
        texts=("合成场景应用问题",),
        idempotency_key="synthetic-p310-scenario-invoke",
    )
    open_api = harness.invocations.invoke(
        api,
        service_id=services["open_api"].service.service_id,
        texts=("合成 Open API 问题",),
        idempotency_key="synthetic-p310-open-api-invoke",
    )

    assert {custom.run.service_id, scenario.run.service_id, open_api.run.service_id} == {
        deployment.service.service_id for deployment in services.values()
    }
    assert all(
        submission.run.service_route_id is not None for submission in (custom, scenario, open_api)
    )
    with pytest.raises(ServiceInvocationDeniedError):
        harness.invocations.invoke(
            browser,
            service_id=services["open_api"].service.service_id,
            texts=("浏览器不能调用 Open API surface",),
            idempotency_key="synthetic-p310-browser-open-api-denied",
        )
    with pytest.raises(ServiceInvocationDeniedError):
        harness.invocations.invoke(
            api,
            service_id=services["custom_knowledge_agent"].service.service_id,
            texts=("API Key 不能调用页面 surface",),
            idempotency_key="synthetic-p310-api-browser-denied",
        )

    with harness.approval.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(assistant_runs)
                .where(assistant_runs.c.workspace_id == owner.workspace_id)
            )
            == 3
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(workspace_usage_records)
                .where(workspace_usage_records.c.workspace_id == owner.workspace_id)
            )
            == 3
        )


def test_api_key_actor_scope_idempotency_hidden_session_and_quota_are_isolated(
    service_delivery_database: ServiceDeliveryHarness,
) -> None:
    """API Key Scope 只能收窄到具体服务，相同幂等键按 Actor 隔离且不泄漏隐藏会话。"""

    # 长函数保留原因: 同一工作空间必须连续证明 Actor 幂等、读取隔离、配额和数据库防改绑。
    harness = service_delivery_database
    owner = register(harness.approval, "delivery-api-actor")
    release = published_release(harness.approval, owner, "delivery-api-actor")
    governance = _governance(harness)
    first_service = governance.create_service(
        context(owner),
        name="合成 API Actor 服务 A",
        release_id=release.release_id,
        service_type="open_api",
        visibility="workspace",
        idempotency_key="synthetic-p310-api-service-a",
    )
    second_service = governance.create_service(
        context(owner),
        name="合成 API Actor 服务 B",
        release_id=release.release_id,
        service_type="open_api",
        visibility="workspace",
        idempotency_key="synthetic-p310-api-service-b",
    )
    first_context = _api_context(owner, first_service.service.service_id)
    second_context = _api_context(owner, first_service.service.service_id)

    first = harness.invocations.invoke(
        first_context,
        service_id=first_service.service.service_id,
        texts=("合成 Actor 隔离问题",),
        idempotency_key="synthetic-p310-shared-actor-key",
    )
    replayed = harness.invocations.invoke(
        first_context,
        service_id=first_service.service.service_id,
        texts=("合成 Actor 隔离问题",),
        idempotency_key="synthetic-p310-shared-actor-key",
    )
    second = harness.invocations.invoke(
        second_context,
        service_id=first_service.service.service_id,
        texts=("合成 Actor 隔离问题",),
        idempotency_key="synthetic-p310-shared-actor-key",
    )

    assert replayed == first
    assert second.run.run_id != first.run.run_id
    assert harness.assistant.list_conversations(context(owner), limit=100) == ()
    with pytest.raises(AssistantNotFoundError):
        harness.invocations.get_invocation(
            second_context,
            service_id=first_service.service.service_id,
            run_id=first.run.run_id,
        )
    with pytest.raises(ServiceInvocationDeniedError):
        harness.invocations.invoke(
            first_context,
            service_id=second_service.service.service_id,
            texts=("资源 Scope 不覆盖服务 B",),
            idempotency_key="synthetic-p310-resource-scope-denied",
        )

    with harness.approval.sessions.begin() as session:
        session.execute(
            update(workspace_entitlements)
            .where(workspace_entitlements.c.workspace_id == owner.workspace_id)
            .values(max_monthly_questions=2, version=2, updated_at=datetime.now(UTC))
        )
    with pytest.raises(QuotaExceededError):
        harness.invocations.invoke(
            first_context,
            service_id=first_service.service.service_id,
            texts=("配额外请求",),
            idempotency_key="synthetic-p310-quota-denied",
        )

    with pytest.raises(DBAPIError), harness.approval.sessions.begin() as session:
        session.execute(
            update(assistant_runs)
            .where(assistant_runs.c.run_id == first.run.run_id)
            .values(requested_by_actor_id=uuid4())
        )
    with harness.approval.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(assistant_runs)
                .where(assistant_runs.c.workspace_id == owner.workspace_id)
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(conversations)
                .where(
                    conversations.c.workspace_id == owner.workspace_id,
                    conversations.c.conversation_kind == "service_invocation",
                )
            )
            == 2
        )


def test_restricted_service_accepts_descendant_department_and_denies_unassigned_member(
    service_delivery_database: ServiceDeliveryHarness,
) -> None:
    """限制部门以组织树根生效，后代部门成员可调用，未分配成员不产生 Run。"""

    # 长函数保留原因: 企业发布、组织归属和服务调用必须位于同一真实 Schema 才能验证闭包查询。
    harness = service_delivery_database
    owner = register(harness.approval, "delivery-enterprise-owner")
    member = register(harness.approval, "delivery-enterprise-member")
    outsider = register(harness.approval, "delivery-enterprise-outsider")
    enterprise = harness.approval.enterprise.create(context(owner), name="合成服务调用企业")
    add_enterprise_member(harness.approval, owner, member, enterprise.workspace_id)
    add_enterprise_member(harness.approval, owner, outsider, enterprise.workspace_id)
    enterprise_owner = replace(owner, workspace_id=enterprise.workspace_id)
    owner_context = context(enterprise_owner)
    root = harness.organization.create_department(
        owner_context,
        workspace_id=enterprise.workspace_id,
        name="合成研发中心",
        parent_department_id=None,
    )
    child = harness.organization.create_department(
        owner_context,
        workspace_id=enterprise.workspace_id,
        name="合成平台组",
        parent_department_id=root.department_id,
    )
    harness.organization.assign_member(
        owner_context,
        workspace_id=enterprise.workspace_id,
        target_account_id=member.account_id,
        department_ids=(child.department_id,),
        primary_department_id=child.department_id,
        position_ids=(),
    )
    release = _enterprise_release(harness, enterprise_owner)
    deployment = _governance(harness).create_service(
        owner_context,
        name="合成部门限制服务",
        release_id=release,
        service_type="custom_knowledge_agent",
        visibility="restricted",
        allowed_department_ids=(root.department_id,),
        idempotency_key="synthetic-p310-department-service",
    )
    member_context = _browser_context(replace(member, workspace_id=enterprise.workspace_id))
    outsider_context = _browser_context(replace(outsider, workspace_id=enterprise.workspace_id))

    allowed = harness.invocations.invoke(
        member_context,
        service_id=deployment.service.service_id,
        texts=("后代部门合成问题",),
        idempotency_key="synthetic-p310-department-allowed",
    )
    with pytest.raises(ServiceInvocationDeniedError):
        harness.invocations.invoke(
            outsider_context,
            service_id=deployment.service.service_id,
            texts=("未分配部门合成问题",),
            idempotency_key="synthetic-p310-department-denied",
        )

    assert allowed.run.requested_by_account_id == member.account_id
    with harness.approval.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(assistant_runs)
                .where(
                    assistant_runs.c.workspace_id == enterprise.workspace_id,
                    assistant_runs.c.service_id == deployment.service.service_id,
                )
            )
            == 1
        )


def test_valkey_limit_counts_new_requests_but_not_idempotent_replay(
    service_delivery_database: ServiceDeliveryHarness,
) -> None:
    """真实 Valkey 对同 Actor 的新请求限流，幂等重放不重复占用窗口。"""

    harness = service_delivery_database
    owner = register(harness.approval, "delivery-rate-limit")
    deployment = _personal_service(harness, owner, "delivery-rate-limit")
    limiter = ValkeyInvocationRateLimiter(
        os.environ.get("AI_PLATFORM_TEST_VALKEY_URL", DEFAULT_VALKEY_URL),
        limit_per_minute=2,
    )
    invocations = ServiceInvocationService(harness.unit_of_work, harness.runtime, limiter)
    browser = _browser_context(owner)
    try:
        first = invocations.invoke(
            browser,
            service_id=deployment.service.service_id,
            texts=("限流请求一",),
            idempotency_key="synthetic-p310-rate-first",
        )
        replayed = invocations.invoke(
            browser,
            service_id=deployment.service.service_id,
            texts=("限流请求一",),
            idempotency_key="synthetic-p310-rate-first",
        )
        invocations.invoke(
            browser,
            service_id=deployment.service.service_id,
            texts=("限流请求二",),
            idempotency_key="synthetic-p310-rate-second",
        )
        with pytest.raises(ServiceInvocationRateLimitedError):
            invocations.invoke(
                browser,
                service_id=deployment.service.service_id,
                texts=("限流请求三",),
                idempotency_key="synthetic-p310-rate-third",
            )
    finally:
        limiter.close()

    assert replayed == first
    with harness.approval.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(assistant_runs)
                .where(
                    assistant_runs.c.workspace_id == owner.workspace_id,
                    assistant_runs.c.service_id == deployment.service.service_id,
                )
            )
            == 2
        )


def test_http_snapshot_and_resumable_sse_observe_one_run_without_reexecution(
    service_delivery_database: ServiceDeliveryHarness,
) -> None:
    """POST、HTTP 查询和 Last-Event-ID 回放指向同一 Run，重连不再调度执行器。"""

    # 长函数保留原因: 同一 HTTP Run 必须连续经历调度、终态写入、完整回放和断点回放。
    harness = service_delivery_database
    owner = register(harness.approval, "delivery-http-sse")
    deployment = _personal_service(harness, owner, "delivery-http-sse")
    browser = _browser_context(owner)
    executor = RecordingExecutor()
    app = FastAPI()
    app.state.service_invocation_service = harness.invocations
    app.state.streaming_service = harness.streams
    app.include_router(service_delivery_router, prefix="/api/v1")
    app.dependency_overrides[trusted_request_context] = lambda: browser
    app.dependency_overrides[assistant_run_executor] = lambda: cast(
        AssistantRunExecutor,
        executor,
    )
    app.dependency_overrides[get_settings] = lambda: cast(
        Settings,
        SimpleNamespace(stream_heartbeat_seconds=15, stream_poll_interval_ms=50),
    )
    path = (
        f"/api/v1/workspaces/{owner.workspace_id}/services/"
        f"{deployment.service.service_id}/invocations"
    )

    with TestClient(app) as client:
        created = client.post(
            path,
            headers={"Idempotency-Key": "synthetic-p310-http-create"},
            json={"parts": [{"type": "text", "text": "合成 HTTP 与 SSE 问题"}]},
        )
        assert created.status_code == 201
        run_id = UUID(created.json()["run"]["run_id"])
        assert executor.run_ids == [run_id]

        claimed = harness.assistant.claim_run(browser, run_id=run_id)
        assert claimed is not None and claimed.assistant_message_id is not None
        now = datetime.now(UTC)
        harness.streams.start_run(
            owner.workspace_id,
            claimed.conversation_id,
            claimed.assistant_message_id,
            run_id,
            now=now,
        )
        first = harness.streams.append(
            run_id,
            "message.delta",
            claimed.trace_id,
            claimed.traceparent,
            {"delta": "第一段"},
            now=now,
        )
        completed_event = harness.streams.append(
            run_id,
            "message.completed",
            claimed.trace_id,
            claimed.traceparent,
            {"text": "第一段第二段"},
            now=now,
        )
        harness.assistant.complete_run(browser, run_id=run_id, text="第一段第二段")
        harness.streams.finish(
            run_id,
            "completed",
            {"status": "completed", "text": "第一段第二段"},
            now=now,
        )

        snapshot = client.get(f"{path}/{run_id}")
        full = client.get(f"{path}/{run_id}/events")
        resumed = client.get(
            f"{path}/{run_id}/events",
            headers={"Last-Event-ID": str(first.event_id)},
        )
        acknowledged = client.get(
            f"{path}/{run_id}/events",
            headers={"Last-Event-ID": str(completed_event.event_id)},
        )

    assert snapshot.status_code == 200
    assert snapshot.json()["run"]["run_id"] == str(run_id)
    assert snapshot.json()["output_message"]["parts"][0]["text"] == "第一段第二段"
    assert created.json()["event_stream_path"] == f"{path}/{run_id}/events"
    assert full.status_code == resumed.status_code == acknowledged.status_code == 200
    assert f"id: {first.event_id}" in full.text
    assert f"id: {first.event_id}" not in resumed.text
    assert f"id: {completed_event.event_id}" in resumed.text
    assert "event: message.snapshot" in acknowledged.text
    assert executor.run_ids == [run_id]
    assert harness.assistant.list_conversations(context(owner), limit=100) == ()
    with harness.approval.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(assistant_runs)
                .where(assistant_runs.c.run_id == run_id)
            )
            == 1
        )


def _governance(harness: ServiceDeliveryHarness) -> ServiceGovernanceService:
    return ServiceGovernanceService(
        SqlAlchemyServiceGovernanceUnitOfWork(harness.approval.sessions)
    )


def _browser_context(account: RegisteredAccount) -> RequestContext:
    return replace(
        context(account),
        authorized_permission_code=SERVICE_PERMISSION,
        authorized_workspace=True,
    )


def _api_context(account: RegisteredAccount, service_id: UUID) -> RequestContext:
    return replace(
        RequestContext.trusted(
            actor_id=uuid4(),
            user_id=account.account_id,
            workspace_id=account.workspace_id,
            trace=context(account).trace,
            authentication_method="open_api_key",
            credential_scopes=frozenset({f"{SERVICE_PERMISSION}.{service_id.hex}"}),
        ),
        authorized_permission_code=SERVICE_PERMISSION,
        authorized_workspace=True,
        authorized_maximum_security_level="INTERNAL",
    )


def _personal_service(
    harness: ServiceDeliveryHarness,
    owner: RegisteredAccount,
    suffix: str,
) -> ServiceDeployment:
    release = published_release(harness.approval, owner, suffix)
    return _governance(harness).create_service(
        context(owner),
        name=f"合成 {suffix} 服务",
        release_id=release.release_id,
        service_type="custom_knowledge_agent",
        visibility="workspace",
        idempotency_key=f"synthetic-p310-service-{suffix}",
    )


def _enterprise_release(harness: ServiceDeliveryHarness, owner: RegisteredAccount) -> UUID:
    """为企业空间创建允许所有者确认的单级策略，并生成一条真实自定义 Release。"""

    owner_context = context(owner)
    harness.approval.policies.create(
        owner_context,
        name="合成服务出口企业发布策略",
        definition=ApprovalPolicyDefinition(
            "agent.release",
            "approve",
            100,
            (),
            ("INTERNAL",),
            ("high",),
            (),
            (
                ApprovalLevelDefinition(
                    1,
                    "any",
                    (ApprovalApproverSource("accounts", (owner.account_id,)),),
                ),
            ),
            allow_self_approval=True,
        ),
    )
    agents, candidate_id = prepare_candidate(
        harness.approval,
        owner,
        "delivery-enterprise",
    )
    requested = agents.request_approval(
        owner_context,
        candidate_id=candidate_id,
        idempotency_key="synthetic-p310-enterprise-approval",
    )
    approved = harness.approval.approvals.act(
        owner_context,
        approval_instance_id=requested.state.instance.approval_instance_id,
        command=ApprovalRuntimeCommand(
            "approve",
            owner.account_id,
            "synthetic-p310-enterprise-approve",
        ),
    )
    assert approved.state.instance.status == "approved"
    release = agents.publish_release(
        owner_context,
        candidate_id=candidate_id,
        idempotency_key="synthetic-p310-enterprise-release",
    )
    return release.release_id
