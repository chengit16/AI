from __future__ import annotations

from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from ai_platform_worker.modules.ingestion.domain.documents import (
    BlockType,
    ParsedBlock,
    ParsedDocument,
    SourcePosition,
)
from ai_platform_worker.modules.ingestion.domain.errors import (
    IngestionError,
    IngestionErrorCode,
)

XHTML_NAMESPACE = "{http://www.w3.org/1999/xhtml}"
TIKA_NULL_REFERENCE = b"&#0;"


def local_name(element: ElementTree.Element) -> str:
    return element.tag.removeprefix(XHTML_NAMESPACE)


def normalized_text(element: ElementTree.Element) -> str:
    return " ".join(unescape("".join(element.itertext())).split())


def table_text(element: ElementTree.Element) -> str:
    rows: list[str] = []
    for row in element.iter(f"{XHTML_NAMESPACE}tr"):
        cells = [
            normalized_text(cell)
            for cell in row
            if local_name(cell) in {"td", "th"} and normalized_text(cell)
        ]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def metadata_from(root: ElementTree.Element) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for element in root.iter(f"{XHTML_NAMESPACE}meta"):
        name = element.attrib.get("name")
        content = element.attrib.get("content")
        if name and content:
            metadata[name] = f"{metadata[name]}, {content}" if name in metadata else content
    return metadata


def append_element(
    blocks: list[ParsedBlock],
    element: ElementTree.Element,
    page_number: int | None,
) -> None:
    tag = local_name(element)
    classes = set(element.attrib.get("class", "").split())
    if tag == "table":
        text = table_text(element)
        block_type: BlockType = "table"
    elif "ocr" in classes:
        text = normalized_text(element)
        block_type = "ocr"
    elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        text = normalized_text(element)
        block_type = "heading"
    elif tag == "p":
        text = normalized_text(element)
        block_type = "paragraph"
    else:
        for child in element:
            append_element(blocks, child, page_number)
        return
    if text:
        blocks.append(
            ParsedBlock(
                block_type=block_type,
                text=text,
                source_position=SourcePosition(page_number=page_number),
            )
        )


def parse_xhtml(payload: bytes, fallback_media_type: str) -> ParsedDocument:
    # Tika 3.2.3 会用 XML 禁止的 &#0; 表示空标题，移除该兼容性占位后再严格解析结构。
    payload = payload.replace(TIKA_NULL_REFERENCE, b"")
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise IngestionError(
            "INGESTION_PARSE_FAILED",
            "Tika 返回了无法解析的结构化结果",
            retryable=False,
        ) from error

    metadata = metadata_from(root)
    body = root.find(f"{XHTML_NAMESPACE}body")
    if body is None:
        raise IngestionError(
            "INGESTION_PARSE_FAILED",
            "Tika 结果缺少正文结构",
            retryable=False,
        )
    blocks: list[ParsedBlock] = []
    pages = [
        element
        for element in body
        if local_name(element) == "div" and "page" in element.attrib.get("class", "").split()
    ]
    if pages:
        for page_number, page in enumerate(pages, start=1):
            for element in page:
                append_element(blocks, element, page_number)
    else:
        for element in body:
            append_element(blocks, element, 1)

    parser_name = metadata.get("X-TIKA:Parsed-By", "apache-tika")
    media_type = metadata.get("Content-Type", fallback_media_type).split(";", maxsplit=1)[0]
    declared_pages = metadata.get("xmpTPg:NPages")
    page_count = (
        int(declared_pages) if declared_pages and declared_pages.isdigit() else len(pages) or 1
    )
    return ParsedDocument(
        media_type=media_type,
        parser_name=parser_name,
        page_count=page_count,
        used_ocr=any(block.block_type == "ocr" for block in blocks),
        blocks=tuple(blocks),
        metadata=metadata,
    )


class TikaDocumentParser:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 30,
        ocr_language: str | None = None,
    ) -> None:
        self._endpoint = f"{base_url.rstrip('/')}/tika"
        self._timeout_seconds = timeout_seconds
        self._ocr_language = ocr_language

    def parse(
        self,
        *,
        content: bytes,
        file_name: str,
        declared_media_type: str | None,
    ) -> ParsedDocument:
        safe_file_name = Path(file_name).name.replace('"', "")
        headers = {
            "Accept": "text/html",
            "Content-Type": declared_media_type or "application/octet-stream",
            "Content-Disposition": f'attachment; filename="{safe_file_name}"',
        }
        if self._ocr_language is not None:
            # 语言选择只在 Tika Adapter 边缘传递，任务层不依赖 Tesseract 专有参数。
            headers["X-Tika-OCRLanguage"] = self._ocr_language
            headers["X-Tika-PDFOcrStrategy"] = "auto"
        request = Request(
            self._endpoint,
            data=content,
            method="PUT",
            headers=headers,
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                payload = response.read()
                response_media_type = response.headers.get_content_type()
        except HTTPError as error:
            retryable = error.code >= 500
            code: IngestionErrorCode = (
                "INGESTION_PARSER_UNAVAILABLE" if retryable else "INGESTION_PARSE_FAILED"
            )
            raise IngestionError(
                code,
                f"Tika 解析请求失败, HTTP 状态码 {error.code}",
                retryable=retryable,
            ) from error
        except (TimeoutError, URLError, OSError) as error:
            raise IngestionError(
                "INGESTION_PARSER_UNAVAILABLE",
                "Tika 当前不可用或请求超时",
                retryable=True,
            ) from error
        return parse_xhtml(payload, declared_media_type or response_media_type)


class TikaChineseOcrAdapter:
    """通过 Tika 隔离 Tesseract 中文语言参数，便于后续替换为 PaddleOCR。"""

    def __init__(self, base_url: str, *, timeout_seconds: float = 30) -> None:
        self._parser = TikaDocumentParser(
            base_url,
            timeout_seconds=timeout_seconds,
            ocr_language="chi_sim+eng",
        )

    def parse(
        self,
        *,
        content: bytes,
        file_name: str,
        declared_media_type: str | None,
    ) -> ParsedDocument:
        try:
            document = self._parser.parse(
                content=content,
                file_name=file_name,
                declared_media_type=declared_media_type,
            )
        except IngestionError as error:
            raise IngestionError(
                error.code,
                str(error),
                retryable=error.retryable,
                stage="ocr",
            ) from error
        metadata = dict(document.metadata)
        metadata["ai-platform:ocr-language"] = "chi_sim+eng"
        parser_name = (
            f"{document.parser_name}+tesseract-chi-sim"
            if document.used_ocr
            else document.parser_name
        )
        return ParsedDocument(
            media_type=document.media_type,
            parser_name=parser_name,
            page_count=document.page_count,
            used_ocr=document.used_ocr,
            blocks=document.blocks,
            metadata=metadata,
        )
