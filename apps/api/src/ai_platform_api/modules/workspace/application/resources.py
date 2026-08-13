from collections.abc import Callable
from datetime import UTC, datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID, uuid4

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


class AuthorizationDeniedError(Exception):
    """稳定表示策略拒绝，不向调用方泄露内部策略原因。"""


class ResourceNotFoundError(Exception):
    """资源不存在或位于其他工作空间时使用相同结果。"""


class WorkspaceUnitOfWork(Protocol):
    resources: WorkspaceResourceRepository
    outbox: OutboxWriter

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
            payload={"title": resource.title},
        )
        with self._unit_of_work as unit_of_work:
            unit_of_work.resources.add(resource)
            unit_of_work.outbox.add(event)
            unit_of_work.commit()
        return event


class ReadWorkspaceResource:
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
