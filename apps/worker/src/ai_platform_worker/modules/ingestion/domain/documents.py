from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

BlockType = Literal["heading", "paragraph", "table", "ocr"]
Visibility = Literal["private", "workspace", "departments", "public"]
SecurityLevel = Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]


@dataclass(frozen=True)
class SourcePosition:
    page_number: int | None = None
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True)
class ParsedBlock:
    block_type: BlockType
    text: str
    source_position: SourcePosition


@dataclass(frozen=True)
class ParsedDocument:
    media_type: str
    parser_name: str
    page_count: int
    used_ocr: bool
    blocks: tuple[ParsedBlock, ...]
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentIdentity:
    workspace_id: UUID
    knowledge_base_id: UUID
    document_id: UUID
    document_version_id: UUID
    index_version_id: UUID
    department_ids: tuple[UUID, ...]
    visibility: Visibility
    security_level: SecurityLevel
    permission_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class IngestionLimits:
    max_file_size_bytes: int
    max_page_count: int
    max_chunk_chars: int
    chunk_overlap_chars: int

    def __post_init__(self) -> None:
        if self.max_file_size_bytes < 1 or self.max_page_count < 1:
            raise ValueError("文档大小和页数限制必须为正整数")
        if self.max_chunk_chars < 32:
            raise ValueError("Chunk 最大字符数不能小于 32")
        if not 0 <= self.chunk_overlap_chars < self.max_chunk_chars:
            raise ValueError("Chunk 重叠必须大于等于 0 且小于最大字符数")


@dataclass(frozen=True)
class IngestionRequest:
    identity: DocumentIdentity
    file_name: str
    declared_media_type: str | None
    limits: IngestionLimits


@dataclass(frozen=True)
class ParseRequest:
    file_name: str
    declared_media_type: str | None
    limits: IngestionLimits


@dataclass(frozen=True)
class ChunkSourcePosition:
    page_number: int | None
    line_start: int | None
    line_end: int | None
    block_start: int
    block_end: int


@dataclass(frozen=True)
class Chunk:
    workspace_id: UUID
    knowledge_base_id: UUID
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    department_ids: tuple[UUID, ...]
    visibility: Visibility
    security_level: SecurityLevel
    source_position: ChunkSourcePosition
    content: str
    content_hash: str
    index_version_id: UUID
    sequence_no: int
    parser_name: str
    ocr_used: bool
    permission_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class IngestionResult:
    parsed_document: ParsedDocument
    chunks: tuple[Chunk, ...]


class DocumentParser(Protocol):
    def parse(
        self,
        *,
        content: bytes,
        file_name: str,
        declared_media_type: str | None,
    ) -> ParsedDocument: ...


class ChineseOcrAdapter(Protocol):
    """将中文图片或扫描文档转换为带来源位置的结构化正文。"""

    def parse(
        self,
        *,
        content: bytes,
        file_name: str,
        declared_media_type: str | None,
    ) -> ParsedDocument: ...


class ParserRouter(Protocol):
    def parser_for(self, file_name: str) -> DocumentParser: ...
