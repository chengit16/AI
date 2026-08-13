from ai_platform_worker.modules.ingestion.application.chunking import StructuralChunker
from ai_platform_worker.modules.ingestion.domain.documents import (
    IngestionRequest,
    IngestionResult,
    ParserRouter,
)
from ai_platform_worker.modules.ingestion.domain.errors import IngestionError


class IngestDocument:
    def __init__(self, parser_router: ParserRouter, chunker: StructuralChunker) -> None:
        self._parser_router = parser_router
        self._chunker = chunker

    def execute(self, request: IngestionRequest, content: bytes) -> IngestionResult:
        if not content:
            raise IngestionError(
                "INGESTION_EMPTY_FILE",
                "文档内容为空",
                retryable=False,
            )
        if len(content) > request.limits.max_file_size_bytes:
            raise IngestionError(
                "INGESTION_FILE_TOO_LARGE",
                "文档超过当前配置的大小限制",
                retryable=False,
            )

        parser = self._parser_router.parser_for(request.file_name)
        parsed_document = parser.parse(
            content=content,
            file_name=request.file_name,
            declared_media_type=request.declared_media_type,
        )
        if parsed_document.page_count > request.limits.max_page_count:
            raise IngestionError(
                "INGESTION_PAGE_LIMIT_EXCEEDED",
                "文档超过当前配置的页数限制",
                retryable=False,
            )
        if not any(block.text.strip() for block in parsed_document.blocks):
            raise IngestionError(
                "INGESTION_EMPTY_CONTENT",
                "解析完成但没有得到可索引文本",
                retryable=False,
            )

        chunks = self._chunker.chunk(parsed_document, request.identity, request.limits)
        if not chunks:
            raise IngestionError(
                "INGESTION_EMPTY_CONTENT",
                "文档没有生成可索引 Chunk",
                retryable=False,
            )
        return IngestionResult(parsed_document=parsed_document, chunks=chunks)
