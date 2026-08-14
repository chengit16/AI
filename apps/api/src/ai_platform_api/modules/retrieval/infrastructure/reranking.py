"""提供不联网、可复现且可替换的本地确定性重排 Adapter。"""

from ai_platform_backend.indexing.tokenization import tokenize_for_search


class DeterministicLexicalReranker:
    """以查询覆盖率和完整短语命中生成稳定相关性分数，供本地 MVP 使用。"""

    model_version = "deterministic-lexical-reranker-v1"

    def score(self, query: str, passages: tuple[str, ...]) -> tuple[float, ...]:
        """返回零到一的一一对应分数；真实供应商接入后可替换本 Adapter。"""

        query_tokens = frozenset(tokenize_for_search(query))
        normalized_query = " ".join(query.split()).casefold()
        if not query_tokens:
            return tuple(0.0 for _ in passages)
        scores: list[float] = []
        for passage in passages:
            passage_tokens = frozenset(tokenize_for_search(passage))
            coverage = len(query_tokens & passage_tokens) / len(query_tokens)
            phrase_bonus = 0.15 if normalized_query in " ".join(passage.split()).casefold() else 0.0
            scores.append(min(1.0, coverage * 0.85 + phrase_bonus))
        return tuple(scores)
