from dataclasses import dataclass, field
from uuid import UUID

import pytest
from ai_platform_api.modules.retrieval.application.citations import CitationService
from ai_platform_api.modules.retrieval.application.reader import (
    AuthorizedDocumentReader,
    ReaderBudget,
)
from ai_platform_api.modules.retrieval.application.search import (
    HybridRetriever,
    reciprocal_rank_fusion,
)
from ai_platform_api.modules.retrieval.application.tokenization import (
    TOKENIZER_VERSION,
    keyword_document,
    keyword_query,
    tokenize_for_search,
)
from ai_platform_api.modules.retrieval.domain.errors import (
    CitationInvalidError,
    RetrievalConfigurationError,
    RetrievalScopeDeniedError,
)
from ai_platform_api.modules.retrieval.domain.models import (
    AuthorizedSearchScope,
    ChannelCandidate,
    RetrievalBudget,
    StoredChunk,
)

WORKSPACE_ID = UUID(int=1)
KNOWLEDGE_BASE_ID = UUID(int=2)
DOCUMENT_ID = UUID(int=3)
DOCUMENT_VERSION_ID = UUID(int=4)
INDEX_VERSION_ID = UUID(int=5)
DEPARTMENT_ID = UUID(int=6)


def scope(*, field_mask: frozenset[str] = frozenset()) -> AuthorizedSearchScope:
    return AuthorizedSearchScope(
        workspace_id=WORKSPACE_ID,
        index_version_ids=frozenset({INDEX_VERSION_ID}),
        knowledge_base_ids=frozenset({KNOWLEDGE_BASE_ID}),
        document_ids=frozenset({DOCUMENT_ID}),
        department_ids=frozenset({DEPARTMENT_ID}),
        visibilities=frozenset({"departments", "workspace"}),
        security_levels=frozenset({"INTERNAL"}),
        field_mask=field_mask,
    )


def stored_chunk(sequence_no: int, content: str | None = None) -> StoredChunk:
    return StoredChunk(
        chunk_id=UUID(int=100 + sequence_no),
        workspace_id=WORKSPACE_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        document_id=DOCUMENT_ID,
        document_version_id=DOCUMENT_VERSION_ID,
        index_version_id=INDEX_VERSION_ID,
        sequence_no=sequence_no,
        content=content or f"合成证据 {sequence_no}",
        content_hash=f"hash-{sequence_no}",
        source_position={"page_number": sequence_no},
    )


@dataclass
class FakeSearchIndex:
    keyword_candidates: tuple[ChannelCandidate, ...] = ()
    vector_candidates: tuple[ChannelCandidate, ...] = ()
    chunks: tuple[StoredChunk, ...] = ()

    def keyword_search(
        self,
        requested_scope: AuthorizedSearchScope,
        keyword_query: str,
        limit: int,
    ) -> tuple[ChannelCandidate, ...]:
        del requested_scope, keyword_query
        return self.keyword_candidates[:limit]

    def vector_search(
        self,
        requested_scope: AuthorizedSearchScope,
        embedding: tuple[float, ...],
        limit: int,
    ) -> tuple[ChannelCandidate, ...]:
        del requested_scope, embedding
        return self.vector_candidates[:limit]

    def read_document_range(
        self,
        requested_scope: AuthorizedSearchScope,
        document_version_id: UUID,
        sequence_start: int,
        sequence_end: int,
    ) -> tuple[StoredChunk, ...]:
        del requested_scope
        return tuple(
            chunk
            for chunk in self.chunks
            if chunk.document_version_id == document_version_id
            and sequence_start <= chunk.sequence_no <= sequence_end
        )

    def get_chunk(
        self,
        requested_scope: AuthorizedSearchScope,
        chunk_id: UUID,
    ) -> StoredChunk | None:
        del requested_scope
        return next((chunk for chunk in self.chunks if chunk.chunk_id == chunk_id), None)


@dataclass
class FakeEmbeddingProvider:
    model_version: str = "synthetic-embedding-v1"
    dimension: int = 1024
    output_dimension: int = 1024

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(
            tuple(1.0 if index == 0 else 0.0 for index in range(self.output_dimension))
            for _ in texts
        )


@dataclass
class FakeReranker:
    scores: tuple[float, ...]
    model_version: str = "synthetic-reranker-v1"
    calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    def score(self, query: str, passages: tuple[str, ...]) -> tuple[float, ...]:
        self.calls.append((query, passages))
        return self.scores


def candidate(
    chunk: StoredChunk,
    channel: str,
    rank: int,
    score: float,
) -> ChannelCandidate:
    if channel not in {"keyword", "vector"}:
        raise ValueError("测试候选通道无效")
    return ChannelCandidate(
        chunk=chunk,
        channel="keyword" if channel == "keyword" else "vector",
        rank=rank,
        score=score,
    )


def budget(*, fastpass_vector_score: float = 0.82) -> RetrievalBudget:
    return RetrievalBudget(
        per_channel_candidates=4,
        rerank_candidates=3,
        final_candidates=2,
        fastpass_vector_score=fastpass_vector_score,
    )


def test_cjk_tokenization_is_deterministic_and_versioned() -> None:
    expected = ("差", "旅", "报", "销", "差旅", "旅报", "报销", "a12")

    assert tokenize_for_search("差旅报销 A12 差旅") == expected
    assert keyword_document("差旅报销 A12 差旅") == " ".join(expected)
    assert keyword_query("差旅报销 A12 差旅") == " | ".join(expected)
    assert TOKENIZER_VERSION == "cjk-bigram-v1"


def test_rrf_deduplicates_chunks_and_duplicate_channel_rows() -> None:
    first = stored_chunk(1)
    second = stored_chunk(2)
    merged = reciprocal_rank_fusion(
        (
            candidate(first, "keyword", 1, 0.9),
            candidate(first, "keyword", 2, 0.8),
            candidate(second, "keyword", 2, 0.8),
        ),
        (candidate(first, "vector", 2, 0.7),),
        constant=60,
    )

    assert [item.chunk.chunk_id for item in merged] == [first.chunk_id, second.chunk_id]
    assert merged[0].hybrid_score == pytest.approx(1 / 61 + 1 / 62)
    assert merged[0].keyword_rank == 1
    assert merged[0].vector_rank == 2


def test_fastpass_skips_reranker_for_strong_dual_channel_candidate() -> None:
    evidence_chunk = stored_chunk(1)
    index = FakeSearchIndex(
        keyword_candidates=(candidate(evidence_chunk, "keyword", 1, 0.8),),
        vector_candidates=(candidate(evidence_chunk, "vector", 1, 0.95),),
    )
    reranker = FakeReranker((0.1,))

    result = HybridRetriever(index, FakeEmbeddingProvider(), reranker).search(
        "差旅报销期限",
        scope(),
        budget(),
    )

    assert result.reranker_used is False
    assert result.reranker_model_version is None
    assert reranker.calls == []
    assert result.evidence[0].chunk == evidence_chunk


def test_reranker_controls_final_order_when_fastpass_does_not_match() -> None:
    first = stored_chunk(1)
    second = stored_chunk(2)
    index = FakeSearchIndex(
        keyword_candidates=(
            candidate(first, "keyword", 1, 0.9),
            candidate(second, "keyword", 2, 0.8),
        ),
        vector_candidates=(candidate(first, "vector", 1, 0.4),),
    )
    reranker = FakeReranker((0.1, 0.95))

    result = HybridRetriever(index, FakeEmbeddingProvider(), reranker).search(
        "差旅报销期限",
        scope(),
        budget(),
    )

    assert result.reranker_used is True
    assert result.reranker_model_version == reranker.model_version
    assert [item.chunk for item in result.evidence] == [second, first]
    assert reranker.calls == [("差旅报销期限", (first.content, second.content))]


@pytest.mark.parametrize(
    ("provider", "reranker"),
    [
        (FakeEmbeddingProvider(dimension=768), FakeReranker(())),
        (FakeEmbeddingProvider(output_dimension=768), FakeReranker(())),
        (FakeEmbeddingProvider(), FakeReranker(())),
    ],
)
def test_invalid_model_adapter_contract_fails_closed(
    provider: FakeEmbeddingProvider,
    reranker: FakeReranker,
) -> None:
    index = FakeSearchIndex(
        keyword_candidates=(candidate(stored_chunk(1), "keyword", 1, 0.8),),
    )

    with pytest.raises(RetrievalConfigurationError):
        HybridRetriever(index, provider, reranker).search(
            "差旅报销期限",
            scope(),
            budget(),
        )


def test_empty_candidates_do_not_claim_reranker_usage() -> None:
    reranker = FakeReranker(())

    result = HybridRetriever(FakeSearchIndex(), FakeEmbeddingProvider(), reranker).search(
        "不存在的制度",
        scope(),
        budget(),
    )

    assert result.evidence == ()
    assert result.reranker_used is False
    assert result.reranker_model_version is None
    assert reranker.calls == []


def test_content_field_mask_blocks_search_reader_and_citation() -> None:
    evidence_chunk = stored_chunk(1)
    index = FakeSearchIndex(chunks=(evidence_chunk,))
    masked_scope = scope(field_mask=frozenset({"content"}))

    with pytest.raises(RetrievalScopeDeniedError):
        HybridRetriever(index, FakeEmbeddingProvider(), FakeReranker(())).search(
            "差旅报销期限",
            masked_scope,
            budget(),
        )
    with pytest.raises(RetrievalScopeDeniedError):
        AuthorizedDocumentReader(index).read(
            masked_scope,
            DOCUMENT_VERSION_ID,
            1,
            ReaderBudget(surrounding_chunks=1, max_chunks=2, max_characters=100),
        )
    with pytest.raises(RetrievalScopeDeniedError):
        CitationService(index).issue(masked_scope, evidence_chunk.chunk_id, "合成证据")


def test_reader_preserves_center_chunk_and_respects_budgets() -> None:
    chunks = (
        stored_chunk(1, "前置内容"),
        stored_chunk(2, "必须保留的中心证据"),
        stored_chunk(3, "后置内容"),
    )
    reader = AuthorizedDocumentReader(FakeSearchIndex(chunks=chunks))

    result = reader.read(
        scope(),
        DOCUMENT_VERSION_ID,
        center_sequence_no=2,
        budget=ReaderBudget(surrounding_chunks=1, max_chunks=2, max_characters=14),
    )

    assert [item.sequence_no for item in result] == [1, 2]
    assert sum(len(item.content) for item in result) <= 14


def test_reader_returns_nothing_when_center_chunk_is_not_authorized() -> None:
    reader = AuthorizedDocumentReader(FakeSearchIndex(chunks=(stored_chunk(1), stored_chunk(3))))

    result = reader.read(
        scope(),
        DOCUMENT_VERSION_ID,
        center_sequence_no=2,
        budget=ReaderBudget(surrounding_chunks=1, max_chunks=3, max_characters=100),
    )

    assert result == ()


def test_citation_must_match_authorized_versioned_source_text() -> None:
    evidence_chunk = stored_chunk(1, "差旅报销应在三十天内提交。")
    service = CitationService(FakeSearchIndex(chunks=(evidence_chunk,)))

    citation = service.issue(scope(), evidence_chunk.chunk_id, "  差旅报销应在三十天内提交。  ")

    assert citation.document_version_id == DOCUMENT_VERSION_ID
    assert citation.index_version_id == INDEX_VERSION_ID
    assert citation.quote == "差旅报销应在三十天内提交。"
    with pytest.raises(CitationInvalidError):
        service.issue(scope(), evidence_chunk.chunk_id, "报销可以在九十天内提交")
    with pytest.raises(CitationInvalidError):
        service.issue(scope(), UUID(int=999), "差旅报销")


def test_source_position_mask_is_applied_before_retrieval_output() -> None:
    chunk = stored_chunk(1, "差旅报销应在三十天内提交。")
    index = FakeSearchIndex(
        chunks=(chunk,),
        keyword_candidates=(candidate(chunk, "keyword", 1, 0.9),),
        vector_candidates=(candidate(chunk, "vector", 1, 0.95),),
    )
    masked_scope = scope(field_mask=frozenset({"source_position"}))

    result = HybridRetriever(index, FakeEmbeddingProvider(), FakeReranker(())).search(
        "差旅报销期限",
        masked_scope,
        budget(),
    )
    citation = CitationService(index).issue(masked_scope, chunk.chunk_id, "差旅报销")
    read = AuthorizedDocumentReader(index).read(
        masked_scope,
        DOCUMENT_VERSION_ID,
        1,
        ReaderBudget(surrounding_chunks=0, max_chunks=1, max_characters=100),
    )

    assert result.evidence[0].chunk.source_position == {}
    assert citation.source_position == {}
    assert read[0].source_position == {}


def test_private_visibility_requires_explicit_document_scope() -> None:
    with pytest.raises(ValueError, match="私有文档"):
        AuthorizedSearchScope(
            workspace_id=WORKSPACE_ID,
            index_version_ids=frozenset({INDEX_VERSION_ID}),
            knowledge_base_ids=None,
            document_ids=None,
            department_ids=frozenset(),
            visibilities=frozenset({"private"}),
            security_levels=frozenset({"INTERNAL"}),
        )
