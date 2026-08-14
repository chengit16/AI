"""定义解析块、表格、文档结果、Chunk 和解析器端口。"""

from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

BlockType = Literal["heading", "paragraph", "table", "ocr"]
Visibility = Literal["private", "workspace", "departments", "public"]
SecurityLevel = Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]


@dataclass(frozen=True)
class SourcePosition:
    """定位原文页码或行号范围，供引用回链和问题诊断。"""

    page_number: int | None = None
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True)
class ParsedBlock:
    """保存解析后的标题、段落或表格文本及其来源位置。"""

    block_type: BlockType
    text: str
    source_position: SourcePosition


@dataclass(frozen=True)
class ParsedDocument:
    """汇总媒体类型、解析器、页数、OCR 标记、结构块和安全元数据。"""

    media_type: str
    parser_name: str
    page_count: int
    used_ocr: bool
    blocks: tuple[ParsedBlock, ...]
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentIdentity:
    """固定入库任务所属空间、知识库、文档、版本和来源标识。"""

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
    """限制页数、字符数、块大小和块数量，防止单文档耗尽 Worker 资源。"""

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
    """定义入库操作的请求字段与协议校验边界。"""

    identity: DocumentIdentity
    file_name: str
    declared_media_type: str | None
    limits: IngestionLimits


@dataclass(frozen=True)
class ParseRequest:
    """定义解析操作的请求字段与协议校验边界。"""

    file_name: str
    declared_media_type: str | None
    limits: IngestionLimits


@dataclass(frozen=True)
class ChunkSourcePosition:
    """汇总一个分块覆盖的页码和行号范围。"""

    page_number: int | None
    line_start: int | None
    line_end: int | None
    block_start: int
    block_end: int


@dataclass(frozen=True)
class Chunk:
    """保存稳定序号、规范文本、内容摘要和来源位置的知识分块。"""

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
    """返回解析文档、最终分块集合和全文内容摘要。"""

    parsed_document: ParsedDocument
    chunks: tuple[Chunk, ...]


class DocumentParser(Protocol):
    """把受支持二进制文档转换为规范结构块，并返回稳定解析错误。"""

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
    """按真实媒体类型选择解析器，未知或不匹配格式必须拒绝。"""

    def parser_for(self, file_name: str) -> DocumentParser: ...
