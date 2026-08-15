"""提供组织用例共享的成员校验、审计和事件记录规则。"""

from datetime import datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.organization_errors import (
    OrganizationConflictError,
    OrganizationGovernanceDeniedError,
    OrganizationNotFoundError,
    OrganizationValidationError,
)
from ai_platform_api.modules.identity.domain.organization import (
    Department,
    DepartmentSummary,
    OrganizationRepository,
    Position,
    PositionSummary,
    summarize_departments,
)


def normalized_name(value: str) -> str:
    """去除组织名称首尾空白并执行非空和长度限制。"""

    normalized = value.strip()
    if not normalized or len(normalized) > 120:
        raise OrganizationValidationError
    return normalized


def unique_ids(values: tuple[UUID, ...]) -> tuple[UUID, ...]:
    """去重并按 UUID 稳定排序，确保整体替换和事件载荷具有确定性。"""

    return tuple(sorted(set(values), key=lambda value: value.int))


def governance_account(context: RequestContext, workspace_id: UUID) -> UUID:
    """从当前工作空间浏览器会话取得治理账号，拒绝跨空间或 API Key 调用。"""

    if (
        context.user_id is None
        or context.authentication_method != "browser_session"
        or context.workspace_id != workspace_id
    ):
        raise OrganizationGovernanceDeniedError
    return context.user_id


def require_owner(
    repository: OrganizationRepository,
    workspace_id: UUID,
    account_id: UUID,
) -> None:
    """校验活动企业空间和所有者成员身份，作为组织写操作统一前置条件。"""

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
        raise OrganizationGovernanceDeniedError


def require_active_enterprise(
    repository: OrganizationRepository,
    workspace_id: UUID,
    account_id: UUID,
) -> None:
    """校验请求者属于活动企业空间，供已授权的只读组织接口复用。"""

    workspace = repository.get_workspace(workspace_id)
    membership = repository.get_membership(workspace_id, account_id)
    if (
        workspace is None
        or workspace.workspace_type != "enterprise"
        or workspace.status != "active"
        or membership is None
        or membership.status != "active"
    ):
        raise OrganizationGovernanceDeniedError


def find_department(departments: tuple[Department, ...], department_id: UUID) -> Department:
    """在已加载部门树中查找节点，不存在时返回稳定资源错误。"""

    for department in departments:
        if department.department_id == department_id:
            return department
    raise OrganizationNotFoundError


def require_effective_department(departments: tuple[Department, ...], department_id: UUID) -> None:
    """校验部门自身及全部祖先均启用，停用子树不能接受新归属。"""

    summaries = {item.department_id: item for item in summarize_departments(departments)}
    department = summaries.get(department_id)
    if department is None:
        raise OrganizationNotFoundError
    if not department.effective_active:
        raise OrganizationConflictError


def require_parent(departments: tuple[Department, ...], parent_department_id: UUID | None) -> None:
    """校验可选父部门存在且有效，根部门使用空父标识。"""

    if parent_department_id is not None:
        require_effective_department(departments, parent_department_id)


def require_unique_department_name(
    departments: tuple[Department, ...],
    *,
    parent_department_id: UUID | None,
    name: str,
) -> None:
    """在同一父部门下按大小写不敏感规则校验名称唯一。"""

    if any(
        item.parent_department_id == parent_department_id
        and item.name.casefold() == name.casefold()
        for item in departments
    ):
        raise OrganizationConflictError


def department_summary(
    departments: tuple[Department, ...], department_id: UUID
) -> DepartmentSummary:
    """从完整树摘要中取得指定部门，保留祖先有效状态和深度。"""

    for summary in summarize_departments(departments):
        if summary.department_id == department_id:
            return summary
    raise OrganizationNotFoundError


def find_position(positions: tuple[Position, ...], position_id: UUID) -> Position:
    """在已加载职位集合中查找目标，不存在时返回稳定资源错误。"""

    for position in positions:
        if position.position_id == position_id:
            return position
    raise OrganizationNotFoundError


def position_summary(
    position: Position,
    effective_departments: dict[UUID, bool],
) -> PositionSummary:
    """结合职位自身状态和所属部门链状态生成可分配性摘要。"""

    return PositionSummary(
        position.position_id,
        position.department_id,
        position.name,
        position.status,
        position.status == "active" and effective_departments.get(position.department_id, False),
        position.version,
    )


def uuid_value(value: UUID | None) -> str | None:
    """把可选 UUID 转为事件可序列化字符串，并保留空值语义。"""

    return str(value) if value is not None else None


def organization_facts(
    *,
    context: RequestContext,
    workspace_id: UUID,
    aggregate_id: UUID,
    aggregate_version: int,
    event_type: str,
    action: str,
    resource_type: str,
    occurred_at: datetime,
    payload: dict[str, object],
    attributes: dict[str, object],
) -> tuple[IntegrationEvent, AuditRecord]:
    """为组织变化生成共享请求与追踪上下文的事件和审计记录。"""

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
            payload=payload,
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
            attributes=attributes,
        ),
    )
