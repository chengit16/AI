"""实现审批策略版本、组织审批目录和事务边界的 PostgreSQL Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from types import TracebackType
from typing import Any, Literal, cast
from uuid import UUID

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import CursorResult, and_, exists, insert, or_, select, update
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.workflow.domain.approvals import (
    ApprovalApproverSource,
    ApprovalMemberIdentity,
    ApprovalOrganizationDirectory,
    ApprovalPolicy,
    ApprovalPolicyRepository,
    ApprovalPolicyStatus,
    ApprovalPolicyUnitOfWork,
    ApprovalPolicyVersion,
    ApprovalPolicyWriteConflictError,
    ApprovalWorkspaceIdentity,
    approval_definition_document,
    approval_definition_from_document,
)
from ai_platform_api.persistence.tables import (
    approval_policies,
    approval_policy_versions,
    department_closure,
    departments,
    membership_departments,
    membership_positions,
    positions,
    role_bindings,
    roles,
    workspace_memberships,
    workspaces,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyApprovalPolicyRepository(ApprovalPolicyRepository):
    """维护策略当前指针和只增版本，并始终以工作空间过滤读写。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_policies(self, workspace_id: UUID) -> tuple[ApprovalPolicy, ...]:
        rows = self._session.execute(
            select(approval_policies)
            .where(approval_policies.c.workspace_id == workspace_id)
            .order_by(approval_policies.c.updated_at.desc(), approval_policies.c.approval_policy_id)
        )
        return tuple(_policy(row) for row in rows)

    def get_policy(
        self,
        workspace_id: UUID,
        approval_policy_id: UUID,
        *,
        for_update: bool = False,
    ) -> ApprovalPolicy | None:
        statement = select(approval_policies).where(
            approval_policies.c.workspace_id == workspace_id,
            approval_policies.c.approval_policy_id == approval_policy_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return _policy(row) if row is not None else None

    def get_version(
        self,
        workspace_id: UUID,
        approval_policy_version_id: UUID,
    ) -> ApprovalPolicyVersion | None:
        row = self._session.execute(
            select(approval_policy_versions).where(
                approval_policy_versions.c.workspace_id == workspace_id,
                approval_policy_versions.c.approval_policy_version_id == approval_policy_version_id,
            )
        ).one_or_none()
        return _version(row) if row is not None else None

    def list_active_versions(self, workspace_id: UUID) -> tuple[ApprovalPolicyVersion, ...]:
        rows = self._session.execute(
            select(approval_policy_versions)
            .join(
                approval_policies,
                (
                    approval_policies.c.approval_policy_id
                    == approval_policy_versions.c.approval_policy_id
                )
                & (approval_policies.c.workspace_id == approval_policy_versions.c.workspace_id)
                & (
                    approval_policies.c.current_version_id
                    == approval_policy_versions.c.approval_policy_version_id
                ),
            )
            .where(
                approval_policies.c.workspace_id == workspace_id,
                approval_policies.c.status == "active",
            )
            .order_by(approval_policy_versions.c.approval_policy_version_id)
        )
        return tuple(_version(row) for row in rows)

    def next_version_number(self, workspace_id: UUID, approval_policy_id: UUID) -> int:
        latest = self._session.scalar(
            select(approval_policy_versions.c.version_number)
            .where(
                approval_policy_versions.c.workspace_id == workspace_id,
                approval_policy_versions.c.approval_policy_id == approval_policy_id,
            )
            .order_by(approval_policy_versions.c.version_number.desc())
            .limit(1)
        )
        return (latest or 0) + 1

    def add_policy(self, policy: ApprovalPolicy, version: ApprovalPolicyVersion) -> None:
        """先写身份再写版本，延迟外键在事务提交时验证当前版本指针。"""

        try:
            self._session.execute(
                insert(approval_policies).values(
                    approval_policy_id=policy.approval_policy_id,
                    workspace_id=policy.workspace_id,
                    name=policy.name,
                    status=policy.status,
                    current_version_id=policy.current_version_id,
                    created_by_account_id=policy.created_by_account_id,
                    created_at=policy.created_at,
                    updated_at=policy.updated_at,
                    version=policy.version,
                )
            )
            self._insert_version(version)
        except IntegrityError as error:
            raise ApprovalPolicyWriteConflictError from error

    def revise_policy(
        self,
        policy: ApprovalPolicy,
        version: ApprovalPolicyVersion,
        *,
        expected_version: int,
    ) -> bool:
        """新增版本后以乐观锁切换指针；失败时外层事务回滚新版本。"""

        try:
            self._insert_version(version)
            result = cast(
                CursorResult[Any],
                self._session.execute(
                    update(approval_policies)
                    .where(
                        approval_policies.c.workspace_id == policy.workspace_id,
                        approval_policies.c.approval_policy_id == policy.approval_policy_id,
                        approval_policies.c.version == expected_version,
                    )
                    .values(
                        status=policy.status,
                        current_version_id=policy.current_version_id,
                        updated_at=policy.updated_at,
                        version=policy.version,
                    )
                ),
            )
            return result.rowcount == 1
        except IntegrityError as error:
            raise ApprovalPolicyWriteConflictError from error

    def _insert_version(self, version: ApprovalPolicyVersion) -> None:
        self._session.execute(
            insert(approval_policy_versions).values(
                approval_policy_version_id=version.approval_policy_version_id,
                approval_policy_id=version.approval_policy_id,
                workspace_id=version.workspace_id,
                version_number=version.version_number,
                definition=approval_definition_document(version.definition),
                definition_digest=version.definition_digest,
                created_by_account_id=version.created_by_account_id,
                created_at=version.created_at,
            )
        )


class SqlAlchemyApprovalOrganizationDirectory(ApprovalOrganizationDirectory):
    """从活动工作空间、成员、角色和职位事实解析审批人，不按名称猜测负责人。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def workspace_identity(self, workspace_id: UUID) -> ApprovalWorkspaceIdentity | None:
        owner_membership = workspace_memberships.alias("approval_owner_membership")
        row = self._session.execute(
            select(
                workspaces.c.workspace_id,
                workspaces.c.workspace_type,
                workspaces.c.owner_account_id,
                owner_membership.c.account_id.label("membership_owner_account_id"),
            )
            .outerjoin(
                owner_membership,
                (owner_membership.c.workspace_id == workspaces.c.workspace_id)
                & (owner_membership.c.membership_type == "owner")
                & (owner_membership.c.status == "active"),
            )
            .where(
                workspaces.c.workspace_id == workspace_id,
                workspaces.c.status == "active",
            )
        ).one_or_none()
        if row is None:
            return None
        owner_account_id = row.owner_account_id or row.membership_owner_account_id
        if owner_account_id is None or row.workspace_type not in {"personal", "enterprise"}:
            return None
        return ApprovalWorkspaceIdentity(
            row.workspace_id,
            cast(Literal["personal", "enterprise"], row.workspace_type),
            owner_account_id,
        )

    def member_identity(
        self,
        workspace_id: UUID,
        account_id: UUID,
    ) -> ApprovalMemberIdentity | None:
        # 1. 成员状态独立保留；调用方需要区分不存在与已停用成员并统一失败关闭。
        membership = self._session.execute(
            select(workspace_memberships).where(
                workspace_memberships.c.workspace_id == workspace_id,
                workspace_memberships.c.account_id == account_id,
            )
        ).one_or_none()
        if membership is None:
            return None
        # 2. 只返回祖先链全部活动的部门，停用祖先会同时移除主部门和负责人解析入口。
        inactive_closure = department_closure.alias("approval_member_inactive_closure")
        inactive_department = departments.alias("approval_member_inactive_department")
        rows = tuple(
            self._session.execute(
                select(
                    membership_departments.c.department_id,
                    membership_departments.c.is_primary,
                )
                .join(
                    departments,
                    (departments.c.workspace_id == membership_departments.c.workspace_id)
                    & (departments.c.department_id == membership_departments.c.department_id),
                )
                .where(
                    membership_departments.c.workspace_id == workspace_id,
                    membership_departments.c.membership_id == membership.membership_id,
                    departments.c.status == "active",
                    ~exists(
                        select(1)
                        .select_from(
                            inactive_closure.join(
                                inactive_department,
                                (
                                    inactive_department.c.workspace_id
                                    == inactive_closure.c.workspace_id
                                )
                                & (
                                    inactive_department.c.department_id
                                    == inactive_closure.c.ancestor_department_id
                                ),
                            )
                        )
                        .where(
                            inactive_closure.c.workspace_id == workspace_id,
                            inactive_closure.c.descendant_department_id
                            == membership_departments.c.department_id,
                            inactive_department.c.status != "active",
                        )
                    ),
                )
                .order_by(
                    membership_departments.c.is_primary.desc(),
                    membership_departments.c.department_id,
                )
            )
        )
        return ApprovalMemberIdentity(
            membership.account_id,
            membership.status == "active",
            tuple(row.department_id for row in rows),
            next((row.department_id for row in rows if row.is_primary), None),
        )

    def resolve_source(
        self,
        workspace_id: UUID,
        source: ApprovalApproverSource,
        *,
        requester: ApprovalMemberIdentity,
        resource_department_ids: tuple[UUID, ...],
    ) -> tuple[UUID, ...]:
        """按来源选择专用查询，所有结果统一过滤活动成员并稳定排序。"""

        if source.source_type == "accounts":
            accounts = self._active_accounts(workspace_id, source.reference_ids)
        elif source.source_type == "roles":
            accounts = self._role_accounts(workspace_id, source.reference_ids)
        elif source.source_type == "department_managers":
            target_departments = resource_department_ids or (
                (requester.primary_department_id,) if requester.primary_department_id else ()
            )
            accounts = self._position_accounts(
                workspace_id,
                source.reference_ids,
                target_departments,
            )
        else:
            target_departments = self._upper_departments(
                workspace_id,
                requester.primary_department_id,
                source.levels_up,
            )
            accounts = self._position_accounts(
                workspace_id,
                source.reference_ids,
                target_departments,
            )
        return tuple(sorted(accounts, key=lambda item: item.int))

    def _active_accounts(
        self,
        workspace_id: UUID,
        account_ids: tuple[UUID, ...],
    ) -> set[UUID]:
        if not account_ids:
            return set()
        return set(
            self._session.scalars(
                select(workspace_memberships.c.account_id).where(
                    workspace_memberships.c.workspace_id == workspace_id,
                    workspace_memberships.c.account_id.in_(account_ids),
                    workspace_memberships.c.status == "active",
                )
            )
        )

    def _role_accounts(
        self,
        workspace_id: UUID,
        role_ids: tuple[UUID, ...],
    ) -> set[UUID]:
        if not role_ids:
            return set()
        membership_department = membership_departments.alias("approval_role_member_department")
        binding_closure = department_closure.alias("approval_role_binding_closure")
        inactive_ancestor = department_closure.alias("approval_role_inactive_closure")
        inactive_department = departments.alias("approval_role_inactive_department")
        effective_assignment = exists(
            select(1)
            .select_from(
                membership_department.join(
                    binding_closure,
                    (binding_closure.c.workspace_id == membership_department.c.workspace_id)
                    & (
                        binding_closure.c.descendant_department_id
                        == membership_department.c.department_id
                    ),
                )
            )
            .where(
                membership_department.c.workspace_id == workspace_id,
                membership_department.c.membership_id == workspace_memberships.c.membership_id,
                binding_closure.c.ancestor_department_id == role_bindings.c.department_id,
                ~exists(
                    select(1)
                    .select_from(
                        inactive_ancestor.join(
                            inactive_department,
                            (inactive_department.c.workspace_id == inactive_ancestor.c.workspace_id)
                            & (
                                inactive_department.c.department_id
                                == inactive_ancestor.c.ancestor_department_id
                            ),
                        )
                    )
                    .where(
                        inactive_ancestor.c.workspace_id == workspace_id,
                        inactive_ancestor.c.descendant_department_id
                        == membership_department.c.department_id,
                        inactive_department.c.status != "active",
                    )
                ),
            )
        )
        rows = self._session.scalars(
            select(workspace_memberships.c.account_id)
            .join(
                role_bindings,
                role_bindings.c.workspace_id == workspace_memberships.c.workspace_id,
            )
            .join(
                roles,
                (roles.c.workspace_id == role_bindings.c.workspace_id)
                & (roles.c.role_id == role_bindings.c.role_id),
            )
            .where(
                workspace_memberships.c.workspace_id == workspace_id,
                workspace_memberships.c.status == "active",
                roles.c.role_id.in_(role_ids),
                roles.c.status == "active",
                role_bindings.c.status == "active",
                or_(
                    role_bindings.c.scope_type == "workspace",
                    and_(
                        role_bindings.c.scope_type == "member",
                        role_bindings.c.membership_id == workspace_memberships.c.membership_id,
                    ),
                    and_(role_bindings.c.scope_type == "department", effective_assignment),
                ),
            )
            .distinct()
        )
        return set(rows)

    def _position_accounts(
        self,
        workspace_id: UUID,
        position_ids: tuple[UUID, ...],
        department_ids: tuple[UUID, ...],
    ) -> set[UUID]:
        if not position_ids or not department_ids:
            return set()
        inactive_closure = department_closure.alias("approval_position_inactive_closure")
        inactive_department = departments.alias("approval_position_inactive_department")
        return set(
            self._session.scalars(
                select(workspace_memberships.c.account_id)
                .join(
                    membership_positions,
                    (membership_positions.c.workspace_id == workspace_memberships.c.workspace_id)
                    & (
                        membership_positions.c.membership_id
                        == workspace_memberships.c.membership_id
                    ),
                )
                .join(
                    positions,
                    (positions.c.workspace_id == membership_positions.c.workspace_id)
                    & (positions.c.position_id == membership_positions.c.position_id)
                    & (positions.c.department_id == membership_positions.c.department_id),
                )
                .join(
                    departments,
                    (departments.c.workspace_id == positions.c.workspace_id)
                    & (departments.c.department_id == positions.c.department_id),
                )
                .where(
                    workspace_memberships.c.workspace_id == workspace_id,
                    workspace_memberships.c.status == "active",
                    membership_positions.c.position_id.in_(position_ids),
                    membership_positions.c.department_id.in_(department_ids),
                    positions.c.status == "active",
                    departments.c.status == "active",
                    ~exists(
                        select(1)
                        .select_from(
                            inactive_closure.join(
                                inactive_department,
                                (
                                    inactive_department.c.workspace_id
                                    == inactive_closure.c.workspace_id
                                )
                                & (
                                    inactive_department.c.department_id
                                    == inactive_closure.c.ancestor_department_id
                                ),
                            )
                        )
                        .where(
                            inactive_closure.c.workspace_id == workspace_id,
                            inactive_closure.c.descendant_department_id
                            == membership_positions.c.department_id,
                            inactive_department.c.status != "active",
                        )
                    ),
                )
                .distinct()
            )
        )

    def _upper_departments(
        self,
        workspace_id: UUID,
        primary_department_id: UUID | None,
        levels_up: int | None,
    ) -> tuple[UUID, ...]:
        if primary_department_id is None or levels_up is None:
            return ()
        return tuple(
            self._session.scalars(
                select(department_closure.c.ancestor_department_id)
                .join(
                    departments,
                    (departments.c.workspace_id == department_closure.c.workspace_id)
                    & (departments.c.department_id == department_closure.c.ancestor_department_id),
                )
                .where(
                    department_closure.c.workspace_id == workspace_id,
                    department_closure.c.descendant_department_id == primary_department_id,
                    department_closure.c.depth == levels_up,
                    departments.c.status == "active",
                )
                .order_by(department_closure.c.ancestor_department_id)
            )
        )


class SqlAlchemyApprovalPolicyUnitOfWork(ApprovalPolicyUnitOfWork):
    """为策略、版本、目录快照、审计和 Outbox 提供不可嵌套事务。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyApprovalPolicyRepository,
                SqlAlchemyApprovalOrganizationDirectory,
                SqlAlchemyAuditWriter,
                SqlAlchemyOutboxWriter,
            ]
            | None
        ] = ContextVar("approval_policy_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyApprovalPolicyUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Approval Policy Unit of Work 不允许重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyApprovalPolicyRepository(session),
                SqlAlchemyApprovalOrganizationDirectory(session),
                SqlAlchemyAuditWriter(session),
                SqlAlchemyOutboxWriter(session),
            )
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        state = self._state.get()
        if state is not None:
            if exc_type is not None:
                state[0].rollback()
            state[0].close()
            self._state.set(None)

    @property
    def approvals(self) -> SqlAlchemyApprovalPolicyRepository:
        return self._require_state()[1]

    @property
    def directory(self) -> SqlAlchemyApprovalOrganizationDirectory:
        return self._require_state()[2]

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        return self._require_state()[3]

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        return self._require_state()[4]

    def commit(self) -> None:
        try:
            self._require_state()[0].commit()
        except IntegrityError as error:
            self._require_state()[0].rollback()
            raise ApprovalPolicyWriteConflictError from error

    def _require_state(
        self,
    ) -> tuple[
        Session,
        SqlAlchemyApprovalPolicyRepository,
        SqlAlchemyApprovalOrganizationDirectory,
        SqlAlchemyAuditWriter,
        SqlAlchemyOutboxWriter,
    ]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Approval Policy Unit of Work 尚未进入事务范围")
        return state


def _policy(row: Row[Any]) -> ApprovalPolicy:
    return ApprovalPolicy(
        row.approval_policy_id,
        row.workspace_id,
        row.name,
        cast(ApprovalPolicyStatus, row.status),
        row.current_version_id,
        row.created_by_account_id,
        row.created_at,
        row.updated_at,
        row.version,
    )


def _version(row: Row[Any]) -> ApprovalPolicyVersion:
    return ApprovalPolicyVersion(
        row.approval_policy_version_id,
        row.approval_policy_id,
        row.workspace_id,
        row.version_number,
        approval_definition_from_document(row.definition),
        row.definition_digest,
        row.created_by_account_id,
        row.created_at,
    )
