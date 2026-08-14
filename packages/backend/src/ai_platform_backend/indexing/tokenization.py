"""提供 API 与 Worker 共用的中英文关键词规范化和切词规则。"""

import re

TOKENIZER_VERSION = "cjk-bigram-v1"
CJK_SEQUENCE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
ALPHANUMERIC = re.compile(r"[a-z0-9]+")


def tokenize_for_search(text: str) -> tuple[str, ...]:
    """处理分词用于搜索，并保持调用方可依赖的稳定返回语义。"""

    normalized = text.lower()
    tokens: list[str] = []
    for sequence in CJK_SEQUENCE.findall(normalized):
        tokens.extend(sequence)
        tokens.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    tokens.extend(ALPHANUMERIC.findall(normalized))
    return tuple(dict.fromkeys(token for token in tokens if token))


def keyword_document(text: str) -> str:
    """处理关键词文档，并保持调用方可依赖的稳定返回语义。"""

    return " ".join(tokenize_for_search(text))


def keyword_query(text: str) -> str:
    # Token 只来自受控字符类，可直接组合为 PostgreSQL simple 配置的 OR 查询。
    """处理关键词查询，并保持调用方可依赖的稳定返回语义。"""

    return " | ".join(tokenize_for_search(text))
