"""验证 P3-10 统一服务出口的授权、配额、幂等和限流纯逻辑。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import TracebackType
from typing import cast
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.assistant.application.errors import AssistantNotFoundError
from ai_platform_api.modules.assistant.application.runner import _retrieval_execution_context
from ai_platform_api.modules.assistant.domain.models import (
    AssistantUnitOfWork,
    Conversation,
    MessageSubmission,
)
from ai_platform_api.modules.identity.application.entitlement_errors import QuotaExceededError
from ai_platform_api.modules.identity.domain.enterprise import WorkspaceRecord
from ai_platform_api.modules.identity.domain.entitlements import (
    UsageCounter,
    UsageMetric,
    UsageRecord,
    WorkspaceEntitlement,
)
from ai_platform_api.modules.service_delivery.application.errors import (
    ServiceInvocationDeniedError,
    ServiceInvocationRateLimitedError,
    ServiceInvocationRateLimitUnavailableError,
)
from ai_platform_api.modules.service_delivery.application.service import ServiceInvocationService
from ai_platform_api.modules.service_delivery.domain.models import InvocationRateLimiter
from ai_platform_api.modules.service_delivery.infrastructure.valkey import (
    RateLimitClient,
    ValkeyInvocationRateLimiter,
)
from ai_platform_api.modules.service_governance.application.support import (
    access_policy_digest,
    route_digest,
)
from ai_platform_api.modules.service_governance.domain.models import (
    Service,
    ServiceAccessPolicyVersion,
    ServiceDeployment,
    ServiceRoute,
    ServiceRoutePublication,
    ServiceType,
)
from ai_platform_api.modules.service_runtime.application.errors import (
    AgentRuntimeReleaseRequiredError,
    RuntimeServiceRouteUnavailableError,
)
from ai_platform_api.modules.service_runtime.application.loader import RuntimeReleaseLoader
from ai_platform_api.modules.service_runtime.domain.models import RuntimeReleaseSnapshot
from redis.exceptions import RedisError

WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000310")
ACCOUNT_ID = UUID("20000000-0000-4000-8000-000000000310")
SECOND_ACCOUNT_ID = UUID("20000000-0000-4000-8000-000000000311")
SERVICE_ID = UUID("30000000-0000-4000-8000-000000000310")
OTHER_SERVICE_ID = UUID("30000000-0000-4000-8000-000000000311")
AGENT_ID = UUID("40000000-0000-4000-8000-000000000310")
RELEASE_ID = UUID("50000000-0000-4000-8000-000000000310")
RUNTIME_CONFIG_ID = UUID("60000000-0000-4000-8000-000000000310")
POLICY_ID = UUID("70000000-0000-4000-8000-000000000310")
ROUTE_ID = UUID("80000000-0000-4000-8000-000000000310")
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
TRACE = TraceContext("1" * 32, "2" * 16)


class MemoryWriter:
    """保存待提交事实数量，测试不读取审计或事件正文。"""

    def __init__(self) -> None:
        self.items: list[object] = []

    def add(self, item: object) -> None:
        self.items.append(item)


class MemoryUsageRepository:
    """以单进程字典模拟同事务额度账本，供应用层规则测试。"""

    def __init__(self, limit: int = 20) -> None:
        self.workspace = WorkspaceRecord(WORKSPACE_ID, "personal", "合成个人空间", "active")
        self.entitlement = WorkspaceEntitlement(
            WORKSPACE_ID,
            "synthetic_local",
            1024,
            2,
            2,
            2,
            limit,
            True,
            False,
            NOW,
            NOW,
            1,
        )
        self.counters: dict[tuple[UUID, UsageMetric, str], UsageCounter] = {}
        self.records: dict[tuple[UUID, str], UsageRecord] = {}

    def get_workspace(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceRecord | None:
        del for_update
        return self.workspace if workspace_id == WORKSPACE_ID else None

    def get_entitlement(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceEntitlement | None:
        del for_update
        return self.entitlement if workspace_id == WORKSPACE_ID else None

    def get_usage_record(self, workspace_id: UUID, idempotency_key: str) -> UsageRecord | None:
        return self.records.get((workspace_id, idempotency_key))

    def get_usage_counter(
        self,
        workspace_id: UUID,
        metric: UsageMetric,
        period_key: str,
        *,
        for_update: bool = False,
    ) -> UsageCounter | None:
        del for_update
        return self.counters.get((workspace_id, metric, period_key))

    def save_usage_counter(
        self,
        counter: UsageCounter,
        *,
        expected_version: int | None,
    ) -> None:
        del expected_version
        self.counters[(counter.workspace_id, counter.metric, counter.period_key)] = counter

    def add_usage_record(self, record: UsageRecord) -> None:
        self.records[(record.workspace_id, record.idempotency_key)] = record


class MemoryAssistantRepository:
    """只实现服务调用创建和按 Actor 读取所需的助手端口。"""

    def __init__(self) -> None:
        self.conversations: list[Conversation] = []
        self.submissions: list[MessageSubmission] = []

    def get_submission(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        idempotency_key: str,
    ) -> MessageSubmission | None:
        return next(
            (
                item
                for item in self.submissions
                if item.run.workspace_id == workspace_id
                and item.run.requested_by_actor_id == actor_id
                and item.run.idempotency_key == idempotency_key
            ),
            None,
        )

    def get_submission_by_run(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        run_id: UUID,
    ) -> MessageSubmission | None:
        return next(
            (
                item
                for item in self.submissions
                if item.run.workspace_id == workspace_id
                and item.run.requested_by_actor_id == actor_id
                and item.run.run_id == run_id
            ),
            None,
        )

    def add_conversation(self, conversation: Conversation) -> None:
        self.conversations.append(conversation)

    def add_submission(self, submission: MessageSubmission) -> None:
        self.submissions.append(submission)


class MemoryServiceRepository:
    """返回单个完整部署，并可切换访问策略结论。"""

    def __init__(self, deployment: ServiceDeployment, *, allowed: bool = True) -> None:
        self.deployment = deployment
        self.allowed = allowed

    def get_service(
        self,
        workspace_id: UUID,
        service_id: UUID,
        *,
        for_update: bool = False,
    ) -> Service | None:
        del for_update
        service = self.deployment.service
        return (
            service
            if (workspace_id, service_id) == (service.workspace_id, service.service_id)
            else None
        )

    def get_access_policy(
        self,
        workspace_id: UUID,
        access_policy_version_id: UUID,
    ) -> ServiceAccessPolicyVersion | None:
        policy = self.deployment.access_policy
        return (
            policy
            if (workspace_id, access_policy_version_id)
            == (
                policy.workspace_id,
                policy.access_policy_version_id,
            )
            else None
        )

    def get_current_route(
        self,
        workspace_id: UUID,
        service_id: UUID,
        *,
        for_update: bool = False,
    ) -> tuple[ServiceRoute, ServiceRoutePublication] | None:
        del for_update
        route = self.deployment.route
        if (workspace_id, service_id) != (route.workspace_id, route.service_id):
            return None
        return route, self.deployment.publication

    def account_can_invoke(self, workspace_id: UUID, service_id: UUID, account_id: UUID) -> bool:
        return (
            self.allowed
            and workspace_id == WORKSPACE_ID
            and service_id == SERVICE_ID
            and account_id in {ACCOUNT_ID, SECOND_ACCOUNT_ID}
        )


class MemoryUnitOfWork:
    """把服务、助手、用量、审计和 Outbox 放入同一测试事务边界。"""

    def __init__(self, deployment: ServiceDeployment, *, allowed: bool = True, limit: int = 20):
        self.assistant = MemoryAssistantRepository()
        self.services = MemoryServiceRepository(deployment, allowed=allowed)
        self.usage = MemoryUsageRepository(limit)
        self.audit = MemoryWriter()
        self.outbox = MemoryWriter()
        self.commit_count = 0

    def __enter__(self) -> MemoryUnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    def commit(self) -> None:
        self.commit_count += 1


class StaticRuntime:
    """返回预置当前快照，并记录灰度分配键是否使用 Actor。"""

    def __init__(self, snapshot: RuntimeReleaseSnapshot) -> None:
        self.snapshot = snapshot
        self.assignments: list[str] = []

    def resolve_current(
        self,
        workspace_id: UUID,
        service_id: UUID,
        assignment_key: str,
    ) -> RuntimeReleaseSnapshot:
        if (workspace_id, service_id) != (self.snapshot.workspace_id, self.snapshot.service_id):
            raise RuntimeServiceRouteUnavailableError
        self.assignments.append(assignment_key)
        return self.snapshot


class MemoryRateLimiter:
    """记录限流主体，便于验证拒绝发生在任何业务写入之前。"""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID, UUID, str]] = []

    def consume(
        self,
        workspace_id: UUID,
        service_id: UUID,
        actor_id: UUID,
        idempotency_key: str,
    ) -> None:
        self.calls.append((workspace_id, service_id, actor_id, idempotency_key))

    def close(self) -> None:
        pass


class ResultRateLimitClient:
    """模拟 Valkey EVAL 返回值或连接错误。"""

    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[object, ...]] = []

    def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object:
        self.calls.append((script, numkeys, *keys_and_args))
        if isinstance(self.result, RedisError):
            raise self.result
        return self.result

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    ("service_type", "authentication_method"),
    [
        ("custom_knowledge_agent", "browser_session"),
        ("scenario_application", "browser_session"),
        ("open_api", "open_api_key"),
    ],
)
def test_three_service_types_share_one_creation_chain(
    service_type: ServiceType,
    authentication_method: str,
) -> None:
    unit_of_work, runtime, limiter, service = _service(service_type)
    context = _context(authentication_method=authentication_method)

    submission = service.invoke(
        context,
        service_id=SERVICE_ID,
        texts=("  合成问题  ",),
        idempotency_key=f"synthetic-{service_type}",
    )

    assert submission.message.parts[0].text == "合成问题"
    assert submission.run.requested_by_actor_id == context.actor_id
    assert submission.run.service_route_id == ROUTE_ID
    assert unit_of_work.assistant.conversations[0].conversation_kind == "service_invocation"
    assert len(unit_of_work.usage.records) == 1
    assert runtime.assignments == [context.actor_id.hex]
    assert limiter.calls[0][2] == context.actor_id


@pytest.mark.parametrize(
    ("service_type", "authentication_method"),
    [
        ("open_api", "browser_session"),
        ("custom_knowledge_agent", "open_api_key"),
        ("scenario_application", "open_api_key"),
    ],
)
def test_surface_mismatch_is_denied_without_run_or_quota(
    service_type: ServiceType,
    authentication_method: str,
) -> None:
    unit_of_work, _, _, service = _service(service_type)

    with pytest.raises(ServiceInvocationDeniedError):
        service.invoke(
            _context(authentication_method=authentication_method),
            service_id=SERVICE_ID,
            texts=("合成问题",),
            idempotency_key="synthetic-surface-denied",
        )

    assert unit_of_work.assistant.submissions == []
    assert unit_of_work.usage.records == {}


def test_resource_scope_only_narrows_and_denied_request_has_no_side_effect() -> None:
    unit_of_work, _, limiter, service = _service("open_api")
    context = _context(
        authentication_method="open_api_key",
        credential_scopes=frozenset({f"service.definition.read.{OTHER_SERVICE_ID.hex}"}),
    )

    with pytest.raises(ServiceInvocationDeniedError):
        service.invoke(
            context,
            service_id=SERVICE_ID,
            texts=("合成问题",),
            idempotency_key="synthetic-scope-denied",
        )

    assert limiter.calls == []
    assert unit_of_work.assistant.submissions == []


def test_actor_idempotency_isolated_and_hidden_invocation_read_is_owned() -> None:
    unit_of_work, _, _, service = _service("custom_knowledge_agent")
    first_context = _context()
    second_context = _context(account_id=SECOND_ACCOUNT_ID)

    first = service.invoke(
        first_context,
        service_id=SERVICE_ID,
        texts=("合成问题",),
        idempotency_key="synthetic-actor-isolation",
    )
    replay = service.invoke(
        first_context,
        service_id=SERVICE_ID,
        texts=("合成问题",),
        idempotency_key="synthetic-actor-isolation",
    )
    second = service.invoke(
        second_context,
        service_id=SERVICE_ID,
        texts=("合成问题",),
        idempotency_key="synthetic-actor-isolation",
    )

    assert replay == first
    assert second.run.run_id != first.run.run_id
    assert len(unit_of_work.assistant.conversations) == 2
    assert len(unit_of_work.usage.records) == 2
    assert (
        service.get_invocation(
            first_context,
            service_id=SERVICE_ID,
            run_id=first.run.run_id,
        )
        == first
    )
    with pytest.raises(AssistantNotFoundError):
        service.get_invocation(
            second_context,
            service_id=SERVICE_ID,
            run_id=first.run.run_id,
        )


def test_quota_and_route_competition_fail_closed_before_run_creation() -> None:
    quota_uow, _, _, quota_service = _service("custom_knowledge_agent", limit=0)
    with pytest.raises(QuotaExceededError):
        quota_service.invoke(
            _context(),
            service_id=SERVICE_ID,
            texts=("合成问题",),
            idempotency_key="synthetic-quota-denied",
        )
    assert quota_uow.assistant.submissions == []

    route_uow, runtime, _, route_service = _service("custom_knowledge_agent")
    runtime.snapshot = replace(runtime.snapshot, route_id=uuid4())
    with pytest.raises(RuntimeServiceRouteUnavailableError):
        route_service.invoke(
            _context(),
            service_id=SERVICE_ID,
            texts=("合成问题",),
            idempotency_key="synthetic-route-race",
        )
    assert route_uow.assistant.submissions == []
    assert route_uow.usage.records == {}


def test_open_api_execution_context_rechecks_account_permissions_without_service_scope() -> None:
    _, _, _, service = _service("open_api")
    api_context = _context(authentication_method="open_api_key")
    submission = service.invoke(
        api_context,
        service_id=SERVICE_ID,
        texts=("合成问题",),
        idempotency_key="synthetic-execution-context",
    )

    execution = _retrieval_execution_context(api_context, submission.run)

    assert execution.actor_id == ACCOUNT_ID
    assert execution.authentication_method == "browser_session"
    assert execution.credential_scopes is None
    assert execution.authorized_permission_code is None
    assert api_context.actor_id != ACCOUNT_ID
    with pytest.raises(AgentRuntimeReleaseRequiredError):
        _retrieval_execution_context(
            api_context,
            replace(submission.run, requested_by_actor_id=uuid4()),
        )


@pytest.mark.parametrize(
    ("result", "expected_error"),
    [
        (-1, ServiceInvocationRateLimitedError),
        (True, ServiceInvocationRateLimitUnavailableError),
        ("invalid", ServiceInvocationRateLimitUnavailableError),
        (RedisError("synthetic-unavailable"), ServiceInvocationRateLimitUnavailableError),
    ],
)
def test_valkey_rate_limiter_uses_stable_digests_and_fails_closed(
    result: object,
    expected_error: type[Exception],
) -> None:
    client = ResultRateLimitClient(result)
    limiter = ValkeyInvocationRateLimiter(client=cast(RateLimitClient, client))

    with pytest.raises(expected_error):
        limiter.consume(WORKSPACE_ID, SERVICE_ID, ACCOUNT_ID, "synthetic-rate-limit")

    call = client.calls[0]
    assert call[1] == 2
    assert str(WORKSPACE_ID) not in str(call[2:4])
    assert str(ACCOUNT_ID) not in str(call[2:4])


def _service(
    service_type: ServiceType,
    *,
    limit: int = 20,
) -> tuple[MemoryUnitOfWork, StaticRuntime, MemoryRateLimiter, ServiceInvocationService]:
    deployment = _deployment(service_type)
    unit_of_work = MemoryUnitOfWork(deployment, limit=limit)
    runtime = StaticRuntime(_snapshot(deployment))
    limiter = MemoryRateLimiter()
    service = ServiceInvocationService(
        cast(AssistantUnitOfWork, unit_of_work),
        cast(RuntimeReleaseLoader, runtime),
        cast(InvocationRateLimiter, limiter),
    )
    return unit_of_work, runtime, limiter, service


def _context(
    *,
    account_id: UUID = ACCOUNT_ID,
    authentication_method: str = "browser_session",
    credential_scopes: frozenset[str] | None = None,
) -> RequestContext:
    actor_id = account_id if authentication_method == "browser_session" else uuid4()
    scopes = credential_scopes
    if authentication_method == "open_api_key" and scopes is None:
        scopes = frozenset({f"service.definition.read.{SERVICE_ID.hex}"})
    return replace(
        RequestContext.trusted(
            actor_id=actor_id,
            user_id=account_id,
            workspace_id=WORKSPACE_ID,
            trace=TRACE,
            authentication_method=authentication_method,
            credential_scopes=scopes,
        ),
        authorized_permission_code="service.definition.read",
        authorized_workspace=True,
        authorized_maximum_security_level="INTERNAL",
    )


def _deployment(service_type: ServiceType) -> ServiceDeployment:
    policy = ServiceAccessPolicyVersion(
        POLICY_ID,
        SERVICE_ID,
        WORKSPACE_ID,
        1,
        "workspace",
        (),
        (),
        access_policy_digest(
            service_id=SERVICE_ID,
            version=1,
            visibility="workspace",
            department_ids=(),
            account_ids=(),
        ),
        ACCOUNT_ID,
        NOW,
    )
    route = ServiceRoute(
        ROUTE_ID,
        SERVICE_ID,
        WORKSPACE_ID,
        1,
        "active",
        RELEASE_ID,
        None,
        0,
        None,
        "",
        ACCOUNT_ID,
        NOW,
    )
    route = replace(route, route_hash=route_digest(route))
    service = Service(
        SERVICE_ID,
        WORKSPACE_ID,
        AGENT_ID,
        "synthetic-service",
        "合成服务",
        service_type,
        "active",
        POLICY_ID,
        ACCOUNT_ID,
        NOW,
        ACCOUNT_ID,
        NOW,
        2,
    )
    publication = ServiceRoutePublication(SERVICE_ID, WORKSPACE_ID, ROUTE_ID, 1, ACCOUNT_ID, NOW)
    return ServiceDeployment(service, policy, route, publication)


def _snapshot(deployment: ServiceDeployment) -> RuntimeReleaseSnapshot:
    service = deployment.service
    route = deployment.route
    return RuntimeReleaseSnapshot(
        workspace_id=WORKSPACE_ID,
        service_id=SERVICE_ID,
        service_key=service.service_key,
        service_status=service.status,
        access_policy_version_id=POLICY_ID,
        route_id=ROUTE_ID,
        service_route_version=1,
        route_mode="active",
        primary_release_id=RELEASE_ID,
        canary_release_id=None,
        canary_percent=0,
        previous_route_id=None,
        route_hash=route.route_hash,
        agent_id=AGENT_ID,
        agent_status="active",
        agent_release_id=RELEASE_ID,
        release_kind="custom",
        release_version=1,
        release_status="released",
        runtime_config_version_id=RUNTIME_CONFIG_ID,
        config_hash="a" * 64,
        release_snapshot={"synthetic": True},
        release_snapshot_hash="b" * 64,
        released_at=NOW,
    )
