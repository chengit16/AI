"""实现企业空间和成员生命周期聚合的 PostgreSQL 事务边界。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from types import TracebackType
from typing import cast
from uuid import UUID

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import delete, func, insert, literal_column, select, union_all, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Join

from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.authorization.domain.grants import system_role_permission_seed
from ai_platform_api.modules.identity.domain.enterprise import (
    EnterpriseConsoleRecentDocument,
    EnterpriseConsoleSnapshot,
    EnterpriseConsoleStatistics,
    EnterpriseConsoleTrendPoint,
    EnterpriseWorkspace,
    EnterpriseWriteConflictError,
    InvitationStatus,
    MembershipType,
    WorkspaceInvitation,
    WorkspaceMembership,
    WorkspaceMemberSummary,
    WorkspaceRecord,
    WorkspaceSummary,
)
from ai_platform_api.modules.identity.domain.entitlements import default_entitlement
from ai_platform_api.modules.identity.domain.models import (
    MembershipStatus,
    WorkspaceStatus,
    WorkspaceType,
)
from ai_platform_api.modules.identity.domain.roles import system_role_seed
from ai_platform_api.persistence.tables import (
    accounts,
    document_publications,
    documents,
    index_versions,
    ingestion_jobs,
    knowledge_bases,
    membership_departments,
    membership_positions,
    role_bindings,
    role_permission_grants,
    roles,
    workspace_entitlements,
    workspace_feature_settings,
    workspace_invitations,
    workspace_memberships,
    workspace_usage_counters,
    workspaces,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyEnterpriseRepository:
    """在企业空间隔离下维护空间、成员、邀请和成员额度读取。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_workspace(
        self,
        workspace: EnterpriseWorkspace,
        owner: WorkspaceMembership,
    ) -> None:
        # 1. 企业空间和唯一所有者成员关系共用创建审计字段，不能出现无所有者空间。
        audit_values = {
            "created_at": workspace.created_at,
            "created_by_actor_id": workspace.created_by_account_id,
            "updated_at": workspace.created_at,
            "updated_by_actor_id": workspace.created_by_account_id,
            "version": 1,
        }
        try:
            self._session.execute(
                insert(workspaces).values(
                    workspace_id=workspace.workspace_id,
                    workspace_type="enterprise",
                    name=workspace.name,
                    owner_account_id=None,
                    entitlement_version=1,
                    role_version=1,
                    status="active",
                    **audit_values,
                )
            )
            self.add_membership(owner)
            # 2. 套餐快照和功能开关与空间同时建立，业务请求不会观察到未初始化权益。
            entitlement, feature_settings = default_entitlement(
                workspace_id=workspace.workspace_id,
                workspace_type="enterprise",
                occurred_at=workspace.created_at,
            )
            self._session.execute(
                insert(workspace_entitlements).values(
                    workspace_id=entitlement.workspace_id,
                    plan_code=entitlement.plan_code,
                    max_storage_bytes=entitlement.max_storage_bytes,
                    max_members=entitlement.max_members,
                    max_knowledge_bases=entitlement.max_knowledge_bases,
                    max_published_agents=entitlement.max_published_agents,
                    max_monthly_questions=entitlement.max_monthly_questions,
                    open_api_allowed=entitlement.open_api_allowed,
                    public_publish_allowed=entitlement.public_publish_allowed,
                    created_at=entitlement.created_at,
                    updated_at=entitlement.updated_at,
                    version=entitlement.version,
                )
            )
            self._session.execute(
                insert(workspace_feature_settings).values(
                    workspace_id=feature_settings.workspace_id,
                    open_api_enabled=feature_settings.open_api_enabled,
                    updated_at=feature_settings.updated_at,
                    version=feature_settings.version,
                )
            )
            # 3. 系统角色、所有者绑定和默认授权作为完整权限种子一次写入。
            system_roles, system_bindings = system_role_seed(
                workspace_id=workspace.workspace_id,
                owner_membership_id=owner.membership_id,
                occurred_at=workspace.created_at,
            )
            self._session.execute(
                insert(roles),
                [
                    {
                        "role_id": role.role_id,
                        "workspace_id": role.workspace_id,
                        "role_key": role.role_key,
                        "name": role.name,
                        "status": role.status,
                        "system_managed": role.system_managed,
                        "created_at": role.created_at,
                        "updated_at": role.updated_at,
                        "version": role.version,
                    }
                    for role in system_roles
                ],
            )
            self._session.execute(
                insert(role_bindings),
                [
                    {
                        "binding_id": binding.binding_id,
                        "workspace_id": binding.workspace_id,
                        "role_id": binding.role_id,
                        "scope_type": binding.scope_type,
                        "department_id": binding.department_id,
                        "membership_id": binding.membership_id,
                        "status": binding.status,
                        "created_at": binding.created_at,
                        "revoked_at": binding.revoked_at,
                        "version": binding.version,
                    }
                    for binding in system_bindings
                ],
            )
            owner_role, member_role = system_roles
            self._session.execute(
                insert(role_permission_grants),
                [
                    {
                        "workspace_id": grant.workspace_id,
                        "role_id": grant.role_id,
                        "permission_code": grant.permission_code,
                        "scope_type": grant.scope_type,
                        "department_ids": list(grant.department_ids),
                        "resource_ids": list(grant.resource_ids),
                        "maximum_security_level": grant.maximum_security_level,
                        "field_mask": sorted(grant.field_mask),
                    }
                    for grant in system_role_permission_seed(
                        workspace_id=workspace.workspace_id,
                        owner_role_id=owner_role.role_id,
                        member_role_id=member_role.role_id,
                    )
                ],
            )
        except IntegrityError as error:
            raise EnterpriseWriteConflictError from error

    def get_workspace(
        self,
        workspace_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceRecord | None:
        statement = select(
            workspaces.c.workspace_id,
            workspaces.c.workspace_type,
            workspaces.c.name,
            workspaces.c.status,
        ).where(workspaces.c.workspace_id == workspace_id)
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return WorkspaceRecord(
            workspace_id=row.workspace_id,
            workspace_type=cast("WorkspaceType", row.workspace_type),
            name=row.name,
            status=cast("WorkspaceStatus", row.status),
        )

    def get_membership(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceMembership | None:
        statement = select(workspace_memberships).where(
            workspace_memberships.c.workspace_id == workspace_id,
            workspace_memberships.c.account_id == account_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return WorkspaceMembership(
            membership_id=row.membership_id,
            workspace_id=row.workspace_id,
            account_id=row.account_id,
            membership_type=cast("MembershipType", row.membership_type),
            status=cast("MembershipStatus", row.status),
            created_at=row.created_at,
            updated_at=row.updated_at,
            version=row.version,
        )

    def find_active_account_id(self, login_name: str) -> UUID | None:
        return self._session.scalar(
            select(accounts.c.account_id).where(
                accounts.c.login_name == login_name,
                accounts.c.status == "active",
            )
        )

    def add_invitation(self, invitation: WorkspaceInvitation) -> None:
        try:
            self._session.execute(
                insert(workspace_invitations).values(
                    invitation_id=invitation.invitation_id,
                    workspace_id=invitation.workspace_id,
                    invited_account_id=invitation.invited_account_id,
                    invited_by_account_id=invitation.invited_by_account_id,
                    status=invitation.status,
                    created_at=invitation.created_at,
                    expires_at=invitation.expires_at,
                    accepted_at=invitation.accepted_at,
                )
            )
        except IntegrityError as error:
            raise EnterpriseWriteConflictError from error

    def get_invitation(
        self,
        invitation_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceInvitation | None:
        statement = select(workspace_invitations).where(
            workspace_invitations.c.invitation_id == invitation_id
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return WorkspaceInvitation(
            invitation_id=row.invitation_id,
            workspace_id=row.workspace_id,
            invited_account_id=row.invited_account_id,
            invited_by_account_id=row.invited_by_account_id,
            status=cast("InvitationStatus", row.status),
            created_at=row.created_at,
            expires_at=row.expires_at,
            accepted_at=row.accepted_at,
        )

    def get_pending_invitation(
        self,
        workspace_id: UUID,
        invited_account_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceInvitation | None:
        statement = select(workspace_invitations).where(
            workspace_invitations.c.workspace_id == workspace_id,
            workspace_invitations.c.invited_account_id == invited_account_id,
            workspace_invitations.c.status == "pending",
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        return WorkspaceInvitation(
            invitation_id=row.invitation_id,
            workspace_id=row.workspace_id,
            invited_account_id=row.invited_account_id,
            invited_by_account_id=row.invited_by_account_id,
            status=cast("InvitationStatus", row.status),
            created_at=row.created_at,
            expires_at=row.expires_at,
            accepted_at=row.accepted_at,
        )

    def save_invitation(self, invitation: WorkspaceInvitation) -> None:
        self._session.execute(
            update(workspace_invitations)
            .where(workspace_invitations.c.invitation_id == invitation.invitation_id)
            .values(status=invitation.status, accepted_at=invitation.accepted_at)
        )

    def save_membership(self, membership: WorkspaceMembership) -> None:
        # 1. 先读取原状态，后续只在成员生命周期真正变化时推进角色版本。
        previous_status = self._session.scalar(
            select(workspace_memberships.c.status).where(
                workspace_memberships.c.membership_id == membership.membership_id,
                workspace_memberships.c.workspace_id == membership.workspace_id,
            )
        )
        if membership.status != "active":
            # 2. 离开或停用同步撤销组织和自定义角色范围，重新加入不能恢复旧权限。
            self._session.execute(
                delete(membership_positions).where(
                    membership_positions.c.workspace_id == membership.workspace_id,
                    membership_positions.c.membership_id == membership.membership_id,
                )
            )
            self._session.execute(
                delete(membership_departments).where(
                    membership_departments.c.workspace_id == membership.workspace_id,
                    membership_departments.c.membership_id == membership.membership_id,
                )
            )
            self._session.execute(
                update(role_bindings)
                .where(
                    role_bindings.c.workspace_id == membership.workspace_id,
                    role_bindings.c.membership_id == membership.membership_id,
                    role_bindings.c.status == "active",
                    role_bindings.c.role_id.in_(
                        select(roles.c.role_id).where(
                            roles.c.workspace_id == membership.workspace_id,
                            roles.c.system_managed.is_(False),
                        )
                    ),
                )
                .values(
                    status="revoked",
                    revoked_at=membership.updated_at,
                    version=role_bindings.c.version + 1,
                )
            )
        # 3. 角色版本和成员事实按同一状态变化更新，使旧缓存键立即失效。
        if previous_status != membership.status:
            self._session.execute(
                update(workspaces)
                .where(workspaces.c.workspace_id == membership.workspace_id)
                .values(role_version=workspaces.c.role_version + 1)
            )
        self._session.execute(
            update(workspace_memberships)
            .where(
                workspace_memberships.c.membership_id == membership.membership_id,
                workspace_memberships.c.workspace_id == membership.workspace_id,
            )
            .values(
                membership_type=membership.membership_type,
                status=membership.status,
                updated_at=membership.updated_at,
                version=membership.version,
            )
        )

    def add_membership(self, membership: WorkspaceMembership) -> None:
        try:
            self._session.execute(
                insert(workspace_memberships).values(
                    membership_id=membership.membership_id,
                    workspace_id=membership.workspace_id,
                    account_id=membership.account_id,
                    membership_type=membership.membership_type,
                    status=membership.status,
                    created_at=membership.created_at,
                    updated_at=membership.updated_at,
                    version=membership.version,
                )
            )
        except IntegrityError as error:
            raise EnterpriseWriteConflictError from error

    def list_workspaces(self, account_id: UUID) -> tuple[WorkspaceSummary, ...]:
        rows = self._session.execute(
            select(
                workspaces.c.workspace_id,
                workspaces.c.workspace_type,
                workspaces.c.name,
                workspaces.c.status,
                workspace_memberships.c.membership_type,
                workspace_memberships.c.status.label("membership_status"),
            )
            .join(
                workspace_memberships,
                workspace_memberships.c.workspace_id == workspaces.c.workspace_id,
            )
            .where(
                workspace_memberships.c.account_id == account_id,
                workspace_memberships.c.status == "active",
                workspaces.c.status == "active",
            )
            .order_by(
                workspaces.c.workspace_type, workspaces.c.created_at, workspaces.c.workspace_id
            )
        ).all()
        return tuple(
            WorkspaceSummary(
                workspace_id=row.workspace_id,
                workspace_type=cast("WorkspaceType", row.workspace_type),
                name=row.name,
                status=cast("WorkspaceStatus", row.status),
                membership_type=cast("MembershipType", row.membership_type),
                membership_status=cast("MembershipStatus", row.membership_status),
            )
            for row in rows
        )

    def list_members(self, workspace_id: UUID) -> tuple[WorkspaceMemberSummary, ...]:
        rows = self._session.execute(
            select(
                accounts.c.account_id,
                accounts.c.display_name,
                workspace_memberships.c.membership_type,
                workspace_memberships.c.status,
            )
            .join(
                workspace_memberships,
                workspace_memberships.c.account_id == accounts.c.account_id,
            )
            .where(workspace_memberships.c.workspace_id == workspace_id)
            .order_by(
                workspace_memberships.c.membership_type,
                accounts.c.display_name,
                accounts.c.account_id,
            )
        ).all()
        return tuple(
            WorkspaceMemberSummary(
                account_id=row.account_id,
                display_name=row.display_name,
                membership_type=cast("MembershipType", row.membership_type),
                status=cast("MembershipStatus", row.status),
            )
            for row in rows
        )

    def member_capacity_available(self, workspace_id: UUID) -> bool:
        limit = self._session.scalar(
            select(workspace_entitlements.c.max_members).where(
                workspace_entitlements.c.workspace_id == workspace_id
            )
        )
        active_members = self._session.scalar(
            select(func.count())
            .select_from(workspace_memberships)
            .where(
                workspace_memberships.c.workspace_id == workspace_id,
                workspace_memberships.c.status == "active",
            )
        )
        return limit is not None and int(active_members or 0) < limit

    def get_console_snapshot(
        self,
        workspace_id: UUID,
        *,
        generated_at: datetime,
        trend_months: int,
        recent_limit: int,
        maximum_security_level: SecurityLevel,
        include_recent_documents: bool,
    ) -> EnterpriseConsoleSnapshot | None:
        """在当前数据库事务中读取企业控制台低敏聚合，所有查询显式绑定空间。"""

        workspace = self.get_workspace(workspace_id)
        if workspace is None:
            return None
        month_start = _utc_month_start(generated_at, offset=trend_months - 1)
        active_members, active_bases = self._console_workspace_counts(workspace_id)
        active_documents, published_documents = self._console_document_counts(
            workspace_id, maximum_security_level
        )
        processing_document_count, failed_document_count = self._console_processing_counts(
            workspace_id, maximum_security_level
        )
        storage_used, storage_limit = self._console_storage(workspace_id)
        trend = self._console_trend(
            workspace_id,
            maximum_security_level,
            month_start=month_start,
            generated_at=generated_at,
            trend_months=trend_months,
        )
        recent_documents = self._console_recent_documents(
            workspace_id,
            maximum_security_level,
            recent_limit=recent_limit,
            include_recent_documents=include_recent_documents,
        )
        return EnterpriseConsoleSnapshot(
            workspace=workspace,
            statistics=EnterpriseConsoleStatistics(
                active_member_count=active_members,
                active_knowledge_base_count=active_bases,
                active_document_count=active_documents,
                published_document_count=published_documents,
                processing_document_count=processing_document_count,
                failed_document_count=failed_document_count,
                storage_used_bytes=storage_used,
                storage_limit_bytes=storage_limit,
            ),
            trend=trend,
            recent_documents=recent_documents,
            generated_at=generated_at,
            time_window_start=month_start,
            time_window_end=generated_at,
            consistency="eventually_consistent",
            profile_description=None,
            profile_logo_url=None,
        )

    def _console_workspace_counts(self, workspace_id: UUID) -> tuple[int, int]:
        """读取活动成员与活动知识库数量，两项均严格限定当前空间。"""

        active_members = self._session.scalar(
            select(func.count())
            .select_from(workspace_memberships)
            .where(
                workspace_memberships.c.workspace_id == workspace_id,
                workspace_memberships.c.status == "active",
            )
        )
        active_bases = self._session.scalar(
            select(func.count())
            .select_from(knowledge_bases)
            .where(
                knowledge_bases.c.workspace_id == workspace_id,
                knowledge_bases.c.status == "active",
            )
        )
        return int(active_members or 0), int(active_bases or 0)

    def _console_document_counts(
        self, workspace_id: UUID, maximum_security_level: SecurityLevel
    ) -> tuple[int, int]:
        """按密级上限统计活动文档及拥有当前发布指针的文档。"""

        active_from = _active_documents_from()
        conditions = _active_document_conditions(workspace_id, maximum_security_level)
        active_count = self._session.scalar(
            select(func.count(func.distinct(documents.c.document_id)))
            .select_from(active_from)
            .where(*conditions)
        )
        published_count = self._session.scalar(
            select(func.count(func.distinct(documents.c.document_id)))
            .select_from(active_from.join(document_publications, _publication_join_condition()))
            .where(*conditions)
        )
        return int(active_count or 0), int(published_count or 0)

    def _console_processing_counts(
        self, workspace_id: UUID, maximum_security_level: SecurityLevel
    ) -> tuple[int, int]:
        """合并入库与索引状态，并按文档去重处理中和失败数量。"""

        conditions = _active_document_conditions(workspace_id, maximum_security_level)
        active_from = _active_documents_from()
        ingestion_from = ingestion_jobs.join(
            active_from,
            (documents.c.workspace_id == ingestion_jobs.c.workspace_id)
            & (documents.c.document_id == ingestion_jobs.c.document_id),
        )
        index_from = index_versions.join(
            active_from,
            (documents.c.workspace_id == index_versions.c.workspace_id)
            & (documents.c.document_id == index_versions.c.document_id),
        )
        processing_ids = union_all(
            select(ingestion_jobs.c.document_id)
            .select_from(ingestion_from)
            .where(
                ingestion_jobs.c.workspace_id == workspace_id,
                ingestion_jobs.c.status.in_(("queued", "running", "retry_wait")),
                *conditions,
            ),
            select(index_versions.c.document_id)
            .select_from(index_from)
            .where(
                index_versions.c.workspace_id == workspace_id,
                index_versions.c.status.in_(
                    (
                        "queued",
                        "embedding_running",
                        "embedding_retry_wait",
                        "index_queued",
                        "index_running",
                        "index_retry_wait",
                    )
                ),
                *conditions,
            ),
        ).subquery()
        failed_ids = union_all(
            select(ingestion_jobs.c.document_id)
            .select_from(ingestion_from)
            .where(
                ingestion_jobs.c.workspace_id == workspace_id,
                ingestion_jobs.c.status.in_(("failed", "timed_out")),
                *conditions,
            ),
            select(index_versions.c.document_id)
            .select_from(index_from)
            .where(
                index_versions.c.workspace_id == workspace_id,
                index_versions.c.status.in_(("failed", "dead_letter")),
                *conditions,
            ),
        ).subquery()
        processing_count = self._session.scalar(
            select(func.count(func.distinct(processing_ids.c.document_id))).select_from(
                processing_ids
            )
        )
        failed_count = self._session.scalar(
            select(func.count(func.distinct(failed_ids.c.document_id))).select_from(failed_ids)
        )
        return int(processing_count or 0), int(failed_count or 0)

    def _console_storage(self, workspace_id: UUID) -> tuple[int, int]:
        """读取最终一致的存储用量计数器和当前套餐额度。"""

        storage_used = self._session.scalar(
            select(workspace_usage_counters.c.used_value).where(
                workspace_usage_counters.c.workspace_id == workspace_id,
                workspace_usage_counters.c.metric == "storage_bytes",
                workspace_usage_counters.c.period_key == "lifetime",
            )
        )
        storage_limit = self._session.scalar(
            select(workspace_entitlements.c.max_storage_bytes).where(
                workspace_entitlements.c.workspace_id == workspace_id
            )
        )
        return int(storage_used or 0), int(storage_limit or 0)

    def _console_trend(
        self,
        workspace_id: UUID,
        maximum_security_level: SecurityLevel,
        *,
        month_start: datetime,
        generated_at: datetime,
        trend_months: int,
    ) -> tuple[EnterpriseConsoleTrendPoint, ...]:
        """按 UTC 月份统计新建文档，并为无数据月份补零。"""

        # PostgreSQL 要求 SELECT 与 GROUP BY 复用同一月份表达式；SQL 字面量避免
        # SQLAlchemy 生成不同绑定参数后把语义相同的 date_trunc 误判为不同表达式。
        month_bucket = func.date_trunc(
            literal_column("'month'"),
            documents.c.created_at,
            literal_column("'UTC'"),
        )
        period = func.to_char(month_bucket, literal_column("'YYYY-MM'")).label("period")
        rows = self._session.execute(
            select(
                period,
                func.count(func.distinct(documents.c.document_id)).label("document_count"),
            )
            .select_from(_active_documents_from())
            .where(
                *_active_document_conditions(workspace_id, maximum_security_level),
                documents.c.created_at >= month_start,
                documents.c.created_at <= generated_at,
            )
            .group_by(month_bucket)
        ).all()
        values = {row.period: int(row.document_count or 0) for row in rows}
        return tuple(
            EnterpriseConsoleTrendPoint(
                period=_month_key(_add_months(month_start, index)),
                document_count=values.get(_month_key(_add_months(month_start, index)), 0),
            )
            for index in range(trend_months)
        )

    def _console_recent_documents(
        self,
        workspace_id: UUID,
        maximum_security_level: SecurityLevel,
        *,
        recent_limit: int,
        include_recent_documents: bool,
    ) -> tuple[EnterpriseConsoleRecentDocument, ...]:
        """返回低敏最近内容；字段遮罩命中时完全跳过标题查询。"""

        if not include_recent_documents:
            return ()
        rows = self._session.execute(
            select(
                documents.c.document_id,
                documents.c.knowledge_base_id,
                knowledge_bases.c.name.label("knowledge_base_name"),
                documents.c.title,
                documents.c.updated_at,
                document_publications.c.published_at,
            )
            .select_from(
                _active_documents_from().outerjoin(
                    document_publications, _publication_join_condition()
                )
            )
            .where(*_active_document_conditions(workspace_id, maximum_security_level))
            .order_by(documents.c.updated_at.desc(), documents.c.document_id)
            .limit(recent_limit)
        ).all()
        return tuple(
            EnterpriseConsoleRecentDocument(
                document_id=row.document_id,
                knowledge_base_id=row.knowledge_base_id,
                knowledge_base_name=row.knowledge_base_name,
                title=row.title,
                updated_at=row.updated_at,
                published_at=row.published_at,
                status="published" if row.published_at is not None else "unpublished",
            )
            for row in rows
        )


def _utc_month_start(value: datetime, *, offset: int) -> datetime:
    """返回向前偏移指定月份后的 UTC 月初，保证趋势窗口稳定可补零。"""

    current = value.astimezone(UTC)
    return _add_months(current.replace(day=1, hour=0, minute=0, second=0, microsecond=0), -offset)


def _active_documents_from() -> Join:
    """构造带工作空间等值条件的活动文档与知识库联接。"""

    return documents.join(
        knowledge_bases,
        (knowledge_bases.c.workspace_id == documents.c.workspace_id)
        & (knowledge_bases.c.knowledge_base_id == documents.c.knowledge_base_id),
    )


def _active_document_conditions(
    workspace_id: UUID, maximum_security_level: SecurityLevel
) -> tuple[ColumnElement[bool], ...]:
    """集中维护控制台所有文档聚合共享的空间、状态和密级约束。"""

    return (
        documents.c.workspace_id == workspace_id,
        documents.c.status == "active",
        knowledge_bases.c.workspace_id == workspace_id,
        knowledge_bases.c.status == "active",
        _document_security_scope(maximum_security_level),
    )


def _publication_join_condition() -> ColumnElement[bool]:
    """保证发布指针只能联接同一工作空间内的目标文档。"""

    return (document_publications.c.workspace_id == documents.c.workspace_id) & (
        document_publications.c.document_id == documents.c.document_id
    )


def _add_months(value: datetime, months: int) -> datetime:
    """按日历月份计算时间，不使用固定天数避免跨月边界漂移。"""

    absolute = value.year * 12 + (value.month - 1) + months
    year, month_index = divmod(absolute, 12)
    return value.replace(year=year, month=month_index + 1, day=1)


def _month_key(value: datetime) -> str:
    return value.strftime("%Y-%m")


def _document_security_scope(
    maximum_security_level: SecurityLevel,
) -> ColumnElement[bool]:
    """把企业控制台密级上限转换为显式允许集合，未知值不能扩大查询。"""

    allowed_levels: dict[SecurityLevel, tuple[SecurityLevel, ...]] = {
        "PUBLIC": ("PUBLIC",),
        "INTERNAL": ("PUBLIC", "INTERNAL"),
        "CONFIDENTIAL": ("PUBLIC", "INTERNAL", "CONFIDENTIAL"),
        "RESTRICTED": ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"),
    }
    return documents.c.security_level.in_(allowed_levels[maximum_security_level])


class SqlAlchemyEnterpriseUnitOfWork:
    """企业空间、成员、邀请、审计与 Outbox 共用一个数据库事务。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyEnterpriseRepository,
                SqlAlchemyAuditWriter,
                SqlAlchemyOutboxWriter,
            ]
            | None
        ] = ContextVar("enterprise_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyEnterpriseUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Enterprise Unit of Work 不允许在同一上下文重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyEnterpriseRepository(session),
                SqlAlchemyAuditWriter(session),
                SqlAlchemyOutboxWriter(session),
            )
        )
        return self

    def _current(
        self,
    ) -> tuple[
        Session,
        SqlAlchemyEnterpriseRepository,
        SqlAlchemyAuditWriter,
        SqlAlchemyOutboxWriter,
    ]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Enterprise Unit of Work 尚未进入事务范围")
        return state

    @property
    def enterprise(self) -> SqlAlchemyEnterpriseRepository:
        return self._current()[1]

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        return self._current()[2]

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        return self._current()[3]

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        state = self._state.get()
        if state is not None:
            session = state[0]
            if exc_type is not None:
                session.rollback()
            session.close()
            self._state.set(None)

    def commit(self) -> None:
        session = self._current()[0]
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise EnterpriseWriteConflictError from error
