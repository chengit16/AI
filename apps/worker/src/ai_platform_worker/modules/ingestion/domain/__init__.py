"""文档解析结果、结构化块、错误和任务端口的公开入口。"""

from ai_platform_worker.modules.ingestion.domain.documents import (
    Chunk,
    ChunkSourcePosition,
    DocumentIdentity,
    IngestionLimits,
    IngestionRequest,
    IngestionResult,
    ParsedBlock,
    ParsedDocument,
    SourcePosition,
)
from ai_platform_worker.modules.ingestion.domain.errors import IngestionError

__all__ = [
    "Chunk",
    "ChunkSourcePosition",
    "DocumentIdentity",
    "IngestionError",
    "IngestionLimits",
    "IngestionRequest",
    "IngestionResult",
    "ParsedBlock",
    "ParsedDocument",
    "SourcePosition",
]
