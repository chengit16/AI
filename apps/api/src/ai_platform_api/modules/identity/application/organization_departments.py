"""编排部门创建、移动和启停事务及组织树约束。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.organization_errors import (
    OrganizationConflictError,
)
from ai_platform_api.modules.identity.application.organization_support import (
    department_summary,
    find_department,
    governance_account,
    normalized_name,
    organization_facts,
    require_active_enterprise,
    require_owner,
    require_parent,
    require_unique_department_name,
    uuid_value,
)
from ai_platform_api.modules.identity.domain.organization import (
    Department,
    DepartmentCycleError,
    DepartmentSummary,
    InvalidOrganizationTransitionError,
    OrganizationUnitOfWork,
    OrganizationWriteConflictError,
    build_department_closure,
    summarize_departments,
)


class DepartmentService:
    """维护部门邻接树与闭包，并保证两类事实在同一事务一致。"""

    def __init__(self, unit_of_work: OrganizationUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def create(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        name: str,
        parent_department_id: UUID | None,
    ) -> DepartmentSummary:
        """校验企业所有者、父部门和同级唯一名称后创建部门并重建闭包。"""

        # 1. 规范化新部门事实，授权和树结构校验留在锁定事务内完成。
        account_id = governance_account(context, workspace_id)
        now = datetime.now(UTC)
        department = Department(
            uuid4(),
            workspace_id,
            parent_department_id,
            normalized_name(name),
            "active",
            now,
            now,
            1,
        )
        try:
            with self._unit_of_work as unit_of_work:
                require_owner(unit_of_work.organization, workspace_id, account_id)
                departments = unit_of_work.organization.list_departments(
                    workspace_id, for_update=True
                )
                require_parent(departments, parent_department_id)
                require_unique_department_name(
                    departments,
                    parent_department_id=parent_department_id,
                    name=department.name,
                )
                # 2. 先在内存中构建候选树，闭包检测失败时不会写入邻接节点。
                updated_departments = (*departments, department)
                event, audit = organization_facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=department.department_id,
                    aggregate_version=1,
                    event_type="organization.department.created",
                    action="organization.department.create",
                    resource_type="department",
                    occurred_at=now,
                    payload={"parent_department_id": uuid_value(parent_department_id)},
                    attributes={"status": department.status},
                )
                # 3. 邻接节点、完整闭包、审计和 Outbox 必须在同一事务切换。
                unit_of_work.organization.add_department(department)
                unit_of_work.organization.replace_department_closure(
                    workspace_id, build_department_closure(updated_departments)
                )
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except (DepartmentCycleError, OrganizationWriteConflictError) as error:
            raise OrganizationConflictError from error
        return department_summary(updated_departments, department.department_id)

    def move(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        department_id: UUID,
        parent_department_id: UUID | None,
    ) -> DepartmentSummary:
        """锁定部门树后移动节点，形成环、跨空间或同级重名时拒绝提交。"""

        # 1. 锁定整棵部门树后校验所有者、目标节点、父节点和同级唯一名称。
        account_id = governance_account(context, workspace_id)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                require_owner(unit_of_work.organization, workspace_id, account_id)
                departments = unit_of_work.organization.list_departments(
                    workspace_id, for_update=True
                )
                current = find_department(departments, department_id)
                require_parent(departments, parent_department_id)
                require_unique_department_name(
                    tuple(item for item in departments if item.department_id != department_id),
                    parent_department_id=parent_department_id,
                    name=current.name,
                )
                # 2. 在内存中替换父节点并重建闭包，任何环或悬空引用都会失败关闭。
                moved = current.move(parent_department_id=parent_department_id, occurred_at=now)
                updated_departments = tuple(
                    moved if item.department_id == department_id else item for item in departments
                )
                event, audit = organization_facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=department_id,
                    aggregate_version=moved.version,
                    event_type="organization.department.moved",
                    action="organization.department.move",
                    resource_type="department",
                    occurred_at=now,
                    payload={"parent_department_id": uuid_value(parent_department_id)},
                    attributes={
                        "previous_parent_department_id": uuid_value(current.parent_department_id)
                    },
                )
                # 3. 邻接树、闭包和角色版本同步提交，避免权限继承读取到混合版本。
                unit_of_work.organization.save_department(moved)
                unit_of_work.organization.replace_department_closure(
                    workspace_id, build_department_closure(updated_departments)
                )
                unit_of_work.organization.bump_role_version(workspace_id)
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except (
            DepartmentCycleError,
            InvalidOrganizationTransitionError,
            OrganizationWriteConflictError,
        ) as error:
            raise OrganizationConflictError from error
        return department_summary(updated_departments, department_id)

    def set_status(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        department_id: UUID,
        active: bool,
    ) -> DepartmentSummary:
        """切换部门状态并更新角色版本，使部门继承授权即时失效。"""

        # 1. 锁定部门树并由领域状态机执行启停转换。
        account_id = governance_account(context, workspace_id)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                require_owner(unit_of_work.organization, workspace_id, account_id)
                departments = unit_of_work.organization.list_departments(
                    workspace_id, for_update=True
                )
                current = find_department(departments, department_id)
                updated = (
                    current.activate(occurred_at=now)
                    if active
                    else current.disable(occurred_at=now)
                )
                updated_departments = tuple(
                    updated if item.department_id == department_id else item for item in departments
                )
                # 2. 状态事件记录前后值，便于审计角色继承为何发生变化。
                event, audit = organization_facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=department_id,
                    aggregate_version=updated.version,
                    event_type=f"organization.department.{updated.status}",
                    action="organization.department.status.update",
                    resource_type="department",
                    occurred_at=now,
                    payload={"status": updated.status},
                    attributes={"previous_status": current.status},
                )
                # 3. 部门状态与角色版本原子提交，缓存按新版本自然失效。
                unit_of_work.organization.save_department(updated)
                unit_of_work.organization.bump_role_version(workspace_id)
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except (InvalidOrganizationTransitionError, OrganizationWriteConflictError) as error:
            raise OrganizationConflictError from error
        return department_summary(updated_departments, department_id)

    def list(self, context: RequestContext, *, workspace_id: UUID) -> tuple[DepartmentSummary, ...]:
        """计算部门树深度和祖先有效状态后返回稳定排序摘要。"""

        account_id = governance_account(context, workspace_id)
        with self._unit_of_work as unit_of_work:
            if context.authorized_permission_code is None:
                require_owner(unit_of_work.organization, workspace_id, account_id)
                return summarize_departments(
                    unit_of_work.organization.list_departments(workspace_id)
                )
            require_active_enterprise(unit_of_work.organization, workspace_id, account_id)
            summaries = summarize_departments(
                unit_of_work.organization.list_departments(workspace_id)
            )
            if context.authorized_workspace:
                return summaries
            return tuple(
                item
                for item in summaries
                if item.department_id in context.authorized_department_ids
            )
