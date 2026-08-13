import hashlib
import json
import os
from pathlib import Path
from typing import TypedDict, cast
from uuid import UUID

import pytest
from ai_platform_worker.modules.ingestion.application.chunking import StructuralChunker
from ai_platform_worker.modules.ingestion.application.ingest import IngestDocument
from ai_platform_worker.modules.ingestion.domain.documents import (
    DocumentIdentity,
    IngestionLimits,
    IngestionRequest,
    IngestionResult,
)
from ai_platform_worker.modules.ingestion.infrastructure.parsers import DefaultParserRouter
from ai_platform_worker.modules.ingestion.infrastructure.pdf import PdfDocumentParser
from ai_platform_worker.modules.ingestion.infrastructure.tika import TikaDocumentParser

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/ingestion"
GENERATED = FIXTURES / "generated"
TIKA_URL = os.environ.get("AI_PLATFORM_TEST_TIKA_URL", "http://127.0.0.1:9998")


class ManifestEntry(TypedDict):
    file: str
    sha256: str
    size_bytes: int
    synthetic: bool


class Manifest(TypedDict):
    dataset_version: str
    files: list[ManifestEntry]


def load_manifest() -> Manifest:
    return cast(Manifest, json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8")))


def identity() -> DocumentIdentity:
    return DocumentIdentity(
        workspace_id=UUID("11000000-0000-4000-8000-000000000001"),
        knowledge_base_id=UUID("22000000-0000-4000-8000-000000000001"),
        document_id=UUID("33000000-0000-4000-8000-000000000001"),
        document_version_id=UUID("44000000-0000-4000-8000-000000000001"),
        index_version_id=UUID("55000000-0000-4000-8000-000000000001"),
        department_ids=(UUID("66000000-0000-4000-8000-000000000001"),),
        visibility="departments",
        security_level="INTERNAL",
    )


@pytest.fixture(scope="module")
def service() -> IngestDocument:
    tika_parser = TikaDocumentParser(TIKA_URL, timeout_seconds=30)
    return IngestDocument(
        DefaultParserRouter(tika_parser, PdfDocumentParser(tika_parser)),
        StructuralChunker(),
    )


def ingest(service: IngestDocument, file_name: str, media_type: str | None) -> IngestionResult:
    return service.execute(
        IngestionRequest(
            identity=identity(),
            file_name=file_name,
            declared_media_type=media_type,
            limits=IngestionLimits(
                max_file_size_bytes=10 * 1024 * 1024,
                max_page_count=20,
                max_chunk_chars=180,
                chunk_overlap_chars=20,
            ),
        ),
        (GENERATED / file_name).read_bytes(),
    )


def combined_content(result: IngestionResult) -> str:
    return "\n".join(chunk.content for chunk in result.chunks)


def test_fixture_manifest_contains_only_reproducible_synthetic_data() -> None:
    manifest = load_manifest()

    assert manifest["dataset_version"] == "p0-07-v1"
    assert len(manifest["files"]) == 7
    for entry in manifest["files"]:
        content = (GENERATED / entry["file"]).read_bytes()
        assert entry["synthetic"] is True
        assert entry["size_bytes"] == len(content)
        assert entry["sha256"] == hashlib.sha256(content).hexdigest()


def test_txt_and_markdown_use_deterministic_local_parsers(service: IngestDocument) -> None:
    text_result = ingest(service, "synthetic-policy.txt", "text/plain")
    markdown_result = ingest(service, "synthetic-handbook.md", "text/markdown")

    assert "访客进入实验室前必须登记" in combined_content(text_result)
    assert text_result.parsed_document.parser_name == "plain-text-v1"
    assert text_result.chunks[0].source_position.line_start == 1
    assert "账号停用" in combined_content(markdown_result)
    assert {block.block_type for block in markdown_result.parsed_document.blocks} == {
        "heading",
        "paragraph",
        "table",
    }


def test_tika_parses_text_pdf_with_page_source(service: IngestDocument) -> None:
    result = ingest(service, "synthetic-text.pdf", "application/pdf")

    assert result.parsed_document.media_type == "application/pdf"
    assert result.parsed_document.page_count == 1
    assert result.parsed_document.used_ocr is False
    assert "Expense claims must be filed within 30 days" in combined_content(result)
    assert all(chunk.source_position.page_number == 1 for chunk in result.chunks)


def test_tika_preserves_docx_table_structure(service: IngestDocument) -> None:
    result = ingest(
        service,
        "synthetic-table.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    table_blocks = [block for block in result.parsed_document.blocks if block.block_type == "table"]
    assert len(table_blocks) == 1
    assert "Level | Approver" in table_blocks[0].text
    assert "Restricted | Department owner" in table_blocks[0].text
    assert "Visitors must register" in combined_content(result)


def test_pdfplumber_preserves_pdf_table_structure(service: IngestDocument) -> None:
    result = ingest(service, "synthetic-pdf-table.pdf", "application/pdf")

    table_blocks = [block for block in result.parsed_document.blocks if block.block_type == "table"]
    assert len(table_blocks) == 1
    assert table_blocks[0].source_position.page_number == 1
    assert "Level | Approver" in table_blocks[0].text
    assert "Restricted | Department owner" in table_blocks[0].text
    assert result.parsed_document.metadata["pdf:tableCount"] == "1"
    assert all(block.block_type == "table" for block in result.parsed_document.blocks)


def test_tika_image_ocr_extracts_expected_text(service: IngestDocument) -> None:
    result = ingest(service, "synthetic-ocr.png", "image/png")

    assert result.parsed_document.used_ocr is True
    assert "TEST POLICY" in combined_content(result)
    assert any(block.block_type == "ocr" for block in result.parsed_document.blocks)
    assert all(chunk.ocr_used is True for chunk in result.chunks)


def test_tika_scanned_pdf_runs_ocr_and_keeps_page_source(service: IngestDocument) -> None:
    result = ingest(service, "synthetic-scan.pdf", "application/pdf")

    assert result.parsed_document.used_ocr is True
    assert result.parsed_document.page_count == 1
    assert combined_content(result).strip()
    assert all(chunk.source_position.page_number == 1 for chunk in result.chunks)


def test_chunks_keep_permission_and_version_metadata(service: IngestDocument) -> None:
    result = ingest(service, "synthetic-table.docx", None)

    assert result.chunks
    assert [chunk.sequence_no for chunk in result.chunks] == list(range(1, len(result.chunks) + 1))
    for chunk in result.chunks:
        assert chunk.workspace_id == identity().workspace_id
        assert chunk.knowledge_base_id == identity().knowledge_base_id
        assert chunk.document_version_id == identity().document_version_id
        assert chunk.index_version_id == identity().index_version_id
        assert chunk.department_ids == identity().department_ids
        assert chunk.visibility == "departments"
        assert chunk.security_level == "INTERNAL"
        assert chunk.content_hash == hashlib.sha256(chunk.content.encode()).hexdigest()
