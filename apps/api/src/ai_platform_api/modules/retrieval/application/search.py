"""在模型前执行权限过滤、关键词与向量检索、RRF 和 FastPass。"""

from dataclasses import replace

from ai_platform_api.modules.retrieval.application.tokenization import (
    TOKENIZER_VERSION,
    keyword_query,
)
from ai_platform_api.modules.retrieval.domain.errors import (
    RetrievalConfigurationError,
    RetrievalScopeDeniedError,
)
from ai_platform_api.modules.retrieval.domain.models import (
    AuthorizedSearchScope,
    ChannelCandidate,
    EmbeddingProvider,
    Evidence,
    Reranker,
    RetrievalBudget,
    RetrievalResult,
    SearchCandidate,
    SearchIndex,
)


def reciprocal_rank_fusion(
    keyword_candidates: tuple[ChannelCandidate, ...],
    vector_candidates: tuple[ChannelCandidate, ...],
    constant: int,
) -> tuple[SearchCandidate, ...]:
    """用倒数排名融合合并多通道候选，重复分块只累计排名贡献。"""

    # 1. 同一 Chunk 在每个通道只贡献一次排名分数，重复结果不能抬高融合排名。
    merged: dict[object, SearchCandidate] = {}
    seen_channels: set[tuple[object, str]] = set()
    for candidate in keyword_candidates + vector_candidates:
        channel_key = (candidate.chunk.chunk_id, candidate.channel)
        if channel_key in seen_channels:
            continue
        seen_channels.add(channel_key)
        existing = merged.get(candidate.chunk.chunk_id)
        contribution = 1 / (constant + candidate.rank)
        if existing is None:
            existing = SearchCandidate(
                chunk=candidate.chunk,
                keyword_rank=None,
                vector_rank=None,
                keyword_score=None,
                vector_score=None,
                hybrid_score=0,
            )
        if candidate.channel == "keyword":
            existing = replace(
                existing,
                keyword_rank=candidate.rank,
                keyword_score=candidate.score,
                hybrid_score=existing.hybrid_score + contribution,
            )
        else:
            existing = replace(
                existing,
                vector_rank=candidate.rank,
                vector_score=candidate.score,
                hybrid_score=existing.hybrid_score + contribution,
            )
        merged[candidate.chunk.chunk_id] = existing
    # 2. 按融合分数和稳定 Chunk ID 排序，保证相同输入得到可复现候选顺序。
    return tuple(
        sorted(
            merged.values(),
            key=lambda item: (-item.hybrid_score, str(item.chunk.chunk_id)),
        )
    )


class HybridRetriever:
    """编排授权过滤、关键词/向量召回、融合、重排和预算裁剪。"""

    def __init__(
        self,
        index: SearchIndex,
        embedding_provider: EmbeddingProvider,
        reranker: Reranker,
    ) -> None:
        self._index = index
        self._embedding_provider = embedding_provider
        self._reranker = reranker

    def search(
        self,
        query: str,
        scope: AuthorizedSearchScope,
        budget: RetrievalBudget,
    ) -> RetrievalResult:
        # 1. 在模型和索引调用前确认内容权限、组件版本和规范化查询，空查询直接返回。
        if not scope.content_allowed:
            raise RetrievalScopeDeniedError
        if self._embedding_provider.dimension != 1024:
            raise RetrievalConfigurationError
        normalized_query = " ".join(query.split())
        tokens = keyword_query(normalized_query)
        if not normalized_query or not tokens:
            return RetrievalResult(
                evidence=(),
                reranker_used=False,
                keyword_candidate_count=0,
                vector_candidate_count=0,
                embedding_model_version=self._embedding_provider.model_version,
                reranker_model_version=None,
                tokenizer_version=TOKENIZER_VERSION,
            )

        # 2. 在同一授权范围内执行关键词和向量召回，再以固定预算融合并截断重排池。
        embedded = self._embedding_provider.embed((normalized_query,))
        if len(embedded) != 1 or len(embedded[0]) != self._embedding_provider.dimension:
            raise RetrievalConfigurationError
        keyword_candidates = self._index.keyword_search(
            scope,
            tokens,
            budget.per_channel_candidates,
        )
        vector_candidates = self._index.vector_search(
            scope,
            embedded[0],
            budget.per_channel_candidates,
        )
        merged = reciprocal_rank_fusion(
            keyword_candidates,
            vector_candidates,
            budget.rrf_constant,
        )
        rerank_pool = merged[: budget.rerank_candidates]
        fastpass = bool(
            rerank_pool
            and rerank_pool[0].keyword_rank is not None
            and rerank_pool[0].vector_score is not None
            and rerank_pool[0].vector_score >= budget.fastpass_vector_score
        )
        ranked: tuple[SearchCandidate, ...]
        # 3. FastPass 只跳过重排器，不跳过最终字段遮罩和证据数量预算。
        if not rerank_pool:
            ranked = ()
        elif fastpass:
            ranked = rerank_pool
        else:
            scores = self._reranker.score(
                normalized_query,
                tuple(candidate.chunk.content for candidate in rerank_pool),
            )
            if len(scores) != len(rerank_pool):
                raise RetrievalConfigurationError
            ranked = tuple(
                sorted(
                    (
                        replace(candidate, rerank_score=score)
                        for candidate, score in zip(rerank_pool, scores, strict=True)
                    ),
                    key=lambda item: (
                        item.rerank_score if item.rerank_score is not None else float("-inf"),
                        item.hybrid_score,
                    ),
                    reverse=True,
                )
            )
        evidence = tuple(
            Evidence(
                chunk=candidate.chunk.apply_field_mask(scope.field_mask),
                score=(
                    candidate.rerank_score
                    if candidate.rerank_score is not None
                    else candidate.hybrid_score
                ),
            )
            for candidate in ranked[: budget.final_candidates]
        )
        reranker_used = not fastpass and bool(rerank_pool)
        return RetrievalResult(
            evidence=evidence,
            reranker_used=reranker_used,
            keyword_candidate_count=len(keyword_candidates),
            vector_candidate_count=len(vector_candidates),
            embedding_model_version=self._embedding_provider.model_version,
            reranker_model_version=self._reranker.model_version if reranker_used else None,
            tokenizer_version=TOKENIZER_VERSION,
        )
