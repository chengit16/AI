"""实现企业分类、团队知识域和授权范围投影的 PostgreSQL 事务。"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime
from types import TracebackType
from typing import Any, cast
from uuid import UUID

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import delete, distinct, func, insert, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.authorization.domain.fields import SECURITY_LEVEL_RANK, SecurityLevel
from ai_platform_api.modules.enterprise_knowledge.domain.models import (
    CategoryVisibility,
    DocumentPublishCandidate,
    DocumentPublishCategorySnapshot,
    DocumentPublishFailureReason,
    DocumentPublishRequest,
    DocumentPublishRequestStatus,
    EnterpriseCategory,
    EnterpriseCategoryView,
    EnterpriseDepartmentOption,
    EnterpriseDocumentOption,
    EnterpriseKnowledgeBaseOption,
    EnterpriseKnowledgePortalSnapshot,
    EnterpriseKnowledgeStatistics,
    EnterpriseKnowledgeWriteConflictError,
    EnterpriseMemberOption,
    GovernanceStatus,
    RagPolicy,
    RagPolicyMode,
    ResolvedKnowledgeDomainScope,
    TeamKnowledgeDomain,
)
from ai_platform_api.persistence.tables import (
    accounts,
    departments,
    document_publish_request_categories,
    document_publish_requests,
    document_versions,
    documents,
    enterprise_categories,
    enterprise_category_documents,
    index_versions,
    knowledge_bases,
    membership_departments,
    team_knowledge_domain_bases,
    team_knowledge_domain_departments,
    team_knowledge_domain_members,
    team_knowledge_domain_rag_policies,
    team_knowledge_domains,
    workspace_memberships,
    workspaces,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyEnterpriseKnowledgeRepository:
    """集中维护企业知识治理多表关系和失败关闭的范围解析。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def require_active_enterprise_member(
        self, workspace_id: UUID, account_id: UUID, *, for_update: bool = False
    ) -> bool:
        # 1. 先读取活动文档和不可变就绪版本，确保候选只属于当前工作空间。
        statement = (
            select(workspace_memberships.c.membership_id)
            .select_from(
                workspace_memberships.join(
                    workspaces,
                    workspaces.c.workspace_id == workspace_memberships.c.workspace_id,
                )
            )
            .where(
                workspace_memberships.c.workspace_id == workspace_id,
                workspace_memberships.c.account_id == account_id,
                workspace_memberships.c.status == "active",
                workspaces.c.workspace_type == "enterprise",
                workspaces.c.status == "active",
            )
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.execute(statement).scalar_one_or_none() is not None

    def get_portal_snapshot(
        self,
        workspace_id: UUID,
        *,
        generated_at: datetime,
        mask_display_name: bool,
        mask_document_title: bool,
        maximum_security_level: SecurityLevel,
    ) -> EnterpriseKnowledgePortalSnapshot | None:
        """用一个数据库事务聚合门户事实，避免前端拼接不同时间点的授权数据。"""

        # 长函数保留原因: 门户快照必须在同一 Repository 调用中完成空间、密级和字段裁剪，
        # 避免拆成可被调用方分别执行的公开查询后产生跨时点授权漂移或误读受限字段。
        # 1. 先计算密级上限并确认目标是活动企业空间，失败时不泄露其他空间事实。
        allowed_levels = tuple(
            level
            for level, rank in SECURITY_LEVEL_RANK.items()
            if rank <= SECURITY_LEVEL_RANK[maximum_security_level]
        )

        workspace = self._session.execute(
            select(workspaces.c.name, workspaces.c.workspace_type, workspaces.c.status).where(
                workspaces.c.workspace_id == workspace_id
            )
        ).one_or_none()
        if (
            workspace is None
            or workspace.workspace_type != "enterprise"
            or workspace.status != "active"
        ):
            return None

        # 2. 聚合分类、裁剪后的文档绑定和知识域，治理关系不复制任何内容事实。
        category_rows = tuple(
            self._session.execute(
                select(enterprise_categories)
                .where(enterprise_categories.c.workspace_id == workspace_id)
                .order_by(
                    enterprise_categories.c.status,
                    enterprise_categories.c.name,
                    enterprise_categories.c.category_id,
                )
            )
        )
        document_bindings: dict[UUID, list[UUID]] = {}
        for row in self._session.execute(
            select(
                enterprise_category_documents.c.category_id,
                enterprise_category_documents.c.document_id,
            )
            .select_from(
                enterprise_category_documents.join(
                    documents,
                    (documents.c.workspace_id == enterprise_category_documents.c.workspace_id)
                    & (documents.c.document_id == enterprise_category_documents.c.document_id),
                )
            )
            .where(
                enterprise_category_documents.c.workspace_id == workspace_id,
                documents.c.status == "active",
                documents.c.security_level.in_(allowed_levels),
            )
            .order_by(
                enterprise_category_documents.c.category_id,
                enterprise_category_documents.c.document_id,
            )
        ):
            document_bindings.setdefault(row.category_id, []).append(row.document_id)
        categories = tuple(
            EnterpriseCategoryView(
                category=_category(row),
                document_ids=tuple(document_bindings.get(row.category_id, ())),
            )
            for row in category_rows
        )

        domain_rows = tuple(
            self._session.execute(
                select(team_knowledge_domains)
                .where(team_knowledge_domains.c.workspace_id == workspace_id)
                .order_by(
                    team_knowledge_domains.c.status,
                    team_knowledge_domains.c.name,
                    team_knowledge_domains.c.domain_id,
                )
            )
        )
        domains = tuple(self._domain_from_row(row) for row in domain_rows)
        # 3. 按字段策略构造可选项并从同一快照计算统计，受限列不会进入 SQL 读取。
        document_statement = select(
            documents.c.document_id,
            documents.c.knowledge_base_id,
            documents.c.security_level,
        )
        if not mask_document_title:
            document_statement = document_statement.add_columns(documents.c.title)
        document_statement = document_statement.where(
            documents.c.workspace_id == workspace_id,
            documents.c.status == "active",
            documents.c.security_level.in_(allowed_levels),
        ).order_by(documents.c.document_id)
        document_options = tuple(
            EnterpriseDocumentOption(
                row.document_id,
                row.knowledge_base_id,
                "受限文档" if mask_document_title else row.title,
                row.security_level,
            )
            for row in self._session.execute(document_statement)
        )
        base_options = tuple(
            EnterpriseKnowledgeBaseOption(
                row.knowledge_base_id,
                row.name,
                row.default_security_level,
            )
            for row in self._session.execute(
                select(
                    knowledge_bases.c.knowledge_base_id,
                    knowledge_bases.c.name,
                    knowledge_bases.c.default_security_level,
                )
                .where(
                    knowledge_bases.c.workspace_id == workspace_id,
                    knowledge_bases.c.status == "active",
                )
                .order_by(knowledge_bases.c.name, knowledge_bases.c.knowledge_base_id)
            )
        )
        department_options = tuple(
            EnterpriseDepartmentOption(row.department_id, row.name)
            for row in self._session.execute(
                select(departments.c.department_id, departments.c.name)
                .where(departments.c.workspace_id == workspace_id, departments.c.status == "active")
                .order_by(departments.c.name, departments.c.department_id)
            )
        )
        member_statement = select(
            workspace_memberships.c.membership_id,
            workspace_memberships.c.account_id,
        ).select_from(
            workspace_memberships.join(
                accounts,
                accounts.c.account_id == workspace_memberships.c.account_id,
            )
        )
        if mask_display_name:
            member_statement = member_statement.where(
                workspace_memberships.c.workspace_id == workspace_id,
                workspace_memberships.c.status == "active",
                accounts.c.status == "active",
            ).order_by(workspace_memberships.c.membership_id)
        else:
            member_statement = (
                member_statement.add_columns(accounts.c.display_name)
                .where(
                    workspace_memberships.c.workspace_id == workspace_id,
                    workspace_memberships.c.status == "active",
                    accounts.c.status == "active",
                )
                .order_by(accounts.c.display_name, workspace_memberships.c.membership_id)
            )
        member_options = tuple(
            EnterpriseMemberOption(
                row.membership_id,
                row.account_id,
                None if mask_display_name else row.display_name,
            )
            for row in self._session.execute(member_statement)
        )
        active_category_ids = {
            item.category.category_id for item in categories if item.category.status == "active"
        }
        classified_documents = {
            document_id
            for item in categories
            if item.category.category_id in active_category_ids
            for document_id in item.document_ids
        }
        active_domains = tuple(item for item in domains if item.status == "active")
        governed_bases = {
            knowledge_base_id
            for item in active_domains
            for knowledge_base_id in item.knowledge_base_ids
        }
        return EnterpriseKnowledgePortalSnapshot(
            workspace_id=workspace_id,
            workspace_name=workspace.name,
            statistics=EnterpriseKnowledgeStatistics(
                active_categories=len(active_category_ids),
                active_domains=len(active_domains),
                classified_documents=len(classified_documents),
                governed_knowledge_bases=len(governed_bases),
            ),
            categories=categories,
            domains=domains,
            documents=document_options,
            knowledge_bases=base_options,
            departments=department_options,
            members=member_options,
            generated_at=generated_at,
        )

    def get_category(
        self, workspace_id: UUID, category_id: UUID, *, for_update: bool = False
    ) -> EnterpriseCategory | None:
        statement = select(enterprise_categories).where(
            enterprise_categories.c.workspace_id == workspace_id,
            enterprise_categories.c.category_id == category_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return None if row is None else _category(row)

    def get_category_document_ids(
        self, workspace_id: UUID, category_id: UUID, *, maximum_security_level: SecurityLevel
    ) -> tuple[UUID, ...]:
        """读取当前密级可见绑定，防止写响应泄露受限文档标识。"""

        allowed_levels = tuple(
            level
            for level, rank in SECURITY_LEVEL_RANK.items()
            if rank <= SECURITY_LEVEL_RANK[maximum_security_level]
        )

        return tuple(
            self._session.execute(
                select(enterprise_category_documents.c.document_id)
                .select_from(
                    enterprise_category_documents.join(
                        documents,
                        (documents.c.workspace_id == enterprise_category_documents.c.workspace_id)
                        & (documents.c.document_id == enterprise_category_documents.c.document_id),
                    )
                )
                .where(
                    enterprise_category_documents.c.workspace_id == workspace_id,
                    enterprise_category_documents.c.category_id == category_id,
                    documents.c.status == "active",
                    documents.c.security_level.in_(allowed_levels),
                )
                .order_by(enterprise_category_documents.c.document_id)
            ).scalars()
        )

    def add_category(self, category: EnterpriseCategory) -> None:
        self._session.execute(insert(enterprise_categories).values(**_category_values(category)))

    def update_category(self, category: EnterpriseCategory, *, expected_version: int) -> None:
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(enterprise_categories)
                .where(
                    enterprise_categories.c.workspace_id == category.workspace_id,
                    enterprise_categories.c.category_id == category.category_id,
                    enterprise_categories.c.version == expected_version,
                )
                .values(**_category_values(category, include_identity=False))
            ),
        )
        if result.rowcount != 1:
            raise EnterpriseKnowledgeWriteConflictError

    def replace_category_documents(
        self,
        *,
        workspace_id: UUID,
        category_id: UUID,
        document_ids: tuple[UUID, ...],
        occurred_at: datetime,
    ) -> None:
        self._session.execute(
            delete(enterprise_category_documents).where(
                enterprise_category_documents.c.workspace_id == workspace_id,
                enterprise_category_documents.c.category_id == category_id,
            )
        )
        if document_ids:
            self._session.execute(
                insert(enterprise_category_documents),
                [
                    {
                        "workspace_id": workspace_id,
                        "category_id": category_id,
                        "document_id": document_id,
                        "created_at": occurred_at,
                    }
                    for document_id in document_ids
                ],
            )

    def get_domain(
        self, workspace_id: UUID, domain_id: UUID, *, for_update: bool = False
    ) -> TeamKnowledgeDomain | None:
        statement = select(team_knowledge_domains).where(
            team_knowledge_domains.c.workspace_id == workspace_id,
            team_knowledge_domains.c.domain_id == domain_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        return None if row is None else self._domain_from_row(row, for_update=for_update)

    def add_domain(self, domain: TeamKnowledgeDomain) -> None:
        self._session.execute(insert(team_knowledge_domains).values(**_domain_values(domain)))
        self._insert_rag_policy(domain)
        self.replace_domain_scope(
            workspace_id=domain.workspace_id,
            domain_id=domain.domain_id,
            member_ids=domain.member_ids,
            department_ids=domain.department_ids,
            knowledge_base_ids=domain.knowledge_base_ids,
            occurred_at=domain.created_at,
        )

    def update_domain(
        self,
        domain: TeamKnowledgeDomain,
        *,
        expected_version: int,
        add_rag_policy_version: bool,
    ) -> None:
        if add_rag_policy_version:
            self._insert_rag_policy(domain)
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                update(team_knowledge_domains)
                .where(
                    team_knowledge_domains.c.workspace_id == domain.workspace_id,
                    team_knowledge_domains.c.domain_id == domain.domain_id,
                    team_knowledge_domains.c.version == expected_version,
                )
                .values(**_domain_values(domain, include_identity=False))
            ),
        )
        if result.rowcount != 1:
            raise EnterpriseKnowledgeWriteConflictError

    def replace_domain_scope(
        self,
        *,
        workspace_id: UUID,
        domain_id: UUID,
        member_ids: tuple[UUID, ...],
        department_ids: tuple[UUID, ...],
        knowledge_base_ids: tuple[UUID, ...],
        occurred_at: datetime,
    ) -> None:
        relations = (
            (team_knowledge_domain_members, "membership_id", member_ids),
            (team_knowledge_domain_departments, "department_id", department_ids),
            (team_knowledge_domain_bases, "knowledge_base_id", knowledge_base_ids),
        )
        # 三类范围在同一事务内整体替换，调用方永远看不到中间扩大或缩小状态。
        for table, key, values in relations:
            self._session.execute(
                delete(table).where(
                    table.c.workspace_id == workspace_id,
                    table.c.domain_id == domain_id,
                )
            )
            if values:
                self._session.execute(
                    insert(table),
                    [
                        {
                            "workspace_id": workspace_id,
                            "domain_id": domain_id,
                            key: value,
                            "created_at": occurred_at,
                        }
                        for value in values
                    ],
                )

    def references_exist(
        self,
        *,
        workspace_id: UUID,
        category_id: UUID | None = None,
        department_ids: tuple[UUID, ...] = (),
        document_ids: tuple[UUID, ...] = (),
        member_ids: tuple[UUID, ...] = (),
        knowledge_base_ids: tuple[UUID, ...] = (),
        maximum_security_level: SecurityLevel = "RESTRICTED",
    ) -> bool:
        if (
            category_id is not None
            and self._count(
                enterprise_categories.c.category_id,
                enterprise_categories.c.workspace_id == workspace_id,
                enterprise_categories.c.category_id == category_id,
                enterprise_categories.c.status == "active",
            )
            != 1
        ):
            return False
        checks = (
            (
                departments.c.department_id,
                department_ids,
                (
                    departments.c.workspace_id == workspace_id,
                    departments.c.status == "active",
                ),
            ),
            (
                documents.c.document_id,
                document_ids,
                (
                    documents.c.workspace_id == workspace_id,
                    documents.c.status == "active",
                    documents.c.security_level.in_(
                        tuple(
                            level
                            for level, rank in SECURITY_LEVEL_RANK.items()
                            if rank <= SECURITY_LEVEL_RANK[maximum_security_level]
                        )
                    ),
                ),
            ),
            (
                workspace_memberships.c.membership_id,
                member_ids,
                (
                    workspace_memberships.c.workspace_id == workspace_id,
                    workspace_memberships.c.status == "active",
                ),
            ),
            (
                knowledge_bases.c.knowledge_base_id,
                knowledge_base_ids,
                (
                    knowledge_bases.c.workspace_id == workspace_id,
                    knowledge_bases.c.status == "active",
                ),
            ),
        )
        return all(
            not values or self._count(column, *conditions, column.in_(values)) == len(values)
            for column, values, conditions in checks
        )

    def category_parent_would_cycle(
        self,
        *,
        workspace_id: UUID,
        category_id: UUID,
        parent_category_id: UUID,
    ) -> bool:
        parent_by_id = {
            row.category_id: row.parent_category_id
            for row in self._session.execute(
                select(
                    enterprise_categories.c.category_id,
                    enterprise_categories.c.parent_category_id,
                ).where(enterprise_categories.c.workspace_id == workspace_id)
            )
        }
        cursor: UUID | None = parent_category_id
        visited: set[UUID] = set()
        while cursor is not None:
            if cursor == category_id or cursor in visited:
                return True
            visited.add(cursor)
            cursor = parent_by_id.get(cursor)
        return False

    def category_has_active_children(self, workspace_id: UUID, category_id: UUID) -> bool:
        return (
            self._session.execute(
                select(enterprise_categories.c.category_id)
                .where(
                    enterprise_categories.c.workspace_id == workspace_id,
                    enterprise_categories.c.parent_category_id == category_id,
                    enterprise_categories.c.status == "active",
                )
                .limit(1)
            ).scalar_one_or_none()
            is not None
        )

    def get_document_publish_candidate(
        self,
        workspace_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
        *,
        for_update: bool = False,
    ) -> DocumentPublishCandidate | None:
        """读取不可变就绪版本及当前活动分类，索引只判断是否存在可发布候选。"""

        # 1. 先读取活动文档和不可变就绪版本，确保候选只属于当前工作空间。
        statement = (
            select(
                documents.c.document_id,
                documents.c.knowledge_base_id,
                documents.c.title,
                documents.c.security_level,
                documents.c.department_ids,
                document_versions.c.document_version_id,
                document_versions.c.version_number,
                document_versions.c.content_hash,
            )
            .join(
                document_versions,
                (document_versions.c.workspace_id == documents.c.workspace_id)
                & (document_versions.c.document_id == documents.c.document_id),
            )
            .where(
                documents.c.workspace_id == workspace_id,
                documents.c.document_id == document_id,
                documents.c.status == "active",
                document_versions.c.document_version_id == document_version_id,
                document_versions.c.status == "ready",
            )
        )
        if for_update:
            # 文档行与版本行必须共用同一锁定快照，避免审批绑定期间版本关系漂移。
            statement = statement.with_for_update()
        row = self._session.execute(statement).one_or_none()
        if row is None or row.content_hash is None:
            return None
        # 2. 冻结当前活动分类及审批开关，批准时通过摘要检测治理漂移。
        category_statement = (
            select(
                enterprise_categories.c.category_id,
                enterprise_categories.c.name,
                enterprise_categories.c.version,
                enterprise_categories.c.approval_required,
            )
            .join(
                enterprise_category_documents,
                (
                    enterprise_category_documents.c.workspace_id
                    == enterprise_categories.c.workspace_id
                )
                & (
                    enterprise_category_documents.c.category_id
                    == enterprise_categories.c.category_id
                ),
            )
            .where(
                enterprise_category_documents.c.workspace_id == workspace_id,
                enterprise_category_documents.c.document_id == document_id,
                enterprise_categories.c.status == "active",
            )
            .order_by(enterprise_categories.c.category_id)
        )
        if for_update:
            category_statement = category_statement.with_for_update(of=enterprise_categories)
        categories = tuple(
            DocumentPublishCategorySnapshot(
                item.category_id,
                item.name,
                item.version,
                item.approval_required,
            )
            for item in self._session.execute(category_statement)
        )
        # 3. 最后读取目标版本可用索引，未就绪只作为失败原因而不改变旧指针。
        index_statement = (
            select(index_versions.c.index_version_id)
            .where(
                index_versions.c.workspace_id == workspace_id,
                index_versions.c.document_id == document_id,
                index_versions.c.document_version_id == document_version_id,
                index_versions.c.status.in_(("ready", "active")),
            )
            .order_by(index_versions.c.build_no.desc())
            .limit(1)
        )
        if for_update:
            index_statement = index_statement.with_for_update()
        index_ready = self._session.scalar(index_statement)
        return DocumentPublishCandidate(
            workspace_id=workspace_id,
            document_id=row.document_id,
            document_version_id=row.document_version_id,
            knowledge_base_id=row.knowledge_base_id,
            title=row.title,
            version_number=row.version_number,
            content_hash=row.content_hash,
            security_level=cast("SecurityLevel", row.security_level),
            department_ids=tuple(row.department_ids),
            index_ready=bool(index_ready),
            categories=categories,
        )

    def get_publish_request_by_approval(
        self,
        workspace_id: UUID,
        approval_instance_id: UUID,
    ) -> DocumentPublishRequest | None:
        row = self._session.execute(
            select(document_publish_requests).where(
                document_publish_requests.c.workspace_id == workspace_id,
                document_publish_requests.c.approval_instance_id == approval_instance_id,
            )
        ).one_or_none()
        return None if row is None else self._publish_request(row)

    def get_publish_request_by_idempotency(
        self,
        workspace_id: UUID,
        requester_account_id: UUID,
        idempotency_key: str,
    ) -> DocumentPublishRequest | None:
        """按申请人幂等键读取既有请求，使终态重放不依赖当前版本状态。"""

        row = self._session.execute(
            select(document_publish_requests).where(
                document_publish_requests.c.workspace_id == workspace_id,
                document_publish_requests.c.requester_account_id == requester_account_id,
                document_publish_requests.c.idempotency_key == idempotency_key,
            )
        ).one_or_none()
        return None if row is None else self._publish_request(row)

    def get_publish_request(
        self,
        workspace_id: UUID,
        publish_request_id: UUID,
    ) -> DocumentPublishRequest | None:
        row = self._session.execute(
            select(document_publish_requests).where(
                document_publish_requests.c.workspace_id == workspace_id,
                document_publish_requests.c.publish_request_id == publish_request_id,
            )
        ).one_or_none()
        return None if row is None else self._publish_request(row)

    def list_publish_requests(
        self,
        workspace_id: UUID,
        *,
        approval_instance_ids: tuple[UUID, ...],
        limit: int,
    ) -> tuple[DocumentPublishRequest, ...]:
        if not approval_instance_ids:
            return ()
        statement = select(document_publish_requests).where(
            document_publish_requests.c.workspace_id == workspace_id,
            document_publish_requests.c.approval_instance_id.in_(approval_instance_ids),
        )
        rows = self._session.execute(
            statement.order_by(document_publish_requests.c.created_at.desc()).limit(limit)
        )
        return tuple(self._publish_request(row) for row in rows)

    def _publish_request(self, row: Any) -> DocumentPublishRequest:
        category_ids = tuple(
            self._session.scalars(
                select(document_publish_request_categories.c.category_id)
                .where(
                    document_publish_request_categories.c.workspace_id == row.workspace_id,
                    document_publish_request_categories.c.publish_request_id
                    == row.publish_request_id,
                )
                .order_by(document_publish_request_categories.c.category_id)
            )
        )
        return DocumentPublishRequest(
            publish_request_id=row.publish_request_id,
            workspace_id=row.workspace_id,
            document_id=row.document_id,
            document_version_id=row.document_version_id,
            knowledge_base_id=row.knowledge_base_id,
            requester_account_id=row.requester_account_id,
            approval_instance_id=row.approval_instance_id,
            category_ids=category_ids,
            version_number=row.version_number,
            content_hash=row.content_hash,
            governance_digest=row.governance_digest,
            idempotency_key=row.idempotency_key,
            status=cast("DocumentPublishRequestStatus", row.status),
            failure_reason_code=cast(
                "DocumentPublishFailureReason | None", row.failure_reason_code
            ),
            created_at=row.created_at,
            updated_at=row.updated_at,
            completed_at=row.completed_at,
            version=row.version,
        )

    def resolve_domain_scope(
        self,
        *,
        workspace_id: UUID,
        domain_id: UUID,
        account_id: UUID,
        authorized_workspace: bool,
        authorized_knowledge_base_ids: frozenset[UUID],
    ) -> ResolvedKnowledgeDomainScope | None:
        # 1. 先读取唯一归属空间内的知识域，归档态立即返回显式空集。
        domain = self.get_domain(workspace_id, domain_id)
        if domain is None:
            return None
        declared = tuple(sorted(domain.knowledge_base_ids, key=lambda item: item.int))
        if domain.status != "active":
            return _empty_scope(domain, declared, "domain_inactive")

        # 2. 复核当前活动成员及活动部门归属，声明范围外主体不得进入知识库计算。
        membership = self._session.execute(
            select(workspace_memberships.c.membership_id, workspace_memberships.c.status)
            .select_from(
                workspace_memberships.join(
                    workspaces,
                    workspaces.c.workspace_id == workspace_memberships.c.workspace_id,
                )
            )
            .where(
                workspace_memberships.c.workspace_id == workspace_id,
                workspace_memberships.c.account_id == account_id,
                workspaces.c.workspace_type == "enterprise",
                workspaces.c.status == "active",
            )
        ).one_or_none()
        if membership is None or membership.status != "active":
            return _empty_scope(domain, declared, "member_inactive")
        membership_department_ids = {
            row.department_id
            for row in self._session.execute(
                select(membership_departments.c.department_id)
                .select_from(
                    membership_departments.join(
                        departments,
                        (departments.c.workspace_id == membership_departments.c.workspace_id)
                        & (departments.c.department_id == membership_departments.c.department_id),
                    )
                )
                .where(
                    membership_departments.c.workspace_id == workspace_id,
                    membership_departments.c.membership_id == membership.membership_id,
                    departments.c.status == "active",
                )
            )
        }
        actor_in_scope = membership.membership_id in domain.member_ids or bool(
            membership_department_ids.intersection(domain.department_ids)
        )
        if not actor_in_scope:
            return _empty_scope(domain, declared, "actor_outside_declared_scope")
        if not declared:
            return _empty_scope(domain, declared, "no_declared_knowledge_bases", actor=True)

        # 3. 最终范围固定为活动声明知识库与当前 PDP 授权的交集，空交集绝不扩大。
        active_declared = {
            row.knowledge_base_id
            for row in self._session.execute(
                select(knowledge_bases.c.knowledge_base_id).where(
                    knowledge_bases.c.workspace_id == workspace_id,
                    knowledge_bases.c.knowledge_base_id.in_(declared),
                    knowledge_bases.c.status == "active",
                )
            )
        }
        authorized = (
            active_declared
            if authorized_workspace
            else active_declared.intersection(authorized_knowledge_base_ids)
        )
        authorized_values = tuple(sorted(authorized, key=lambda item: item.int))
        if not authorized_values:
            return ResolvedKnowledgeDomainScope(
                domain_id=domain.domain_id,
                policy_version=domain.rag_policy.policy_version,
                actor_in_declared_scope=True,
                declared_knowledge_base_ids=declared,
                authorized_knowledge_base_ids=(),
                effective_knowledge_base_ids=(),
                empty_reason="pdp_scope_empty",
            )
        return ResolvedKnowledgeDomainScope(
            domain_id=domain.domain_id,
            policy_version=domain.rag_policy.policy_version,
            actor_in_declared_scope=True,
            declared_knowledge_base_ids=declared,
            authorized_knowledge_base_ids=authorized_values,
            effective_knowledge_base_ids=authorized_values,
            empty_reason="none",
        )

    def _domain_from_row(self, row: Any, *, for_update: bool = False) -> TeamKnowledgeDomain:
        # 1. 先读取当前不可变 RAG 策略；写事务按需对策略行加锁以保持版本一致。
        policy_statement = select(team_knowledge_domain_rag_policies).where(
            team_knowledge_domain_rag_policies.c.workspace_id == row.workspace_id,
            team_knowledge_domain_rag_policies.c.domain_id == row.domain_id,
            team_knowledge_domain_rag_policies.c.policy_version == row.current_rag_policy_version,
        )
        if for_update:
            policy_statement = policy_statement.with_for_update()
        policy = self._session.execute(policy_statement).one()
        # 2. 再从三张显式关系表恢复完整声明范围，空集合保持为空而非默认全企业。
        return TeamKnowledgeDomain(
            domain_id=row.domain_id,
            workspace_id=row.workspace_id,
            name=row.name,
            description=row.description,
            member_ids=self._relation_ids(
                team_knowledge_domain_members,
                "membership_id",
                row.workspace_id,
                row.domain_id,
                for_update=for_update,
            ),
            department_ids=self._relation_ids(
                team_knowledge_domain_departments,
                "department_id",
                row.workspace_id,
                row.domain_id,
                for_update=for_update,
            ),
            knowledge_base_ids=self._relation_ids(
                team_knowledge_domain_bases,
                "knowledge_base_id",
                row.workspace_id,
                row.domain_id,
                for_update=for_update,
            ),
            rag_policy=RagPolicy(
                policy.policy_version,
                cast("RagPolicyMode", policy.mode),
                policy.top_k,
                policy.minimum_score,
            ),
            status=cast("GovernanceStatus", row.status),
            created_by_account_id=row.created_by_account_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            version=row.version,
        )

    def _relation_ids(
        self,
        table: Any,
        key: str,
        workspace_id: UUID,
        domain_id: UUID,
        *,
        for_update: bool,
    ) -> tuple[UUID, ...]:
        column = table.c[key]
        statement = (
            select(column)
            .where(table.c.workspace_id == workspace_id, table.c.domain_id == domain_id)
            .order_by(column)
        )
        if for_update:
            statement = statement.with_for_update()
        return tuple(self._session.execute(statement).scalars())

    def _insert_rag_policy(self, domain: TeamKnowledgeDomain) -> None:
        self._session.execute(
            insert(team_knowledge_domain_rag_policies).values(
                workspace_id=domain.workspace_id,
                domain_id=domain.domain_id,
                policy_version=domain.rag_policy.policy_version,
                mode=domain.rag_policy.mode,
                top_k=domain.rag_policy.top_k,
                minimum_score=domain.rag_policy.minimum_score,
                created_by_account_id=domain.created_by_account_id,
                created_at=domain.updated_at,
            )
        )

    def _count(self, column: Any, *conditions: Any) -> int:
        return int(
            self._session.execute(
                select(func.count(distinct(column))).where(*conditions)
            ).scalar_one()
        )


class SqlAlchemyEnterpriseKnowledgeUnitOfWork:
    """让企业知识事实、审计和 Outbox 共享一个短事务。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._state: ContextVar[
            tuple[
                Session,
                SqlAlchemyEnterpriseKnowledgeRepository,
                SqlAlchemyAuditWriter,
                SqlAlchemyOutboxWriter,
            ]
            | None
        ] = ContextVar("enterprise_knowledge_unit_of_work", default=None)

    def __enter__(self) -> SqlAlchemyEnterpriseKnowledgeUnitOfWork:
        if self._state.get() is not None:
            raise RuntimeError("Enterprise Knowledge Unit of Work 不允许重复进入")
        session = self._session_factory()
        self._state.set(
            (
                session,
                SqlAlchemyEnterpriseKnowledgeRepository(session),
                SqlAlchemyAuditWriter(session),
                SqlAlchemyOutboxWriter(session),
            )
        )
        return self

    def _current(
        self,
    ) -> tuple[
        Session,
        SqlAlchemyEnterpriseKnowledgeRepository,
        SqlAlchemyAuditWriter,
        SqlAlchemyOutboxWriter,
    ]:
        state = self._state.get()
        if state is None:
            raise RuntimeError("Enterprise Knowledge Unit of Work 尚未进入事务范围")
        return state

    @property
    def enterprise_knowledge(self) -> SqlAlchemyEnterpriseKnowledgeRepository:
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
        if exc_type is not None and issubclass(exc_type, IntegrityError):
            raise EnterpriseKnowledgeWriteConflictError from exc_value

    def commit(self) -> None:
        session = self._current()[0]
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise EnterpriseKnowledgeWriteConflictError from error


def _category(row: Any) -> EnterpriseCategory:
    return EnterpriseCategory(
        category_id=row.category_id,
        workspace_id=row.workspace_id,
        parent_category_id=row.parent_category_id,
        name=row.name,
        description=row.description,
        visibility=cast("CategoryVisibility", row.visibility),
        department_ids=tuple(row.department_ids),
        status=cast("GovernanceStatus", row.status),
        created_by_account_id=row.created_by_account_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        version=row.version,
        approval_required=row.approval_required,
    )


def _category_values(
    value: EnterpriseCategory, *, include_identity: bool = True
) -> dict[str, object]:
    result: dict[str, object] = {
        "parent_category_id": value.parent_category_id,
        "name": value.name,
        "description": value.description,
        "visibility": value.visibility,
        "department_ids": list(value.department_ids),
        "approval_required": value.approval_required,
        "status": value.status,
        "created_by_account_id": value.created_by_account_id,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
        "version": value.version,
    }
    if include_identity:
        result.update(category_id=value.category_id, workspace_id=value.workspace_id)
    return result


def _domain_values(
    value: TeamKnowledgeDomain, *, include_identity: bool = True
) -> dict[str, object]:
    result: dict[str, object] = {
        "name": value.name,
        "description": value.description,
        "current_rag_policy_version": value.rag_policy.policy_version,
        "status": value.status,
        "created_by_account_id": value.created_by_account_id,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
        "version": value.version,
    }
    if include_identity:
        result.update(domain_id=value.domain_id, workspace_id=value.workspace_id)
    return result


def _empty_scope(
    domain: TeamKnowledgeDomain,
    declared: tuple[UUID, ...],
    reason: str,
    *,
    actor: bool = False,
) -> ResolvedKnowledgeDomainScope:
    return ResolvedKnowledgeDomainScope(
        domain_id=domain.domain_id,
        policy_version=domain.rag_policy.policy_version,
        actor_in_declared_scope=actor,
        declared_knowledge_base_ids=declared,
        authorized_knowledge_base_ids=(),
        effective_knowledge_base_ids=(),
        empty_reason=cast("Any", reason),
    )
