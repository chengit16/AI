"""聚合组织管理服务并向 Router 暴露稳定部门、岗位和成员用例。"""

from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.organization_departments import (
    DepartmentService,
)
from ai_platform_api.modules.identity.application.organization_errors import (
    OrganizationConflictError,
    OrganizationGovernanceDeniedError,
    OrganizationNotFoundError,
    OrganizationValidationError,
)
from ai_platform_api.modules.identity.application.organization_members import (
    MemberOrganizationService,
)
from ai_platform_api.modules.identity.application.organization_positions import PositionService
from ai_platform_api.modules.identity.application.organization_support import governance_account
from ai_platform_api.modules.identity.domain.organization import (
    DepartmentSummary,
    OrganizationAssignment,
    OrganizationUnitOfWork,
    PositionSummary,
)

__all__ = [
    "DepartmentSummary",
    "OrganizationAssignment",
    "OrganizationConflictError",
    "OrganizationGovernanceDeniedError",
    "OrganizationNotFoundError",
    "OrganizationService",
    "OrganizationValidationError",
    "PositionSummary",
]


class OrganizationService:
    """为 Router 提供稳定入口，具体事务按部门、岗位和成员归属分治。"""

    def __init__(self, unit_of_work: OrganizationUnitOfWork) -> None:
        self._departments = DepartmentService(unit_of_work)
        self._positions = PositionService(unit_of_work)
        self._members = MemberOrganizationService(unit_of_work)

    def create_department(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        name: str,
        parent_department_id: UUID | None,
    ) -> DepartmentSummary:
        """委托部门用例创建节点，并统一维护闭包、审计和角色版本。"""

        return self._departments.create(
            context,
            workspace_id=workspace_id,
            name=name,
            parent_department_id=parent_department_id,
        )

    def move_department(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        department_id: UUID,
        parent_department_id: UUID | None,
    ) -> DepartmentSummary:
        """移动部门后重建闭包，检测到环或悬空父节点时整体回滚。"""

        return self._departments.move(
            context,
            workspace_id=workspace_id,
            department_id=department_id,
            parent_department_id=parent_department_id,
        )

    def set_department_status(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        department_id: UUID,
        active: bool,
    ) -> DepartmentSummary:
        """切换部门状态并递增角色版本，使继承权限重新解析。"""

        return self._departments.set_status(
            context,
            workspace_id=workspace_id,
            department_id=department_id,
            active=active,
        )

    def list_departments(
        self, context: RequestContext, *, workspace_id: UUID
    ) -> tuple[DepartmentSummary, ...]:
        """返回含祖先有效状态的部门摘要，停用祖先会关闭整棵子树。"""

        return self._departments.list(context, workspace_id=workspace_id)

    def create_position(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        department_id: UUID,
        name: str,
    ) -> PositionSummary:
        """在有效部门下创建职位，并拒绝跨空间部门引用。"""

        return self._positions.create(
            context,
            workspace_id=workspace_id,
            department_id=department_id,
            name=name,
        )

    def set_position_status(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        position_id: UUID,
        active: bool,
    ) -> PositionSummary:
        """切换职位状态并递增成员归属版本和角色版本。"""

        return self._positions.set_status(
            context,
            workspace_id=workspace_id,
            position_id=position_id,
            active=active,
        )

    def list_positions(
        self, context: RequestContext, *, workspace_id: UUID
    ) -> tuple[PositionSummary, ...]:
        """列出职位及其部门链有效状态，供成员分配前校验。"""

        return self._positions.list(context, workspace_id=workspace_id)

    def assign_member(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
        department_ids: tuple[UUID, ...],
        primary_department_id: UUID | None,
        position_ids: tuple[UUID, ...],
    ) -> OrganizationAssignment:
        """整体替换成员部门和职位归属，主部门必须属于所选部门集合。"""

        return self._members.assign(
            context,
            workspace_id=workspace_id,
            target_account_id=target_account_id,
            department_ids=department_ids,
            primary_department_id=primary_department_id,
            position_ids=position_ids,
        )

    def get_assignment(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
    ) -> OrganizationAssignment:
        """读取成员组织归属并校验请求者具有当前空间访问权。"""

        return self._members.get(
            context,
            workspace_id=workspace_id,
            target_account_id=target_account_id,
        )

    _governance_account = staticmethod(governance_account)
