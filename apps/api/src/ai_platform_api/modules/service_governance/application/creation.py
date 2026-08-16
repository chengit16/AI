"""实现自定义知识 Agent 服务的原子创建和初始路由激活。"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.service_governance.application.errors import (
    ServiceValidationError,
)
from ai_platform_api.modules.service_governance.application.support import (
    CREATE_SERVICE_OPERATION,
    access_policy_digest,
    browser_account,
    deployment_from_request,
    document_digest,
    normalize_name,
    normalize_policy_subjects,
    raise_write_conflict,
    record_service_change,
    require_create_scope,
    require_idempotency_key,
    require_request_hash,
    require_routable_release,
    route_digest,
    service_request,
)
from ai_platform_api.modules.service_governance.domain.models import (
    Service,
    ServiceAccessPolicyVersion,
    ServiceAccessVisibility,
    ServiceDeployment,
    ServiceGovernanceUnitOfWork,
    ServiceRoute,
    ServiceRoutePublication,
    ServiceType,
    ServiceWriteConflictError,
)


def create_service(
    unit_of_work_factory: ServiceGovernanceUnitOfWork,
    context: RequestContext,
    *,
    name: str,
    release_id: UUID,
    service_type: ServiceType,
    visibility: str,
    allowed_department_ids: tuple[UUID, ...],
    allowed_account_ids: tuple[UUID, ...],
    idempotency_key: str,
) -> ServiceDeployment:
    """创建自定义知识服务，并在同一事务从 draft 激活首个 Route。"""

    # 1. 先规范公开参数；创建尚无 service_id，只允许工作空间级授权主体执行。
    account_id = browser_account(context)
    require_create_scope(context)
    normalized_name = normalize_name(name)
    if service_type not in {"custom_knowledge_agent", "scenario_application", "open_api"}:
        raise ServiceValidationError
    normalized_policy = normalize_policy_subjects(
        visibility,
        allowed_department_ids,
        allowed_account_ids,
    )
    require_idempotency_key(idempotency_key)
    request_hash = document_digest(
        {
            "operation": CREATE_SERVICE_OPERATION,
            "name": normalized_name,
            "release_id": str(release_id),
            "service_type": service_type,
            "visibility": normalized_policy[0],
            "allowed_department_ids": [str(value) for value in normalized_policy[1]],
            "allowed_account_ids": [str(value) for value in normalized_policy[2]],
        }
    )
    now = datetime.now(UTC)
    try:
        with unit_of_work_factory as unit_of_work:
            # 2. 幂等重放返回请求时冻结的响应，而不是可能已经更新的服务当前态。
            request = unit_of_work.services.get_request(
                context.workspace_id,
                context.actor_id,
                CREATE_SERVICE_OPERATION,
                idempotency_key,
            )
            if request is not None:
                require_request_hash(request, request_hash)
                return deployment_from_request(request)

            release = require_routable_release(
                unit_of_work.services.get_routable_release(
                    context.workspace_id,
                    release_id,
                    for_share=True,
                ),
                expected_kind="custom",
            )
            if not unit_of_work.services.subjects_exist(
                context.workspace_id,
                normalized_policy[1],
                normalized_policy[2],
            ):
                raise ServiceValidationError

            # 3. Draft、策略、首个不可变路由、当前指针和 active 终态一次提交。
            deployment = _new_custom_deployment(
                context,
                account_id=account_id,
                agent_id=release.agent_id,
                release_id=release.release_id,
                name=normalized_name,
                service_type=service_type,
                visibility=normalized_policy[0],
                department_ids=normalized_policy[1],
                account_ids=normalized_policy[2],
                now=now,
            )
            draft = replace(deployment.service, status="draft", version=1)
            unit_of_work.services.add_deployment(
                draft,
                deployment.service,
                deployment.access_policy,
                deployment.route,
                deployment.publication,
            )
            unit_of_work.services.add_request(
                service_request(
                    context,
                    CREATE_SERVICE_OPERATION,
                    idempotency_key,
                    request_hash,
                    deployment,
                    now,
                )
            )
            record_service_change(
                unit_of_work,
                context,
                action="service.definition.created",
                deployment=deployment,
                occurred_at=now,
                attributes={"previous_status": "draft"},
            )
            unit_of_work.commit()
            return deployment
    except ServiceWriteConflictError as error:
        replayed = _recover_creation(
            unit_of_work_factory,
            context,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replayed is not None:
            return replayed
        raise_write_conflict(error)


def _new_custom_deployment(
    context: RequestContext,
    *,
    account_id: UUID,
    agent_id: UUID,
    release_id: UUID,
    name: str,
    service_type: ServiceType,
    visibility: str,
    department_ids: tuple[UUID, ...],
    account_ids: tuple[UUID, ...],
    now: datetime,
) -> ServiceDeployment:
    """构造版本 2 的 active 服务及版本 1 的策略和初始路由。"""

    service_id = uuid4()
    policy_id = uuid4()
    route_id = uuid4()
    policy = ServiceAccessPolicyVersion(
        access_policy_version_id=policy_id,
        service_id=service_id,
        workspace_id=context.workspace_id,
        version=1,
        visibility=cast("ServiceAccessVisibility", visibility),
        allowed_department_ids=department_ids,
        allowed_account_ids=account_ids,
        policy_hash=access_policy_digest(
            service_id=service_id,
            version=1,
            visibility=visibility,
            department_ids=department_ids,
            account_ids=account_ids,
        ),
        created_by_account_id=account_id,
        created_at=now,
    )
    service = Service(
        service_id=service_id,
        workspace_id=context.workspace_id,
        agent_id=agent_id,
        service_key=f"{service_type.replace('_', '-')}-{service_id.hex}",
        name=name,
        service_type=service_type,
        status="active",
        access_policy_version_id=policy_id,
        created_by_account_id=account_id,
        created_at=now,
        updated_by_account_id=account_id,
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
        created_by_account_id=account_id,
        created_at=now,
    )
    route = replace(route, route_hash=route_digest(route))
    publication = ServiceRoutePublication(
        service_id=service_id,
        workspace_id=context.workspace_id,
        route_id=route_id,
        generation=1,
        published_by_account_id=account_id,
        published_at=now,
    )
    return ServiceDeployment(service, policy, route, publication)


def _recover_creation(
    unit_of_work_factory: ServiceGovernanceUnitOfWork,
    context: RequestContext,
    *,
    idempotency_key: str,
    request_hash: str,
) -> ServiceDeployment | None:
    """在并发唯一键回滚后读取获胜请求的稳定结果。"""

    with unit_of_work_factory as unit_of_work:
        request = unit_of_work.services.get_request(
            context.workspace_id,
            context.actor_id,
            CREATE_SERVICE_OPERATION,
            idempotency_key,
        )
        if request is None:
            return None
        require_request_hash(request, request_hash)
        return deployment_from_request(request)
