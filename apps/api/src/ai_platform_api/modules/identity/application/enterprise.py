from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.identity.application.entitlement_errors import QuotaExceededError
from ai_platform_api.modules.identity.domain.enterprise import (
    EnterpriseRepository,
    EnterpriseUnitOfWork,
    EnterpriseWorkspace,
    EnterpriseWriteConflictError,
    InvalidInvitationTransitionError,
    InvalidMembershipTransitionError,
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceMemberSummary,
    WorkspaceRecord,
    WorkspaceSummary,
)

__all__ = [
    "EnterpriseWorkspaceService",
    "WorkspaceMemberSummary",
    "WorkspaceSummary",
]


class WorkspaceGovernanceDeniedError(PlatformError):
    """空间不存在、类型不符或主体没有临时治理权限时统一拒绝。"""

    error_code = "POLICY_DENIED"


class WorkspaceLifecycleConflictError(PlatformError):
    """邀请或成员生命周期发生重复、过期或非法状态转换。"""

    error_code = "WORKSPACE_LIFECYCLE_CONFLICT"


class WorkspaceGovernanceValidationError(PlatformError):
    error_code = "VALIDATION_ERROR"


class WorkspaceGovernanceNotFoundError(PlatformError):
    error_code = "RESOURCE_NOT_FOUND"


class EnterpriseWorkspaceService:
    """在角色系统落地前，以不可移除的 owner 成员提供最小企业治理边界。"""

    def __init__(self, unit_of_work: EnterpriseUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def create(self, context: RequestContext, *, name: str) -> WorkspaceSummary:
        account_id = self._browser_account(context)
        normalized_name = name.strip()
        if not normalized_name or len(normalized_name) > 120:
            raise WorkspaceGovernanceValidationError
        now = datetime.now(UTC)
        workspace = EnterpriseWorkspace(uuid4(), normalized_name, account_id, now)
        owner = WorkspaceMembership(
            membership_id=uuid4(),
            workspace_id=workspace.workspace_id,
            account_id=account_id,
            membership_type="owner",
            status="active",
            created_at=now,
            updated_at=now,
            version=1,
        )
        event, audit = self._facts(
            context=context,
            workspace_id=workspace.workspace_id,
            aggregate_id=workspace.workspace_id,
            event_type="workspace.enterprise.created",
            action="workspace.enterprise.create",
            resource_type="workspace",
            occurred_at=now,
            payload={"workspace_type": "enterprise"},
            attributes={"membership_type": "owner"},
        )
        try:
            with self._unit_of_work as unit_of_work:
                unit_of_work.enterprise.add_workspace(workspace, owner)
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except EnterpriseWriteConflictError as error:
            raise WorkspaceLifecycleConflictError from error
        return WorkspaceSummary(
            workspace_id=workspace.workspace_id,
            workspace_type="enterprise",
            name=workspace.name,
            status="active",
            membership_type="owner",
            membership_status="active",
        )

    def invite(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        login_name: str,
    ) -> WorkspaceInvitation:
        account_id = self._browser_account(context)
        self._require_current_workspace(context, workspace_id)
        normalized_login = login_name.strip().casefold()
        if not normalized_login or len(normalized_login) > 255:
            raise WorkspaceGovernanceValidationError
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                self._require_owner(unit_of_work.enterprise, workspace_id, account_id)
                invited_account_id = unit_of_work.enterprise.find_active_account_id(
                    normalized_login
                )
                if invited_account_id is None:
                    raise WorkspaceGovernanceNotFoundError
                if invited_account_id == account_id:
                    raise WorkspaceLifecycleConflictError
                membership = unit_of_work.enterprise.get_membership(
                    workspace_id,
                    invited_account_id,
                    for_update=True,
                )
                if membership is not None and membership.status == "active":
                    raise WorkspaceLifecycleConflictError
                previous_invitation = unit_of_work.enterprise.get_pending_invitation(
                    workspace_id,
                    invited_account_id,
                    for_update=True,
                )
                if previous_invitation is not None:
                    if previous_invitation.expires_at > now:
                        raise WorkspaceLifecycleConflictError
                    unit_of_work.enterprise.save_invitation(
                        previous_invitation.expire(occurred_at=now)
                    )
                invitation = WorkspaceInvitation(
                    invitation_id=uuid4(),
                    workspace_id=workspace_id,
                    invited_account_id=invited_account_id,
                    invited_by_account_id=account_id,
                    status="pending",
                    created_at=now,
                    expires_at=now + timedelta(days=7),
                )
                event, audit = self._facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=invitation.invitation_id,
                    event_type="workspace.member.invited",
                    action="workspace.member.invite",
                    resource_type="workspace_invitation",
                    occurred_at=now,
                    payload={"invitation_id": str(invitation.invitation_id)},
                    attributes={"expires_at": invitation.expires_at.isoformat()},
                )
                unit_of_work.enterprise.add_invitation(invitation)
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except InvalidInvitationTransitionError as error:
            raise WorkspaceLifecycleConflictError from error
        except EnterpriseWriteConflictError as error:
            raise WorkspaceLifecycleConflictError from error
        return invitation

    def accept_invitation(
        self,
        context: RequestContext,
        *,
        invitation_id: UUID,
    ) -> WorkspaceSummary:
        account_id = self._browser_account(context)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                invitation = unit_of_work.enterprise.get_invitation(
                    invitation_id,
                    for_update=True,
                )
                if invitation is None or invitation.invited_account_id != account_id:
                    raise WorkspaceGovernanceNotFoundError
                workspace = unit_of_work.enterprise.get_workspace(
                    invitation.workspace_id,
                    for_update=True,
                )
                if (
                    workspace is None
                    or workspace.workspace_type != "enterprise"
                    or workspace.status != "active"
                ):
                    raise WorkspaceGovernanceDeniedError
                accepted = invitation.accept(account_id=account_id, occurred_at=now)
                membership = unit_of_work.enterprise.get_membership(
                    invitation.workspace_id,
                    account_id,
                    for_update=True,
                )
                if not unit_of_work.enterprise.member_capacity_available(invitation.workspace_id):
                    raise QuotaExceededError
                if membership is None:
                    membership = WorkspaceMembership(
                        membership_id=uuid4(),
                        workspace_id=invitation.workspace_id,
                        account_id=account_id,
                        membership_type="member",
                        status="active",
                        created_at=now,
                        updated_at=now,
                        version=1,
                    )
                    unit_of_work.enterprise.add_membership(membership)
                else:
                    membership = membership.activate_as_member(occurred_at=now)
                    unit_of_work.enterprise.save_membership(membership)
                event, audit = self._facts(
                    context=context,
                    workspace_id=invitation.workspace_id,
                    aggregate_id=membership.membership_id,
                    event_type="workspace.member.joined",
                    action="workspace.member.join",
                    resource_type="workspace_membership",
                    occurred_at=now,
                    payload={"membership_id": str(membership.membership_id)},
                    attributes={"membership_type": "member"},
                    aggregate_version=membership.version,
                )
                unit_of_work.enterprise.save_invitation(accepted)
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except (InvalidInvitationTransitionError, InvalidMembershipTransitionError) as error:
            raise WorkspaceLifecycleConflictError from error
        except EnterpriseWriteConflictError as error:
            raise WorkspaceLifecycleConflictError from error
        return WorkspaceSummary(
            workspace_id=workspace.workspace_id,
            workspace_type="enterprise",
            name=workspace.name,
            status=workspace.status,
            membership_type="member",
            membership_status="active",
        )

    def leave(self, context: RequestContext, *, workspace_id: UUID) -> WorkspaceMembership:
        account_id = self._browser_account(context)
        self._require_current_workspace(context, workspace_id)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                self._require_enterprise(unit_of_work.enterprise, workspace_id)
                membership = unit_of_work.enterprise.get_membership(
                    workspace_id,
                    account_id,
                    for_update=True,
                )
                if membership is None:
                    raise WorkspaceGovernanceNotFoundError
                updated = membership.leave(occurred_at=now)
                event, audit = self._facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=membership.membership_id,
                    event_type="workspace.member.left",
                    action="workspace.member.leave",
                    resource_type="workspace_membership",
                    occurred_at=now,
                    payload={"membership_id": str(membership.membership_id)},
                    attributes={"membership_status": "left"},
                    aggregate_version=updated.version,
                )
                unit_of_work.enterprise.save_membership(updated)
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except InvalidMembershipTransitionError as error:
            raise WorkspaceLifecycleConflictError from error
        return updated

    def disable_member(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
    ) -> WorkspaceMembership:
        account_id = self._browser_account(context)
        self._require_current_workspace(context, workspace_id)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                self._require_owner(unit_of_work.enterprise, workspace_id, account_id)
                target = unit_of_work.enterprise.get_membership(
                    workspace_id,
                    target_account_id,
                    for_update=True,
                )
                if target is None:
                    raise WorkspaceGovernanceNotFoundError
                updated = target.disable(occurred_at=now)
                event, audit = self._facts(
                    context=context,
                    workspace_id=workspace_id,
                    aggregate_id=target.membership_id,
                    event_type="workspace.member.disabled",
                    action="workspace.member.disable",
                    resource_type="workspace_membership",
                    occurred_at=now,
                    payload={"membership_id": str(target.membership_id)},
                    attributes={"membership_status": "disabled"},
                    aggregate_version=updated.version,
                )
                unit_of_work.enterprise.save_membership(updated)
                unit_of_work.audit.add(audit)
                unit_of_work.outbox.add(event)
                unit_of_work.commit()
        except InvalidMembershipTransitionError as error:
            raise WorkspaceLifecycleConflictError from error
        return updated

    def list_workspaces(self, context: RequestContext) -> tuple[WorkspaceSummary, ...]:
        account_id = self._browser_account(context)
        with self._unit_of_work as unit_of_work:
            return unit_of_work.enterprise.list_workspaces(account_id)

    def list_members(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
    ) -> tuple[WorkspaceMemberSummary, ...]:
        account_id = self._browser_account(context)
        self._require_current_workspace(context, workspace_id)
        with self._unit_of_work as unit_of_work:
            self._require_owner(unit_of_work.enterprise, workspace_id, account_id)
            return unit_of_work.enterprise.list_members(workspace_id)

    def switch(self, context: RequestContext, *, workspace_id: UUID) -> WorkspaceSummary:
        account_id = self._browser_account(context)
        with self._unit_of_work as unit_of_work:
            workspace = unit_of_work.enterprise.get_workspace(workspace_id)
            membership = unit_of_work.enterprise.get_membership(workspace_id, account_id)
            if (
                workspace is None
                or membership is None
                or workspace.status != "active"
                or membership.status != "active"
                or (
                    workspace.workspace_type == "personal" and membership.membership_type != "owner"
                )
            ):
                raise WorkspaceGovernanceDeniedError
            return WorkspaceSummary(
                workspace_id=workspace.workspace_id,
                workspace_type=workspace.workspace_type,
                name=workspace.name,
                status=workspace.status,
                membership_type=membership.membership_type,
                membership_status=membership.status,
            )

    @staticmethod
    def _browser_account(context: RequestContext) -> UUID:
        if context.user_id is None or context.authentication_method != "browser_session":
            raise WorkspaceGovernanceDeniedError
        return context.user_id

    @staticmethod
    def _require_current_workspace(context: RequestContext, workspace_id: UUID) -> None:
        if context.workspace_id != workspace_id:
            raise WorkspaceGovernanceDeniedError

    @staticmethod
    def _require_enterprise(
        repository: EnterpriseRepository,
        workspace_id: UUID,
    ) -> WorkspaceRecord:
        workspace = repository.get_workspace(workspace_id, for_update=True)
        if (
            workspace is None
            or workspace.workspace_type != "enterprise"
            or workspace.status != "active"
        ):
            raise WorkspaceGovernanceDeniedError
        return workspace

    @classmethod
    def _require_owner(
        cls,
        repository: EnterpriseRepository,
        workspace_id: UUID,
        account_id: UUID,
    ) -> None:
        cls._require_enterprise(repository, workspace_id)
        membership = repository.get_membership(workspace_id, account_id, for_update=True)
        if (
            membership is None
            or membership.status != "active"
            or membership.membership_type != "owner"
        ):
            raise WorkspaceGovernanceDeniedError

    @staticmethod
    def _facts(
        *,
        context: RequestContext,
        workspace_id: UUID,
        aggregate_id: UUID,
        event_type: str,
        action: str,
        resource_type: str,
        occurred_at: datetime,
        payload: dict[str, object],
        attributes: dict[str, object],
        aggregate_version: int = 1,
    ) -> tuple[IntegrationEvent, AuditRecord]:
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
                attributes=attributes,
            ),
        )
