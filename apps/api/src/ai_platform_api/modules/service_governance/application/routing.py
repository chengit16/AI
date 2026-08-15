"""实现服务灰度、正式晋级和追加式回滚的原子路由切换。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.service_governance.application.errors import (
    ServiceRouteConflictError,
    ServiceRouteUnavailableError,
    ServiceValidationError,
)
from ai_platform_api.modules.service_governance.application.support import (
    CANARY_SERVICE_ROUTE_OPERATION,
    PROMOTE_SERVICE_ROUTE_OPERATION,
    ROLLBACK_SERVICE_ROUTE_OPERATION,
    browser_account,
    deployment_from_request,
    document_digest,
    raise_write_conflict,
    record_service_change,
    require_deployment,
    require_idempotency_key,
    require_request_hash,
    require_routable_release,
    require_service_scope,
    route_digest,
    service_request,
)
from ai_platform_api.modules.service_governance.domain.models import (
    CurrentRouteInvalidator,
    ServiceControlOperation,
    ServiceDeployment,
    ServiceGovernanceUnitOfWork,
    ServiceRepository,
    ServiceRoute,
    ServiceRouteMode,
    ServiceRoutePublication,
    ServiceWriteConflictError,
)

RouteChangeKind = Literal["canary", "promote", "rollback"]


@dataclass(frozen=True)
class RouteChangeIntent:
    """保存一次路由操作的规范参数，不携带可变当前态。"""

    kind: RouteChangeKind
    operation: ServiceControlOperation
    target_release_id: UUID | None
    canary_percent: int | None


def start_canary(
    unit_of_work_factory: ServiceGovernanceUnitOfWork,
    invalidator: CurrentRouteInvalidator | None,
    context: RequestContext,
    *,
    service_id: UUID,
    release_id: UUID,
    canary_percent: int,
    expected_generation: int,
    idempotency_key: str,
) -> ServiceDeployment:
    """追加灰度 Route；提高比例时保持原 primary 和灰度 Release 不变。"""

    if not 1 <= canary_percent <= 99:
        raise ServiceValidationError
    return _change_route(
        unit_of_work_factory,
        invalidator,
        context,
        service_id=service_id,
        expected_generation=expected_generation,
        idempotency_key=idempotency_key,
        intent=RouteChangeIntent(
            "canary",
            CANARY_SERVICE_ROUTE_OPERATION,
            release_id,
            canary_percent,
        ),
    )


def promote_route(
    unit_of_work_factory: ServiceGovernanceUnitOfWork,
    invalidator: CurrentRouteInvalidator | None,
    context: RequestContext,
    *,
    service_id: UUID,
    release_id: UUID,
    expected_generation: int,
    idempotency_key: str,
) -> ServiceDeployment:
    """把指定有效 Release 原子切为服务唯一正式版本。"""

    return _change_route(
        unit_of_work_factory,
        invalidator,
        context,
        service_id=service_id,
        expected_generation=expected_generation,
        idempotency_key=idempotency_key,
        intent=RouteChangeIntent(
            "promote",
            PROMOTE_SERVICE_ROUTE_OPERATION,
            release_id,
            None,
        ),
    )


def rollback_route(
    unit_of_work_factory: ServiceGovernanceUnitOfWork,
    invalidator: CurrentRouteInvalidator | None,
    context: RequestContext,
    *,
    service_id: UUID,
    expected_generation: int,
    idempotency_key: str,
) -> ServiceDeployment:
    """创建新的 rollback Route，恢复操作前最近的稳定 Release。"""

    return _change_route(
        unit_of_work_factory,
        invalidator,
        context,
        service_id=service_id,
        expected_generation=expected_generation,
        idempotency_key=idempotency_key,
        intent=RouteChangeIntent(
            "rollback",
            ROLLBACK_SERVICE_ROUTE_OPERATION,
            None,
            None,
        ),
    )


def _change_route(
    unit_of_work_factory: ServiceGovernanceUnitOfWork,
    invalidator: CurrentRouteInvalidator | None,
    context: RequestContext,
    *,
    service_id: UUID,
    expected_generation: int,
    idempotency_key: str,
    intent: RouteChangeIntent,
) -> ServiceDeployment:
    """冻结请求并在提交后主动清除所有 Runtime current 分桶。"""

    # 1. 先验证可信主体和并发前提，再把完整意图写入稳定请求摘要。
    browser_account(context)
    require_service_scope(context, service_id)
    require_idempotency_key(idempotency_key)
    if expected_generation < 1:
        raise ServiceValidationError
    request_hash = document_digest(
        {
            "operation": intent.operation,
            "service_id": str(service_id),
            "expected_generation": expected_generation,
            "release_id": (
                str(intent.target_release_id) if intent.target_release_id is not None else None
            ),
            "canary_percent": intent.canary_percent,
        }
    )
    # 2. 数据库写入或幂等恢复完成后再失效缓存，缓存不能参与发布事务成败。
    try:
        deployment = _write_route_change(
            unit_of_work_factory,
            context,
            service_id=service_id,
            expected_generation=expected_generation,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            intent=intent,
        )
    except ServiceWriteConflictError as error:
        replayed = _recover_route_change(
            unit_of_work_factory,
            context,
            operation=intent.operation,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replayed is None:
            raise_write_conflict(error)
        deployment = replayed
    # 数据库已经提交或确认了同一幂等结果；缓存失效不得进入发布事务或反向撤销事实。
    if invalidator is not None:
        invalidator.invalidate_current(context.workspace_id, service_id)
    return deployment


def _write_route_change(
    unit_of_work_factory: ServiceGovernanceUnitOfWork,
    context: RequestContext,
    *,
    service_id: UUID,
    expected_generation: int,
    idempotency_key: str,
    request_hash: str,
    intent: RouteChangeIntent,
) -> ServiceDeployment:
    """在一个事务内完成重放、锁定、Route 追加、指针切换和事件写入。"""

    now = datetime.now(UTC)
    with unit_of_work_factory as unit_of_work:
        # 1. 幂等重放先于当前态校验，已成功请求不会因后续 Route 变化失去原始响应。
        request = unit_of_work.services.get_request(
            context.workspace_id,
            context.actor_id,
            intent.operation,
            idempotency_key,
        )
        if request is not None:
            require_request_hash(request, request_hash)
            return deployment_from_request(request)

        # 2. Service 与 publication 使用统一锁顺序；相同 generation 的第二个发布者随后必定冲突。
        current = require_deployment(
            unit_of_work.services,
            context.workspace_id,
            service_id,
            for_update=True,
        )
        if (
            current.service.status != "active"
            or current.publication.generation != expected_generation
        ):
            raise ServiceRouteConflictError
        route = _next_route(
            unit_of_work.services,
            current,
            intent,
            actor_id=context.actor_id,
            now=now,
        )
        publication = ServiceRoutePublication(
            service_id=service_id,
            workspace_id=context.workspace_id,
            route_id=route.route_id,
            generation=expected_generation + 1,
            published_by_account_id=context.actor_id,
            published_at=now,
        )
        if not unit_of_work.services.append_route(
            route,
            publication,
            expected_generation=expected_generation,
        ):
            raise ServiceRouteConflictError

        # 3. Service 版本与路由 generation 一起推进，保证聚合事件版本严格单调。
        updated_service = replace(
            current.service,
            updated_by_account_id=context.actor_id,
            updated_at=now,
            version=current.service.version + 1,
        )
        if not unit_of_work.services.save_service(
            updated_service,
            expected_version=current.service.version,
        ):
            raise ServiceRouteConflictError
        deployment = ServiceDeployment(
            updated_service,
            current.access_policy,
            route,
            publication,
        )
        unit_of_work.services.add_request(
            service_request(
                context,
                intent.operation,
                idempotency_key,
                request_hash,
                deployment,
                now,
            )
        )
        action, event_type = _route_event(intent.kind)
        record_service_change(
            unit_of_work,
            context,
            action=action,
            deployment=deployment,
            occurred_at=now,
            attributes={
                "previous_route_id": str(current.route.route_id),
                "previous_route_version": current.route.route_version,
                "route_mode": route.route_mode,
                "canary_release_id": (
                    str(route.canary_release_id) if route.canary_release_id is not None else None
                ),
                "canary_percent": route.canary_percent,
                "publication_generation": publication.generation,
            },
            event_type=event_type,
        )
        unit_of_work.commit()
        return deployment


def _next_route(
    repository: ServiceRepository,
    current: ServiceDeployment,
    intent: RouteChangeIntent,
    *,
    actor_id: UUID,
    now: datetime,
) -> ServiceRoute:
    """从受锁当前态推导下一条 Route，并重新验证将被引用的 Release。"""

    if intent.kind == "canary":
        primary_id, canary_id, percent, mode = _canary_target(repository, current, intent)
    elif intent.kind == "promote":
        primary_id = _promote_target(repository, current, intent)
        canary_id, percent, mode = None, 0, "active"
    else:
        primary_id = _rollback_target(repository, current)
        canary_id, percent, mode = None, 0, "rollback"
    route = ServiceRoute(
        route_id=uuid4(),
        service_id=current.service.service_id,
        workspace_id=current.service.workspace_id,
        route_version=current.route.route_version + 1,
        route_mode=mode,
        primary_release_id=primary_id,
        canary_release_id=canary_id,
        canary_percent=percent,
        previous_route_id=current.route.route_id,
        route_hash="",
        created_by_account_id=actor_id,
        created_at=now,
    )
    return replace(route, route_hash=route_digest(route))


def _canary_target(
    repository: ServiceRepository,
    current: ServiceDeployment,
    intent: RouteChangeIntent,
) -> tuple[UUID, UUID, int, ServiceRouteMode]:
    release_id = intent.target_release_id
    percent = intent.canary_percent
    if release_id is None or percent is None or release_id == current.route.primary_release_id:
        raise ServiceValidationError
    if current.route.route_mode == "canary" and current.route.canary_release_id != release_id:
        raise ServiceRouteConflictError
    if current.route.route_mode == "canary" and current.route.canary_percent == percent:
        raise ServiceValidationError
    _require_service_release(repository, current, current.route.primary_release_id)
    _require_service_release(repository, current, release_id)
    return current.route.primary_release_id, release_id, percent, "canary"


def _promote_target(
    repository: ServiceRepository,
    current: ServiceDeployment,
    intent: RouteChangeIntent,
) -> UUID:
    release_id = intent.target_release_id
    if release_id is None or release_id == current.route.primary_release_id:
        raise ServiceValidationError
    _require_service_release(repository, current, release_id)
    return release_id


def _rollback_target(
    repository: ServiceRepository,
    current: ServiceDeployment,
) -> UUID:
    if current.route.route_mode == "rollback":
        raise ServiceRouteConflictError
    if current.route.route_mode == "canary":
        release_id = current.route.primary_release_id
    else:
        previous_id = current.route.previous_route_id
        if previous_id is None:
            raise ServiceRouteUnavailableError
        previous = repository.get_route(current.service.workspace_id, previous_id)
        if previous is None or previous.route_version != current.route.route_version - 1:
            raise ServiceRouteUnavailableError
        release_id = previous.primary_release_id
    _require_service_release(repository, current, release_id)
    return release_id


def _require_service_release(
    repository: ServiceRepository,
    current: ServiceDeployment,
    release_id: UUID,
) -> None:
    release = require_routable_release(
        repository.get_routable_release(
            current.service.workspace_id,
            release_id,
            for_share=True,
        ),
        expected_kind=(
            "system" if current.service.service_type == "system_assistant" else "custom"
        ),
    )
    if release.agent_id != current.service.agent_id:
        raise ServiceRouteUnavailableError


def _route_event(kind: RouteChangeKind) -> tuple[str, str]:
    return {
        "canary": ("service.route.canary_started", "service.route.canary_started"),
        "promote": ("service.route.promoted", "service.route.promoted"),
        "rollback": ("service.route.rolled_back", "service.route.rolled_back"),
    }[kind]


def _recover_route_change(
    unit_of_work_factory: ServiceGovernanceUnitOfWork,
    context: RequestContext,
    *,
    operation: ServiceControlOperation,
    idempotency_key: str,
    request_hash: str,
) -> ServiceDeployment | None:
    """唯一键竞争回滚后，只恢复完全匹配的获胜请求结果。"""

    with unit_of_work_factory as unit_of_work:
        request = unit_of_work.services.get_request(
            context.workspace_id,
            context.actor_id,
            operation,
            idempotency_key,
        )
        if request is None:
            return None
        require_request_hash(request, request_hash)
        return deployment_from_request(request)
