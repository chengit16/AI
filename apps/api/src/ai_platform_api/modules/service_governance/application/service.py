"""提供稳定服务治理应用接口，并委托给职责清晰的用例模块。"""

from __future__ import annotations

from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.service_governance.application.creation import create_service
from ai_platform_api.modules.service_governance.application.errors import (
    ServiceDeniedError,
    ServiceIdempotencyConflictError,
    ServiceNotFoundError,
    ServiceRouteConflictError,
    ServiceRouteUnavailableError,
    ServiceValidationError,
)
from ai_platform_api.modules.service_governance.application.queries import (
    get_service,
    list_services,
)
from ai_platform_api.modules.service_governance.application.routing import (
    promote_route,
    rollback_route,
    start_canary,
)
from ai_platform_api.modules.service_governance.application.updates import update_service
from ai_platform_api.modules.service_governance.domain.models import (
    CurrentRouteInvalidator,
    ServiceDeployment,
    ServiceGovernanceUnitOfWork,
    ServiceStatus,
    ServiceType,
)

__all__ = [
    "ServiceDeniedError",
    "ServiceDeployment",
    "ServiceGovernanceService",
    "ServiceIdempotencyConflictError",
    "ServiceNotFoundError",
    "ServiceRouteConflictError",
    "ServiceRouteUnavailableError",
    "ServiceValidationError",
]


class ServiceGovernanceService:
    """管理自定义服务定义、当前访问策略和启停状态。"""

    def __init__(
        self,
        unit_of_work: ServiceGovernanceUnitOfWork,
        current_route_invalidator: CurrentRouteInvalidator | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._current_route_invalidator = current_route_invalidator

    def create_service(
        self,
        context: RequestContext,
        *,
        name: str,
        release_id: UUID,
        service_type: ServiceType = "custom_knowledge_agent",
        visibility: str,
        allowed_department_ids: tuple[UUID, ...] = (),
        allowed_account_ids: tuple[UUID, ...] = (),
        idempotency_key: str,
    ) -> ServiceDeployment:
        """从有效自定义 Release 创建活动知识服务和首个路由。"""

        return create_service(
            self._unit_of_work,
            context,
            name=name,
            release_id=release_id,
            service_type=service_type,
            visibility=visibility,
            allowed_department_ids=allowed_department_ids,
            allowed_account_ids=allowed_account_ids,
            idempotency_key=idempotency_key,
        )

    def get_service(
        self,
        context: RequestContext,
        *,
        service_id: UUID,
    ) -> ServiceDeployment:
        """读取工作空间内服务的当前策略和当前路由。"""

        return get_service(self._unit_of_work, context, service_id=service_id)

    def list_services(
        self,
        context: RequestContext,
        *,
        limit: int = 100,
    ) -> tuple[ServiceDeployment, ...]:
        """列出当前主体具有工作空间管理范围的服务。"""

        return list_services(self._unit_of_work, context, limit=limit)

    def update_service(
        self,
        context: RequestContext,
        *,
        service_id: UUID,
        expected_version: int,
        name: str | None = None,
        target_status: ServiceStatus | None = None,
        visibility: str | None = None,
        allowed_department_ids: tuple[UUID, ...] = (),
        allowed_account_ids: tuple[UUID, ...] = (),
        idempotency_key: str,
    ) -> ServiceDeployment:
        """更新服务名称、访问策略或状态，并保留旧策略和路由历史。"""

        return update_service(
            self._unit_of_work,
            self._current_route_invalidator,
            context,
            service_id=service_id,
            expected_version=expected_version,
            name=name,
            target_status=target_status,
            visibility=visibility,
            allowed_department_ids=allowed_department_ids,
            allowed_account_ids=allowed_account_ids,
            idempotency_key=idempotency_key,
        )

    def start_canary(
        self,
        context: RequestContext,
        *,
        service_id: UUID,
        release_id: UUID,
        canary_percent: int,
        expected_generation: int,
        idempotency_key: str,
    ) -> ServiceDeployment:
        """为服务追加或扩大稳定灰度 Route。"""

        return start_canary(
            self._unit_of_work,
            self._current_route_invalidator,
            context,
            service_id=service_id,
            release_id=release_id,
            canary_percent=canary_percent,
            expected_generation=expected_generation,
            idempotency_key=idempotency_key,
        )

    def promote_route(
        self,
        context: RequestContext,
        *,
        service_id: UUID,
        release_id: UUID,
        expected_generation: int,
        idempotency_key: str,
    ) -> ServiceDeployment:
        """把指定有效 Release 切换为服务唯一正式版本。"""

        return promote_route(
            self._unit_of_work,
            self._current_route_invalidator,
            context,
            service_id=service_id,
            release_id=release_id,
            expected_generation=expected_generation,
            idempotency_key=idempotency_key,
        )

    def rollback_route(
        self,
        context: RequestContext,
        *,
        service_id: UUID,
        expected_generation: int,
        idempotency_key: str,
    ) -> ServiceDeployment:
        """追加 rollback Route 并恢复最近稳定 Release。"""

        return rollback_route(
            self._unit_of_work,
            self._current_route_invalidator,
            context,
            service_id=service_id,
            expected_generation=expected_generation,
            idempotency_key=idempotency_key,
        )
