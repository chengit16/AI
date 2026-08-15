"""索引版本、构建产物和持久化契约的公开入口。"""

from ai_platform_backend.indexing.domain import (
    BuiltIndexChunk,
    ClaimedIndexVersion,
    EmbeddingAdapter,
    IndexArtifactStorage,
    IndexVersionStore,
)
from ai_platform_backend.indexing.embeddings import DeterministicHashEmbeddingAdapter
from ai_platform_backend.indexing.maintenance import (
    IndexCleanupResult,
    IndexInspectionFinding,
    IndexInspectionReport,
    IndexMaintenanceStore,
    IndexRebuildBatchResult,
    IndexRepairResult,
)

__all__ = [
    "BuiltIndexChunk",
    "ClaimedIndexVersion",
    "DeterministicHashEmbeddingAdapter",
    "EmbeddingAdapter",
    "IndexArtifactStorage",
    "IndexCleanupResult",
    "IndexInspectionFinding",
    "IndexInspectionReport",
    "IndexMaintenanceStore",
    "IndexRebuildBatchResult",
    "IndexRepairResult",
    "IndexVersionStore",
]
