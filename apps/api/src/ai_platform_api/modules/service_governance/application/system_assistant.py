"""把阶段 1 系统知识助手发布指针兼容同步到服务治理事实。"""

from dataclasses import replace
from datetime import datetime
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.service_governance.application.errors import (
    ServiceRouteUnavailableError,
)
from ai_platform_api.modules.service_governance.application.support import (
    access_policy_digest,
    record_service_change,
    require_deployment,
    require_routable_release,
    route_digest,
)
from ai_platform_api.modules.service_governance.domain.models import (
    Service,
    ServiceAccessPolicyVersion,
    ServiceDeployment,
    ServiceGovernanceUnitOfWork,
    ServiceRoute,
    ServiceRoutePublication,
)

SYSTEM_SERVICE_KEY = "system-knowledge"
SYSTEM_SERVICE_NAME = "系统知识助手"


def ensure_system_service_route(
    unit_of_work: ServiceGovernanceUnitOfWork,
    context: RequestContext,
    *,
    agent_id: UUID,
    release_id: UUID,
    occurred_at: datetime,
) -> ServiceDeployment:
    """在助手原事务内创建或追加系统服务 Route，不改变原发布和历史 Run。"""

    # 1. 先锁定并校验原系统 Release，兼容同步不能接受自定义或失效快照。
    release = require_routable_release(
        unit_of_work.services.get_routable_release(
            context.workspace_id,
            release_id,
            for_share=True,
        ),
        expected_kind="system",
    )
    if release.agent_id != agent_id:
        raise ServiceRouteUnavailableError
    # 2. 首次同步原子创建系统服务；相同 Release 重放不追加重复 Route。
    service = unit_of_work.services.get_service_by_key(
        context.workspace_id,
        SYSTEM_SERVICE_KEY,
        for_update=True,
    )
    if service is None:
        deployment = _new_system_deployment(
            context,
            agent_id=agent_id,
            release_id=release_id,
            now=occurred_at,
        )
        draft = replace(deployment.service, status="draft", version=1)
        unit_of_work.services.add_deployment(
            draft,
            deployment.service,
            deployment.access_policy,
            deployment.route,
            deployment.publication,
        )
        record_service_change(
            unit_of_work,
            context,
            action="service.system_migrated",
            deployment=deployment,
            occurred_at=occurred_at,
            attributes={"previous_status": "draft"},
        )
        return deployment

    current = require_deployment(
        unit_of_work.services,
        context.workspace_id,
        service.service_id,
        for_update=True,
    )
    if service.agent_id != agent_id or service.service_type != "system_assistant":
        raise ServiceRouteUnavailableError
    if current.route.primary_release_id == release_id:
        return current

    # 3. 系统配置变化只追加 active Route；服务若被显式 suspended，状态保持不变。
    route = ServiceRoute(
        route_id=uuid4(),
        service_id=service.service_id,
        workspace_id=context.workspace_id,
        route_version=current.route.route_version + 1,
        route_mode="active",
        primary_release_id=release_id,
        canary_release_id=None,
        canary_percent=0,
        previous_route_id=current.route.route_id,
        route_hash="",
        created_by_account_id=context.actor_id,
        created_at=occurred_at,
    )
    route = replace(route, route_hash=route_digest(route))
    publication = ServiceRoutePublication(
        service_id=service.service_id,
        workspace_id=context.workspace_id,
        route_id=route.route_id,
        generation=current.publication.generation + 1,
        published_by_account_id=context.actor_id,
        published_at=occurred_at,
    )
    if not unit_of_work.services.append_route(
        route,
        publication,
        expected_generation=current.publication.generation,
    ):
        raise ServiceRouteUnavailableError
    # Route 与状态更新共享 Service 聚合版本，保证 Outbox aggregate_version 严格单调。
    updated_service = replace(
        service,
        updated_by_account_id=context.actor_id,
        updated_at=occurred_at,
        version=service.version + 1,
    )
    if not unit_of_work.services.save_service(
        updated_service,
        expected_version=service.version,
    ):
        raise ServiceRouteUnavailableError
    deployment = ServiceDeployment(
        updated_service,
        current.access_policy,
        route,
        publication,
    )
    record_service_change(
        unit_of_work,
        context,
        action="service.system_route_synced",
        deployment=deployment,
        occurred_at=occurred_at,
        attributes={"previous_route_id": str(current.route.route_id)},
    )
    return deployment


def _new_system_deployment(
    context: RequestContext,
    *,
    agent_id: UUID,
    release_id: UUID,
    now: datetime,
) -> ServiceDeployment:
    """为已有系统助手发布构造工作空间可见的兼容服务。"""

    service_id = uuid4()
    policy_id = uuid4()
    route_id = uuid4()
    policy = ServiceAccessPolicyVersion(
        access_policy_version_id=policy_id,
        service_id=service_id,
        workspace_id=context.workspace_id,
        version=1,
        visibility="workspace",
        allowed_department_ids=(),
        allowed_account_ids=(),
        policy_hash=access_policy_digest(
            service_id=service_id,
            version=1,
            visibility="workspace",
            department_ids=(),
            account_ids=(),
        ),
        created_by_account_id=context.actor_id,
        created_at=now,
    )
    service = Service(
        service_id=service_id,
        workspace_id=context.workspace_id,
        agent_id=agent_id,
        service_key=SYSTEM_SERVICE_KEY,
        name=SYSTEM_SERVICE_NAME,
        service_type="system_assistant",
        status="active",
        access_policy_version_id=policy_id,
        created_by_account_id=context.actor_id,
        created_at=now,
        updated_by_account_id=context.actor_id,
        updated_at=now,
        version=2,
    )
    route = ServiceRoute(
        route_id=route_id,
        service_id=service_id,
        workspace_id=context.workspace_id,
        route_version=1,
        route_mode="active",
        primary_release_id=release_id,
        canary_release_id=None,
        canary_percent=0,
        previous_route_id=None,
        route_hash="",
        created_by_account_id=context.actor_id,
        created_at=now,
    )
    route = replace(route, route_hash=route_digest(route))
    publication = ServiceRoutePublication(
        service_id=service_id,
        workspace_id=context.workspace_id,
        route_id=route_id,
        generation=1,
        published_by_account_id=context.actor_id,
        published_at=now,
    )
    return ServiceDeployment(service, policy, route, publication)
