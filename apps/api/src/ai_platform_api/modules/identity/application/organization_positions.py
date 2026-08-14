"""编排岗位创建与启停事务，并复核所属部门有效性。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.organization_errors import (
    OrganizationConflictError,
)
from ai_platform_api.modules.identity.application.organization_support import (
    find_position,
    governance_account,
    normalized_name,
    organization_facts,
    position_summary,
    require_active_enterprise,
    require_effective_department,
    require_owner,
)
from ai_platform_api.modules.identity.domain.organization import (
    InvalidOrganizationTransitionError,
    OrganizationUnitOfWork,
    OrganizationWriteConflictError,
    Position,
    PositionSummary,
    summarize_departments,
)


class PositionService:
    """管理企业职位创建、启停和按部门有效状态投影。"""

    def __init__(self, unit_of_work: OrganizationUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def create(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        department_id: UUID,
        name: str,
    ) -> PositionSummary:
        """校验企业所有者、有效部门和部门内唯一名称后创建职位。"""

        # 1. 构造规范化职位，存在性、有效性和重名检查在锁定事务内完成。
        account_id = governance_account(context, workspace_id)
        now = datetime.now(UTC)
        position = Position(
            uuid4(), workspace_id, department_id, normalized_name(name), "active", now, now, 1
        )
        try:
            with self._unit_of_work as unit_of_work:
                require_owner(unit_of_work.organization, workspace_id, account_id)
                departments = unit_of_work.organization.list_departments(
                    workspace_id, for_update=True
                )
                require_effective_department(departments, department_id)
                current_positions = unit_of_work.organization.list_positions(
                    workspace_id, for_update=True
                )
                if any(
                    item.department_id == department_id
                    and item.name.casefold() == position.name.casefold()
                    for item in current_positions
                ):
                    raise OrganizationConflictError
                # 2. 校验通过后生成可追踪的职位创建事实。
                event, audit = organization_facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=position.position_id,
                    aggregate_version=1,
                    event_type="organization.position.created",
                    action="organization.position.create",
                    resource_type="position",
                    occurred_at=now,
                    payload={"department_id": str(department_id)},
                    attributes={"status": position.status},
                )
                # 3. 职位、审计与 Outbox 原子提交，约束冲突不留下孤立职位。
                unit_of_work.organization.add_position(position)
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except OrganizationWriteConflictError as error:
            raise OrganizationConflictError from error
        return PositionSummary(
            position.position_id,
            department_id,
            position.name,
            "active",
            True,
            1,
        )

    def set_status(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        position_id: UUID,
        active: bool,
    ) -> PositionSummary:
        """切换职位状态并更新角色版本，停用职位不再参与有效归属。"""

        # 1. 锁定职位并通过领域状态机执行启停，重复状态转换直接拒绝。
        account_id = governance_account(context, workspace_id)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                require_owner(unit_of_work.organization, workspace_id, account_id)
                departments = unit_of_work.organization.list_departments(workspace_id)
                positions = unit_of_work.organization.list_positions(workspace_id, for_update=True)
                current = find_position(positions, position_id)
                updated = (
                    current.activate(occurred_at=now)
                    if active
                    else current.disable(occurred_at=now)
                )
                # 2. 状态变化及前值写入审计和事件，方便解释成员权限变化。
                event, audit = organization_facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=position_id,
                    aggregate_version=updated.version,
                    event_type=f"organization.position.{updated.status}",
                    action="organization.position.status.update",
                    resource_type="position",
                    occurred_at=now,
                    payload={"status": updated.status},
                    attributes={"previous_status": current.status},
                )
                # 3. 职位状态、审计和事件同事务提交。
                unit_of_work.organization.save_position(updated)
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except (InvalidOrganizationTransitionError, OrganizationWriteConflictError) as error:
            raise OrganizationConflictError from error
        effective_departments = {
            item.department_id: item.effective_active for item in summarize_departments(departments)
        }
        return position_summary(updated, effective_departments)

    def list(self, context: RequestContext, *, workspace_id: UUID) -> tuple[PositionSummary, ...]:
        """结合部门有效状态列出职位，避免把停用子树职位显示为可选。"""

        account_id = governance_account(context, workspace_id)
        with self._unit_of_work as unit_of_work:
            direct_owner_call = context.authorized_permission_code is None
            if direct_owner_call:
                require_owner(unit_of_work.organization, workspace_id, account_id)
            else:
                require_active_enterprise(unit_of_work.organization, workspace_id, account_id)
            departments = unit_of_work.organization.list_departments(workspace_id)
            positions = unit_of_work.organization.list_positions(workspace_id)
        effective_departments = {
            item.department_id: item.effective_active for item in summarize_departments(departments)
        }
        summaries = tuple(position_summary(item, effective_departments) for item in positions)
        if direct_owner_call or context.authorized_workspace:
            return summaries
        return tuple(
            item for item in summaries if item.department_id in context.authorized_department_ids
        )
