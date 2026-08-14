"""向 API 检索模块公开共享的中文关键词切词规则。"""

from ai_platform_backend.indexing.tokenization import (
    TOKENIZER_VERSION,
    keyword_document,
    keyword_query,
    tokenize_for_search,
)

__all__ = [
    "TOKENIZER_VERSION",
    "keyword_document",
    "keyword_query",
    "tokenize_for_search",
]
