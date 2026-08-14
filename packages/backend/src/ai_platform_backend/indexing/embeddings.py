"""提供 API 与 Worker 共用的确定性本地 Embedding Adapter。"""

import hashlib
import math

from ai_platform_backend.indexing.tokenization import tokenize_for_search


class DeterministicHashEmbeddingAdapter:
    """为本地功能闭环生成可复现向量，不代表真实语义质量验收。"""

    model_version = "deterministic-hash-1024-v1"
    dimension = 1024

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """按输入顺序返回单位向量，使索引构建与在线查询使用相同算法。"""

        return tuple(self._embed_one(text) for text in texts)

    def _embed_one(self, text: str) -> tuple[float, ...]:
        values = [0.0] * self.dimension
        tokens = tokenize_for_search(text) or tuple(
            character for character in text if character.strip()
        )
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            position = int.from_bytes(digest[:4], "big") % self.dimension
            values[position] += -1.0 if digest[4] & 1 else 1.0
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0:
            values[0] = 1.0
            norm = 1.0
        return tuple(value / norm for value in values)
