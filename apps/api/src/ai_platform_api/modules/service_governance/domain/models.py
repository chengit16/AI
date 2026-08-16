"""定义服务、访问策略、不可变路由和当前路由指针事实。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter

from ai_platform_api.modules.integration.domain.events import OutboxWriter

ServiceType = Literal[
    "system_assistant",
    "custom_knowledge_agent",
    "scenario_application",
    "open_api",
]
ServiceStatus = Literal["draft", "active", "suspended", "archived"]
ServiceAccessVisibility = Literal["workspace", "restricted"]
ServiceRouteMode = Literal["active", "canary", "rollback"]
ServiceControlOperation = Literal[
    "service.create",
    "service.update",
    "service.route.canary",
    "service.route.promote",
    "service.route.rollback",
]


class CurrentRouteInvalidator(Protocol):
    """在服务事实提交后清除 Runtime current 派生缓存。"""

    def invalidate_current(self, workspace_id: UUID, service_id: UUID) -> None: ...


@dataclass(frozen=True)
class RoutableAgentRelease:
    """保存判断 Release 是否可进入服务路由所需的最小可信事实。"""

    release_id: UUID
    agent_id: UUID
    workspace_id: UUID
    release_kind: Literal["system", "custom"]
    release_version: int
    release_status: str
    agent_status: str
    snapshot_hash: str | None


@dataclass(frozen=True)
class ServiceAccessPolicyVersion:
    """冻结服务受众范围；策略变化必须创建新版本。"""

    access_policy_version_id: UUID
    service_id: UUID
    workspace_id: UUID
    version: int
    visibility: ServiceAccessVisibility
    allowed_department_ids: tuple[UUID, ...]
    allowed_account_ids: tuple[UUID, ...]
    policy_hash: str
    created_by_account_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class Service:
    """表示工作空间内稳定的服务身份、状态和当前访问策略。"""

    service_id: UUID
    workspace_id: UUID
    agent_id: UUID
    service_key: str
    name: str
    service_type: ServiceType
    status: ServiceStatus
    access_policy_version_id: UUID
    created_by_account_id: UUID
    created_at: datetime
    updated_by_account_id: UUID
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class ServiceRoute:
    """冻结一次服务路由决策；历史版本不能更新或删除。"""

    route_id: UUID
    service_id: UUID
    workspace_id: UUID
    route_version: int
    route_mode: ServiceRouteMode
    primary_release_id: UUID
    canary_release_id: UUID | None
    canary_percent: int
    previous_route_id: UUID | None
    route_hash: str
    created_by_account_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class ServiceRoutePublication:
    """保存服务当前路由指针和并发控制 generation。"""

    service_id: UUID
    workspace_id: UUID
    route_id: UUID
    generation: int
    published_by_account_id: UUID
    published_at: datetime


@dataclass(frozen=True)
class ServiceDeployment:
    """聚合服务管理界面一次读取所需的当前事实。"""

    service: Service
    access_policy: ServiceAccessPolicyVersion
    route: ServiceRoute
    publication: ServiceRoutePublication


@dataclass(frozen=True)
class ServiceControlRequest:
    """冻结服务写请求及原始响应身份，保证重试结果不会漂移。"""

    request_id: UUID
    workspace_id: UUID
    actor_id: UUID
    operation: ServiceControlOperation
    idempotency_key: str
    request_hash: str
    service_id: UUID
    result_snapshot: dict[str, object]
    created_at: datetime


class ServiceWriteConflictError(Exception):
    """表示服务版本、路由版本或幂等唯一约束发生竞争。"""

    def __init__(self, reason: Literal["idempotency", "version", "route", "write"]) -> None:
        self.reason = reason
        super().__init__(reason)


class ServiceRepository(Protocol):
    """独占服务定义、策略版本、路由历史和当前指针的持久化规则。"""

    def get_routable_release(
        self,
        workspace_id: UUID,
        release_id: UUID,
        *,
        for_share: bool = False,
    ) -> RoutableAgentRelease | None: ...

    def subjects_exist(
        self,
        workspace_id: UUID,
        department_ids: tuple[UUID, ...],
        account_ids: tuple[UUID, ...],
    ) -> bool: ...

    def account_can_invoke(
        self,
        workspace_id: UUID,
        service_id: UUID,
        account_id: UUID,
    ) -> bool: ...

    def get_service(
        self,
        workspace_id: UUID,
        service_id: UUID,
        *,
        for_update: bool = False,
    ) -> Service | None: ...

    def get_service_by_key(
        self,
        workspace_id: UUID,
        service_key: str,
        *,
        for_update: bool = False,
    ) -> Service | None: ...

    def list_services(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[Service, ...]: ...

    def get_access_policy(
        self,
        workspace_id: UUID,
        access_policy_version_id: UUID,
    ) -> ServiceAccessPolicyVersion | None: ...

    def next_access_policy_version(self, workspace_id: UUID, service_id: UUID) -> int: ...

    def get_route(
        self,
        workspace_id: UUID,
        route_id: UUID,
    ) -> ServiceRoute | None: ...

    def get_current_route(
        self,
        workspace_id: UUID,
        service_id: UUID,
        *,
        for_update: bool = False,
    ) -> tuple[ServiceRoute, ServiceRoutePublication] | None: ...

    def add_deployment(
        self,
        draft: Service,
        active: Service,
        policy: ServiceAccessPolicyVersion,
        route: ServiceRoute,
        publication: ServiceRoutePublication,
    ) -> None: ...

    def save_service(self, service: Service, *, expected_version: int) -> bool: ...

    def add_access_policy(self, policy: ServiceAccessPolicyVersion) -> None: ...

    def append_route(
        self,
        route: ServiceRoute,
        publication: ServiceRoutePublication,
        *,
        expected_generation: int,
    ) -> bool: ...

    def get_request(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        operation: ServiceControlOperation,
        idempotency_key: str,
    ) -> ServiceControlRequest | None: ...

    def add_request(self, request: ServiceControlRequest) -> None: ...


class ServiceGovernanceUnitOfWork(Protocol):
    """保证服务事实、审计和 Outbox 在同一事务内提交。"""

    @property
    def services(self) -> ServiceRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> ServiceGovernanceUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
