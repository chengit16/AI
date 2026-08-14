"""编排文档解析、结构化切片并输出可索引入库结果。"""

from ai_platform_worker.modules.ingestion.application.chunking import StructuralChunker
from ai_platform_worker.modules.ingestion.domain.documents import (
    IngestionRequest,
    IngestionResult,
    ParsedDocument,
    ParseRequest,
    ParserRouter,
)
from ai_platform_worker.modules.ingestion.domain.errors import IngestionError


class ParseDocument:
    """组合解析结果、分块产物和最终内容摘要。"""

    def __init__(self, parser_router: ParserRouter) -> None:
        self._parser_router = parser_router

    def execute(self, request: ParseRequest, content: bytes) -> ParsedDocument:
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

        return parsed_document


class IngestDocument:
    """编排对象读取、摘要校验、文档解析、分块和产物写入。"""

    def __init__(self, parser_router: ParserRouter, chunker: StructuralChunker) -> None:
        self._parser = ParseDocument(parser_router)
        self._chunker = chunker

    def execute(self, request: IngestionRequest, content: bytes) -> IngestionResult:
        parsed_document = self._parser.execute(
            ParseRequest(
                file_name=request.file_name,
                declared_media_type=request.declared_media_type,
                limits=request.limits,
            ),
            content,
        )
        chunks = self._chunker.chunk(parsed_document, request.identity, request.limits)
        if not chunks:
            raise IngestionError(
                "INGESTION_EMPTY_CONTENT",
                "文档没有生成可索引 Chunk",
                retryable=False,
            )
        return IngestionResult(parsed_document=parsed_document, chunks=chunks)
