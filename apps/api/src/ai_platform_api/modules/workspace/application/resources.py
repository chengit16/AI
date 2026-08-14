"""编排工作空间资源授权、字段投影、审计和 Outbox 同事务写入。"""

from collections.abc import Callable
from datetime import UTC, datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord, AuditWriter

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.policy import (
    PolicyDecisionPoint,
    PolicyRequest,
    ResourceReference,
)
from ai_platform_api.modules.integration.domain.events import IntegrationEvent, OutboxWriter
from ai_platform_api.modules.workspace.domain.resource import (
    WorkspaceResource,
    WorkspaceResourceRepository,
)


class AuthorizationDeniedError(PlatformError):
    """稳定表示策略拒绝，不向调用方泄露内部策略原因。"""

    error_code = "POLICY_DENIED"


class ResourceNotFoundError(PlatformError):
    """资源不存在或位于其他工作空间时使用相同结果。"""

    error_code = "RESOURCE_NOT_FOUND"


class WorkspaceUnitOfWork(Protocol):
    """约束工作空间单元相关写入、审计和事件使用同一事务边界。"""

    resources: WorkspaceResourceRepository
    outbox: OutboxWriter
    audit: AuditWriter

    def __enter__(self) -> "WorkspaceUnitOfWork": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


EventIdFactory = Callable[[], UUID]


class CreateWorkspaceResource:
    """创建请求工作空间内的资源并同步写入审计与 Outbox。"""

    def __init__(
        self,
        policy: PolicyDecisionPoint,
        unit_of_work: WorkspaceUnitOfWork,
        event_id_factory: EventIdFactory = uuid4,
    ) -> None:
        self._policy = policy
        self._unit_of_work = unit_of_work
        self._event_id_factory = event_id_factory

    def execute(self, context: RequestContext, resource: WorkspaceResource) -> IntegrationEvent:
        if resource.workspace_id != context.workspace_id:
            raise AuthorizationDeniedError
        decision = self._policy.decide(
            PolicyRequest(
                context=context,
                permission_code="workspace.resource.create",
                resource=ResourceReference(
                    resource_type="workspace_resource",
                    resource_id=resource.resource_id,
                    workspace_id=resource.workspace_id,
                ),
            )
        )
        if not decision.allowed:
            raise AuthorizationDeniedError

        event = IntegrationEvent(
            event_id=self._event_id_factory(),
            event_type="workspace.resource.created",
            workspace_id=resource.workspace_id,
            aggregate_id=resource.resource_id,
            aggregate_version=resource.version,
            occurred_at=datetime.now(UTC),
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload={"title": resource.title},
        )
        audit = AuditRecord(
            audit_id=self._event_id_factory(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action="workspace.resource.create",
            resource_type="workspace_resource",
            resource_id=resource.resource_id,
            outcome="succeeded",
            occurred_at=event.occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            # 审计属性只保留版本等非敏感结构，不复制资源标题或受保护字段正文。
            attributes={"resource_version": resource.version},
        )
        with self._unit_of_work as unit_of_work:
            unit_of_work.resources.add(resource)
            unit_of_work.audit.add(audit)
            unit_of_work.outbox.add(event)
            unit_of_work.commit()
        return event


class ReadWorkspaceResource:
    """按可信工作空间上下文读取资源，跨空间标识统一视为不存在。"""

    def __init__(self, policy: PolicyDecisionPoint, unit_of_work: WorkspaceUnitOfWork) -> None:
        self._policy = policy
        self._unit_of_work = unit_of_work

    def execute(self, context: RequestContext, resource_id: UUID) -> WorkspaceResource:
        with self._unit_of_work as unit_of_work:
            resource = unit_of_work.resources.get_scoped(context.workspace_id, resource_id)
            if resource is None:
                raise ResourceNotFoundError
            decision = self._policy.decide(
                PolicyRequest(
                    context=context,
                    permission_code="workspace.resource.read",
                    resource=ResourceReference(
                        resource_type="workspace_resource",
                        resource_id=resource.resource_id,
                        workspace_id=resource.workspace_id,
                    ),
                )
            )
            if not decision.allowed:
                raise AuthorizationDeniedError
            return resource.mask(decision.field_mask)
