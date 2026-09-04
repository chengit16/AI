"""定义企业分类、团队知识域、门户快照与安全范围投影。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter

from ai_platform_api.modules.authorization.domain.fields import SecurityLevel

CategoryVisibility = Literal["public", "departments", "private"]
GovernanceStatus = Literal["active", "archived"]
RagPolicyMode = Literal["balanced", "precision", "recall"]
DocumentPublishRequestStatus = Literal[
    "pending",
    "published",
    "rejected",
    "withdrawn",
    "expired",
    "publish_failed",
]
DocumentPublishFailureReason = Literal[
    "requester_inactive",
    "permission_revoked",
    "policy_unavailable",
    "document_inactive",
    "version_changed",
    "governance_changed",
    "index_not_ready",
]

_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class InvalidEnterpriseKnowledgeError(Exception):
    """企业知识治理事实不满足名称、范围或状态约束。"""


class EnterpriseKnowledgeWriteConflictError(Exception):
    """并发版本或数据库约束变化时拒绝覆盖企业知识事实。"""


@dataclass(frozen=True)
class EnterpriseCategory:
    """表达企业治理分类及其独立可见策略，不改变文档本身权限。"""

    category_id: UUID
    workspace_id: UUID
    parent_category_id: UUID | None
    name: str
    description: str | None
    visibility: CategoryVisibility
    department_ids: tuple[UUID, ...]
    status: GovernanceStatus
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    version: int
    approval_required: bool = False

    def assert_valid(self) -> None:
        normalized_name = self.name.strip()
        if not 1 <= len(normalized_name) <= 120 or self.name != normalized_name:
            raise InvalidEnterpriseKnowledgeError
        if self.description is not None and len(self.description) > 1000:
            raise InvalidEnterpriseKnowledgeError
        if self.parent_category_id == self.category_id or self.version < 1:
            raise InvalidEnterpriseKnowledgeError
        if self.visibility == "departments" and not self.department_ids:
            raise InvalidEnterpriseKnowledgeError
        if self.visibility != "departments" and self.department_ids:
            raise InvalidEnterpriseKnowledgeError
        if len(set(self.department_ids)) != len(self.department_ids):
            raise InvalidEnterpriseKnowledgeError


@dataclass(frozen=True)
class DocumentPublishRequest:
    """冻结一个不可变文档版本的企业发布申请和最终发布结果。"""

    publish_request_id: UUID
    workspace_id: UUID
    document_id: UUID
    document_version_id: UUID
    knowledge_base_id: UUID
    requester_account_id: UUID
    approval_instance_id: UUID
    category_ids: tuple[UUID, ...]
    version_number: int
    content_hash: str
    governance_digest: str
    idempotency_key: str
    status: DocumentPublishRequestStatus
    failure_reason_code: DocumentPublishFailureReason | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    version: int

    def assert_valid(self) -> None:
        """验证摘要、状态和终态时间，拒绝无法追溯的半完成事实。"""

        valid_hashes = (self.content_hash, self.governance_digest)
        if (
            self.version_number < 1
            or self.version < 1
            or not self.category_ids
            or len(set(self.category_ids)) != len(self.category_ids)
            or not _IDEMPOTENCY_PATTERN.fullmatch(self.idempotency_key)
            or any(
                len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
                for value in valid_hashes
            )
        ):
            raise InvalidEnterpriseKnowledgeError
        terminal = self.status != "pending"
        if terminal != (self.completed_at is not None):
            raise InvalidEnterpriseKnowledgeError
        if (self.status == "publish_failed") != (self.failure_reason_code is not None):
            raise InvalidEnterpriseKnowledgeError

    def finish(
        self,
        *,
        status: Literal["published", "rejected", "withdrawn", "expired", "publish_failed"],
        occurred_at: datetime,
        failure_reason_code: DocumentPublishFailureReason | None = None,
    ) -> DocumentPublishRequest:
        """只允许待审批申请进入一个可解释终态，审批事实不会被复活。"""

        if self.status != "pending":
            raise InvalidEnterpriseKnowledgeError
        candidate = replace(
            self,
            status=status,
            failure_reason_code=failure_reason_code,
            updated_at=occurred_at,
            completed_at=occurred_at,
            version=self.version + 1,
        )
        candidate.assert_valid()
        return candidate


@dataclass(frozen=True)
class DocumentPublishCategorySnapshot:
    """冻结发布申请命中的活动分类版本，供批准时检测治理漂移。"""

    category_id: UUID
    name: str
    category_version: int
    approval_required: bool


@dataclass(frozen=True)
class DocumentPublishCandidate:
    """提供发起审批所需的低敏版本、索引和分类治理事实。"""

    workspace_id: UUID
    document_id: UUID
    document_version_id: UUID
    knowledge_base_id: UUID
    title: str
    version_number: int
    content_hash: str
    security_level: SecurityLevel
    department_ids: tuple[UUID, ...]
    index_ready: bool
    categories: tuple[DocumentPublishCategorySnapshot, ...]

    @property
    def approval_required(self) -> bool:
        """任一活动分类要求审批时，企业发布不能走直接发布入口。"""

        return any(item.approval_required for item in self.categories)

    @property
    def governance_digest(self) -> str:
        """对排序后的分类版本和审批开关生成稳定摘要，不包含文档正文。"""

        document = [
            {
                "approval_required": item.approval_required,
                "category_id": str(item.category_id),
                "category_version": item.category_version,
            }
            for item in sorted(self.categories, key=lambda value: value.category_id.int)
        ]
        encoded = json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RagPolicy:
    """记录团队知识域不可变的 RAG 策略版本。"""

    policy_version: int
    mode: RagPolicyMode
    top_k: int
    minimum_score: float

    def assert_valid(self) -> None:
        if (
            self.policy_version < 1
            or not 1 <= self.top_k <= 50
            or not 0.0 <= self.minimum_score <= 1.0
        ):
            raise InvalidEnterpriseKnowledgeError


@dataclass(frozen=True)
class TeamKnowledgeDomain:
    """表达团队范围、知识库集合和当前 RAG 策略版本。"""

    domain_id: UUID
    workspace_id: UUID
    name: str
    description: str | None
    member_ids: tuple[UUID, ...]
    department_ids: tuple[UUID, ...]
    knowledge_base_ids: tuple[UUID, ...]
    rag_policy: RagPolicy
    status: GovernanceStatus
    created_by_account_id: UUID
    created_at: datetime
    updated_at: datetime
    version: int

    def assert_valid(self) -> None:
        normalized_name = self.name.strip()
        if not 1 <= len(normalized_name) <= 120 or self.name != normalized_name:
            raise InvalidEnterpriseKnowledgeError
        if self.description is not None and len(self.description) > 1000:
            raise InvalidEnterpriseKnowledgeError
        if self.version < 1:
            raise InvalidEnterpriseKnowledgeError
        # 空团队范围和空知识库范围都表示显式空集，运行时不得扩成全企业。
        for values in (self.member_ids, self.department_ids, self.knowledge_base_ids):
            if len(set(values)) != len(values):
                raise InvalidEnterpriseKnowledgeError
        self.rag_policy.assert_valid()


@dataclass(frozen=True)
class EnterpriseDocumentOption:
    """提供分类绑定所需的低敏文档选项。"""

    document_id: UUID
    knowledge_base_id: UUID
    title: str
    security_level: SecurityLevel


@dataclass(frozen=True)
class EnterpriseKnowledgeBaseOption:
    """提供知识域绑定所需的活动知识库选项。"""

    knowledge_base_id: UUID
    name: str
    default_security_level: SecurityLevel


@dataclass(frozen=True)
class EnterpriseDepartmentOption:
    """提供分类和知识域范围所需的活动部门选项。"""

    department_id: UUID
    name: str


@dataclass(frozen=True)
class EnterpriseMemberOption:
    """提供知识域直接成员范围所需的活动成员选项。"""

    membership_id: UUID
    account_id: UUID
    display_name: str | None


@dataclass(frozen=True)
class EnterpriseCategoryView:
    """分类及其显式文档绑定的统一读模型。"""

    category: EnterpriseCategory
    document_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class EnterpriseKnowledgeStatistics:
    """记录企业知识门户顶部治理计数。"""

    active_categories: int
    active_domains: int
    classified_documents: int
    governed_knowledge_bases: int


@dataclass(frozen=True)
class EnterpriseKnowledgePortalSnapshot:
    """在单一事务口径聚合企业分类、团队知识域与可绑定事实。"""

    workspace_id: UUID
    workspace_name: str
    statistics: EnterpriseKnowledgeStatistics
    categories: tuple[EnterpriseCategoryView, ...]
    domains: tuple[TeamKnowledgeDomain, ...]
    documents: tuple[EnterpriseDocumentOption, ...]
    knowledge_bases: tuple[EnterpriseKnowledgeBaseOption, ...]
    departments: tuple[EnterpriseDepartmentOption, ...]
    members: tuple[EnterpriseMemberOption, ...]
    generated_at: datetime


@dataclass(frozen=True)
class ResolvedKnowledgeDomainScope:
    """解释知识域声明范围与当前 PDP 授权的交集结果。"""

    domain_id: UUID
    policy_version: int
    actor_in_declared_scope: bool
    declared_knowledge_base_ids: tuple[UUID, ...]
    authorized_knowledge_base_ids: tuple[UUID, ...]
    effective_knowledge_base_ids: tuple[UUID, ...]
    empty_reason: Literal[
        "none",
        "domain_inactive",
        "member_inactive",
        "actor_outside_declared_scope",
        "no_declared_knowledge_bases",
        "pdp_scope_empty",
    ]


class EnterpriseKnowledgeRepository(Protocol):
    """在单一企业空间和事务内维护治理事实与显式关系。"""

    def require_active_enterprise_member(
        self, workspace_id: UUID, account_id: UUID, *, for_update: bool = False
    ) -> bool: ...

    def get_portal_snapshot(
        self,
        workspace_id: UUID,
        *,
        generated_at: datetime,
        mask_display_name: bool,
        mask_document_title: bool,
        maximum_security_level: SecurityLevel,
    ) -> EnterpriseKnowledgePortalSnapshot | None: ...

    def get_category(
        self, workspace_id: UUID, category_id: UUID, *, for_update: bool = False
    ) -> EnterpriseCategory | None: ...

    def get_category_document_ids(
        self, workspace_id: UUID, category_id: UUID, *, maximum_security_level: SecurityLevel
    ) -> tuple[UUID, ...]: ...

    def add_category(self, category: EnterpriseCategory) -> None: ...

    def update_category(self, category: EnterpriseCategory, *, expected_version: int) -> None: ...

    def replace_category_documents(
        self,
        *,
        workspace_id: UUID,
        category_id: UUID,
        document_ids: tuple[UUID, ...],
        occurred_at: datetime,
    ) -> None: ...

    def get_domain(
        self, workspace_id: UUID, domain_id: UUID, *, for_update: bool = False
    ) -> TeamKnowledgeDomain | None: ...

    def add_domain(self, domain: TeamKnowledgeDomain) -> None: ...

    def update_domain(
        self,
        domain: TeamKnowledgeDomain,
        *,
        expected_version: int,
        add_rag_policy_version: bool,
    ) -> None: ...

    def replace_domain_scope(
        self,
        *,
        workspace_id: UUID,
        domain_id: UUID,
        member_ids: tuple[UUID, ...],
        department_ids: tuple[UUID, ...],
        knowledge_base_ids: tuple[UUID, ...],
        occurred_at: datetime,
    ) -> None: ...

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
    ) -> bool: ...

    def category_parent_would_cycle(
        self,
        *,
        workspace_id: UUID,
        category_id: UUID,
        parent_category_id: UUID,
    ) -> bool: ...

    def category_has_active_children(self, workspace_id: UUID, category_id: UUID) -> bool: ...

    def get_document_publish_candidate(
        self,
        workspace_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
        *,
        for_update: bool = False,
    ) -> DocumentPublishCandidate | None: ...

    def get_publish_request_by_approval(
        self,
        workspace_id: UUID,
        approval_instance_id: UUID,
    ) -> DocumentPublishRequest | None: ...

    def get_publish_request_by_idempotency(
        self,
        workspace_id: UUID,
        requester_account_id: UUID,
        idempotency_key: str,
    ) -> DocumentPublishRequest | None: ...

    def get_publish_request(
        self,
        workspace_id: UUID,
        publish_request_id: UUID,
    ) -> DocumentPublishRequest | None: ...

    def list_publish_requests(
        self,
        workspace_id: UUID,
        *,
        approval_instance_ids: tuple[UUID, ...],
        limit: int,
    ) -> tuple[DocumentPublishRequest, ...]: ...

    def resolve_domain_scope(
        self,
        *,
        workspace_id: UUID,
        domain_id: UUID,
        account_id: UUID,
        authorized_workspace: bool,
        authorized_department_ids: frozenset[UUID],
        authorized_account_ids: frozenset[UUID],
        authorized_document_ids: frozenset[UUID],
        maximum_security_level: SecurityLevel,
    ) -> ResolvedKnowledgeDomainScope | None: ...


class EnterpriseKnowledgeUnitOfWork(Protocol):
    """保证企业知识事实、审计与 Outbox 原子提交。"""

    @property
    def enterprise_knowledge(self) -> EnterpriseKnowledgeRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> EnterpriseKnowledgeUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
