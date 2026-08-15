"""实现工作空间隔离的服务当前事实查询。"""

from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.service_governance.application.errors import (
    ServiceValidationError,
)
from ai_platform_api.modules.service_governance.application.support import (
    browser_account,
    require_deployment,
    require_service_scope,
)
from ai_platform_api.modules.service_governance.domain.models import (
    ServiceDeployment,
    ServiceGovernanceUnitOfWork,
)


def get_service(
    unit_of_work_factory: ServiceGovernanceUnitOfWork,
    context: RequestContext,
    *,
    service_id: UUID,
) -> ServiceDeployment:
    """读取服务、当前策略和路由，跨空间目标使用不存在语义。"""

    browser_account(context)
    require_service_scope(context, service_id)
    with unit_of_work_factory as unit_of_work:
        return require_deployment(unit_of_work.services, context.workspace_id, service_id)


def list_services(
    unit_of_work_factory: ServiceGovernanceUnitOfWork,
    context: RequestContext,
    *,
    limit: int,
) -> tuple[ServiceDeployment, ...]:
    """按更新时间列出当前主体可管理的服务，不返回访问主体明细之外的敏感数据。"""

    browser_account(context)
    if not context.authorized_workspace or not 1 <= limit <= 200:
        raise ServiceValidationError
    with unit_of_work_factory as unit_of_work:
        services = unit_of_work.services.list_services(context.workspace_id, limit=limit)
        return tuple(
            require_deployment(unit_of_work.services, context.workspace_id, service.service_id)
            for service in services
        )
