"""保留 Worker 旧导入路径，实际实现由共享索引包统一提供。"""

from ai_platform_backend.indexing.embeddings import DeterministicHashEmbeddingAdapter

__all__ = ["DeterministicHashEmbeddingAdapter"]
