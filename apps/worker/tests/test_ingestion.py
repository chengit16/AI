"""验证 Worker 文档解析、结构化切片和稳定标识。"""

from types import TracebackType
from typing import Self
from urllib.request import Request
from uuid import UUID

import pytest
from ai_platform_worker.modules.ingestion.application.chunking import StructuralChunker
from ai_platform_worker.modules.ingestion.application.ingest import IngestDocument
from ai_platform_worker.modules.ingestion.domain.documents import (
    DocumentIdentity,
    IngestionLimits,
    IngestionRequest,
    ParsedBlock,
    ParsedDocument,
    SourcePosition,
)
from ai_platform_worker.modules.ingestion.domain.errors import IngestionError
from ai_platform_worker.modules.ingestion.infrastructure.parsers import DefaultParserRouter
from ai_platform_worker.modules.ingestion.infrastructure.tika import (
    TikaDocumentParser,
    parse_xhtml,
)


class StubTikaHeaders:
    """为 Tika HTTP 边界测试提供最小响应头接口。"""

    def get_content_type(self) -> str:
        return "text/html"


class StubTikaResponse:
    """返回固定 XHTML，避免回归测试依赖真实 Tika 服务。"""

    headers = StubTikaHeaders()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def read(self) -> bytes:
        return (
            b'<html xmlns="http://www.w3.org/1999/xhtml"><head>'
            b'<meta name="Content-Type" content="application/pdf"/></head>'
            b"<body><p>Synthetic content</p></body></html>"
        )


class FailingTikaParser:
    def parse(
        self,
        *,
        content: bytes,
        file_name: str,
        declared_media_type: str | None,
    ) -> ParsedDocument:
        raise AssertionError("此测试不应调用 Tika")


class TwoPageParser:
    def parse(
        self,
        *,
        content: bytes,
        file_name: str,
        declared_media_type: str | None,
    ) -> ParsedDocument:
        return ParsedDocument(
            media_type="application/pdf",
            parser_name="synthetic-two-page",
            page_count=2,
            used_ocr=False,
            blocks=(
                ParsedBlock("paragraph", "第一页", SourcePosition(page_number=1)),
                ParsedBlock("paragraph", "第二页", SourcePosition(page_number=2)),
            ),
        )


def identity() -> DocumentIdentity:
    return DocumentIdentity(
        workspace_id=UUID("10000000-0000-4000-8000-000000000001"),
        knowledge_base_id=UUID("20000000-0000-4000-8000-000000000001"),
        document_id=UUID("30000000-0000-4000-8000-000000000001"),
        document_version_id=UUID("40000000-0000-4000-8000-000000000001"),
        index_version_id=UUID("50000000-0000-4000-8000-000000000001"),
        department_ids=(UUID("60000000-0000-4000-8000-000000000001"),),
        visibility="departments",
        security_level="CONFIDENTIAL",
    )


def limits(**overrides: int) -> IngestionLimits:
    values = {
        "max_file_size_bytes": 1024,
        "max_page_count": 10,
        "max_chunk_chars": 64,
        "chunk_overlap_chars": 8,
    }
    values.update(overrides)
    return IngestionLimits(**values)


def request(file_name: str, ingestion_limits: IngestionLimits | None = None) -> IngestionRequest:
    return IngestionRequest(
        identity=identity(),
        file_name=file_name,
        declared_media_type=None,
        limits=ingestion_limits or limits(),
    )


def test_markdown_parser_preserves_heading_table_and_lines() -> None:
    service = IngestDocument(
        DefaultParserRouter(FailingTikaParser()),
        StructuralChunker(),
    )

    result = service.execute(
        request("synthetic.md"),
        "# 标题\n\n正文规则。\n\n| 字段 | 值 |\n| --- | --- |\n| 状态 | 启用 |\n".encode(),
    )

    assert [block.block_type for block in result.parsed_document.blocks] == [
        "heading",
        "paragraph",
        "table",
    ]
    assert result.parsed_document.blocks[0].source_position.line_start == 1
    assert result.parsed_document.blocks[2].source_position.line_start == 5
    assert "状态 | 启用" in result.parsed_document.blocks[2].text


def test_chunking_propagates_security_metadata_and_has_stable_ids() -> None:
    document = ParsedDocument(
        media_type="text/plain",
        parser_name="synthetic-parser",
        page_count=2,
        used_ocr=False,
        blocks=(
            ParsedBlock("paragraph", "A" * 40, SourcePosition(page_number=1)),
            ParsedBlock("paragraph", "B" * 40, SourcePosition(page_number=2)),
        ),
    )
    chunker = StructuralChunker()

    first = chunker.chunk(document, identity(), limits())
    second = chunker.chunk(document, identity(), limits())

    assert len(first) == 2
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert [chunk.source_position.page_number for chunk in first] == [1, 2]
    assert first[1].content == "B" * 40
    assert all(chunk.workspace_id == identity().workspace_id for chunk in first)
    assert all(chunk.department_ids == identity().department_ids for chunk in first)
    assert all(chunk.security_level == "CONFIDENTIAL" for chunk in first)
    assert all(len(chunk.content_hash) == 64 for chunk in first)


def test_full_size_chunk_does_not_add_zero_length_overlap() -> None:
    document = ParsedDocument(
        media_type="text/plain",
        parser_name="synthetic-parser",
        page_count=1,
        used_ocr=False,
        blocks=(
            ParsedBlock("paragraph", "A" * 32, SourcePosition(page_number=1)),
            ParsedBlock("paragraph", "B" * 32, SourcePosition(page_number=1)),
        ),
    )

    chunks = StructuralChunker().chunk(
        document,
        identity(),
        limits(max_chunk_chars=32, chunk_overlap_chars=8),
    )

    assert [chunk.content for chunk in chunks] == ["A" * 32, "B" * 32]
    assert all(len(chunk.content) <= 32 for chunk in chunks)


def test_file_size_is_rejected_before_parser_call() -> None:
    service = IngestDocument(
        DefaultParserRouter(FailingTikaParser()),
        StructuralChunker(),
    )

    with pytest.raises(IngestionError) as captured:
        service.execute(
            request("synthetic.pdf", limits(max_file_size_bytes=2)),
            b"too-large",
        )

    assert captured.value.code == "INGESTION_FILE_TOO_LARGE"
    assert captured.value.retryable is False


def test_page_limit_has_stable_non_retryable_error() -> None:
    service = IngestDocument(
        DefaultParserRouter(TwoPageParser()),
        StructuralChunker(),
    )

    with pytest.raises(IngestionError) as captured:
        service.execute(
            request("synthetic.pdf", limits(max_page_count=1)),
            b"synthetic-pdf",
        )

    assert captured.value.code == "INGESTION_PAGE_LIMIT_EXCEEDED"
    assert captured.value.retryable is False


def test_unsupported_format_is_rejected() -> None:
    service = IngestDocument(
        DefaultParserRouter(FailingTikaParser()),
        StructuralChunker(),
    )

    with pytest.raises(IngestionError) as captured:
        service.execute(request("synthetic.pptx"), b"synthetic")

    assert captured.value.code == "INGESTION_UNSUPPORTED_FORMAT"
    assert captured.value.retryable is False


def test_tika_null_title_reference_does_not_break_structured_parse() -> None:
    document = parse_xhtml(
        b'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>&#0;</title>'
        b'<meta name="Content-Type" content="application/pdf"/></head>'
        b'<body><div class="page"><p>Synthetic content</p></div></body></html>',
        "application/pdf",
    )

    assert document.media_type == "application/pdf"
    assert document.blocks[0].text == "Synthetic content"
    assert document.blocks[0].source_position.page_number == 1


def test_tika_html_named_entities_are_normalized_before_xml_parse() -> None:
    """Tika 输出的标准 HTML Entity 不应破坏严格 XHTML 解析。"""

    document = parse_xhtml(
        b'<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        b'<meta name="Content-Type" content="application/pdf"/></head>'
        b"<body><p>Rule &ldquo;A&rdquo; &rarr; done &bull; next &hellip;</p>"
        b"<p>Unknown &not-a-tika-entity;</p></body></html>",
        "application/pdf",
    )

    assert document.blocks[0].text == "Rule \u201cA\u201d \u2192 done \u2022 next \u2026"
    assert document.blocks[1].text == "Unknown &not-a-tika-entity;"


def test_tika_request_encodes_non_ascii_file_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """中文文件名必须形成 ASCII Header，同时保留标准 UTF-8 文件名参数。"""

    def fake_urlopen(request: Request, timeout: float) -> StubTikaResponse:
        assert timeout == 30
        content_disposition = request.get_header("Content-disposition")
        assert content_disposition is not None
        content_disposition.encode("latin-1")
        assert "filename*=UTF-8''Harness%20%E5%9B%A2%E9%98%9F.pdf" in content_disposition
        return StubTikaResponse()

    monkeypatch.setattr(
        "ai_platform_worker.modules.ingestion.infrastructure.tika.urlopen",
        fake_urlopen,
    )

    document = TikaDocumentParser("http://tika:9998").parse(
        content=b"%PDF-synthetic",
        file_name="Harness 团队.pdf",
        declared_media_type="application/pdf",
    )

    assert document.blocks[0].text == "Synthetic content"
