"""验证 P1E-02 的有界查询改写、权限过滤和检索事实快照。"""

from dataclasses import dataclass
from types import TracebackType
from typing import Any
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.agent_control.application.releases import release_snapshot_digest
from ai_platform_api.modules.authorization.domain.fields import FieldPolicyRegistry, FieldRule
from ai_platform_api.modules.authorization.domain.policy import (
    PolicyDecision,
    PolicyRequest,
    ResourceScope,
)
from ai_platform_api.modules.retrieval.application.planning import (
    BoundedRetrievalPlanningService,
    DeterministicQueryRewriter,
)
from ai_platform_api.modules.retrieval.domain.errors import (
    RetrievalConfigurationError,
    RetrievalScopeDeniedError,
)
from ai_platform_api.modules.retrieval.domain.models import (
    AuthorizedSearchScope,
    ChannelCandidate,
    StoredChunk,
)
from ai_platform_api.modules.retrieval.domain.planning import (
    RetrievalPlannerBudget,
    RetrievalPlanSnapshot,
    RetrievalRunInput,
    release_knowledge_scope_version_ids,
)
from ai_platform_backend.indexing.embeddings import DeterministicHashEmbeddingAdapter

WORKSPACE_ID = UUID(int=1)
ACCOUNT_ID = UUID(int=2)
INDEX_ID = UUID(int=3)
KNOWLEDGE_BASE_ID = UUID(int=4)
DOCUMENT_ID = UUID(int=5)
DOCUMENT_VERSION_ID = UUID(int=6)
DEPARTMENT_ID = UUID(int=7)
RUN_ID = UUID(int=8)
CHUNK_ID = UUID(int=20)
TRACE = TraceContext.continue_from("00-7123456789abcdef0123456789abcdef-7123456789abcdef-01")


class AllowPolicy:
    """为应用服务测试提供工作空间级、内部密级的合成授权。"""

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            decision_id=UUID(int=9),
            decision="allow",
            permission_code=request.permission_code,
            workspace_id=request.context.workspace_id,
            resource_scope=ResourceScope(workspace=True),
            field_mask=frozenset(),
            policy_version=1,
            cache_ttl_seconds=0,
            reason="synthetic_allow",
            maximum_security_level="INTERNAL",
        )


class DenyPolicy:
    """验证 PDP 拒绝时服务不会继续触发索引搜索。"""

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            decision_id=UUID(int=10),
            decision="deny",
            permission_code=request.permission_code,
            workspace_id=request.context.workspace_id,
            resource_scope=ResourceScope(),
            field_mask=frozenset(),
            policy_version=1,
            cache_ttl_seconds=0,
            reason="synthetic_deny",
        )


def context() -> RequestContext:
    return RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TRACE,
        authentication_method="browser_session",
    )


def run_input(query: str = "请总结差旅报销制度") -> RetrievalRunInput:
    return RetrievalRunInput(
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        requested_by_account_id=ACCOUNT_ID,
        runtime_config_version_id=UUID(int=11),
        status="queued",
        query=query,
        embedding_model_version=DeterministicHashEmbeddingAdapter.model_version,
        retrieval_strategy_version="hybrid-rrf-v1",
        tokenizer_version="cjk-bigram-v1",
    )


def chunk(
    *,
    visibility: str = "workspace",
    workspace_id: UUID = WORKSPACE_ID,
    chunk_id: UUID = CHUNK_ID,
    document_id: UUID = DOCUMENT_ID,
    department_ids: tuple[UUID, ...] | None = None,
) -> StoredChunk:
    return StoredChunk(
        chunk_id=chunk_id,
        workspace_id=workspace_id,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        document_id=document_id,
        document_version_id=DOCUMENT_VERSION_ID,
        index_version_id=INDEX_ID,
        sequence_no=1,
        content="合成差旅制度正文",
        content_hash="a" * 64,
        source_position={"page_number": 1},
        department_ids=(
            department_ids
            if department_ids is not None
            else ((DEPARTMENT_ID,) if visibility == "departments" else ())
        ),
        visibility=visibility,  # type: ignore[arg-type]
        security_level="INTERNAL",
    )


@dataclass
class FakeSearchIndex:
    keyword_candidates: tuple[ChannelCandidate, ...] = ()
    vector_candidates: tuple[ChannelCandidate, ...] = ()
    calls: int = 0

    def keyword_search(
        self,
        scope: AuthorizedSearchScope,
        keyword_query: str,
        limit: int,
    ) -> tuple[ChannelCandidate, ...]:
        del scope, keyword_query
        self.calls += 1
        return self.keyword_candidates[:limit]

    def vector_search(
        self,
        scope: AuthorizedSearchScope,
        embedding: tuple[float, ...],
        limit: int,
    ) -> tuple[ChannelCandidate, ...]:
        del scope, embedding
        self.calls += 1
        return self.vector_candidates[:limit]

    def read_document_range(
        self,
        scope: AuthorizedSearchScope,
        document_version_id: UUID,
        sequence_start: int,
        sequence_end: int,
    ) -> tuple[StoredChunk, ...]:
        del scope, document_version_id, sequence_start, sequence_end
        return ()

    def get_chunk(
        self,
        scope: AuthorizedSearchScope,
        chunk_id: UUID,
    ) -> StoredChunk | None:
        del scope, chunk_id
        return None


@dataclass
class FakePlanningRepository:
    run: RetrievalRunInput
    scope: AuthorizedSearchScope | None
    plan: RetrievalPlanSnapshot | None = None
    stored: RetrievalPlanSnapshot | None = None

    def lock_run(self, run_id: UUID) -> RetrievalRunInput | None:
        return self.run if run_id == self.run.run_id else None

    def get_plan(self, run_id: UUID) -> RetrievalPlanSnapshot | None:
        return self.stored if run_id == self.run.run_id else None

    def resolve_search_scope(
        self,
        run: RetrievalRunInput,
        authorization: Any,
    ) -> AuthorizedSearchScope | None:
        del run, authorization
        return self.scope

    def add_plan(self, plan: RetrievalPlanSnapshot) -> None:
        self.stored = plan


@dataclass
class FakeUnitOfWork:
    planning: FakePlanningRepository
    search_index: FakeSearchIndex
    commits: int = 0

    def __enter__(self) -> "FakeUnitOfWork":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    def commit(self) -> None:
        self.commits += 1


def service(
    unit_of_work: FakeUnitOfWork,
    *,
    policy: object | None = None,
    budget: RetrievalPlannerBudget | None = None,
    field_registry: FieldPolicyRegistry | None = None,
) -> BoundedRetrievalPlanningService:
    return BoundedRetrievalPlanningService(
        unit_of_work,
        policy or AllowPolicy(),  # type: ignore[arg-type]
        field_registry or FieldPolicyRegistry(1, 1, ()),
        DeterministicHashEmbeddingAdapter(),
        budget=budget,
    )


def test_rewriter_normalizes_and_limits_variants() -> None:
    rewriter = DeterministicQueryRewriter()
    classification, variants = rewriter.rewrite(
        "  请比较 甲方 与 乙方 的区别  ",
        RetrievalPlannerBudget(max_query_variants=3, max_search_operations=6),
    )

    assert classification == "comparison"
    assert len(variants) == 3
    assert variants[0].text == "请比较 甲方 与 乙方 的区别"
    assert len({variant.query_hash for variant in variants}) == 3


def test_rewriter_rejects_empty_or_oversized_query() -> None:
    rewriter = DeterministicQueryRewriter()
    with pytest.raises(RetrievalConfigurationError):
        rewriter.rewrite("   ", RetrievalPlannerBudget())
    with pytest.raises(RetrievalConfigurationError):
        rewriter.rewrite("a" * 5, RetrievalPlannerBudget(max_query_characters=4))


def test_release_knowledge_scope_parser_accepts_system_and_valid_custom_snapshot() -> None:
    """系统助手保留全空间兼容语义，自定义 Release 返回冻结范围版本。"""

    runtime_config_version_id = UUID(int=11)
    first_scope_id = UUID(int=12)
    second_scope_id = UUID(int=13)
    snapshot: dict[str, object] = {
        "snapshot_schema_version": 1,
        "configuration": {
            "runtime_config_version_id": str(runtime_config_version_id),
            "knowledge_scope_version_ids": [str(first_scope_id), str(second_scope_id)],
        },
    }

    assert (
        release_knowledge_scope_version_ids(
            release_kind="system",
            release_snapshot=None,
            release_snapshot_hash=None,
            runtime_config_version_id=runtime_config_version_id,
        )
        is None
    )
    assert release_knowledge_scope_version_ids(
        release_kind="custom",
        release_snapshot=snapshot,
        release_snapshot_hash=release_snapshot_digest(snapshot),
        runtime_config_version_id=runtime_config_version_id,
    ) == (first_scope_id, second_scope_id)


@pytest.mark.parametrize(
    ("release_kind", "snapshot_mutation", "snapshot_hash"),
    (
        ("unknown", {}, None),
        ("custom", {"snapshot_schema_version": 2}, "digest"),
        (
            "custom",
            {
                "snapshot_schema_version": 1,
                "configuration": {
                    "runtime_config_version_id": str(UUID(int=99)),
                    "knowledge_scope_version_ids": [],
                },
            },
            "computed",
        ),
        (
            "custom",
            {
                "snapshot_schema_version": 1,
                "configuration": {
                    "runtime_config_version_id": str(UUID(int=11)),
                    "knowledge_scope_version_ids": [str(UUID(int=12)), str(UUID(int=12))],
                },
            },
            "computed",
        ),
    ),
)
def test_release_knowledge_scope_parser_fails_closed(
    release_kind: str,
    snapshot_mutation: dict[str, object],
    snapshot_hash: str | None,
) -> None:
    """未知类型、损坏版本、运行配置漂移和重复范围都不能进入检索。"""

    resolved_hash = (
        release_snapshot_digest(snapshot_mutation) if snapshot_hash == "computed" else snapshot_hash
    )
    with pytest.raises(ValueError):
        release_knowledge_scope_version_ids(
            release_kind=release_kind,
            release_snapshot=snapshot_mutation,
            release_snapshot_hash=resolved_hash,
            runtime_config_version_id=UUID(int=11),
        )


def test_planning_is_bounded_and_idempotent_without_storing_content() -> None:
    evidence = chunk()
    index = FakeSearchIndex(
        keyword_candidates=(ChannelCandidate(evidence, "keyword", 1, 0.8),),
        vector_candidates=(ChannelCandidate(evidence, "vector", 1, 0.9),),
    )
    unit = FakeUnitOfWork(FakePlanningRepository(run_input(), _scope()), index)
    planner = service(unit)

    first = planner.retrieve(context(), RUN_ID)
    second = planner.retrieve(context(), RUN_ID)

    assert first is second
    assert len(first.variants) == 2
    assert first.search_operation_count == 4
    assert len(first.candidates) == 1
    assert first.candidates[0].content_hash == "a" * 64
    assert not hasattr(first.candidates[0], "content")
    assert index.calls == 4
    assert unit.commits == 1


def test_content_field_mask_fails_closed_before_search() -> None:
    evidence = chunk()
    index = FakeSearchIndex(
        keyword_candidates=(ChannelCandidate(evidence, "keyword", 1, 0.8),),
        vector_candidates=(ChannelCandidate(evidence, "vector", 1, 0.9),),
    )
    registry = FieldPolicyRegistry(1, 1, (FieldRule("chunk", "content", "RESTRICTED"),))
    unit = FakeUnitOfWork(FakePlanningRepository(run_input(), _scope()), index)

    with pytest.raises(RetrievalScopeDeniedError):
        service(unit, field_registry=registry).retrieve(context(), RUN_ID)
    assert index.calls == 0


def test_department_candidates_are_filtered_again_after_search() -> None:
    allowed = chunk(
        visibility="departments",
        chunk_id=UUID(int=21),
        department_ids=(DEPARTMENT_ID,),
    )
    denied = chunk(
        visibility="departments",
        chunk_id=UUID(int=22),
        document_id=UUID(int=23),
        department_ids=(UUID(int=24),),
    )
    candidates = (
        ChannelCandidate(allowed, "keyword", 1, 0.9),
        ChannelCandidate(denied, "keyword", 2, 0.8),
    )
    index = FakeSearchIndex(keyword_candidates=candidates, vector_candidates=())
    department_scope = AuthorizedSearchScope(
        workspace_id=WORKSPACE_ID,
        index_version_ids=frozenset({INDEX_ID}),
        knowledge_base_ids=None,
        document_ids=None,
        department_ids=frozenset({DEPARTMENT_ID}),
        visibilities=frozenset({"departments"}),
        security_levels=frozenset({"INTERNAL"}),
    )
    unit = FakeUnitOfWork(FakePlanningRepository(run_input(), department_scope), index)

    plan = service(unit).retrieve(context(), RUN_ID)

    assert [candidate.chunk_id for candidate in plan.candidates] == [allowed.chunk_id]


def test_policy_denial_fails_closed() -> None:
    unit = FakeUnitOfWork(FakePlanningRepository(run_input(), _scope()), FakeSearchIndex())

    with pytest.raises(RetrievalScopeDeniedError):
        service(unit, policy=DenyPolicy()).retrieve(context(), RUN_ID)


def _scope() -> AuthorizedSearchScope:
    return AuthorizedSearchScope(
        workspace_id=WORKSPACE_ID,
        index_version_ids=frozenset({INDEX_ID}),
        knowledge_base_ids=None,
        document_ids=None,
        department_ids=frozenset({DEPARTMENT_ID}),
        visibilities=frozenset({"workspace"}),
        security_levels=frozenset({"INTERNAL"}),
        field_mask=frozenset(),
    )
