"""编排团队聚合读取、邀请撤销和成员一体化治理事务。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.enterprise import (
    WorkspaceGovernanceDeniedError,
    WorkspaceGovernanceNotFoundError,
    WorkspaceGovernanceValidationError,
    WorkspaceLifecycleConflictError,
)
from ai_platform_api.modules.identity.application.team_management_views import (
    InvitationLifecycleView,
    MembershipLifecycleView,
    TeamManagementView,
    invitation_lifecycle_view,
    membership_lifecycle_view,
    team_management_view,
)
from ai_platform_api.modules.identity.domain.enterprise import (
    InvalidInvitationTransitionError,
    InvalidMembershipTransitionError,
    WorkspaceMembership,
)
from ai_platform_api.modules.identity.domain.organization import (
    DepartmentSummary,
    PositionSummary,
)
from ai_platform_api.modules.identity.domain.roles import Role
from ai_platform_api.modules.identity.domain.team_management import (
    TeamManagementRepository,
    TeamManagementUnitOfWork,
    TeamManagementWriteConflictError,
)


class TeamManagementService:
    """隐藏团队读模型、多表校验、乐观锁和治理事实写入细节。"""

    def __init__(self, unit_of_work: TeamManagementUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def get_snapshot(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        audit_limit: int = 20,
    ) -> TeamManagementView:
        """返回同一事务口径的团队、邀请、组织、角色和审计聚合。"""

        account_id = self._account(context, workspace_id)
        if (
            context.authorized_permission_code != "workspace.team.read"
            or not context.authorized_workspace
            or "account_id" in context.authorized_field_mask
            or not 1 <= audit_limit <= 100
        ):
            raise WorkspaceGovernanceDeniedError
        generated_at = datetime.now(UTC)
        with self._unit_of_work as unit_of_work:
            self._require_owner(unit_of_work.team, workspace_id, account_id)
            snapshot = unit_of_work.team.get_snapshot(
                workspace_id,
                generated_at=generated_at,
                audit_limit=audit_limit,
                mask_display_name="display_name" in context.authorized_field_mask,
                mask_login_name="login_name" in context.authorized_field_mask,
            )
            if snapshot is None:
                raise WorkspaceGovernanceDeniedError
            return team_management_view(snapshot)

    def cancel_invitation(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        invitation_id: UUID,
    ) -> InvitationLifecycleView:
        """撤销当前空间仍有效的待处理邀请，过期或已处理邀请拒绝变更。"""

        # 1. 先复核邀请资源级授权，其他邀请的允许决策不得复用于当前目标。
        account_id = self._authorized_account(
            context,
            workspace_id,
            "workspace.invitation.cancel",
            resource_id=invitation_id,
        )
        now = datetime.now(UTC)
        # 2. 锁定邀请后由领域状态机撤销，邀请、审计和 Outbox 在同一事务提交。
        try:
            with self._unit_of_work as unit_of_work:
                self._require_owner(unit_of_work.team, workspace_id, account_id)
                invitation = unit_of_work.team.get_invitation(
                    workspace_id, invitation_id, for_update=True
                )
                if invitation is None:
                    raise WorkspaceGovernanceNotFoundError
                cancelled = invitation.cancel(occurred_at=now)
                event, audit = self._facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=invitation_id,
                    aggregate_version=1,
                    event_type="workspace.invitation.cancelled",
                    action="workspace.invitation.cancel",
                    resource_type="workspace_invitation",
                    occurred_at=now,
                    payload={"invitation_id": str(invitation_id)},
                    attributes={"previous_status": invitation.status},
                )
                unit_of_work.team.save_invitation(cancelled)
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except (InvalidInvitationTransitionError, TeamManagementWriteConflictError) as error:
            raise WorkspaceLifecycleConflictError from error
        return invitation_lifecycle_view(cancelled)

    def update_member(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
        expected_version: int,
        department_ids: tuple[UUID, ...],
        primary_department_id: UUID | None,
        position_ids: tuple[UUID, ...],
        direct_role_ids: tuple[UUID, ...],
    ) -> MembershipLifecycleView:
        """原子替换成员部门、岗位和自定义直接角色，禁止产生中间授权态。"""

        # 1. 先在事务外规范化集合与主部门约束，重复或自相矛盾的请求不能触碰授权事实。
        account_id = self._authorized_account(
            context,
            workspace_id,
            "workspace.member.update",
            resource_id=target_account_id,
        )
        normalized_departments = self._unique(department_ids)
        normalized_positions = self._unique(position_ids)
        normalized_roles = self._unique(direct_role_ids)
        if (
            expected_version < 1
            or bool(normalized_departments) != (primary_department_id is not None)
            or (
                primary_department_id is not None
                and primary_department_id not in normalized_departments
            )
        ):
            raise WorkspaceGovernanceValidationError
        now = datetime.now(UTC)
        # 2. 在同一锁定事务中校验引用，并整体替换组织、角色和成员版本。
        try:
            with self._unit_of_work as unit_of_work:
                self._require_owner(unit_of_work.team, workspace_id, account_id)
                membership = self._editable_member(
                    unit_of_work.team, workspace_id, target_account_id
                )
                departments = unit_of_work.team.list_departments(workspace_id, for_update=True)
                positions = unit_of_work.team.list_positions(workspace_id, for_update=True)
                roles = unit_of_work.team.list_roles(workspace_id, for_update=True)
                self._validate_configuration(
                    department_ids=normalized_departments,
                    position_ids=normalized_positions,
                    direct_role_ids=normalized_roles,
                    departments=departments,
                    positions=positions,
                    roles=roles,
                )
                updated = unit_of_work.team.replace_member_configuration(
                    workspace_id=workspace_id,
                    membership=membership,
                    expected_version=expected_version,
                    department_ids=normalized_departments,
                    primary_department_id=primary_department_id,
                    position_ids=normalized_positions,
                    direct_role_ids=normalized_roles,
                    occurred_at=now,
                )
                # 3. 授权事实更新成功后再追加审计与 Outbox，最终由同一事务统一提交。
                event, audit = self._facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=membership.membership_id,
                    aggregate_version=updated.version,
                    event_type="workspace.member.configuration.updated",
                    action="workspace.member.configuration.update",
                    resource_type="organization_assignment",
                    occurred_at=now,
                    payload={
                        "membership_id": str(membership.membership_id),
                        "role_ids": [str(value) for value in normalized_roles],
                    },
                    attributes={
                        "department_count": len(normalized_departments),
                        "position_count": len(normalized_positions),
                        "direct_role_count": len(normalized_roles),
                    },
                )
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except TeamManagementWriteConflictError as error:
            raise WorkspaceLifecycleConflictError from error
        return membership_lifecycle_view(updated)

    def activate_member(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
    ) -> MembershipLifecycleView:
        """恢复被停用成员并重新启用其保留的组织和直接角色配置。"""

        return self._change_member_status(
            context,
            workspace_id=workspace_id,
            target_account_id=target_account_id,
            action="activate",
        )

    def remove_member(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
    ) -> MembershipLifecycleView:
        """把成员置为离开终态并清理组织与自定义直接角色关系。"""

        return self._change_member_status(
            context,
            workspace_id=workspace_id,
            target_account_id=target_account_id,
            action="remove",
        )

    def _change_member_status(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
        action: str,
    ) -> MembershipLifecycleView:
        # 1. 动作只映射到固定权限和事件，调用方不能用任意字符串扩展治理能力。
        action_facts = {
            "activate": ("workspace.member.activate", "workspace.member.activated"),
            "remove": ("workspace.member.remove", "workspace.member.removed"),
        }
        try:
            permission_code, event_type = action_facts[action]
        except KeyError as error:
            raise WorkspaceGovernanceValidationError from error
        account_id = self._authorized_account(
            context,
            workspace_id,
            permission_code,
            resource_id=target_account_id,
        )
        now = datetime.now(UTC)
        # 2. 锁定成员后通过领域状态机转换，成员事实、撤权版本、审计和 Outbox 原子提交。
        try:
            with self._unit_of_work as unit_of_work:
                self._require_owner(unit_of_work.team, workspace_id, account_id)
                membership = unit_of_work.team.get_membership(
                    workspace_id, target_account_id, for_update=True
                )
                if membership is None:
                    raise WorkspaceGovernanceNotFoundError
                updated = (
                    membership.reactivate(occurred_at=now)
                    if action == "activate"
                    else membership.remove(occurred_at=now)
                )
                event, audit = self._facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=membership.membership_id,
                    aggregate_version=updated.version,
                    event_type=event_type,
                    action=f"workspace.member.{action}",
                    resource_type="workspace_membership",
                    occurred_at=now,
                    payload={"membership_id": str(membership.membership_id)},
                    attributes={"previous_status": membership.status},
                )
                unit_of_work.team.save_membership(updated, clear_assignments=action == "remove")
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except (InvalidMembershipTransitionError, TeamManagementWriteConflictError) as error:
            raise WorkspaceLifecycleConflictError from error
        return membership_lifecycle_view(updated)

    @staticmethod
    def _account(context: RequestContext, workspace_id: UUID) -> UUID:
        if (
            context.user_id is None
            or context.authentication_method != "browser_session"
            or context.workspace_id != workspace_id
        ):
            raise WorkspaceGovernanceDeniedError
        return context.user_id

    @classmethod
    def _authorized_account(
        cls,
        context: RequestContext,
        workspace_id: UUID,
        permission_code: str,
        *,
        resource_id: UUID,
    ) -> UUID:
        """复核 PDP 权限和目标范围，避免用其他资源的允许决策执行当前写入。"""

        account_id = cls._account(context, workspace_id)
        if context.authorized_permission_code != permission_code or (
            not context.authorized_workspace and resource_id not in context.authorized_resource_ids
        ):
            raise WorkspaceGovernanceDeniedError
        return account_id

    @staticmethod
    def _require_owner(
        repository: TeamManagementRepository, workspace_id: UUID, account_id: UUID
    ) -> None:
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
            raise WorkspaceGovernanceDeniedError

    @staticmethod
    def _editable_member(
        repository: TeamManagementRepository, workspace_id: UUID, account_id: UUID
    ) -> WorkspaceMembership:
        membership = repository.get_membership(workspace_id, account_id, for_update=True)
        if (
            membership is None
            or membership.membership_type == "owner"
            or membership.status != "active"
        ):
            raise WorkspaceGovernanceNotFoundError
        return membership

    @staticmethod
    def _unique(values: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(values) != len(set(values)):
            raise WorkspaceGovernanceValidationError
        return tuple(sorted(values, key=lambda value: value.int))

    @staticmethod
    def _validate_configuration(
        *,
        department_ids: tuple[UUID, ...],
        position_ids: tuple[UUID, ...],
        direct_role_ids: tuple[UUID, ...],
        departments: tuple[DepartmentSummary, ...],
        positions: tuple[PositionSummary, ...],
        roles: tuple[Role, ...],
    ) -> None:
        department_by_id = {item.department_id: item for item in departments}
        position_by_id = {item.position_id: item for item in positions}
        role_by_id = {item.role_id: item for item in roles}
        if any(
            department_by_id.get(value) is None or not department_by_id[value].effective_active
            for value in department_ids
        ):
            raise WorkspaceGovernanceValidationError
        if any(
            position_by_id.get(value) is None
            or not position_by_id[value].effective_active
            or position_by_id[value].department_id not in department_ids
            for value in position_ids
        ):
            raise WorkspaceGovernanceValidationError
        if any(
            role_by_id.get(value) is None
            or role_by_id[value].system_managed
            or role_by_id[value].status != "active"
            for value in direct_role_ids
        ):
            raise WorkspaceGovernanceValidationError

    @staticmethod
    def _facts(
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
        """让业务事实和审计共享实际通过的 PDP 与追踪上下文。"""

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
