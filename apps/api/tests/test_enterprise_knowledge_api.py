"""验证企业分类、团队知识域和有效范围解释 HTTP 协议。"""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.enterprise_knowledge.api.dependencies import (
    enterprise_knowledge_service,
    trusted_request_context,
)
from ai_platform_api.modules.enterprise_knowledge.api.routes import router
from ai_platform_api.modules.enterprise_knowledge.application.service import (
    EnterpriseKnowledgeService,
)
from ai_platform_api.modules.enterprise_knowledge.application.views import (
    DocumentPublishApprovalLevelView,
    DocumentPublishApprovalView,
    DocumentPublishRequestView,
    EnterpriseCategoryResultView,
    EnterpriseKnowledgePortalView,
    ResolvedKnowledgeDomainScopeView,
    TeamKnowledgeDomainView,
    enterprise_category_result_view,
    enterprise_knowledge_portal_view,
    resolved_knowledge_domain_scope_view,
    team_knowledge_domain_view,
)
from ai_platform_api.modules.enterprise_knowledge.domain.models import (
    CategoryVisibility,
    EnterpriseCategory,
    EnterpriseCategoryView,
    EnterpriseDepartmentOption,
    EnterpriseDocumentOption,
    EnterpriseKnowledgeBaseOption,
    EnterpriseKnowledgePortalSnapshot,
    EnterpriseKnowledgeStatistics,
    EnterpriseMemberOption,
    GovernanceStatus,
    RagPolicy,
    RagPolicyMode,
    ResolvedKnowledgeDomainScope,
    TeamKnowledgeDomain,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000903")
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000903")
CATEGORY_ID = UUID("30000000-0000-4000-8000-000000000903")
DOMAIN_ID = UUID("40000000-0000-4000-8000-000000000903")
DOCUMENT_ID = UUID("50000000-0000-4000-8000-000000000903")
DOCUMENT_VERSION_ID = UUID("51000000-0000-4000-8000-000000000903")
PUBLISH_REQUEST_ID = UUID("52000000-0000-4000-8000-000000000903")
APPROVAL_INSTANCE_ID = UUID("53000000-0000-4000-8000-000000000903")
KNOWLEDGE_BASE_ID = UUID("60000000-0000-4000-8000-000000000903")
DEPARTMENT_ID = UUID("70000000-0000-4000-8000-000000000903")
MEMBERSHIP_ID = UUID("80000000-0000-4000-8000-000000000903")
NOW = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
CONTEXT = RequestContext.trusted(
    actor_id=ACCOUNT_ID,
    user_id=ACCOUNT_ID,
    workspace_id=WORKSPACE_ID,
    trace=TraceContext("a" * 32, "b" * 16),
    authentication_method="browser_session",
)


class StubEnterpriseKnowledgeService:
    """记录 Router 传入的完整命令，并返回稳定合成领域事实。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def get_portal(
        self, context: RequestContext, *, workspace_id: UUID
    ) -> EnterpriseKnowledgePortalView:
        assert context is CONTEXT and workspace_id == WORKSPACE_ID
        self.calls.append(("portal", workspace_id))
        return enterprise_knowledge_portal_view(
            EnterpriseKnowledgePortalSnapshot(
                workspace_id=workspace_id,
                workspace_name="合成治理企业",
                statistics=EnterpriseKnowledgeStatistics(1, 1, 1, 1),
                categories=(EnterpriseCategoryView(self._category(), (DOCUMENT_ID,)),),
                domains=(self._domain(),),
                documents=(
                    EnterpriseDocumentOption(
                        DOCUMENT_ID, KNOWLEDGE_BASE_ID, "合成制度文档", "INTERNAL"
                    ),
                ),
                knowledge_bases=(
                    EnterpriseKnowledgeBaseOption(KNOWLEDGE_BASE_ID, "合成研发库", "INTERNAL"),
                ),
                departments=(EnterpriseDepartmentOption(DEPARTMENT_ID, "合成研发部"),),
                members=(EnterpriseMemberOption(MEMBERSHIP_ID, ACCOUNT_ID, "合成研发成员"),),
                generated_at=NOW,
            )
        )

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
        assert context is CONTEXT and workspace_id == WORKSPACE_ID
        self.calls.append(
            (
                "create_category",
                (
                    name,
                    description,
                    parent_category_id,
                    visibility,
                    department_ids,
                    document_ids,
                    approval_required,
                ),
            )
        )
        return enterprise_category_result_view(
            EnterpriseCategoryView(
                self._category(
                    name=name,
                    visibility=visibility,
                    approval_required=approval_required,
                ),
                document_ids,
            )
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
        assert context is CONTEXT and workspace_id == WORKSPACE_ID and category_id == CATEGORY_ID
        self.calls.append(
            (
                "update_category",
                (
                    expected_version,
                    name,
                    description,
                    parent_category_id,
                    visibility,
                    department_ids,
                    approval_required,
                ),
            )
        )
        return enterprise_category_result_view(
            EnterpriseCategoryView(
                self._category(
                    name=name,
                    visibility=visibility,
                    version=expected_version + 1,
                    approval_required=bool(approval_required),
                ),
                (DOCUMENT_ID,),
            )
        )

    def archive_category(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        category_id: UUID,
        expected_version: int,
    ) -> EnterpriseCategoryResultView:
        assert context is CONTEXT and workspace_id == WORKSPACE_ID and category_id == CATEGORY_ID
        self.calls.append(("archive_category", expected_version))
        return enterprise_category_result_view(
            EnterpriseCategoryView(
                self._category(status="archived", version=expected_version + 1),
                (DOCUMENT_ID,),
            )
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
        assert context is CONTEXT and workspace_id == WORKSPACE_ID and category_id == CATEGORY_ID
        self.calls.append(("replace_category_documents", (expected_version, document_ids)))
        return enterprise_category_result_view(
            EnterpriseCategoryView(self._category(version=expected_version + 1), document_ids)
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
        assert context is CONTEXT and workspace_id == WORKSPACE_ID
        self.calls.append(
            (
                "create_domain",
                (
                    name,
                    description,
                    member_ids,
                    department_ids,
                    knowledge_base_ids,
                    rag_mode,
                    top_k,
                    minimum_score,
                ),
            )
        )
        return team_knowledge_domain_view(
            self._domain(
                name=name,
                member_ids=member_ids,
                department_ids=department_ids,
                knowledge_base_ids=knowledge_base_ids,
                rag_mode=rag_mode,
                top_k=top_k,
                minimum_score=minimum_score,
            )
        )

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
        assert context is CONTEXT and workspace_id == WORKSPACE_ID and domain_id == DOMAIN_ID
        self.calls.append(
            (
                "update_domain",
                (expected_version, name, description, rag_mode, top_k, minimum_score),
            )
        )
        return team_knowledge_domain_view(
            self._domain(
                name=name,
                rag_mode=rag_mode,
                top_k=top_k,
                minimum_score=minimum_score,
                policy_version=2,
                version=expected_version + 1,
            )
        )

    def archive_domain(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        domain_id: UUID,
        expected_version: int,
    ) -> TeamKnowledgeDomainView:
        assert context is CONTEXT and workspace_id == WORKSPACE_ID and domain_id == DOMAIN_ID
        self.calls.append(("archive_domain", expected_version))
        return team_knowledge_domain_view(
            self._domain(status="archived", version=expected_version + 1)
        )

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
        assert context is CONTEXT and workspace_id == WORKSPACE_ID and domain_id == DOMAIN_ID
        self.calls.append(
            (
                "replace_domain_scope",
                (expected_version, member_ids, department_ids, knowledge_base_ids),
            )
        )
        return team_knowledge_domain_view(
            self._domain(
                member_ids=member_ids,
                department_ids=department_ids,
                knowledge_base_ids=knowledge_base_ids,
                version=expected_version + 1,
            )
        )

    def resolve_domain_scope(
        self, context: RequestContext, *, workspace_id: UUID, domain_id: UUID
    ) -> ResolvedKnowledgeDomainScopeView:
        assert context is CONTEXT and workspace_id == WORKSPACE_ID and domain_id == DOMAIN_ID
        self.calls.append(("resolve_domain_scope", domain_id))
        return resolved_knowledge_domain_scope_view(
            ResolvedKnowledgeDomainScope(
                domain_id=domain_id,
                policy_version=9,
                actor_in_declared_scope=True,
                declared_knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
                authorized_knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
                effective_knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
                empty_reason="none",
            )
        )

    def request_document_publish(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
        idempotency_key: str,
    ) -> DocumentPublishRequestView:
        """记录发布申请的资源标识和幂等键。"""

        assert context is CONTEXT and workspace_id == WORKSPACE_ID
        self.calls.append(
            (
                "request_document_publish",
                (document_id, document_version_id, idempotency_key),
            )
        )
        return self._publish_request()

    def list_document_publish_requests(
        self, context: RequestContext, *, workspace_id: UUID, limit: int
    ) -> tuple[DocumentPublishRequestView, ...]:
        """记录发布台账的参与者可见列表查询。"""

        assert context is CONTEXT and workspace_id == WORKSPACE_ID
        self.calls.append(("list_document_publish_requests", limit))
        return (self._publish_request(),)

    def get_document_publish_request(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        publish_request_id: UUID,
    ) -> DocumentPublishRequestView:
        """记录发布台账详情的精确资源查询。"""

        assert context is CONTEXT and workspace_id == WORKSPACE_ID
        self.calls.append(("get_document_publish_request", publish_request_id))
        return self._publish_request()

    @staticmethod
    def _publish_request() -> DocumentPublishRequestView:
        """构造仅含协议所需低敏字段的合成发布申请。"""

        level = DocumentPublishApprovalLevelView(
            sequence_no=1,
            mode="any",
            status="active",
            approver_account_ids=(ACCOUNT_ID,),
            fallback_activated=False,
            reminder_at=None,
            timeout_at=None,
            completed_at=None,
        )
        return DocumentPublishRequestView(
            publish_request_id=PUBLISH_REQUEST_ID,
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            requester_account_id=ACCOUNT_ID,
            category_ids=(CATEGORY_ID,),
            version_number=1,
            status="pending",
            failure_reason_code=None,
            created_at=NOW,
            updated_at=NOW,
            completed_at=None,
            version=1,
            approval=DocumentPublishApprovalView(
                approval_instance_id=APPROVAL_INSTANCE_ID,
                status="pending",
                current_sequence_no=1,
                personal_owner_confirmation=False,
                levels=(level,),
            ),
        )

    @staticmethod
    def _category(
        *,
        name: str = "合成制度分类",
        visibility: CategoryVisibility = "public",
        status: GovernanceStatus = "active",
        version: int = 1,
        approval_required: bool = False,
    ) -> EnterpriseCategory:
        return EnterpriseCategory(
            category_id=CATEGORY_ID,
            workspace_id=WORKSPACE_ID,
            parent_category_id=None,
            name=name,
            description="仅用于 HTTP 测试",
            visibility=visibility,
            department_ids=(),
            status=status,
            created_by_account_id=ACCOUNT_ID,
            created_at=NOW,
            updated_at=NOW,
            version=version,
            approval_required=approval_required,
        )

    @staticmethod
    def _domain(
        *,
        name: str = "合成研发知识域",
        member_ids: tuple[UUID, ...] = (ACCOUNT_ID,),
        department_ids: tuple[UUID, ...] = (DEPARTMENT_ID,),
        knowledge_base_ids: tuple[UUID, ...] = (KNOWLEDGE_BASE_ID,),
        rag_mode: RagPolicyMode = "balanced",
        top_k: int = 8,
        minimum_score: float = 0.25,
        policy_version: int = 1,
        status: GovernanceStatus = "active",
        version: int = 1,
    ) -> TeamKnowledgeDomain:
        return TeamKnowledgeDomain(
            domain_id=DOMAIN_ID,
            workspace_id=WORKSPACE_ID,
            name=name,
            description="仅用于 HTTP 测试",
            member_ids=member_ids,
            department_ids=department_ids,
            knowledge_base_ids=knowledge_base_ids,
            rag_policy=RagPolicy(policy_version, rag_mode, top_k, minimum_score),
            status=status,
            created_by_account_id=ACCOUNT_ID,
            created_at=NOW,
            updated_at=NOW,
            version=version,
        )


def enterprise_knowledge_client(
    service: StubEnterpriseKnowledgeService,
) -> TestClient:
    """只替换可信上下文和应用服务，保留真实 Router 与 Pydantic 协议层。"""

    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[trusted_request_context] = lambda: CONTEXT
    application.dependency_overrides[enterprise_knowledge_service] = lambda: cast(
        "EnterpriseKnowledgeService", service
    )
    return TestClient(application)


def test_enterprise_knowledge_routes_preserve_complete_governance_commands() -> None:
    """十个接口必须保留整体命令、乐观版本和完整响应，不泄露内部定位。"""

    service = StubEnterpriseKnowledgeService()
    client = enterprise_knowledge_client(service)
    base = f"/api/v1/workspaces/{WORKSPACE_ID}"
    category_body = {
        "name": "合成制度分类",
        "description": "仅用于 HTTP 测试",
        "parent_category_id": None,
        "visibility": "public",
        "department_ids": [],
        "document_ids": [str(DOCUMENT_ID)],
        "approval_required": True,
    }
    domain_body = {
        "name": "合成研发知识域",
        "description": "仅用于 HTTP 测试",
        "member_ids": [str(ACCOUNT_ID)],
        "department_ids": [str(DEPARTMENT_ID)],
        "knowledge_base_ids": [str(KNOWLEDGE_BASE_ID)],
        "rag_mode": "balanced",
        "top_k": 8,
        "minimum_score": 0.25,
    }

    # 1. 真实 Router 依次接收统一读取、分类和知识域的十项协议请求。
    with client:
        portal = client.get(f"{base}/enterprise-knowledge")
        category_created = client.post(f"{base}/enterprise-categories", json=category_body)
        category_updated = client.put(
            f"{base}/enterprise-categories/{CATEGORY_ID}",
            json={
                "expected_version": 2,
                "name": category_body["name"],
                "description": category_body["description"],
                "parent_category_id": category_body["parent_category_id"],
                "visibility": category_body["visibility"],
                "department_ids": category_body["department_ids"],
                "approval_required": category_body["approval_required"],
            },
        )
        category_archived = client.post(
            f"{base}/enterprise-categories/{CATEGORY_ID}/archive",
            json={"expected_version": 3},
        )
        category_bound = client.put(
            f"{base}/enterprise-categories/{CATEGORY_ID}/documents",
            json={"expected_version": 4, "document_ids": [str(DOCUMENT_ID)]},
        )
        domain_created = client.post(f"{base}/team-knowledge-domains", json=domain_body)
        domain_updated = client.put(
            f"{base}/team-knowledge-domains/{DOMAIN_ID}",
            json={
                "expected_version": 2,
                "name": "合成研发知识域二版",
                "description": None,
                "rag_mode": "precision",
                "top_k": 6,
                "minimum_score": 0.4,
            },
        )
        domain_archived = client.post(
            f"{base}/team-knowledge-domains/{DOMAIN_ID}/archive",
            json={"expected_version": 3},
        )
        domain_scoped = client.put(
            f"{base}/team-knowledge-domains/{DOMAIN_ID}/scope",
            json={
                "expected_version": 4,
                "member_ids": [str(ACCOUNT_ID)],
                "department_ids": [str(DEPARTMENT_ID)],
                "knowledge_base_ids": [str(KNOWLEDGE_BASE_ID)],
            },
        )
        scope = client.get(f"{base}/team-knowledge-domains/{DOMAIN_ID}/resolved-scope")

    # 2. 响应保留当前绑定、策略版本和交集解释，且不包含对象存储或正文事实。
    responses = (
        portal,
        category_created,
        category_updated,
        category_archived,
        category_bound,
        domain_created,
        domain_updated,
        domain_archived,
        domain_scoped,
        scope,
    )
    assert [response.status_code for response in responses] == [200] * 10
    assert portal.json()["categories"][0]["document_ids"] == [str(DOCUMENT_ID)]
    assert category_updated.json()["document_ids"] == [str(DOCUMENT_ID)]
    assert category_bound.json()["document_ids"] == [str(DOCUMENT_ID)]
    assert category_archived.json()["status"] == "archived"
    assert domain_updated.json()["rag_policy"] == {
        "policy_version": 2,
        "mode": "precision",
        "top_k": 6,
        "minimum_score": 0.4,
    }
    assert domain_archived.json()["status"] == "archived"
    assert domain_scoped.json()["knowledge_base_ids"] == [str(KNOWLEDGE_BASE_ID)]
    assert scope.json()["effective_knowledge_base_ids"] == [str(KNOWLEDGE_BASE_ID)]
    assert scope.json()["policy_version"] == 9
    assert "object_key" not in "".join(response.text for response in responses)
    assert [call[0] for call in service.calls] == [
        "portal",
        "create_category",
        "update_category",
        "archive_category",
        "replace_category_documents",
        "create_domain",
        "update_domain",
        "archive_domain",
        "replace_domain_scope",
        "resolve_domain_scope",
    ]


def test_enterprise_knowledge_contract_rejects_partial_scope_and_extra_fields() -> None:
    """原子范围缺字段和分类未知字段必须在协议层拒绝，不进入应用服务。"""

    service = StubEnterpriseKnowledgeService()
    client = enterprise_knowledge_client(service)
    base = f"/api/v1/workspaces/{WORKSPACE_ID}"

    with client:
        partial_scope = client.put(
            f"{base}/team-knowledge-domains/{DOMAIN_ID}/scope",
            json={"expected_version": 1, "member_ids": []},
        )
        extra_category = client.post(
            f"{base}/enterprise-categories",
            json={
                "name": "合成非法分类",
                "visibility": "public",
                "unexpected": "必须拒绝",
            },
        )

    assert partial_scope.status_code == 422
    assert extra_category.status_code == 422
    assert service.calls == []


def test_document_publish_routes_preserve_idempotency_and_low_sensitive_views() -> None:
    """发布申请、台账和详情必须保留命令语义且只返回低敏审批事实。"""

    service = StubEnterpriseKnowledgeService()
    client = enterprise_knowledge_client(service)
    base = f"/api/v1/workspaces/{WORKSPACE_ID}"
    request_path = (
        f"{base}/enterprise-documents/{DOCUMENT_ID}/versions/{DOCUMENT_VERSION_ID}/publish-requests"
    )

    with client:
        created = client.post(
            request_path,
            json={"idempotency_key": "synthetic-p6b04-http-1"},
        )
        listed = client.get(f"{base}/document-publish-requests", params={"limit": 25})
        detailed = client.get(f"{base}/document-publish-requests/{PUBLISH_REQUEST_ID}")

    assert [created.status_code, listed.status_code, detailed.status_code] == [200, 200, 200]
    assert service.calls == [
        (
            "request_document_publish",
            (DOCUMENT_ID, DOCUMENT_VERSION_ID, "synthetic-p6b04-http-1"),
        ),
        ("list_document_publish_requests", 25),
        ("get_document_publish_request", PUBLISH_REQUEST_ID),
    ]
    assert listed.json() == [created.json()]
    assert detailed.json() == created.json()
    assert created.json()["approval"]["levels"][0]["approver_account_ids"] == [str(ACCOUNT_ID)]
    serialized = created.text + listed.text + detailed.text
    for forbidden in (
        "content_hash",
        "governance_digest",
        "idempotency_key",
        "object_key",
        "policy_conditions",
    ):
        assert forbidden not in serialized


def test_document_publish_contract_rejects_invalid_idempotency_keys() -> None:
    """空白、非法字符和超长幂等键必须在协议层拒绝，不进入应用服务。"""

    service = StubEnterpriseKnowledgeService()
    client = enterprise_knowledge_client(service)
    path = (
        f"/api/v1/workspaces/{WORKSPACE_ID}/enterprise-documents/{DOCUMENT_ID}/"
        f"versions/{DOCUMENT_VERSION_ID}/publish-requests"
    )

    with client:
        responses = [
            client.post(path, json={"idempotency_key": value})
            for value in ("", "has space", "x" * 129)
        ]
        extra = client.post(
            path,
            json={
                "idempotency_key": "synthetic-p6b04-valid",
                "document_content": "不得进入审批主题",
            },
        )

    assert [response.status_code for response in (*responses, extra)] == [422] * 4
    assert service.calls == []
