"""验证 P3-07 服务策略、Route 摘要和幂等快照纯逻辑。"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from ai_platform_api.modules.service_governance.application.errors import (
    ServiceRouteUnavailableError,
    ServiceValidationError,
)
from ai_platform_api.modules.service_governance.application.support import (
    access_policy_digest,
    deployment_from_request,
    deployment_snapshot,
    normalize_policy_subjects,
    require_routable_release,
    route_digest,
)
from ai_platform_api.modules.service_governance.domain.models import (
    RoutableAgentRelease,
    Service,
    ServiceAccessPolicyVersion,
    ServiceControlRequest,
    ServiceDeployment,
    ServiceRoute,
    ServiceRoutePublication,
)

WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000307")
ACCOUNT_ID = UUID("20000000-0000-4000-8000-000000000307")
AGENT_ID = UUID("30000000-0000-4000-8000-000000000307")
RELEASE_ID = UUID("40000000-0000-4000-8000-000000000307")
SERVICE_ID = UUID("50000000-0000-4000-8000-000000000307")
POLICY_ID = UUID("60000000-0000-4000-8000-000000000307")
ROUTE_ID = UUID("70000000-0000-4000-8000-000000000307")
NOW = datetime(2026, 8, 16, 10, 0, tzinfo=UTC)


def test_policy_subjects_are_canonical_and_reject_ambiguous_scope() -> None:
    """限制策略排序后稳定，工作空间策略和重复主体不能产生歧义。"""

    second = UUID("80000000-0000-4000-8000-000000000307")
    normalized = normalize_policy_subjects(
        "restricted",
        (),
        (second, ACCOUNT_ID),
    )
    assert normalized == ("restricted", (), (ACCOUNT_ID, second))
    assert access_policy_digest(
        service_id=SERVICE_ID,
        version=1,
        visibility=normalized[0],
        department_ids=normalized[1],
        account_ids=normalized[2],
    ) == access_policy_digest(
        service_id=SERVICE_ID,
        version=1,
        visibility="restricted",
        department_ids=(),
        account_ids=(ACCOUNT_ID, second),
    )
    with pytest.raises(ServiceValidationError):
        normalize_policy_subjects("workspace", (), (ACCOUNT_ID,))
    with pytest.raises(ServiceValidationError):
        normalize_policy_subjects("restricted", (), ())
    with pytest.raises(ServiceValidationError):
        normalize_policy_subjects("restricted", (), (ACCOUNT_ID, ACCOUNT_ID))


def test_route_digest_changes_with_version_chain() -> None:
    """Route 摘要覆盖 Release 和前序身份，但不依赖创建时间或操作者。"""

    route = _deployment().route
    assert route.route_hash == route_digest(route)
    changed = replace(route, route_version=2, previous_route_id=ROUTE_ID, route_hash="")
    assert route_digest(changed) != route.route_hash
    assert route_digest(replace(route, created_at=NOW.replace(hour=11))) == route.route_hash


def test_request_snapshot_replays_original_service_state() -> None:
    """幂等请求从冻结快照恢复响应，不需要读取已经变化的 Service 当前态。"""

    deployment = _deployment()
    request = ServiceControlRequest(
        request_id=UUID("90000000-0000-4000-8000-000000000307"),
        workspace_id=WORKSPACE_ID,
        actor_id=ACCOUNT_ID,
        operation="service.create",
        idempotency_key="synthetic-service-request",
        request_hash="a" * 64,
        service_id=SERVICE_ID,
        result_snapshot=deployment_snapshot(deployment),
        created_at=NOW,
    )
    assert deployment_from_request(request) == deployment


def test_routable_release_gate_rejects_incomplete_custom_snapshot() -> None:
    """服务路由只能接受活动 Agent 的已发布事实，自定义 Release 必须有快照摘要。"""

    release = RoutableAgentRelease(
        RELEASE_ID,
        AGENT_ID,
        WORKSPACE_ID,
        "custom",
        1,
        "released",
        "active",
        "b" * 64,
    )
    assert require_routable_release(release, expected_kind="custom") == release
    with pytest.raises(ServiceRouteUnavailableError):
        require_routable_release(replace(release, snapshot_hash=None), expected_kind="custom")
    with pytest.raises(ServiceRouteUnavailableError):
        require_routable_release(replace(release, agent_status="archived"), expected_kind="custom")


def _deployment() -> ServiceDeployment:
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
    service = Service(
        SERVICE_ID,
        WORKSPACE_ID,
        AGENT_ID,
        "custom-50000000000040008000000000000307",
        "合成服务",
        "custom_knowledge_agent",
        "active",
        POLICY_ID,
        ACCOUNT_ID,
        NOW,
        ACCOUNT_ID,
        NOW,
        2,
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
    publication = ServiceRoutePublication(
        SERVICE_ID,
        WORKSPACE_ID,
        ROUTE_ID,
        1,
        ACCOUNT_ID,
        NOW,
    )
    return ServiceDeployment(service, policy, route, publication)
