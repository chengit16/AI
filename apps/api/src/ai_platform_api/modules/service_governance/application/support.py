"""集中服务治理的规范化、摘要、授权和幂等重放规则。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import NoReturn, cast
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.integration.domain.events import IntegrationEvent
from ai_platform_api.modules.service_governance.application.errors import (
    ServiceDeniedError,
    ServiceIdempotencyConflictError,
    ServiceNotFoundError,
    ServiceRouteConflictError,
    ServiceRouteUnavailableError,
    ServiceValidationError,
)
from ai_platform_api.modules.service_governance.domain.models import (
    RoutableAgentRelease,
    Service,
    ServiceAccessPolicyVersion,
    ServiceAccessVisibility,
    ServiceControlOperation,
    ServiceControlRequest,
    ServiceDeployment,
    ServiceGovernanceUnitOfWork,
    ServiceRepository,
    ServiceRoute,
    ServiceRouteMode,
    ServiceRoutePublication,
    ServiceStatus,
    ServiceType,
    ServiceWriteConflictError,
)

IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
CREATE_SERVICE_OPERATION: ServiceControlOperation = "service.create"
UPDATE_SERVICE_OPERATION: ServiceControlOperation = "service.update"
CANARY_SERVICE_ROUTE_OPERATION: ServiceControlOperation = "service.route.canary"
PROMOTE_SERVICE_ROUTE_OPERATION: ServiceControlOperation = "service.route.promote"
ROLLBACK_SERVICE_ROUTE_OPERATION: ServiceControlOperation = "service.route.rollback"


def canonical_json(document: object) -> bytes:
    """编码稳定 JSON，使请求、访问策略和路由摘要可跨进程复算。"""

    try:
        return json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError) as error:
        raise ServiceValidationError from error


def document_digest(document: object) -> str:
    """计算服务治理文档的 SHA-256 内容身份。"""

    return hashlib.sha256(canonical_json(document)).hexdigest()


def normalize_name(value: str) -> str:
    """规范服务名称并保持与契约列长度一致。"""

    normalized = value.strip()
    if not 1 <= len(normalized) <= 120:
        raise ServiceValidationError
    return normalized


def require_idempotency_key(value: str) -> None:
    """拒绝不满足稳定字符集与长度边界的服务写请求幂等键。"""

    if IDEMPOTENCY_PATTERN.fullmatch(value) is None:
        raise ServiceValidationError


def browser_account(context: RequestContext) -> UUID:
    """服务管理只接受认证链建立的浏览器人类主体。"""

    if (
        context.user_id is None
        or context.user_id != context.actor_id
        or context.authentication_method != "browser_session"
    ):
        raise ServiceDeniedError
    return context.user_id


def require_create_scope(context: RequestContext) -> None:
    """创建尚无资源身份，必须具有可信工作空间级管理范围。"""

    if not context.authorized_workspace:
        raise ServiceDeniedError


def require_service_scope(context: RequestContext, service_id: UUID) -> None:
    """读取或修改服务时要求工作空间全域或目标资源授权。"""

    if not context.authorized_workspace and service_id not in context.authorized_resource_ids:
        raise ServiceDeniedError


def normalize_policy_subjects(
    visibility: str,
    allowed_department_ids: tuple[UUID, ...],
    allowed_account_ids: tuple[UUID, ...],
) -> tuple[str, tuple[UUID, ...], tuple[UUID, ...]]:
    """规范访问策略集合并拒绝重复、空限制或工作空间策略携带名单。"""

    if visibility not in {"workspace", "restricted"}:
        raise ServiceValidationError
    departments = tuple(sorted(allowed_department_ids, key=str))
    accounts = tuple(sorted(allowed_account_ids, key=str))
    if len(departments) != len(set(departments)) or len(accounts) != len(set(accounts)):
        raise ServiceValidationError
    if visibility == "workspace" and (departments or accounts):
        raise ServiceValidationError
    if visibility == "restricted" and not (departments or accounts):
        raise ServiceValidationError
    return visibility, departments, accounts


def access_policy_digest(
    *,
    service_id: UUID,
    version: int,
    visibility: str,
    department_ids: tuple[UUID, ...],
    account_ids: tuple[UUID, ...],
) -> str:
    """固定服务、版本和受众集合，防止策略身份被跨服务复用。"""

    return document_digest(
        {
            "service_id": str(service_id),
            "version": version,
            "visibility": visibility,
            "allowed_department_ids": [str(value) for value in department_ids],
            "allowed_account_ids": [str(value) for value in account_ids],
        }
    )


def route_digest(route: ServiceRoute) -> str:
    """固定路由链和 Release 身份，不把可变当前指针计入历史摘要。"""

    return document_digest(
        {
            "service_id": str(route.service_id),
            "workspace_id": str(route.workspace_id),
            "route_version": route.route_version,
            "route_mode": route.route_mode,
            "primary_release_id": str(route.primary_release_id),
            "canary_release_id": (
                str(route.canary_release_id) if route.canary_release_id is not None else None
            ),
            "canary_percent": route.canary_percent,
            "previous_route_id": (
                str(route.previous_route_id) if route.previous_route_id is not None else None
            ),
        }
    )


def require_routable_release(
    release: RoutableAgentRelease | None,
    *,
    expected_kind: str,
) -> RoutableAgentRelease:
    """只接受活动 Agent 的已发布事实，自定义 Release 还必须具有快照摘要。"""

    if (
        release is None
        or release.release_kind != expected_kind
        or release.release_status != "released"
        or release.agent_status != "active"
        or (expected_kind == "custom" and release.snapshot_hash is None)
    ):
        raise ServiceRouteUnavailableError
    return release


def require_deployment(
    repository: ServiceRepository,
    workspace_id: UUID,
    service_id: UUID,
    *,
    for_update: bool = False,
) -> ServiceDeployment:
    """读取同一工作空间的服务、当前策略和当前路由完整事实。"""

    service = repository.get_service(workspace_id, service_id, for_update=for_update)
    if service is None:
        raise ServiceNotFoundError
    policy = repository.get_access_policy(workspace_id, service.access_policy_version_id)
    current = repository.get_current_route(
        workspace_id,
        service_id,
        for_update=for_update,
    )
    if policy is None or current is None:
        raise ServiceRouteUnavailableError
    route, publication = current
    if policy.service_id != service_id or route.service_id != service_id:
        raise ServiceRouteUnavailableError
    if policy.policy_hash != access_policy_digest(
        service_id=service_id,
        version=policy.version,
        visibility=policy.visibility,
        department_ids=policy.allowed_department_ids,
        account_ids=policy.allowed_account_ids,
    ) or route.route_hash != route_digest(route):
        raise ServiceRouteUnavailableError
    return ServiceDeployment(service, policy, route, publication)


def deployment_snapshot(deployment: ServiceDeployment) -> dict[str, object]:
    """把一次写响应冻结为无敏感正文的稳定幂等快照。"""

    service = deployment.service
    policy = deployment.access_policy
    route = deployment.route
    publication = deployment.publication
    return {
        "service": {
            "service_id": str(service.service_id),
            "workspace_id": str(service.workspace_id),
            "agent_id": str(service.agent_id),
            "service_key": service.service_key,
            "name": service.name,
            "service_type": service.service_type,
            "status": service.status,
            "access_policy_version_id": str(service.access_policy_version_id),
            "created_by_account_id": str(service.created_by_account_id),
            "created_at": service.created_at.isoformat(),
            "updated_by_account_id": str(service.updated_by_account_id),
            "updated_at": service.updated_at.isoformat(),
            "version": service.version,
        },
        "access_policy": {
            "access_policy_version_id": str(policy.access_policy_version_id),
            "service_id": str(policy.service_id),
            "workspace_id": str(policy.workspace_id),
            "version": policy.version,
            "visibility": policy.visibility,
            "allowed_department_ids": [str(value) for value in policy.allowed_department_ids],
            "allowed_account_ids": [str(value) for value in policy.allowed_account_ids],
            "policy_hash": policy.policy_hash,
            "created_by_account_id": str(policy.created_by_account_id),
            "created_at": policy.created_at.isoformat(),
        },
        "route": {
            "route_id": str(route.route_id),
            "service_id": str(route.service_id),
            "workspace_id": str(route.workspace_id),
            "route_version": route.route_version,
            "route_mode": route.route_mode,
            "primary_release_id": str(route.primary_release_id),
            "canary_release_id": (
                str(route.canary_release_id) if route.canary_release_id is not None else None
            ),
            "canary_percent": route.canary_percent,
            "previous_route_id": (
                str(route.previous_route_id) if route.previous_route_id is not None else None
            ),
            "route_hash": route.route_hash,
            "created_by_account_id": str(route.created_by_account_id),
            "created_at": route.created_at.isoformat(),
        },
        "publication": {
            "service_id": str(publication.service_id),
            "workspace_id": str(publication.workspace_id),
            "route_id": str(publication.route_id),
            "generation": publication.generation,
            "published_by_account_id": str(publication.published_by_account_id),
            "published_at": publication.published_at.isoformat(),
        },
    }


def deployment_from_request(request: ServiceControlRequest) -> ServiceDeployment:
    """从不可变请求快照恢复原始响应，不回读可能已更新的 Service 当前态。"""

    try:
        root = request.result_snapshot
        raw_service = cast("dict[str, object]", root["service"])
        raw_policy = cast("dict[str, object]", root["access_policy"])
        raw_route = cast("dict[str, object]", root["route"])
        raw_publication = cast("dict[str, object]", root["publication"])
        service = _service_from_snapshot(raw_service)
        policy = _policy_from_snapshot(raw_policy)
        route = _route_from_snapshot(raw_route)
        publication = _publication_from_snapshot(raw_publication)
    except (KeyError, TypeError, ValueError) as error:
        raise ServiceRouteConflictError from error
    deployment = ServiceDeployment(service, policy, route, publication)
    if service.service_id != request.service_id or deployment_snapshot(deployment) != root:
        raise ServiceRouteConflictError
    return deployment


def service_request(
    context: RequestContext,
    operation: ServiceControlOperation,
    idempotency_key: str,
    request_hash: str,
    deployment: ServiceDeployment,
    created_at: datetime,
) -> ServiceControlRequest:
    """创建与服务写入同事务保存的不可变幂等请求事实。"""

    return ServiceControlRequest(
        request_id=uuid4(),
        workspace_id=context.workspace_id,
        actor_id=context.actor_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        service_id=deployment.service.service_id,
        result_snapshot=deployment_snapshot(deployment),
        created_at=created_at,
    )


def require_request_hash(request: ServiceControlRequest, expected_hash: str) -> None:
    """拒绝用相同幂等键提交不同服务管理参数。"""

    if request.request_hash != expected_hash:
        raise ServiceIdempotencyConflictError


def raise_write_conflict(error: ServiceWriteConflictError) -> NoReturn:
    """把数据库竞争转换为稳定服务路由错误并隐藏约束名。"""

    if error.reason == "idempotency":
        raise ServiceIdempotencyConflictError from error
    raise ServiceRouteConflictError from error


def record_service_change(
    unit_of_work: ServiceGovernanceUnitOfWork,
    context: RequestContext,
    *,
    action: str,
    deployment: ServiceDeployment,
    occurred_at: datetime,
    attributes: dict[str, object],
    aggregate_version: int | None = None,
    event_type: str = "service.state.changed",
) -> None:
    """同步记录服务治理审计和 Outbox，不传播访问主体清单。"""

    service = deployment.service
    event_attributes = {
        "service_type": service.service_type,
        "service_status": service.status,
        "service_version": service.version,
        "access_policy_version_id": str(service.access_policy_version_id),
        "route_id": str(deployment.route.route_id),
        "route_version": deployment.route.route_version,
        "primary_release_id": str(deployment.route.primary_release_id),
        **attributes,
    }
    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type="service_definition",
            resource_id=service.service_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes=event_attributes,
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type=event_type,
            workspace_id=context.workspace_id,
            aggregate_id=service.service_id,
            aggregate_version=aggregate_version or service.version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=event_attributes,
        )
    )


def _service_from_snapshot(value: dict[str, object]) -> Service:
    return Service(
        service_id=UUID(str(value["service_id"])),
        workspace_id=UUID(str(value["workspace_id"])),
        agent_id=UUID(str(value["agent_id"])),
        service_key=str(value["service_key"]),
        name=str(value["name"]),
        service_type=cast("ServiceType", value["service_type"]),
        status=cast("ServiceStatus", value["status"]),
        access_policy_version_id=UUID(str(value["access_policy_version_id"])),
        created_by_account_id=UUID(str(value["created_by_account_id"])),
        created_at=datetime.fromisoformat(str(value["created_at"])),
        updated_by_account_id=UUID(str(value["updated_by_account_id"])),
        updated_at=datetime.fromisoformat(str(value["updated_at"])),
        version=int(str(value["version"])),
    )


def _policy_from_snapshot(value: dict[str, object]) -> ServiceAccessPolicyVersion:
    return ServiceAccessPolicyVersion(
        access_policy_version_id=UUID(str(value["access_policy_version_id"])),
        service_id=UUID(str(value["service_id"])),
        workspace_id=UUID(str(value["workspace_id"])),
        version=int(str(value["version"])),
        visibility=cast("ServiceAccessVisibility", value["visibility"]),
        allowed_department_ids=tuple(
            UUID(str(item)) for item in cast("list[object]", value["allowed_department_ids"])
        ),
        allowed_account_ids=tuple(
            UUID(str(item)) for item in cast("list[object]", value["allowed_account_ids"])
        ),
        policy_hash=str(value["policy_hash"]),
        created_by_account_id=UUID(str(value["created_by_account_id"])),
        created_at=datetime.fromisoformat(str(value["created_at"])),
    )


def _route_from_snapshot(value: dict[str, object]) -> ServiceRoute:
    raw_canary = value["canary_release_id"]
    raw_previous = value["previous_route_id"]
    return ServiceRoute(
        route_id=UUID(str(value["route_id"])),
        service_id=UUID(str(value["service_id"])),
        workspace_id=UUID(str(value["workspace_id"])),
        route_version=int(str(value["route_version"])),
        route_mode=cast("ServiceRouteMode", value["route_mode"]),
        primary_release_id=UUID(str(value["primary_release_id"])),
        canary_release_id=UUID(str(raw_canary)) if raw_canary is not None else None,
        canary_percent=int(str(value["canary_percent"])),
        previous_route_id=UUID(str(raw_previous)) if raw_previous is not None else None,
        route_hash=str(value["route_hash"]),
        created_by_account_id=UUID(str(value["created_by_account_id"])),
        created_at=datetime.fromisoformat(str(value["created_at"])),
    )


def _publication_from_snapshot(value: dict[str, object]) -> ServiceRoutePublication:
    return ServiceRoutePublication(
        service_id=UUID(str(value["service_id"])),
        workspace_id=UUID(str(value["workspace_id"])),
        route_id=UUID(str(value["route_id"])),
        generation=int(str(value["generation"])),
        published_by_account_id=UUID(str(value["published_by_account_id"])),
        published_at=datetime.fromisoformat(str(value["published_at"])),
    )
