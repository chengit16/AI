"""验证 P1E-03 FastPass、重排、来源排序、受控精读和降级规则。"""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.domain.fields import FieldPolicyRegistry, FieldRule
from ai_platform_api.modules.authorization.domain.policy import (
    PolicyDecision,
    PolicyRequest,
    ResourceScope,
)
from ai_platform_api.modules.retrieval.application.evidence import RetrievalEvidenceService
from ai_platform_api.modules.retrieval.domain.errors import (
    RetrievalConfigurationError,
    RetrievalScopeDeniedError,
)
from ai_platform_api.modules.retrieval.domain.evidence import (
    EvidenceCandidateSource,
    EvidenceProcessingBudget,
    EvidenceSetSnapshot,
)
from ai_platform_api.modules.retrieval.domain.models import (
    AuthorizedSearchScope,
    ChannelCandidate,
    StoredChunk,
)
from ai_platform_api.modules.retrieval.domain.planning import (
    QueryVariant,
    RetrievalAuthorization,
    RetrievalCandidateSnapshot,
    RetrievalPlannerBudget,
    RetrievalPlanSnapshot,
    RetrievalRunInput,
)
from ai_platform_api.modules.retrieval.infrastructure.reranking import (
    DeterministicLexicalReranker,
)

WORKSPACE_ID = UUID(int=1)
ACCOUNT_ID = UUID(int=2)
RUN_ID = UUID(int=3)
PLAN_ID = UUID(int=4)
INDEX_ID = UUID(int=5)
KNOWLEDGE_BASE_ID = UUID(int=6)
DOCUMENT_ID = UUID(int=7)
VERSION_ID = UUID(int=8)
CHUNK_ID = UUID(int=9)
TRACE = TraceContext.continue_from("00-8123456789abcdef0123456789abcdef-8123456789abcdef-01")
NOW = datetime(2026, 8, 15, tzinfo=UTC)


class AllowPolicy:
    """提供固定策略版本和工作空间读取范围。"""

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            decision_id=UUID(int=10),
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


@dataclass
class FixedReranker:
    """返回测试指定分数并记录是否真正经过重排通道。"""

    scores: tuple[float, ...]
    model_version: str = "synthetic-reranker-v1"
    calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    def score(self, query: str, passages: tuple[str, ...]) -> tuple[float, ...]:
        self.calls.append((query, passages))
        return self.scores


@dataclass
class FakeSearchIndex:
    """只按当前合成 Chunk 集合提供精读和引用复核。"""

    chunks: tuple[StoredChunk, ...]

    def keyword_search(
        self,
        scope: AuthorizedSearchScope,
        keyword_query: str,
        limit: int,
    ) -> tuple[ChannelCandidate, ...]:
        del scope, keyword_query, limit
        return ()

    def vector_search(
        self,
        scope: AuthorizedSearchScope,
        embedding: tuple[float, ...],
        limit: int,
    ) -> tuple[ChannelCandidate, ...]:
        del scope, embedding, limit
        return ()

    def read_document_range(
        self,
        scope: AuthorizedSearchScope,
        document_version_id: UUID,
        sequence_start: int,
        sequence_end: int,
    ) -> tuple[StoredChunk, ...]:
        del scope
        return tuple(
            chunk
            for chunk in self.chunks
            if chunk.document_version_id == document_version_id
            and sequence_start <= chunk.sequence_no <= sequence_end
        )

    def get_chunk(
        self,
        scope: AuthorizedSearchScope,
        chunk_id: UUID,
    ) -> StoredChunk | None:
        del scope
        return next((chunk for chunk in self.chunks if chunk.chunk_id == chunk_id), None)


@dataclass
class FakePlanningRepository:
    """返回冻结 Run、检索计划和当前授权索引范围。"""

    run: RetrievalRunInput
    plan: RetrievalPlanSnapshot
    scope: AuthorizedSearchScope | None

    def lock_run(self, run_id: UUID) -> RetrievalRunInput | None:
        return self.run if run_id == self.run.run_id else None

    def get_plan(self, run_id: UUID) -> RetrievalPlanSnapshot | None:
        return self.plan if run_id == self.plan.run_id else None

    def resolve_search_scope(
        self,
        run: RetrievalRunInput,
        authorization: RetrievalAuthorization,
    ) -> AuthorizedSearchScope | None:
        del run, authorization
        return self.scope

    def add_plan(self, plan: RetrievalPlanSnapshot) -> None:
        raise AssertionError("P1E-03 不应改写检索计划")


@dataclass
class FakeEvidenceRepository:
    """按 Chunk ID 提供已复核来源并捕获最终证据快照。"""

    sources: dict[UUID, EvidenceCandidateSource]
    existing: EvidenceSetSnapshot | None = None
    saved: EvidenceSetSnapshot | None = None

    def get_evidence_set(self, run_id: UUID) -> EvidenceSetSnapshot | None:
        del run_id
        return self.existing

    def load_candidate_source(
        self,
        scope: AuthorizedSearchScope,
        candidate: RetrievalCandidateSnapshot,
    ) -> EvidenceCandidateSource | None:
        del scope
        return self.sources.get(candidate.chunk_id)

    def evidence_set_is_current(
        self,
        scope: AuthorizedSearchScope,
        evidence_set: EvidenceSetSnapshot,
    ) -> bool:
        del scope
        return all(item.chunk_id in self.sources for item in evidence_set.items)

    def add_evidence_set(self, evidence_set: EvidenceSetSnapshot) -> None:
        self.saved = evidence_set


@dataclass
class FakeUnitOfWork:
    """让单元测试复用与生产一致的规划、证据和索引端口。"""

    planning: FakePlanningRepository
    evidence: FakeEvidenceRepository
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


def request_context() -> RequestContext:
    return RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=WORKSPACE_ID,
        trace=TRACE,
        authentication_method="browser_session",
    )


def field_registry() -> FieldPolicyRegistry:
    return FieldPolicyRegistry(
        1,
        1,
        (
            FieldRule("chunk", "content", "INTERNAL"),
            FieldRule("chunk", "source_position", "INTERNAL"),
        ),
    )


def run_input(*, reranker_version: str = "synthetic-reranker-v1") -> RetrievalRunInput:
    return RetrievalRunInput(
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        requested_by_account_id=ACCOUNT_ID,
        runtime_config_version_id=UUID(int=11),
        status="queued",
        query="差旅报销期限",
        embedding_model_version="deterministic-hash-1024-v1",
        retrieval_strategy_version="hybrid-rrf-v1",
        tokenizer_version="cjk-bigram-v1",
        reranker_model_version=reranker_version,
        source_ranking_version="source-priority-v1",
    )


def stored_chunk(
    *,
    chunk_id: UUID = CHUNK_ID,
    document_id: UUID = DOCUMENT_ID,
    version_id: UUID = VERSION_ID,
    content: str = "差旅报销应在三十天内提交。",
) -> StoredChunk:
    return StoredChunk(
        chunk_id=chunk_id,
        workspace_id=WORKSPACE_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        document_id=document_id,
        document_version_id=version_id,
        index_version_id=INDEX_ID,
        sequence_no=1,
        content=content,
        content_hash=(f"{chunk_id.int:x}"[-1:] or "a") * 64,
        source_position={"page_number": 1, "synthetic": True},
        visibility="workspace",
        security_level="INTERNAL",
    )


def candidate(
    chunk: StoredChunk,
    *,
    rank: int,
    score: float,
    keyword_hits: int = 1,
    vector_hits: int = 1,
) -> RetrievalCandidateSnapshot:
    return RetrievalCandidateSnapshot(
        rank=rank,
        chunk_id=chunk.chunk_id,
        index_version_id=chunk.index_version_id,
        knowledge_base_id=chunk.knowledge_base_id,
        document_id=chunk.document_id,
        document_version_id=chunk.document_version_id,
        content_hash=chunk.content_hash,
        source_position=chunk.source_position,
        score=score,
        query_hit_count=1,
        keyword_hit_count=keyword_hits,
        vector_hit_count=vector_hits,
    )


def plan(
    *candidates: RetrievalCandidateSnapshot, classification: str = "knowledge"
) -> RetrievalPlanSnapshot:
    if classification not in {"exact_lookup", "summary", "comparison", "knowledge"}:
        raise ValueError("测试查询分类无效")
    return RetrievalPlanSnapshot(
        retrieval_plan_id=PLAN_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        requested_by_account_id=ACCOUNT_ID,
        original_query_hash="a" * 64,
        classification=classification,  # type: ignore[arg-type]
        strategy_version="hybrid-rrf-v1",
        policy_decision_id=UUID(int=10),
        policy_version=1,
        maximum_security_level="INTERNAL",
        field_mask=frozenset(),
        embedding_model_version="deterministic-hash-1024-v1",
        tokenizer_version="cjk-bigram-v1",
        budget=RetrievalPlannerBudget(),
        variants=(QueryVariant(1, "original", "差旅报销期限", "b" * 64),),
        candidates=tuple(candidates),
        search_operation_count=2,
        keyword_candidate_count=len(candidates),
        vector_candidate_count=len(candidates),
        created_at=NOW,
        completed_at=NOW,
        duration_ms=10,
    )


def scope() -> AuthorizedSearchScope:
    return AuthorizedSearchScope(
        workspace_id=WORKSPACE_ID,
        index_version_ids=frozenset({INDEX_ID}),
        knowledge_base_ids=None,
        document_ids=None,
        department_ids=frozenset(),
        visibilities=frozenset({"workspace"}),
        security_levels=frozenset({"INTERNAL"}),
    )


def source(
    chunk: StoredChunk,
    snapshot: RetrievalCandidateSnapshot,
    *,
    title: str = "差旅制度",
    source_kind: str = "upload",
) -> EvidenceCandidateSource:
    if source_kind not in {"manual", "upload", "web", "data_source"}:
        raise ValueError("测试来源类型无效")
    return EvidenceCandidateSource(
        candidate=snapshot,
        chunk=chunk,
        document_title=title,
        source_kind=source_kind,  # type: ignore[arg-type]
        source_name="合成制度来源",
        captured_at=NOW,
        published_at=NOW,
    )


def harness(
    chunks: tuple[StoredChunk, ...],
    retrieval_plan: RetrievalPlanSnapshot,
    sources: dict[UUID, EvidenceCandidateSource],
) -> FakeUnitOfWork:
    return FakeUnitOfWork(
        FakePlanningRepository(run_input(), retrieval_plan, scope()),
        FakeEvidenceRepository(sources),
        FakeSearchIndex(chunks),
    )


def test_deterministic_reranker_prefers_query_coverage() -> None:
    reranker = DeterministicLexicalReranker()

    scores = reranker.score(
        "差旅报销期限",
        ("差旅报销期限为三十天", "办公用品采购流程"),
    )

    assert scores[0] > scores[1]
    assert reranker.score("差旅报销期限", ("差旅报销期限为三十天",)) == (scores[0],)


def test_fastpass_still_reads_and_verifies_citation() -> None:
    chunk = stored_chunk()
    snapshot = candidate(chunk, rank=1, score=0.05)
    unit_of_work = harness((chunk,), plan(snapshot), {chunk.chunk_id: source(chunk, snapshot)})
    reranker = FixedReranker((0.1,))

    result = RetrievalEvidenceService(
        unit_of_work,
        AllowPolicy(),
        field_registry(),
        reranker,
    ).prepare(request_context(), RUN_ID)

    assert result.status == "sufficient"
    assert result.fastpass_used is True
    assert result.reranker_used is False
    assert result.items[0].quote == "差旅报销应在三十天内提交。"
    assert result.items[0].content_hash == chunk.content_hash
    assert reranker.calls == []
    assert unit_of_work.commits == 1


def test_completed_run_can_only_reauthorize_existing_evidence() -> None:
    chunk = stored_chunk()
    snapshot = candidate(chunk, rank=1, score=0.05)
    unit_of_work = harness((chunk,), plan(snapshot), {chunk.chunk_id: source(chunk, snapshot)})
    service = RetrievalEvidenceService(
        unit_of_work,
        AllowPolicy(),
        field_registry(),
        FixedReranker((0.1,)),
    )
    existing = service.prepare(request_context(), RUN_ID)

    # 来源接口在 Run 完成后只复核首次证据；删除既有证据不能触发补生成。
    unit_of_work.planning.run = replace(unit_of_work.planning.run, status="completed")
    unit_of_work.evidence.existing = existing
    assert service.prepare(request_context(), RUN_ID) == existing

    unit_of_work.evidence.existing = None
    with pytest.raises(RetrievalScopeDeniedError):
        service.prepare(request_context(), RUN_ID)


def test_reranker_and_source_policy_control_final_order() -> None:
    first = stored_chunk(content="普通流程说明")
    second = stored_chunk(
        chunk_id=UUID(int=20),
        document_id=UUID(int=21),
        version_id=UUID(int=22),
        content="差旅报销期限为三十天。",
    )
    first_snapshot = candidate(first, rank=1, score=0.05, vector_hits=0)
    second_snapshot = candidate(second, rank=2, score=0.049, vector_hits=0)
    retrieval_plan = plan(first_snapshot, second_snapshot)
    unit_of_work = harness(
        (first, second),
        retrieval_plan,
        {
            first.chunk_id: source(first, first_snapshot, source_kind="manual"),
            second.chunk_id: source(second, second_snapshot, source_kind="web"),
        },
    )
    reranker = FixedReranker((0.1, 0.95))

    result = RetrievalEvidenceService(
        unit_of_work,
        AllowPolicy(),
        field_registry(),
        reranker,
    ).prepare(request_context(), RUN_ID)

    assert result.reranker_used is True
    assert result.items[0].chunk_id == second.chunk_id
    assert reranker.calls == [("差旅报销期限", (first.content, second.content))]


def test_revoked_or_expired_candidates_degrade_without_reading_body() -> None:
    chunk = stored_chunk()
    snapshot = candidate(chunk, rank=1, score=0.05)
    unit_of_work = harness((chunk,), plan(snapshot), {})

    result = RetrievalEvidenceService(
        unit_of_work,
        AllowPolicy(),
        field_registry(),
        FixedReranker(()),
    ).prepare(request_context(), RUN_ID)

    assert result.status == "uncertain"
    assert result.degradation_reason == "no_current_evidence"
    assert result.items == ()
    assert result.rejected_candidate_count == 1


def test_conflicting_current_sources_are_marked_and_degraded() -> None:
    first = stored_chunk(content="差旅制度规定应在 30 天内提交。")
    second = stored_chunk(
        chunk_id=UUID(int=30),
        document_id=UUID(int=31),
        version_id=UUID(int=32),
        content="差旅制度规定应在 90 天内提交。",
    )
    first_snapshot = candidate(first, rank=1, score=0.05, vector_hits=0)
    second_snapshot = candidate(second, rank=2, score=0.049, vector_hits=0)
    unit_of_work = harness(
        (first, second),
        plan(first_snapshot, second_snapshot),
        {
            first.chunk_id: source(first, first_snapshot, title="差旅制度"),
            second.chunk_id: source(second, second_snapshot, title="差旅制度"),
        },
    )

    result = RetrievalEvidenceService(
        unit_of_work,
        AllowPolicy(),
        field_registry(),
        FixedReranker((0.9, 0.9)),
    ).prepare(request_context(), RUN_ID)

    assert result.status == "uncertain"
    assert result.degradation_reason == "conflicting_evidence"
    assert result.conflict_count == 2
    assert all(item.conflict_detected for item in result.items)


def test_comparison_requires_two_current_documents() -> None:
    chunk = stored_chunk()
    snapshot = candidate(chunk, rank=1, score=0.05)
    unit_of_work = harness(
        (chunk,),
        plan(snapshot, classification="comparison"),
        {chunk.chunk_id: source(chunk, snapshot)},
    )

    result = RetrievalEvidenceService(
        unit_of_work,
        AllowPolicy(),
        field_registry(),
        FixedReranker((0.9,)),
        budget=EvidenceProcessingBudget(fastpass_score_ratio=2),
    ).prepare(request_context(), RUN_ID)

    assert result.status == "uncertain"
    assert result.degradation_reason == "insufficient_sources"


def test_frozen_reranker_version_mismatch_fails_closed() -> None:
    chunk = stored_chunk()
    snapshot = candidate(chunk, rank=1, score=0.05)
    unit_of_work = harness((chunk,), plan(snapshot), {chunk.chunk_id: source(chunk, snapshot)})
    unit_of_work.planning.run = run_input(reranker_version="other-reranker-v1")

    with pytest.raises(RetrievalConfigurationError):
        RetrievalEvidenceService(
            unit_of_work,
            AllowPolicy(),
            field_registry(),
            FixedReranker((0.9,)),
        ).prepare(request_context(), RUN_ID)
