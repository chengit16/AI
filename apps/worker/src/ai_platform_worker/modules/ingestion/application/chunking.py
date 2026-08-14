"""按标题、段落、表格、页码和字符预算生成稳定结构化 Chunk。"""

import hashlib
import re
from dataclasses import dataclass
from uuid import uuid5

from ai_platform_worker.modules.ingestion.domain.documents import (
    Chunk,
    ChunkSourcePosition,
    DocumentIdentity,
    IngestionLimits,
    ParsedBlock,
    ParsedDocument,
)

SENTENCE_BOUNDARY = re.compile(r"(?<=[。!?])\s+|\n+")


@dataclass(frozen=True)
class ChunkPiece:
    """保存待归并文本片段、块类型和原始来源位置。"""

    text: str
    block_index: int
    block: ParsedBlock


def normalize_text(value: str) -> str:
    """规范化文本，并在可信上下文内维持授权、事务与审计边界。"""

    lines = [" ".join(line.split()) for line in value.replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line).strip()


def split_large_block(block: ParsedBlock, block_index: int, limit: int) -> list[ChunkPiece]:
    """按最大字符数拆分超长块，并保留来源位置用于引用回链。"""

    text = normalize_text(block.text)
    if len(text) <= limit:
        return [ChunkPiece(text=text, block_index=block_index, block=block)]

    sentences = [part.strip() for part in SENTENCE_BOUNDARY.split(text) if part.strip()]
    pieces: list[ChunkPiece] = []
    buffer = ""
    for sentence in sentences or [text]:
        remaining = sentence
        while remaining:
            available = limit - len(buffer) - (1 if buffer else 0)
            if available <= 0:
                pieces.append(ChunkPiece(buffer, block_index, block))
                buffer = ""
                continue
            if len(remaining) <= available:
                buffer = f"{buffer}\n{remaining}".strip()
                remaining = ""
                continue
            if buffer:
                pieces.append(ChunkPiece(buffer, block_index, block))
                buffer = ""
                continue
            pieces.append(ChunkPiece(remaining[:limit], block_index, block))
            remaining = remaining[limit:]
    if buffer:
        pieces.append(ChunkPiece(buffer, block_index, block))
    return pieces


def source_position(pieces: list[ChunkPiece]) -> ChunkSourcePosition:
    """把页码或行号位置转换为稳定显示文本，缺失位置时返回空值。"""

    pages = {piece.block.source_position.page_number for piece in pieces}
    pages.discard(None)
    line_starts = [
        value for piece in pieces if (value := piece.block.source_position.line_start) is not None
    ]
    line_ends = [
        value for piece in pieces if (value := piece.block.source_position.line_end) is not None
    ]
    return ChunkSourcePosition(
        page_number=next(iter(pages)) if len(pages) == 1 else None,
        line_start=min(line_starts) if line_starts else None,
        line_end=max(line_ends) if line_ends else None,
        block_start=pieces[0].block_index,
        block_end=pieces[-1].block_index,
    )


class StructuralChunker:
    """优先保持页、段落和表格边界，超长块才退化为字符窗口。"""

    def chunk(
        self,
        document: ParsedDocument,
        identity: DocumentIdentity,
        limits: IngestionLimits,
    ) -> tuple[Chunk, ...]:
        pieces = [
            piece
            for index, block in enumerate(document.blocks)
            for piece in split_large_block(block, index, limits.max_chunk_chars)
            if piece.text
        ]
        groups: list[list[ChunkPiece]] = []
        current: list[ChunkPiece] = []
        current_length = 0
        for piece in pieces:
            page_changed = bool(
                current
                and current[-1].block.source_position.page_number
                != piece.block.source_position.page_number
                and piece.block.source_position.page_number is not None
            )
            separator_length = 1 if current else 0
            if current and (
                page_changed
                or current_length + separator_length + len(piece.text) > limits.max_chunk_chars
            ):
                groups.append(current)
                current = []
                current_length = 0
            current.append(piece)
            current_length += len(piece.text) + (1 if current_length else 0)
        if current:
            groups.append(current)

        chunks: list[Chunk] = []
        previous_tail = ""
        previous_page: int | None = None
        for sequence_no, group in enumerate(groups, start=1):
            body = "\n".join(piece.text for piece in group)
            position = source_position(group)
            same_page = previous_page == position.page_number
            if previous_tail and limits.chunk_overlap_chars and same_page:
                available = limits.max_chunk_chars - len(body) - 1
                overlap_length = min(limits.chunk_overlap_chars, max(available, 0))
                overlap = previous_tail[-overlap_length:] if overlap_length else ""
                content = f"{overlap}\n{body}" if overlap else body
            else:
                content = body
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            chunks.append(
                Chunk(
                    workspace_id=identity.workspace_id,
                    knowledge_base_id=identity.knowledge_base_id,
                    document_id=identity.document_id,
                    document_version_id=identity.document_version_id,
                    chunk_id=uuid5(
                        identity.document_version_id,
                        f"{sequence_no}:{content_hash}",
                    ),
                    department_ids=identity.department_ids,
                    visibility=identity.visibility,
                    security_level=identity.security_level,
                    source_position=position,
                    content=content,
                    content_hash=content_hash,
                    index_version_id=identity.index_version_id,
                    sequence_no=sequence_no,
                    parser_name=document.parser_name,
                    ocr_used=document.used_ocr,
                    permission_labels=identity.permission_labels,
                )
            )
            previous_tail = body
            previous_page = position.page_number
        return tuple(chunks)
