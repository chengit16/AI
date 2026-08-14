"""按真实媒体类型路由 Markdown、DOCX、PDF 和 Tika 解析器。"""

import re
from pathlib import Path

from ai_platform_worker.modules.ingestion.domain.documents import (
    ChineseOcrAdapter,
    DocumentParser,
    ParsedBlock,
    ParsedDocument,
    SourcePosition,
)
from ai_platform_worker.modules.ingestion.domain.errors import IngestionError

HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def decode_utf8(content: bytes) -> str:
    """处理解码UTF-8，在基础设施边界维持稳定领域对象映射。"""

    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise IngestionError(
            "INGESTION_PARSE_FAILED",
            "文本文件必须使用 UTF-8 编码",
            retryable=False,
        ) from error


class PlainTextParser:
    """封装纯文本文本解析器执行边界，并将失败转换为可追踪的稳定结果。"""

    def parse(
        self,
        *,
        content: bytes,
        file_name: str,
        declared_media_type: str | None,
    ) -> ParsedDocument:
        """以严格 UTF-8 解码纯文本并返回规范段落，非法编码时拒绝入库。"""

        blocks = tuple(
            ParsedBlock(
                block_type="paragraph",
                text=line.strip(),
                source_position=SourcePosition(line_start=index, line_end=index),
            )
            for index, line in enumerate(decode_utf8(content).splitlines(), start=1)
            if line.strip()
        )
        return ParsedDocument(
            media_type=declared_media_type or "text/plain",
            parser_name="plain-text-v1",
            page_count=1,
            used_ocr=False,
            blocks=blocks,
            metadata={"file_name": file_name},
        )


class MarkdownParser:
    """封装Markdown解析器执行边界，并将失败转换为可追踪的稳定结果。"""

    def parse(
        self,
        *,
        content: bytes,
        file_name: str,
        declared_media_type: str | None,
    ) -> ParsedDocument:
        """解析 Markdown 结构并保留标题层级，禁止执行其中的嵌入内容。"""

        # 1. 严格解码文本后按行扫描，解析器不执行链接、HTML 或代码块中的内容。
        lines = decode_utf8(content).splitlines()
        blocks: list[ParsedBlock] = []
        index = 0
        while index < len(lines):
            line = lines[index].strip()
            line_number = index + 1
            if not line:
                index += 1
                continue
            # 2. 标题和表格优先形成结构块，其余连续文本合并为带行号的段落。
            heading = HEADING_PATTERN.match(line)
            if heading:
                blocks.append(
                    ParsedBlock(
                        block_type="heading",
                        text=heading.group(2),
                        source_position=SourcePosition(
                            line_start=line_number,
                            line_end=line_number,
                        ),
                    )
                )
                index += 1
                continue
            if line.startswith("|"):
                table_lines: list[str] = []
                start = line_number
                while index < len(lines) and lines[index].strip().startswith("|"):
                    table_lines.append(lines[index].strip())
                    index += 1
                blocks.append(
                    ParsedBlock(
                        block_type="table",
                        text="\n".join(table_lines),
                        source_position=SourcePosition(line_start=start, line_end=index),
                    )
                )
                continue
            paragraph_lines = [line]
            start = line_number
            index += 1
            while index < len(lines):
                candidate = lines[index].strip()
                if not candidate or candidate.startswith("|") or HEADING_PATTERN.match(candidate):
                    break
                paragraph_lines.append(candidate)
                index += 1
            blocks.append(
                ParsedBlock(
                    block_type="paragraph",
                    text=" ".join(paragraph_lines),
                    source_position=SourcePosition(line_start=start, line_end=index),
                )
            )
        # 3. 返回稳定顺序和来源位置，后续分块不再重新解释 Markdown 语法。
        return ParsedDocument(
            media_type=declared_media_type or "text/markdown",
            parser_name="markdown-structural-v1",
            page_count=1,
            used_ocr=False,
            blocks=tuple(blocks),
            metadata={"file_name": file_name},
        )


class DefaultParserRouter:
    """按真实媒体类型选择纯文本、Markdown、PDF 或 Tika 解析器。"""

    def __init__(
        self,
        tika_parser: DocumentParser,
        pdf_parser: DocumentParser | None = None,
        ocr_adapter: ChineseOcrAdapter | None = None,
    ) -> None:
        self._tika_parser = tika_parser
        self._pdf_parser = pdf_parser or tika_parser
        self._ocr_adapter = ocr_adapter or tika_parser
        self._plain_text_parser = PlainTextParser()
        self._markdown_parser = MarkdownParser()

    def parser_for(self, file_name: str) -> DocumentParser:
        extension = Path(file_name).suffix.lower()
        if extension == ".txt":
            return self._plain_text_parser
        if extension in {".md", ".markdown"}:
            return self._markdown_parser
        if extension == ".pdf":
            return self._pdf_parser
        if extension in IMAGE_EXTENSIONS:
            return self._ocr_adapter
        if extension == ".docx":
            return self._tika_parser
        raise IngestionError(
            "INGESTION_UNSUPPORTED_FORMAT",
            f"不支持的文档格式: {extension or '无扩展名'}",
            retryable=False,
        )
