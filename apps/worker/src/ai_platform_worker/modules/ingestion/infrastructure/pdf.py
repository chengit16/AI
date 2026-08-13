from io import BytesIO

import pdfplumber

from ai_platform_worker.modules.ingestion.domain.documents import (
    DocumentParser,
    ParsedBlock,
    ParsedDocument,
    SourcePosition,
)
from ai_platform_worker.modules.ingestion.domain.errors import IngestionError


def normalized_cell(value: str | None) -> str:
    return " ".join((value or "").split())


def without_flattened_table_duplicates(
    blocks: tuple[ParsedBlock, ...],
    table_blocks: list[ParsedBlock],
) -> tuple[ParsedBlock, ...]:
    flattened_by_page: dict[int | None, list[str]] = {}
    for block in table_blocks:
        flattened = normalized_cell(block.text.replace("|", " "))
        flattened_by_page.setdefault(block.source_position.page_number, []).append(flattened)
    return tuple(
        block
        for block in blocks
        if block.block_type != "paragraph"
        or not any(
            normalized_cell(block.text) in flattened
            for flattened in flattened_by_page.get(block.source_position.page_number, [])
        )
    )


class PdfDocumentParser:
    """由 Tika 解析正文和 OCR，再用 pdfplumber 补充基础线框表格结构。"""

    def __init__(self, base_parser: DocumentParser) -> None:
        self._base_parser = base_parser

    def parse(
        self,
        *,
        content: bytes,
        file_name: str,
        declared_media_type: str | None,
    ) -> ParsedDocument:
        document = self._base_parser.parse(
            content=content,
            file_name=file_name,
            declared_media_type=declared_media_type,
        )
        table_blocks: list[ParsedBlock] = []
        try:
            with pdfplumber.open(BytesIO(content)) as pdf:
                for page_number, page in enumerate(pdf.pages, start=1):
                    for table in page.extract_tables():
                        rows = [
                            " | ".join(normalized_cell(cell) for cell in row)
                            for row in table
                            if any(normalized_cell(cell) for cell in row)
                        ]
                        if rows:
                            table_blocks.append(
                                ParsedBlock(
                                    block_type="table",
                                    text="\n".join(rows),
                                    source_position=SourcePosition(page_number=page_number),
                                )
                            )
        except Exception as error:
            # PDF 专用解析器的异常类型并不稳定，对任务层统一为可定位且不可盲目重试的错误。
            raise IngestionError(
                "INGESTION_PARSE_FAILED",
                "PDF 基础表格解析失败",
                retryable=False,
            ) from error

        if not table_blocks:
            return document
        metadata = dict(document.metadata)
        metadata["pdf:tableCount"] = str(len(table_blocks))
        return ParsedDocument(
            media_type=document.media_type,
            parser_name=f"{document.parser_name}+pdfplumber",
            page_count=document.page_count,
            used_ocr=document.used_ocr,
            blocks=without_flattened_table_duplicates(document.blocks, table_blocks)
            + tuple(table_blocks),
            metadata=metadata,
        )
