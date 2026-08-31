"""验证 P6B-02 团队管理授权、校验、状态和事务事实。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.enterprise import (
    WorkspaceGovernanceDeniedError,
    WorkspaceGovernanceNotFoundError,
    WorkspaceGovernanceValidationError,
    WorkspaceLifecycleConflictError,
)
from ai_platform_api.modules.identity.application.team_management import TeamManagementService
from ai_platform_api.modules.identity.domain.enterprise import (
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceRecord,
)
from ai_platform_api.modules.identity.domain.organization import (
    DepartmentSummary,
    PositionSummary,
)
from ai_platform_api.modules.identity.domain.roles import Role
from ai_platform_api.modules.identity.domain.team_management import (
    TeamManagementSnapshot,
    TeamManagementStatistics,
    TeamManagementUnitOfWork,
    TeamManagementWriteConflictError,
)
from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000920")
OTHER_WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000921")
OWNER_ID = UUID("10000000-0000-4000-8000-000000000920")
MEMBER_ID = UUID("10000000-0000-4000-8000-000000000921")
OTHER_MEMBER_ID = UUID("10000000-0000-4000-8000-000000000922")
OWNER_MEMBERSHIP_ID = UUID("30000000-0000-4000-8000-000000000920")
MEMBER_MEMBERSHIP_ID = UUID("30000000-0000-4000-8000-000000000921")
INVITATION_ID = UUID("40000000-0000-4000-8000-000000000920")
DEPARTMENT_ID = UUID("50000000-0000-4000-8000-000000000920")
POSITION_ID = UUID("60000000-0000-4000-8000-000000000920")
ROLE_ID = UUID("70000000-0000-4000-8000-000000000920")
SYSTEM_ROLE_ID = UUID("70000000-0000-4000-8000-000000000921")
NOW = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)


class RecordWriter:
    """收集团队用例产生的审计和 Outbox 事实。"""

    def __init__(self) -> None:
        self.records: list[AuditRecord | IntegrationEvent] = []

    def add(self, record: AuditRecord | IntegrationEvent) -> None:
        self.records.append(record)


class TeamRepositoryScenario:
    """提供团队应用服务所需的可变领域场景。"""

    def __init__(self) -> None:
        self.workspace = WorkspaceRecord(WORKSPACE_ID, "enterprise", "合成团队企业", "active")
        self.owner = _membership(OWNER_ID, OWNER_MEMBERSHIP_ID, "owner", "active")
        self.member = _membership(MEMBER_ID, MEMBER_MEMBERSHIP_ID, "member", "active")
        self.invitation = WorkspaceInvitation(
            INVITATION_ID,
            WORKSPACE_ID,
            MEMBER_ID,
            OWNER_ID,
            "pending",
            NOW,
            NOW + timedelta(days=7),
        )
        self.departments = (
            DepartmentSummary(DEPARTMENT_ID, None, "合成研发部", "active", True, 0, 1),
        )
        self.positions = (
            PositionSummary(POSITION_ID, DEPARTMENT_ID, "合成工程师", "active", True, 1),
        )
        self.roles = (
            Role(
                ROLE_ID,
                WORKSPACE_ID,
                "synthetic_reviewer",
                "合成审核员",
                "active",
                False,
                NOW,
                NOW,
                1,
            ),
            Role(
                SYSTEM_ROLE_ID,
                WORKSPACE_ID,
                "workspace_member",
                "空间成员",
                "active",
                True,
                NOW,
                NOW,
                1,
            ),
        )
        self.snapshot_arguments: dict[str, object] = {}
        self.saved_invitation: WorkspaceInvitation | None = None
        self.saved_membership: tuple[WorkspaceMembership, bool] | None = None
        self.replacement_arguments: dict[str, object] = {}
        self.raise_write_conflict = False

    def get_workspace(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> WorkspaceRecord | None:
        del for_update
        return self.workspace if workspace_id == WORKSPACE_ID else None

    def get_membership(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceMembership | None:
        del for_update
        if workspace_id != WORKSPACE_ID:
            return None
        return {OWNER_ID: self.owner, MEMBER_ID: self.member}.get(account_id)

    def get_invitation(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceInvitation | None:
        del for_update
        if workspace_id == WORKSPACE_ID and invitation_id == INVITATION_ID:
            return self.invitation
        return None

    def save_invitation(self, invitation: WorkspaceInvitation) -> None:
        if self.raise_write_conflict:
            raise TeamManagementWriteConflictError
        self.saved_invitation = invitation

    def get_snapshot(
        self,
        workspace_id: UUID,
        *,
        generated_at: datetime,
        audit_limit: int,
        mask_display_name: bool,
        mask_login_name: bool,
    ) -> TeamManagementSnapshot | None:
        self.snapshot_arguments = {
            "workspace_id": workspace_id,
            "audit_limit": audit_limit,
            "mask_display_name": mask_display_name,
            "mask_login_name": mask_login_name,
        }
        return TeamManagementSnapshot(
            self.workspace,
            TeamManagementStatistics(2, 0, 1, 1, 1),
            (),
            (),
            self.departments,
            self.positions,
            (),
            (),
            generated_at,
        )

    def list_departments(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> tuple[DepartmentSummary, ...]:
        del workspace_id, for_update
        return self.departments

    def list_positions(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> tuple[PositionSummary, ...]:
        del workspace_id, for_update
        return self.positions

    def list_roles(self, workspace_id: UUID, *, for_update: bool = False) -> tuple[Role, ...]:
        del workspace_id, for_update
        return self.roles

    def replace_member_configuration(
        self,
        *,
        workspace_id: UUID,
        membership: WorkspaceMembership,
        expected_version: int,
        department_ids: tuple[UUID, ...],
        primary_department_id: UUID | None,
        position_ids: tuple[UUID, ...],
        direct_role_ids: tuple[UUID, ...],
        occurred_at: datetime,
    ) -> WorkspaceMembership:
        if self.raise_write_conflict or expected_version != membership.version:
            raise TeamManagementWriteConflictError
        self.replacement_arguments = {
            "workspace_id": workspace_id,
            "department_ids": department_ids,
            "primary_department_id": primary_department_id,
            "position_ids": position_ids,
            "direct_role_ids": direct_role_ids,
        }
        return replace(membership, version=membership.version + 1, updated_at=occurred_at)

    def save_membership(self, membership: WorkspaceMembership, *, clear_assignments: bool) -> None:
        if self.raise_write_conflict:
            raise TeamManagementWriteConflictError
        self.saved_membership = membership, clear_assignments


class TeamUnitOfWorkScenario:
    """记录应用服务是否把业务、审计和 Outbox 一起提交。"""

    def __init__(self) -> None:
        self.team = TeamRepositoryScenario()
        self.audit = RecordWriter()
        self.outbox = RecordWriter()
        self.commit_count = 0

    def __enter__(self) -> TeamUnitOfWorkScenario:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def commit(self) -> None:
        self.commit_count += 1


def _membership(
    account_id: UUID,
    membership_id: UUID,
    membership_type: Literal["owner", "member"],
    status: Literal["active", "disabled", "left"],
) -> WorkspaceMembership:
    return WorkspaceMembership(
        membership_id,
        WORKSPACE_ID,
        account_id,
        membership_type,
        status,
        NOW,
        NOW,
        1,
    )


def _context(
    permission_code: str,
    *,
    workspace_scope: bool = True,
    resource_ids: frozenset[UUID] = frozenset(),
) -> RequestContext:
    return replace(
        RequestContext.trusted(
            actor_id=OWNER_ID,
            user_id=OWNER_ID,
            workspace_id=WORKSPACE_ID,
            authentication_method="browser_session",
            trace=TraceContext("c" * 32, "d" * 16),
        ),
        authorized_permission_code=permission_code,
        authorized_policy_decision_id=UUID("90000000-0000-4000-8000-000000000920"),
        authorized_policy_version=30,
        authorized_workspace=workspace_scope,
        authorized_resource_ids=resource_ids,
    )


def _service(scenario: TeamUnitOfWorkScenario) -> TeamManagementService:
    return TeamManagementService(cast(TeamManagementUnitOfWork, scenario))


def test_team_snapshot_applies_field_masks_and_rejects_account_id_mask() -> None:
    """姓名和登录名可独立遮罩，账号标识不可见时整个聚合失败关闭。"""

    scenario = TeamUnitOfWorkScenario()
    masked = replace(
        _context("workspace.team.read"),
        authorized_field_mask=frozenset({"display_name", "login_name"}),
    )
    snapshot = _service(scenario).get_snapshot(masked, workspace_id=WORKSPACE_ID, audit_limit=7)
    assert snapshot.statistics.active_members == 2
    assert scenario.team.snapshot_arguments == {
        "workspace_id": WORKSPACE_ID,
        "audit_limit": 7,
        "mask_display_name": True,
        "mask_login_name": True,
    }

    with pytest.raises(WorkspaceGovernanceDeniedError):
        _service(scenario).get_snapshot(
            replace(masked, authorized_field_mask=frozenset({"account_id"})),
            workspace_id=WORKSPACE_ID,
        )


@pytest.mark.parametrize(
    "invalid_context",
    [
        replace(_context("workspace.team.read"), authentication_method="open_api_key"),
        replace(_context("workspace.team.read"), workspace_id=OTHER_WORKSPACE_ID),
        _context("workspace.member.read"),
        _context("workspace.team.read", workspace_scope=False),
    ],
)
def test_team_snapshot_rejects_untrusted_cross_workspace_or_narrow_context(
    invalid_context: RequestContext,
) -> None:
    """团队聚合只允许当前企业 Owner 的全空间浏览器授权。"""

    scenario = TeamUnitOfWorkScenario()
    with pytest.raises(WorkspaceGovernanceDeniedError):
        _service(scenario).get_snapshot(invalid_context, workspace_id=WORKSPACE_ID)
    assert scenario.team.snapshot_arguments == {}


def test_team_snapshot_rejects_personal_space_and_inactive_owner() -> None:
    """个人空间和失效 Owner 即使持有旧决策也不得读取团队事实。"""

    personal = TeamUnitOfWorkScenario()
    personal.team.workspace = WorkspaceRecord(WORKSPACE_ID, "personal", "合成个人空间", "active")
    with pytest.raises(WorkspaceGovernanceDeniedError):
        _service(personal).get_snapshot(_context("workspace.team.read"), workspace_id=WORKSPACE_ID)

    inactive = TeamUnitOfWorkScenario()
    inactive.team.owner = replace(inactive.team.owner, status="disabled")
    with pytest.raises(WorkspaceGovernanceDeniedError):
        _service(inactive).get_snapshot(_context("workspace.team.read"), workspace_id=WORKSPACE_ID)


def test_cancel_invitation_commits_state_audit_and_outbox_together() -> None:
    """有效邀请撤销必须同时产生状态、审计和集成事件。"""

    scenario = TeamUnitOfWorkScenario()
    cancelled = _service(scenario).cancel_invitation(
        _context(
            "workspace.invitation.cancel",
            workspace_scope=False,
            resource_ids=frozenset({INVITATION_ID}),
        ),
        workspace_id=WORKSPACE_ID,
        invitation_id=INVITATION_ID,
    )
    assert cancelled.status == "cancelled"
    assert scenario.team.saved_invitation is not None
    assert scenario.team.saved_invitation.invitation_id == cancelled.invitation_id
    assert scenario.team.saved_invitation.status == cancelled.status
    assert scenario.commit_count == 1
    assert cast(AuditRecord, scenario.audit.records[0]).action == "workspace.invitation.cancel"
    assert cast(IntegrationEvent, scenario.outbox.records[0]).event_type == (
        "workspace.invitation.cancelled"
    )


def test_write_rejects_wrong_resource_scope_and_illegal_invitation_state() -> None:
    """其他资源授权和过期邀请均不得被当前写操作复用。"""

    scoped = TeamUnitOfWorkScenario()
    with pytest.raises(WorkspaceGovernanceDeniedError):
        _service(scoped).cancel_invitation(
            _context(
                "workspace.invitation.cancel",
                workspace_scope=False,
                resource_ids=frozenset({UUID(int=99)}),
            ),
            workspace_id=WORKSPACE_ID,
            invitation_id=INVITATION_ID,
        )

    expired = TeamUnitOfWorkScenario()
    expired.team.invitation = replace(expired.team.invitation, expires_at=datetime.now(UTC))
    with pytest.raises(WorkspaceLifecycleConflictError):
        _service(expired).cancel_invitation(
            _context("workspace.invitation.cancel"),
            workspace_id=WORKSPACE_ID,
            invitation_id=INVITATION_ID,
        )
    assert expired.commit_count == 0


def test_update_member_validates_references_and_commits_one_configuration() -> None:
    """成员组织、岗位和自定义直接角色使用同一乐观版本提交。"""

    scenario = TeamUnitOfWorkScenario()
    updated = _service(scenario).update_member(
        _context("workspace.member.update"),
        workspace_id=WORKSPACE_ID,
        target_account_id=MEMBER_ID,
        expected_version=1,
        department_ids=(DEPARTMENT_ID,),
        primary_department_id=DEPARTMENT_ID,
        position_ids=(POSITION_ID,),
        direct_role_ids=(ROLE_ID,),
    )
    assert updated.version == 2
    assert scenario.team.replacement_arguments["direct_role_ids"] == (ROLE_ID,)
    assert scenario.commit_count == 1
    assert cast(AuditRecord, scenario.audit.records[0]).action == (
        "workspace.member.configuration.update"
    )


@pytest.mark.parametrize(
    "department_ids,primary_department_id,position_ids,direct_role_ids",
    [
        ((DEPARTMENT_ID, DEPARTMENT_ID), DEPARTMENT_ID, (), ()),
        ((DEPARTMENT_ID,), None, (), ()),
        ((DEPARTMENT_ID,), DEPARTMENT_ID, (UUID(int=10),), ()),
        ((DEPARTMENT_ID,), DEPARTMENT_ID, (), (SYSTEM_ROLE_ID,)),
    ],
)
def test_update_member_rejects_invalid_primary_references_and_system_roles(
    department_ids: tuple[UUID, ...],
    primary_department_id: UUID | None,
    position_ids: tuple[UUID, ...],
    direct_role_ids: tuple[UUID, ...],
) -> None:
    """重复集合、无主部门、非法岗位和系统角色都在写入前失败。"""

    scenario = TeamUnitOfWorkScenario()
    with pytest.raises(WorkspaceGovernanceValidationError):
        _service(scenario).update_member(
            _context("workspace.member.update"),
            workspace_id=WORKSPACE_ID,
            target_account_id=MEMBER_ID,
            expected_version=1,
            department_ids=department_ids,
            primary_department_id=primary_department_id,
            position_ids=position_ids,
            direct_role_ids=direct_role_ids,
        )
    assert scenario.commit_count == 0


def test_update_member_rejects_owner_target_and_version_conflict() -> None:
    """Owner 不可编辑，陈旧成员版本不得覆盖新组织事实。"""

    owner = TeamUnitOfWorkScenario()
    with pytest.raises(WorkspaceGovernanceNotFoundError):
        _service(owner).update_member(
            _context("workspace.member.update"),
            workspace_id=WORKSPACE_ID,
            target_account_id=OWNER_ID,
            expected_version=1,
            department_ids=(),
            primary_department_id=None,
            position_ids=(),
            direct_role_ids=(),
        )

    conflict = TeamUnitOfWorkScenario()
    with pytest.raises(WorkspaceLifecycleConflictError):
        _service(conflict).update_member(
            _context("workspace.member.update"),
            workspace_id=WORKSPACE_ID,
            target_account_id=MEMBER_ID,
            expected_version=2,
            department_ids=(DEPARTMENT_ID,),
            primary_department_id=DEPARTMENT_ID,
            position_ids=(),
            direct_role_ids=(),
        )


def test_activate_and_remove_keep_explicit_assignment_cleanup_semantics() -> None:
    """恢复保留配置，移除进入终态并显式清理组织和自定义角色。"""

    activation = TeamUnitOfWorkScenario()
    activation.team.member = replace(activation.team.member, status="disabled")
    restored = _service(activation).activate_member(
        _context("workspace.member.activate"),
        workspace_id=WORKSPACE_ID,
        target_account_id=MEMBER_ID,
    )
    assert restored.status == "active"
    assert activation.team.saved_membership is not None
    assert activation.team.saved_membership[0].account_id == restored.account_id
    assert activation.team.saved_membership[0].status == restored.status
    assert activation.team.saved_membership[0].version == restored.version
    assert activation.team.saved_membership[1] is False
    assert cast(IntegrationEvent, activation.outbox.records[0]).event_type == (
        "workspace.member.activated"
    )

    removal = TeamUnitOfWorkScenario()
    removed = _service(removal).remove_member(
        _context("workspace.member.remove"),
        workspace_id=WORKSPACE_ID,
        target_account_id=MEMBER_ID,
    )
    assert removed.status == "left"
    assert removal.team.saved_membership is not None
    assert removal.team.saved_membership[0].account_id == removed.account_id
    assert removal.team.saved_membership[0].status == removed.status
    assert removal.team.saved_membership[0].version == removed.version
    assert removal.team.saved_membership[1] is True
    assert cast(IntegrationEvent, removal.outbox.records[0]).event_type == (
        "workspace.member.removed"
    )
