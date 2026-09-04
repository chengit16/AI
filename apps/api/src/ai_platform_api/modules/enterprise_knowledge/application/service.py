"""编排企业分类、团队知识域、显式绑定与授权范围解析事务。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.fields import SecurityLevel
from ai_platform_api.modules.enterprise_knowledge.application.views import (
    DocumentPublishRequestView,
    EnterpriseCategoryResultView,
    EnterpriseKnowledgePortalView,
    ResolvedKnowledgeDomainScopeView,
    TeamKnowledgeDomainView,
    document_publish_request_view,
    enterprise_category_result_view,
    enterprise_knowledge_portal_view,
    resolved_knowledge_domain_scope_view,
    team_knowledge_domain_view,
)
from ai_platform_api.modules.enterprise_knowledge.domain.models import (
    CategoryVisibility,
    EnterpriseCategory,
    EnterpriseCategoryView,
    EnterpriseKnowledgeRepository,
    EnterpriseKnowledgeUnitOfWork,
    EnterpriseKnowledgeWriteConflictError,
    InvalidEnterpriseKnowledgeError,
    RagPolicy,
    RagPolicyMode,
    TeamKnowledgeDomain,
)
from ai_platform_api.modules.workflow.application.approval_runtime import ApprovalInstanceService
from ai_platform_api.modules.workflow.domain.approvals import ApprovalSubject


class EnterpriseKnowledgeDeniedError(PlatformError):
    """当前主体、空间、资源范围或权限不足时统一拒绝。"""

    error_code = "POLICY_DENIED"


class EnterpriseKnowledgeNotFoundError(PlatformError):
    """资源不存在或跨空间时使用相同错误，避免泄露资源事实。"""

    error_code = "RESOURCE_NOT_FOUND"


class EnterpriseKnowledgeConflictError(PlatformError):
    """状态、名称、层级或乐观版本冲突。"""

    error_code = "KNOWLEDGE_CONFLICT"


class EnterpriseCategoryHasActiveChildrenError(PlatformError):
    """分类仍有活动子分类时拒绝归档，调用方应先收敛子树。"""

    error_code = "CATEGORY_HAS_ACTIVE_CHILDREN"


class EnterpriseKnowledgeValidationError(PlatformError):
    """请求不满足企业分类或知识域结构约束。"""

    error_code = "VALIDATION_ERROR"


class EnterpriseDocumentPublishNotReadyError(PlatformError):
    """文档版本、解析索引或审批分类尚不满足发起条件。"""

    error_code = "DOCUMENT_PUBLISH_NOT_READY"


class EnterpriseKnowledgeService:
    """以小接口隐藏多表关系、授权复核、审计和 Outbox 细节。"""

    def __init__(
        self,
        unit_of_work: EnterpriseKnowledgeUnitOfWork,
        approval_instances: ApprovalInstanceService | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._approval_instances = approval_instances

    def get_portal(
        self, context: RequestContext, *, workspace_id: UUID
    ) -> EnterpriseKnowledgePortalView:
        """返回同一事务口径的企业分类、知识域和可绑定事实。"""

        account_id = self._authorized_account(
            context, workspace_id, "enterprise.knowledge.read", require_workspace=True
        )
        with self._unit_of_work as unit_of_work:
            self._require_member(unit_of_work.enterprise_knowledge, workspace_id, account_id)
            snapshot = unit_of_work.enterprise_knowledge.get_portal_snapshot(
                workspace_id,
                generated_at=datetime.now(UTC),
                mask_display_name="display_name" in context.authorized_field_mask,
                mask_document_title="title" in context.authorized_field_mask,
                maximum_security_level=context.authorized_maximum_security_level,
            )
            if snapshot is None:
                raise EnterpriseKnowledgeDeniedError
            return enterprise_knowledge_portal_view(snapshot)

    def create_category(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        name: str,
        description: str | None,
        parent_category_id: UUID | None,
        visibility: CategoryVisibility,
        department_ids: tuple[UUID, ...],
        document_ids: tuple[UUID, ...],
        approval_required: bool = False,
    ) -> EnterpriseCategoryResultView:
        """创建企业分类并原子建立初始文档绑定。"""

        # 1. 先从可信上下文完成空间级授权，并把输入规范化为待持久化领域事实。
        account_id = self._authorized_account(
            context, workspace_id, "enterprise.category.create", require_workspace=True
        )
        now = datetime.now(UTC)
        category = EnterpriseCategory(
            category_id=uuid4(),
            workspace_id=workspace_id,
            parent_category_id=parent_category_id,
            name=name.strip(),
            description=_description(description),
            visibility=visibility,
            department_ids=self._unique(department_ids),
            status="active",
            created_by_account_id=account_id,
            created_at=now,
            updated_at=now,
            version=1,
            approval_required=approval_required,
        )
        normalized_documents = self._unique(document_ids)
        # 2. 在同一事务内复核成员与引用，随后提交分类、绑定、审计和 Outbox。
        try:
            category.assert_valid()
            with self._unit_of_work as unit_of_work:
                repository = unit_of_work.enterprise_knowledge
                self._require_member(repository, workspace_id, account_id, for_update=True)
                self._require_references(
                    repository,
                    workspace_id=workspace_id,
                    category_id=parent_category_id,
                    department_ids=category.department_ids,
                    document_ids=normalized_documents,
                    maximum_security_level=context.authorized_maximum_security_level,
                )
                repository.add_category(category)
                repository.replace_category_documents(
                    workspace_id=workspace_id,
                    category_id=category.category_id,
                    document_ids=normalized_documents,
                    occurred_at=now,
                )
                visible_document_ids = repository.get_category_document_ids(
                    workspace_id,
                    category.category_id,
                    maximum_security_level=context.authorized_maximum_security_level,
                )
                self._record(
                    unit_of_work,
                    context,
                    aggregate_id=category.category_id,
                    aggregate_version=category.version,
                    event_type="enterprise.category.created",
                    action="enterprise.category.create",
                    resource_type="enterprise_category",
                    occurred_at=now,
                    attributes={"document_count": len(normalized_documents)},
                )
                unit_of_work.commit()
        except InvalidEnterpriseKnowledgeError as error:
            raise EnterpriseKnowledgeValidationError from error
        except EnterpriseKnowledgeWriteConflictError as error:
            raise EnterpriseKnowledgeConflictError from error
        return enterprise_category_result_view(
            EnterpriseCategoryView(category=category, document_ids=visible_document_ids)
        )

    def update_category(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        category_id: UUID,
        expected_version: int,
        name: str,
        description: str | None,
        parent_category_id: UUID | None,
        visibility: CategoryVisibility,
        department_ids: tuple[UUID, ...],
        approval_required: bool | None = None,
    ) -> EnterpriseCategoryResultView:
        """按乐观版本更新分类元数据和独立可见策略。"""

        # 1. 先执行资源级授权，再锁定当前分类以校验生命周期和乐观版本。
        account_id = self._authorized_account(
            context,
            workspace_id,
            "enterprise.category.update",
            resource_id=category_id,
        )
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                repository = unit_of_work.enterprise_knowledge
                self._require_member(repository, workspace_id, account_id, for_update=True)
                current = repository.get_category(workspace_id, category_id, for_update=True)
                if current is None:
                    raise EnterpriseKnowledgeNotFoundError
                if current.status != "active" or current.version != expected_version:
                    raise EnterpriseKnowledgeConflictError
                # 2. 候选事实通过层级、范围和环路校验后，与审计及 Outbox 原子提交。
                candidate = replace(
                    current,
                    parent_category_id=parent_category_id,
                    name=name.strip(),
                    description=_description(description),
                    visibility=visibility,
                    department_ids=self._unique(department_ids),
                    approval_required=(
                        current.approval_required
                        if approval_required is None
                        else approval_required
                    ),
                    updated_at=now,
                    version=current.version + 1,
                )
                candidate.assert_valid()
                self._require_references(
                    repository,
                    workspace_id=workspace_id,
                    category_id=parent_category_id,
                    department_ids=candidate.department_ids,
                )
                if parent_category_id is not None and repository.category_parent_would_cycle(
                    workspace_id=workspace_id,
                    category_id=category_id,
                    parent_category_id=parent_category_id,
                ):
                    raise EnterpriseKnowledgeConflictError
                # 3. 持久化后只读取当前密级可见绑定，审计与 Outbox 随事务提交。
                repository.update_category(candidate, expected_version=expected_version)
                document_ids = repository.get_category_document_ids(
                    workspace_id,
                    category_id,
                    maximum_security_level=context.authorized_maximum_security_level,
                )
                self._record(
                    unit_of_work,
                    context,
                    aggregate_id=category_id,
                    aggregate_version=candidate.version,
                    event_type="enterprise.category.updated",
                    action="enterprise.category.update",
                    resource_type="enterprise_category",
                    occurred_at=now,
                    attributes={"visibility": visibility},
                )
                unit_of_work.commit()
        except InvalidEnterpriseKnowledgeError as error:
            raise EnterpriseKnowledgeValidationError from error
        except EnterpriseKnowledgeWriteConflictError as error:
            raise EnterpriseKnowledgeConflictError from error
        return enterprise_category_result_view(
            EnterpriseCategoryView(category=candidate, document_ids=document_ids)
        )

    def archive_category(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        category_id: UUID,
        expected_version: int,
    ) -> EnterpriseCategoryResultView:
        """归档无活动子分类的分类，并保留历史文档关系供审计导出。"""

        # 1. 先执行资源级授权并锁定分类，避免并发更新绕过版本与子分类约束。
        account_id = self._authorized_account(
            context,
            workspace_id,
            "enterprise.category.archive",
            resource_id=category_id,
        )
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                repository = unit_of_work.enterprise_knowledge
                self._require_member(repository, workspace_id, account_id, for_update=True)
                current = repository.get_category(workspace_id, category_id, for_update=True)
                if current is None:
                    raise EnterpriseKnowledgeNotFoundError
                if current.status != "active" or current.version != expected_version:
                    raise EnterpriseKnowledgeConflictError
                if repository.category_has_active_children(workspace_id, category_id):
                    raise EnterpriseCategoryHasActiveChildrenError
                # 2. 只变更分类生命周期，历史文档关系、审计事实和事件证据继续保留。
                archived = replace(
                    current, status="archived", updated_at=now, version=current.version + 1
                )
                repository.update_category(archived, expected_version=expected_version)
                document_ids = repository.get_category_document_ids(
                    workspace_id,
                    category_id,
                    maximum_security_level=context.authorized_maximum_security_level,
                )
                self._record(
                    unit_of_work,
                    context,
                    aggregate_id=category_id,
                    aggregate_version=archived.version,
                    event_type="enterprise.category.archived",
                    action="enterprise.category.archive",
                    resource_type="enterprise_category",
                    occurred_at=now,
                    attributes={},
                )
                unit_of_work.commit()
        except EnterpriseKnowledgeWriteConflictError as error:
            raise EnterpriseKnowledgeConflictError from error
        return enterprise_category_result_view(
            EnterpriseCategoryView(category=archived, document_ids=document_ids)
        )

    def replace_category_documents(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        category_id: UUID,
        expected_version: int,
        document_ids: tuple[UUID, ...],
    ) -> EnterpriseCategoryResultView:
        """整体替换分类与文档的显式关系，不复制或修改文档权限。"""

        # 1. 先执行分类资源级授权并规范化完整文档集合，重复 ID 直接拒绝。
        account_id = self._authorized_account(
            context,
            workspace_id,
            "enterprise.category.bind",
            resource_id=category_id,
        )
        normalized_documents = self._unique(document_ids)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                repository = unit_of_work.enterprise_knowledge
                self._require_member(repository, workspace_id, account_id, for_update=True)
                current = repository.get_category(workspace_id, category_id, for_update=True)
                if current is None:
                    raise EnterpriseKnowledgeNotFoundError
                if current.status != "active" or current.version != expected_version:
                    raise EnterpriseKnowledgeConflictError
                # 2. 仅允许绑定当前密级可见文档，并在单一事务内整体替换关系及版本。
                self._require_references(
                    repository,
                    workspace_id=workspace_id,
                    document_ids=normalized_documents,
                    maximum_security_level=context.authorized_maximum_security_level,
                )
                updated = replace(current, updated_at=now, version=current.version + 1)
                repository.replace_category_documents(
                    workspace_id=workspace_id,
                    category_id=category_id,
                    document_ids=normalized_documents,
                    occurred_at=now,
                )
                repository.update_category(updated, expected_version=expected_version)
                visible_document_ids = repository.get_category_document_ids(
                    workspace_id,
                    category_id,
                    maximum_security_level=context.authorized_maximum_security_level,
                )
                self._record(
                    unit_of_work,
                    context,
                    aggregate_id=category_id,
                    aggregate_version=updated.version,
                    event_type="enterprise.category.documents.replaced",
                    action="enterprise.category.bind",
                    resource_type="enterprise_category",
                    occurred_at=now,
                    attributes={"document_count": len(normalized_documents)},
                )
                unit_of_work.commit()
        except EnterpriseKnowledgeWriteConflictError as error:
            raise EnterpriseKnowledgeConflictError from error
        return enterprise_category_result_view(
            EnterpriseCategoryView(category=updated, document_ids=visible_document_ids)
        )

    def create_domain(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        name: str,
        description: str | None,
        member_ids: tuple[UUID, ...],
        department_ids: tuple[UUID, ...],
        knowledge_base_ids: tuple[UUID, ...],
        rag_mode: RagPolicyMode,
        top_k: int,
        minimum_score: float,
    ) -> TeamKnowledgeDomainView:
        """创建知识域、初始范围和 RAG 策略版本，空集合保持显式为空。"""

        # 1. 从可信上下文完成空间级授权，并构造显式范围与首个 RAG 策略版本。
        account_id = self._authorized_account(
            context, workspace_id, "enterprise.domain.create", require_workspace=True
        )
        now = datetime.now(UTC)
        domain = TeamKnowledgeDomain(
            domain_id=uuid4(),
            workspace_id=workspace_id,
            name=name.strip(),
            description=_description(description),
            member_ids=self._unique(member_ids),
            department_ids=self._unique(department_ids),
            knowledge_base_ids=self._unique(knowledge_base_ids),
            rag_policy=RagPolicy(1, rag_mode, top_k, minimum_score),
            status="active",
            created_by_account_id=account_id,
            created_at=now,
            updated_at=now,
            version=1,
        )
        # 2. 在同一事务内复核成员、部门和知识库引用，再提交领域事实与治理证据。
        try:
            domain.assert_valid()
            with self._unit_of_work as unit_of_work:
                repository = unit_of_work.enterprise_knowledge
                self._require_member(repository, workspace_id, account_id, for_update=True)
                self._require_references(
                    repository,
                    workspace_id=workspace_id,
                    member_ids=domain.member_ids,
                    department_ids=domain.department_ids,
                    knowledge_base_ids=domain.knowledge_base_ids,
                )
                repository.add_domain(domain)
                self._record(
                    unit_of_work,
                    context,
                    aggregate_id=domain.domain_id,
                    aggregate_version=domain.version,
                    event_type="enterprise.knowledge_domain.created",
                    action="enterprise.domain.create",
                    resource_type="team_knowledge_domain",
                    occurred_at=now,
                    attributes={
                        "member_count": len(domain.member_ids),
                        "department_count": len(domain.department_ids),
                        "knowledge_base_count": len(domain.knowledge_base_ids),
                    },
                )
                unit_of_work.commit()
        except InvalidEnterpriseKnowledgeError as error:
            raise EnterpriseKnowledgeValidationError from error
        except EnterpriseKnowledgeWriteConflictError as error:
            raise EnterpriseKnowledgeConflictError from error
        return team_knowledge_domain_view(domain)

    def update_domain(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        domain_id: UUID,
        expected_version: int,
        name: str,
        description: str | None,
        rag_mode: RagPolicyMode,
        top_k: int,
        minimum_score: float,
    ) -> TeamKnowledgeDomainView:
        """更新知识域元数据；RAG 参数变化时只追加新策略版本。"""

        # 1. 先执行资源级授权并锁定当前知识域，拒绝归档态或过期版本写入。
        account_id = self._authorized_account(
            context, workspace_id, "enterprise.domain.update", resource_id=domain_id
        )
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                repository = unit_of_work.enterprise_knowledge
                self._require_member(repository, workspace_id, account_id, for_update=True)
                current = repository.get_domain(workspace_id, domain_id, for_update=True)
                if current is None:
                    raise EnterpriseKnowledgeNotFoundError
                if current.status != "active" or current.version != expected_version:
                    raise EnterpriseKnowledgeConflictError
                # 2. 仅在参数实际变化时追加策略版本，并与知识域、审计和事件原子提交。
                policy_changed = (
                    current.rag_policy.mode != rag_mode
                    or current.rag_policy.top_k != top_k
                    or current.rag_policy.minimum_score != minimum_score
                )
                policy = RagPolicy(
                    current.rag_policy.policy_version + int(policy_changed),
                    rag_mode,
                    top_k,
                    minimum_score,
                )
                updated = replace(
                    current,
                    name=name.strip(),
                    description=_description(description),
                    rag_policy=policy,
                    updated_at=now,
                    version=current.version + 1,
                )
                updated.assert_valid()
                repository.update_domain(
                    updated,
                    expected_version=expected_version,
                    add_rag_policy_version=policy_changed,
                )
                self._record(
                    unit_of_work,
                    context,
                    aggregate_id=domain_id,
                    aggregate_version=updated.version,
                    event_type="enterprise.knowledge_domain.updated",
                    action="enterprise.domain.update",
                    resource_type="team_knowledge_domain",
                    occurred_at=now,
                    attributes={"rag_policy_version": policy.policy_version},
                )
                unit_of_work.commit()
        except InvalidEnterpriseKnowledgeError as error:
            raise EnterpriseKnowledgeValidationError from error
        except EnterpriseKnowledgeWriteConflictError as error:
            raise EnterpriseKnowledgeConflictError from error
        return team_knowledge_domain_view(updated)

    def archive_domain(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        domain_id: UUID,
        expected_version: int,
    ) -> TeamKnowledgeDomainView:
        """归档知识域；历史策略和显式范围保留，运行解析立即返回空集。"""

        # 1. 先执行资源级授权并锁定当前知识域，防止并发归档覆盖新版本。
        account_id = self._authorized_account(
            context, workspace_id, "enterprise.domain.archive", resource_id=domain_id
        )
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                repository = unit_of_work.enterprise_knowledge
                self._require_member(repository, workspace_id, account_id, for_update=True)
                current = repository.get_domain(workspace_id, domain_id, for_update=True)
                if current is None:
                    raise EnterpriseKnowledgeNotFoundError
                if current.status != "active" or current.version != expected_version:
                    raise EnterpriseKnowledgeConflictError
                # 2. 只冻结生命周期并保留策略与范围历史，使后续范围解析稳定失败关闭。
                archived = replace(
                    current, status="archived", updated_at=now, version=current.version + 1
                )
                repository.update_domain(
                    archived, expected_version=expected_version, add_rag_policy_version=False
                )
                self._record(
                    unit_of_work,
                    context,
                    aggregate_id=domain_id,
                    aggregate_version=archived.version,
                    event_type="enterprise.knowledge_domain.archived",
                    action="enterprise.domain.archive",
                    resource_type="team_knowledge_domain",
                    occurred_at=now,
                    attributes={},
                )
                unit_of_work.commit()
        except EnterpriseKnowledgeWriteConflictError as error:
            raise EnterpriseKnowledgeConflictError from error
        return team_knowledge_domain_view(archived)

    def replace_domain_scope(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        domain_id: UUID,
        expected_version: int,
        member_ids: tuple[UUID, ...],
        department_ids: tuple[UUID, ...],
        knowledge_base_ids: tuple[UUID, ...],
    ) -> TeamKnowledgeDomainView:
        """原子替换成员、部门和知识库范围，禁止出现中间扩大授权态。"""

        # 1. 先执行资源级授权并规范化三个完整集合，重复引用直接拒绝。
        account_id = self._authorized_account(
            context, workspace_id, "enterprise.domain.scope", resource_id=domain_id
        )
        normalized_members = self._unique(member_ids)
        normalized_departments = self._unique(department_ids)
        normalized_bases = self._unique(knowledge_base_ids)
        now = datetime.now(UTC)
        try:
            with self._unit_of_work as unit_of_work:
                repository = unit_of_work.enterprise_knowledge
                self._require_member(repository, workspace_id, account_id, for_update=True)
                current = repository.get_domain(workspace_id, domain_id, for_update=True)
                if current is None:
                    raise EnterpriseKnowledgeNotFoundError
                if current.status != "active" or current.version != expected_version:
                    raise EnterpriseKnowledgeConflictError
                # 2. 复核全部引用后一次替换三类关系，并与版本、审计和 Outbox 原子提交。
                self._require_references(
                    repository,
                    workspace_id=workspace_id,
                    member_ids=normalized_members,
                    department_ids=normalized_departments,
                    knowledge_base_ids=normalized_bases,
                )
                updated = replace(
                    current,
                    member_ids=normalized_members,
                    department_ids=normalized_departments,
                    knowledge_base_ids=normalized_bases,
                    updated_at=now,
                    version=current.version + 1,
                )
                updated.assert_valid()
                repository.replace_domain_scope(
                    workspace_id=workspace_id,
                    domain_id=domain_id,
                    member_ids=normalized_members,
                    department_ids=normalized_departments,
                    knowledge_base_ids=normalized_bases,
                    occurred_at=now,
                )
                repository.update_domain(
                    updated, expected_version=expected_version, add_rag_policy_version=False
                )
                self._record(
                    unit_of_work,
                    context,
                    aggregate_id=domain_id,
                    aggregate_version=updated.version,
                    event_type="enterprise.knowledge_domain.scope_replaced",
                    action="enterprise.domain.scope",
                    resource_type="team_knowledge_domain",
                    occurred_at=now,
                    attributes={
                        "member_count": len(normalized_members),
                        "department_count": len(normalized_departments),
                        "knowledge_base_count": len(normalized_bases),
                    },
                )
                unit_of_work.commit()
        except InvalidEnterpriseKnowledgeError as error:
            raise EnterpriseKnowledgeValidationError from error
        except EnterpriseKnowledgeWriteConflictError as error:
            raise EnterpriseKnowledgeConflictError from error
        return team_knowledge_domain_view(updated)

    def resolve_domain_scope(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        domain_id: UUID,
    ) -> ResolvedKnowledgeDomainScopeView:
        """计算声明范围与当前 PDP 的交集；空交集永远不回退到全企业。"""

        account_id = self._authorized_account(
            context, workspace_id, "enterprise.domain.resolve", resource_id=domain_id
        )
        with self._unit_of_work as unit_of_work:
            result = unit_of_work.enterprise_knowledge.resolve_domain_scope(
                workspace_id=workspace_id,
                domain_id=domain_id,
                account_id=account_id,
                authorized_workspace=context.authorized_workspace,
                authorized_department_ids=context.authorized_department_ids,
                authorized_account_ids=context.authorized_account_ids,
                authorized_document_ids=context.authorized_resource_ids,
                maximum_security_level=context.authorized_maximum_security_level,
            )
            if result is None:
                raise EnterpriseKnowledgeNotFoundError
            return resolved_knowledge_domain_scope_view(result)

    def request_document_publish(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
        idempotency_key: str,
    ) -> DocumentPublishRequestView:
        """冻结就绪版本与治理摘要，并复用通用审批运行时创建发布请求。"""

        # 1. 验证当前企业成员、资源授权和审批服务装配状态。
        account_id = self._authorized_account(
            context,
            workspace_id,
            "enterprise.document.publish.request",
            resource_id=document_id,
        )
        approvals = self._require_approval_instances()
        # 2. 在事务快照中优先回放幂等键，再冻结候选版本与治理分类。
        with self._unit_of_work as unit_of_work:
            repository = unit_of_work.enterprise_knowledge
            self._require_member(repository, workspace_id, account_id)
            existing = repository.get_publish_request_by_idempotency(
                workspace_id, account_id, idempotency_key
            )
            if existing is not None:
                if (
                    existing.document_id != document_id
                    or existing.document_version_id != document_version_id
                ):
                    raise EnterpriseKnowledgeConflictError
                approval = approvals.get_participant_visible(
                    context, approval_instance_id=existing.approval_instance_id
                )
                return document_publish_request_view(existing, approval)
            candidate = repository.get_document_publish_candidate(
                workspace_id, document_id, document_version_id
            )
        if candidate is None or not candidate.index_ready or not candidate.approval_required:
            raise EnterpriseDocumentPublishNotReadyError
        subject = ApprovalSubject(
            workspace_id=workspace_id,
            requester_account_id=account_id,
            resource_type="document.publish",
            operation="publish",
            resource_id=document_id,
            department_ids=candidate.department_ids,
            security_level=candidate.security_level,
            risk_level="high",
            fields={
                "category_ids": [str(item.category_id) for item in candidate.categories],
                "content_hash": candidate.content_hash,
                "document_version_id": str(candidate.document_version_id),
                "governance_digest": candidate.governance_digest,
                "knowledge_base_id": str(candidate.knowledge_base_id),
                "version_number": candidate.version_number,
            },
        )
        result = approvals.start(context, subject=subject, idempotency_key=idempotency_key)
        with self._unit_of_work as unit_of_work:
            request = unit_of_work.enterprise_knowledge.get_publish_request_by_approval(
                workspace_id, result.state.instance.approval_instance_id
            )
        if request is None:
            raise EnterpriseKnowledgeConflictError
        return document_publish_request_view(request, result.state)

    def list_document_publish_requests(
        self, context: RequestContext, *, workspace_id: UUID, limit: int
    ) -> tuple[DocumentPublishRequestView, ...]:
        """列出申请人或审批人可见的发布台账，普通菜单权限不能扩大审批可见性。"""

        self._authorized_account(context, workspace_id, "enterprise.document.publish.read")
        if not 1 <= limit <= 200:
            raise EnterpriseKnowledgeValidationError
        approvals = self._require_approval_instances()
        visible_states = tuple(
            state
            for state in approvals.list(context, limit=200)
            if state.instance.resource_type == "document.publish"
        )
        states_by_id = {state.instance.approval_instance_id: state for state in visible_states}
        with self._unit_of_work as unit_of_work:
            requests = unit_of_work.enterprise_knowledge.list_publish_requests(
                workspace_id,
                approval_instance_ids=tuple(states_by_id),
                limit=limit,
            )
        return tuple(
            document_publish_request_view(request, states_by_id[request.approval_instance_id])
            for request in requests
            if request.approval_instance_id in states_by_id
        )

    def get_document_publish_request(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        publish_request_id: UUID,
    ) -> DocumentPublishRequestView:
        """读取参与者可见的发布请求详情与冻结审批链。"""

        self._authorized_account(context, workspace_id, "enterprise.document.publish.read")
        with self._unit_of_work as unit_of_work:
            request = unit_of_work.enterprise_knowledge.get_publish_request(
                workspace_id, publish_request_id
            )
        if request is None:
            raise EnterpriseKnowledgeNotFoundError
        approval = self._require_approval_instances().get_participant_visible(
            context, approval_instance_id=request.approval_instance_id
        )
        return document_publish_request_view(request, approval)

    def _require_approval_instances(self) -> ApprovalInstanceService:
        if self._approval_instances is None:
            raise RuntimeError("企业文档发布审批服务尚未完成装配")
        return self._approval_instances

    @staticmethod
    def _authorized_account(
        context: RequestContext,
        workspace_id: UUID,
        permission_code: str,
        *,
        resource_id: UUID | None = None,
        require_workspace: bool = False,
    ) -> UUID:
        if (
            context.user_id is None
            or context.authentication_method != "browser_session"
            or context.workspace_id != workspace_id
            or context.authorized_permission_code != permission_code
        ):
            raise EnterpriseKnowledgeDeniedError
        if require_workspace and not context.authorized_workspace:
            raise EnterpriseKnowledgeDeniedError
        if resource_id is not None and (
            not context.authorized_workspace and resource_id not in context.authorized_resource_ids
        ):
            raise EnterpriseKnowledgeDeniedError
        return context.user_id

    @staticmethod
    def _require_member(
        repository: EnterpriseKnowledgeRepository,
        workspace_id: UUID,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> None:
        if not repository.require_active_enterprise_member(
            workspace_id, account_id, for_update=for_update
        ):
            raise EnterpriseKnowledgeDeniedError

    @staticmethod
    def _unique(values: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(values) != len(set(values)):
            raise EnterpriseKnowledgeValidationError
        return tuple(sorted(values, key=lambda item: item.int))

    @staticmethod
    def _require_references(
        repository: EnterpriseKnowledgeRepository,
        *,
        workspace_id: UUID,
        category_id: UUID | None = None,
        department_ids: tuple[UUID, ...] = (),
        document_ids: tuple[UUID, ...] = (),
        member_ids: tuple[UUID, ...] = (),
        knowledge_base_ids: tuple[UUID, ...] = (),
        maximum_security_level: SecurityLevel = "RESTRICTED",
    ) -> None:
        if not repository.references_exist(
            workspace_id=workspace_id,
            category_id=category_id,
            department_ids=department_ids,
            document_ids=document_ids,
            member_ids=member_ids,
            knowledge_base_ids=knowledge_base_ids,
            maximum_security_level=maximum_security_level,
        ):
            raise EnterpriseKnowledgeValidationError

    @staticmethod
    def _record(
        unit_of_work: EnterpriseKnowledgeUnitOfWork,
        context: RequestContext,
        *,
        aggregate_id: UUID,
        aggregate_version: int,
        event_type: str,
        action: str,
        resource_type: str,
        occurred_at: datetime,
        attributes: dict[str, object],
    ) -> None:
        """将治理事实、授权证据与追踪上下文写入同一事务。"""

        unit_of_work.outbox.add(
            IntegrationEvent(
                event_id=uuid4(),
                event_type=event_type,
                workspace_id=context.workspace_id,
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                occurred_at=occurred_at,
                trace_id=context.trace.trace_id,
                traceparent=context.trace.traceparent,
                actor_id=context.actor_id,
                user_id=context.user_id,
                request_id=context.request_id,
                payload={"resource_id": str(aggregate_id)},
            )
        )
        unit_of_work.audit.add(
            AuditRecord(
                audit_id=uuid4(),
                workspace_id=context.workspace_id,
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
            )
        )


def _description(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
