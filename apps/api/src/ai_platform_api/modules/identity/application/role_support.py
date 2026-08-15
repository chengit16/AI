"""提供角色用例共享的主体授权、审计和缓存失效规则。"""

from datetime import datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.role_errors import (
    RoleConflictError,
    RoleGovernanceDeniedError,
    RoleNotFoundError,
    RoleValidationError,
)
from ai_platform_api.modules.identity.domain.roles import Role, RoleRepository


def browser_account(context: RequestContext, workspace_id: UUID) -> UUID:
    """从当前工作空间浏览器会话取得账号，拒绝 API Key 和跨空间上下文。"""

    if (
        context.user_id is None
        or context.authentication_method != "browser_session"
        or context.workspace_id != workspace_id
    ):
        raise RoleGovernanceDeniedError
    return context.user_id


def require_owner(repository: RoleRepository, workspace_id: UUID, account_id: UUID) -> None:
    """校验活动企业空间和所有者成员身份，个人空间与普通成员均拒绝。"""

    workspace = repository.get_workspace(workspace_id, for_update=True)
    membership = repository.get_membership(workspace_id, account_id, for_update=True)
    if (
        workspace is None
        or workspace.workspace_type != "enterprise"
        or workspace.status != "active"
        or membership is None
        or membership.status != "active"
        or membership.membership_type != "owner"
    ):
        raise RoleGovernanceDeniedError


def normalized_role_name(value: str) -> str:
    """去除角色名称首尾空白并执行非空和长度限制。"""

    normalized = value.strip()
    if not normalized or len(normalized) > 120:
        raise RoleValidationError
    return normalized


def find_role(roles: tuple[Role, ...], role_id: UUID) -> Role:
    """按标识查找当前工作空间角色，不存在时返回稳定资源错误。"""

    for role in roles:
        if role.role_id == role_id:
            return role
    raise RoleNotFoundError


def role_facts(
    *,
    context: RequestContext,
    workspace_id: UUID,
    aggregate_id: UUID,
    aggregate_version: int,
    event_type: str,
    action: str,
    resource_type: str,
    occurred_at: datetime,
    role_version: int,
    attributes: dict[str, object],
) -> tuple[IntegrationEvent, AuditRecord]:
    """为角色或绑定变化生成共享追踪上下文的事件与审计记录。"""

    return (
        IntegrationEvent(
            event_id=uuid4(),
            event_type=event_type,
            workspace_id=workspace_id,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload={"role_version": role_version},
        ),
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=aggregate_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes={**attributes, "role_version": role_version},
        ),
    )


def next_role_version(repository: RoleRepository, workspace_id: UUID) -> int:
    """在乐观锁保护下递增角色版本，并把并发冲突转换为业务冲突。"""

    current = repository.get_role_version(workspace_id, for_update=True)
    if current is None:
        raise RoleNotFoundError
    try:
        return repository.bump_role_version(workspace_id, current)
    except ValueError as error:
        raise RoleConflictError from error
