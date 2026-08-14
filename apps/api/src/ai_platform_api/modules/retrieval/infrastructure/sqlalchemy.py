"""实现带工作空间和资源范围过滤的 PostgreSQL 混合检索索引。"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import ColumnElement, RowMapping, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from ai_platform_api.modules.retrieval.domain.models import (
    AuthorizedSearchScope,
    ChannelCandidate,
    IndexedChunk,
    StoredChunk,
)
from ai_platform_api.persistence.tables import retrieval_chunks


def scope_conditions(scope: AuthorizedSearchScope) -> tuple[ColumnElement[bool], ...]:
    """处理作用域条件，在基础设施边界维持稳定领域对象映射。"""

    conditions: list[ColumnElement[bool]] = [
        retrieval_chunks.c.workspace_id == scope.workspace_id,
        retrieval_chunks.c.active.is_(True),
        retrieval_chunks.c.index_version_id.in_(scope.index_version_ids),
        retrieval_chunks.c.visibility.in_(scope.visibilities),
        retrieval_chunks.c.security_level.in_(scope.security_levels),
    ]
    if scope.knowledge_base_ids is not None:
        conditions.append(retrieval_chunks.c.knowledge_base_id.in_(scope.knowledge_base_ids))
    if scope.document_ids is not None:
        conditions.append(retrieval_chunks.c.document_id.in_(scope.document_ids))
    conditions.append(
        or_(
            retrieval_chunks.c.visibility != "departments",
            retrieval_chunks.c.department_ids.overlap(list(scope.department_ids)),
        )
    )
    return tuple(conditions)


def stored_chunk(mapping: RowMapping) -> StoredChunk:
    """处理已存储分块，在基础设施边界维持稳定领域对象映射。"""

    return StoredChunk(
        chunk_id=mapping["chunk_id"],
        workspace_id=mapping["workspace_id"],
        knowledge_base_id=mapping["knowledge_base_id"],
        document_id=mapping["document_id"],
        document_version_id=mapping["document_version_id"],
        index_version_id=mapping["index_version_id"],
        sequence_no=mapping["sequence_no"],
        content=mapping["content"],
        content_hash=mapping["content_hash"],
        source_position=mapping["source_position"],
    )


class SqlAlchemySearchIndex:
    """在工作空间和授权条件下执行 PostgreSQL 关键词与 pgvector 搜索。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, chunk: IndexedChunk) -> None:
        # 1. 当前物理维度与 BGE-M3 冻结版本绑定，提前失败可避免晦涩的数据库错误。
        if len(chunk.embedding) != 1024:
            raise ValueError("索引向量维度必须为 1024")
        # 2. 以索引版本和 Chunk ID 幂等 Upsert，重试只能更新同一构建版本内的投影字段。
        statement = postgresql_insert(retrieval_chunks).values(
            index_version_id=chunk.index_version_id,
            chunk_id=chunk.chunk_id,
            workspace_id=chunk.workspace_id,
            knowledge_base_id=chunk.knowledge_base_id,
            document_id=chunk.document_id,
            document_version_id=chunk.document_version_id,
            sequence_no=chunk.sequence_no,
            content=chunk.content,
            content_hash=chunk.content_hash,
            embedding=list(chunk.embedding),
            keyword_text=chunk.keyword_text,
            department_ids=list(chunk.department_ids),
            visibility=chunk.visibility,
            security_level=chunk.security_level,
            source_position=chunk.source_position,
            active=chunk.active,
        )
        self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    retrieval_chunks.c.index_version_id,
                    retrieval_chunks.c.chunk_id,
                ],
                set_={
                    "content": statement.excluded.content,
                    "content_hash": statement.excluded.content_hash,
                    "embedding": statement.excluded.embedding,
                    "keyword_text": statement.excluded.keyword_text,
                    "department_ids": statement.excluded.department_ids,
                    "visibility": statement.excluded.visibility,
                    "security_level": statement.excluded.security_level,
                    "source_position": statement.excluded.source_position,
                    "active": statement.excluded.active,
                },
            )
        )

    def add_all(self, chunks: Sequence[IndexedChunk]) -> None:
        for chunk in chunks:
            self.add(chunk)

    def keyword_search(
        self,
        scope: AuthorizedSearchScope,
        keyword_query: str,
        limit: int,
    ) -> tuple[ChannelCandidate, ...]:
        parsed_query = func.to_tsquery("simple", keyword_query)
        rank_score = func.ts_rank_cd(retrieval_chunks.c.keyword_vector, parsed_query).label("score")
        rows = (
            self._session.execute(
                select(retrieval_chunks, rank_score)
                .where(
                    and_(*scope_conditions(scope)),
                    retrieval_chunks.c.keyword_vector.op("@@")(parsed_query),
                )
                .order_by(rank_score.desc(), retrieval_chunks.c.chunk_id)
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return tuple(
            ChannelCandidate(
                chunk=stored_chunk(row),
                channel="keyword",
                rank=index,
                score=float(row["score"]),
            )
            for index, row in enumerate(rows, start=1)
        )

    def vector_search(
        self,
        scope: AuthorizedSearchScope,
        embedding: tuple[float, ...],
        limit: int,
    ) -> tuple[ChannelCandidate, ...]:
        distance = retrieval_chunks.c.embedding.cosine_distance(list(embedding)).label("distance")
        rows = (
            self._session.execute(
                select(retrieval_chunks, distance)
                .where(and_(*scope_conditions(scope)))
                .order_by(distance, retrieval_chunks.c.chunk_id)
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return tuple(
            ChannelCandidate(
                chunk=stored_chunk(row),
                channel="vector",
                rank=index,
                score=max(-1.0, min(1.0, 1.0 - float(row["distance"]))),
            )
            for index, row in enumerate(rows, start=1)
        )

    def read_document_range(
        self,
        scope: AuthorizedSearchScope,
        document_version_id: UUID,
        sequence_start: int,
        sequence_end: int,
    ) -> tuple[StoredChunk, ...]:
        rows = (
            self._session.execute(
                select(retrieval_chunks)
                .where(
                    and_(*scope_conditions(scope)),
                    retrieval_chunks.c.document_version_id == document_version_id,
                    retrieval_chunks.c.sequence_no.between(sequence_start, sequence_end),
                )
                .order_by(retrieval_chunks.c.sequence_no)
            )
            .mappings()
            .all()
        )
        return tuple(stored_chunk(row) for row in rows)

    def get_chunk(
        self,
        scope: AuthorizedSearchScope,
        chunk_id: UUID,
    ) -> StoredChunk | None:
        row = (
            self._session.execute(
                select(retrieval_chunks).where(
                    and_(*scope_conditions(scope)),
                    retrieval_chunks.c.chunk_id == chunk_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        return stored_chunk(row) if row is not None else None
