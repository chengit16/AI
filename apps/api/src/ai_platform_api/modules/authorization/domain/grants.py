"""定义角色授权聚合、主体解析端口和授权事务边界。"""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.authorization.domain.policy import DataScopeType

OWNER_PERMISSION_CODES = (
    "agent.definition.archive",
    "agent.definition.create",
    "agent.definition.read",
    "agent.definition.update",
    "agent.operations.read",
    "agent.page.access",
    "agent.release.approve",
    "agent.release.publish",
    "agent.release.read",
    "agent.release.request",
    "agent.test.execute",
    "agent.test.read",
    "approval.chain.preview",
    "approval.instance.approve",
    "approval.instance.create",
    "approval.instance.process_due",
    "approval.instance.read",
    "approval.instance.reject",
    "approval.instance.transfer",
    "approval.instance.withdraw",
    "approval.policy.create",
    "approval.policy.read",
    "approval.policy.update",
    "authorization.binding.create",
    "authorization.binding.revoke",
    "authorization.effective_role.read",
    "authorization.menu.manage",
    "authorization.menu.read",
    "authorization.menu_release.approve",
    "authorization.menu_release.create",
    "authorization.menu_release.publish",
    "authorization.menu_release.read",
    "authorization.menu_release.rollback",
    "authorization.menu_release.validate",
    "authorization.role.create",
    "authorization.role.read",
    "authorization.role.status",
    "authorization.role_permission.read",
    "authorization.role_permission.manage",
    "enterprise.invitation.accept",
    "enterprise.category.archive",
    "enterprise.category.bind",
    "enterprise.category.create",
    "enterprise.category.update",
    "enterprise.domain.archive",
    "enterprise.domain.create",
    "enterprise.domain.resolve",
    "enterprise.domain.scope",
    "enterprise.domain.update",
    "enterprise.document.publish.read",
    "enterprise.document.publish.request",
    "enterprise.knowledge.access",
    "enterprise.knowledge.read",
    "enterprise.workspace.create",
    "knowledge.base.create",
    "knowledge.base.delete",
    "knowledge.base.read",
    "knowledge.document.create",
    "knowledge.document.delete",
    "knowledge.document.folder.bind",
    "knowledge.document.tag.bind",
    "knowledge.document.download",
    "knowledge.document.read",
    "knowledge.document.version.create",
    "knowledge.document.version.publish",
    "knowledge.document.version.ready",
    "knowledge.document.favorite",
    "knowledge.folder.create",
    "knowledge.folder.delete",
    "knowledge.folder.purge",
    "knowledge.folder.read",
    "knowledge.folder.restore",
    "knowledge.folder.update",
    "knowledge.tag.create",
    "knowledge.tag.delete",
    "knowledge.tag.read",
    "knowledge.tag.restore",
    "knowledge.tag.update",
    "knowledge.trash.purge",
    "knowledge.trash.read",
    "knowledge.trash.restore",
    "knowledge.ingestion.read",
    "knowledge.ingestion.retry",
    "knowledge.ingestion.cancel",
    "knowledge.production.access",
    "assistant.conversation.create",
    "assistant.conversation.read",
    "assistant.conversation.archive",
    "assistant.message.create",
    "assistant.attachment.manage",
    "assistant.conversation.scope.manage",
    "assistant.page.access",
    "assistant.run.cancel",
    "assistant.source.read",
    "assistant.feedback.manage",
    "organization.assignment.read",
    "organization.assignment.write",
    "organization.department.create",
    "organization.department.move",
    "organization.department.read",
    "organization.department.status",
    "organization.position.create",
    "organization.position.read",
    "organization.position.status",
    "organization.structure.access",
    "operations.outbox.replay",
    "operations.records.read",
    "operations.control_tower.read",
    "operations.index.inspect",
    "operations.index.rebuild",
    "operations.index.cleanup",
    "service.definition.read",
    "service.definition.create",
    "service.definition.update",
    "service.page.access",
    "service.route.canary",
    "service.route.promote",
    "service.route.rollback",
    "system.runtime.access",
    "tool.catalog.read",
    "tool.confirmation.respond",
    "tool.page.access",
    "tool.run.cancel",
    "tool.run.create",
    "tool.run.read",
    "workspace.context.switch",
    "workspace.entitlement.read",
    "workspace.lifecycle.export",
    "workspace.lifecycle.compliance_proof.read",
    "workspace.lifecycle.legal_hold.create",
    "workspace.lifecycle.legal_hold.release",
    "workspace.lifecycle.purge",
    "workspace.lifecycle.regulatory_policy.manage",
    "workspace.lifecycle.retention.execute",
    "workspace.member.disable",
    "workspace.member.activate",
    "workspace.member.invite",
    "workspace.member.read",
    "workspace.member.remove",
    "workspace.member.update",
    "workspace.members.access",
    "workspace.invitation.cancel",
    "workspace.team.read",
    "workspace.membership.leave",
    "workspace.open_api.manage",
    "workspace.overview.access",
    "workflow.definition.create",
    "workflow.definition.publish",
    "workflow.definition.read",
    "workflow.definition.update",
    "workflow.page.access",
    "workflow.run.create",
    "workflow.run.read",
)
MEMBER_PERMISSION_CODES = (
    "agent.definition.read",
    "agent.page.access",
    "agent.release.read",
    "agent.test.read",
    "approval.chain.preview",
    "approval.instance.approve",
    "approval.instance.create",
    "approval.instance.read",
    "approval.instance.reject",
    "approval.instance.transfer",
    "approval.instance.withdraw",
    "authorization.effective_role.read",
    "authorization.menu_release.read",
    "workspace.context.switch",
    "workspace.entitlement.read",
    "workspace.membership.leave",
    "workspace.overview.access",
    "assistant.conversation.create",
    "assistant.conversation.read",
    "assistant.conversation.archive",
    "assistant.message.create",
    "assistant.attachment.manage",
    "assistant.conversation.scope.manage",
    "assistant.page.access",
    "assistant.run.cancel",
    "assistant.source.read",
    "assistant.feedback.manage",
    "service.definition.read",
    "service.page.access",
    "workflow.definition.read",
    "workflow.page.access",
    "workflow.run.create",
    "workflow.run.read",
)


@dataclass(frozen=True)
class RolePermissionGrant:
    """定义角色在数据范围、安全级别和字段掩码下可执行的权限。"""

    workspace_id: UUID
    role_id: UUID
    permission_code: str
    scope_type: DataScopeType
    department_ids: frozenset[UUID] = frozenset()
    resource_ids: frozenset[UUID] = frozenset()
    maximum_security_level: SecurityLevel = "RESTRICTED"
    field_mask: frozenset[str] = frozenset()

    def assert_valid(self) -> None:
        if self.scope_type in {"workspace", "self"}:
            valid = not self.department_ids and not self.resource_ids
        elif self.scope_type == "department_tree":
            valid = bool(self.department_ids) and not self.resource_ids
        else:
            valid = bool(self.resource_ids) and not self.department_ids
        if not valid:
            raise InvalidRolePermissionGrantError


class InvalidRolePermissionGrantError(Exception):
    """权限的数据范围与其必需目标不一致。"""


class RolePermissionWriteConflictError(Exception):
    """数据库约束或角色版本变化拒绝本次权限替换。"""


@dataclass(frozen=True)
class PolicySubject:
    """汇总账号、成员、角色版本和有效角色，作为策略计算的可信主体。"""

    account_id: UUID
    membership_id: UUID
    role_version: int
    role_ids: frozenset[UUID]


class PolicyGrantReader(Protocol):
    """按可信上下文解析授权主体，并读取其有效角色授权项。"""

    def resolve_subject(self, context: RequestContext) -> PolicySubject | None: ...

    def list_role_grants(
        self,
        workspace_id: UUID,
        role_ids: frozenset[UUID],
    ) -> tuple[RolePermissionGrant, ...]: ...

    def expand_department_tree(
        self,
        workspace_id: UUID,
        department_ids: frozenset[UUID],
    ) -> frozenset[UUID]: ...


class RolePermissionRepository(PolicyGrantReader, Protocol):
    """在工作空间边界内整体替换角色授权并递增角色版本。"""

    def get_requester_membership_type(
        self,
        workspace_id: UUID,
        account_id: UUID,
    ) -> str | None: ...

    def get_role(self, workspace_id: UUID, role_id: UUID) -> tuple[str, bool, str] | None: ...

    def replace_role_grants(
        self,
        workspace_id: UUID,
        role_id: UUID,
        grants: tuple[RolePermissionGrant, ...],
    ) -> None: ...

    def bump_role_version(self, workspace_id: UUID) -> int: ...


class RolePermissionUnitOfWork(Protocol):
    """保证角色权限、审计和 Outbox 在同一事务内提交。"""

    @property
    def permissions(self) -> RolePermissionRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> RolePermissionUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


def system_role_permission_seed(
    *,
    workspace_id: UUID,
    owner_role_id: UUID,
    member_role_id: UUID,
) -> tuple[RolePermissionGrant, ...]:
    """系统角色的默认授权必须同时供 Migration 与新空间写入使用。"""

    return tuple(
        RolePermissionGrant(
            workspace_id,
            owner_role_id,
            code,
            "workspace",
            maximum_security_level="RESTRICTED",
        )
        for code in OWNER_PERMISSION_CODES
    ) + tuple(
        RolePermissionGrant(
            workspace_id,
            member_role_id,
            code,
            "workspace",
            maximum_security_level="INTERNAL",
        )
        for code in MEMBER_PERMISSION_CODES
    )
