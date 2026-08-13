import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.modules.retrieval.application.citations import CitationService
from ai_platform_api.modules.retrieval.application.reader import (
    AuthorizedDocumentReader,
    ReaderBudget,
)
from ai_platform_api.modules.retrieval.application.tokenization import (
    keyword_document,
    keyword_query,
)
from ai_platform_api.modules.retrieval.domain.errors import CitationInvalidError
from ai_platform_api.modules.retrieval.domain.models import (
    AuthorizedSearchScope,
    IndexedChunk,
)
from ai_platform_api.modules.retrieval.infrastructure.sqlalchemy import SqlAlchemySearchIndex
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)

WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000001")
OTHER_WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000002")
KNOWLEDGE_BASE_ID = UUID("20000000-0000-4000-8000-000000000001")
OTHER_KNOWLEDGE_BASE_ID = UUID("20000000-0000-4000-8000-000000000002")
DOCUMENT_ID = UUID("30000000-0000-4000-8000-000000000001")
OTHER_DOCUMENT_ID = UUID("30000000-0000-4000-8000-000000000002")
DOCUMENT_VERSION_ID = UUID("40000000-0000-4000-8000-000000000001")
OTHER_DOCUMENT_VERSION_ID = UUID("40000000-0000-4000-8000-000000000002")
INDEX_VERSION_ID = UUID("50000000-0000-4000-8000-000000000001")
OLD_INDEX_VERSION_ID = UUID("50000000-0000-4000-8000-000000000002")
DEPARTMENT_ID = UUID("60000000-0000-4000-8000-000000000001")
OTHER_DEPARTMENT_ID = UUID("60000000-0000-4000-8000-000000000002")
CENTER_CHUNK_ID = UUID("70000000-0000-4000-8000-000000000002")


@dataclass(frozen=True)
class DatabaseHarness:
    schema: str
    engine: Engine
    sessions: sessionmaker[Session]


def vector(first: float, second: float = 0.0) -> tuple[float, ...]:
    return (first, second, *(0.0 for _ in range(1022)))


def indexed_chunk(
    *,
    chunk_id: UUID,
    sequence_no: int,
    content: str,
    embedding: tuple[float, ...],
    workspace_id: UUID = WORKSPACE_ID,
    knowledge_base_id: UUID = KNOWLEDGE_BASE_ID,
    document_id: UUID = DOCUMENT_ID,
    document_version_id: UUID = DOCUMENT_VERSION_ID,
    index_version_id: UUID = INDEX_VERSION_ID,
    department_ids: tuple[UUID, ...] = (DEPARTMENT_ID,),
    visibility: str = "departments",
    security_level: str = "INTERNAL",
    active: bool = True,
) -> IndexedChunk:
    if visibility not in {"private", "workspace", "departments", "public"}:
        raise ValueError("测试可见性无效")
    if security_level not in {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"}:
        raise ValueError("测试密级无效")
    return IndexedChunk(
        chunk_id=chunk_id,
        workspace_id=workspace_id,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        document_version_id=document_version_id,
        index_version_id=index_version_id,
        sequence_no=sequence_no,
        content=content,
        content_hash=f"synthetic-hash-{chunk_id}",
        embedding=embedding,
        keyword_text=keyword_document(content),
        department_ids=department_ids,
        visibility=(
            "private"
            if visibility == "private"
            else "workspace"
            if visibility == "workspace"
            else "departments"
            if visibility == "departments"
            else "public"
        ),
        security_level=(
            "PUBLIC"
            if security_level == "PUBLIC"
            else "INTERNAL"
            if security_level == "INTERNAL"
            else "CONFIDENTIAL"
            if security_level == "CONFIDENTIAL"
            else "RESTRICTED"
        ),
        source_position={"page_number": sequence_no, "synthetic": True},
        active=active,
    )


def authorized_scope() -> AuthorizedSearchScope:
    return AuthorizedSearchScope(
        workspace_id=WORKSPACE_ID,
        index_version_ids=frozenset({INDEX_VERSION_ID}),
        knowledge_base_ids=frozenset({KNOWLEDGE_BASE_ID}),
        document_ids=frozenset({DOCUMENT_ID}),
        department_ids=frozenset({DEPARTMENT_ID}),
        visibilities=frozenset({"departments"}),
        security_levels=frozenset({"INTERNAL"}),
    )


@pytest.fixture(scope="module")
def database() -> Iterator[DatabaseHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p008_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)

    # 随机 Schema 将本节点的 Migration 和数据与本地既有环境完全隔离。
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "infra/migrations"))
    config.set_main_option("prepend_sys_path", str(ROOT / "apps/api/src"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("ai_platform_schema", schema)
    command.upgrade(config, "head")

    engine = create_platform_engine(database_url, schema)
    sessions = create_session_factory(engine)
    try:
        with sessions.begin() as session:
            index = SqlAlchemySearchIndex(session)
            index.add_all(synthetic_dataset())
        yield DatabaseHarness(schema=schema, engine=engine, sessions=sessions)
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def synthetic_dataset() -> tuple[IndexedChunk, ...]:
    allowed = (
        indexed_chunk(
            chunk_id=UUID("70000000-0000-4000-8000-000000000001"),
            sequence_no=1,
            content="合成制度前言, 仅用于检索测试。",
            embedding=vector(0.2, 0.98),
        ),
        indexed_chunk(
            chunk_id=CENTER_CHUNK_ID,
            sequence_no=2,
            content="差旅报销必须在三十天内提交, 并附合成票据。",
            embedding=vector(1.0),
        ),
        indexed_chunk(
            chunk_id=UUID("70000000-0000-4000-8000-000000000003"),
            sequence_no=3,
            content="合成制度附则, 不包含真实企业信息。",
            embedding=vector(0.3, 0.95),
        ),
    )
    # 每个越权样本都故意使用完全匹配的文本和向量，证明过滤发生在排序之前。
    excluded = (
        indexed_chunk(
            chunk_id=UUID("71000000-0000-4000-8000-000000000001"),
            sequence_no=1,
            content="差旅报销必须在三十天内提交。",
            embedding=vector(1.0),
            workspace_id=OTHER_WORKSPACE_ID,
        ),
        indexed_chunk(
            chunk_id=UUID("72000000-0000-4000-8000-000000000001"),
            sequence_no=1,
            content="差旅报销必须在三十天内提交。",
            embedding=vector(1.0),
            knowledge_base_id=OTHER_KNOWLEDGE_BASE_ID,
        ),
        indexed_chunk(
            chunk_id=UUID("73000000-0000-4000-8000-000000000001"),
            sequence_no=1,
            content="差旅报销必须在三十天内提交。",
            embedding=vector(1.0),
            document_id=OTHER_DOCUMENT_ID,
            document_version_id=OTHER_DOCUMENT_VERSION_ID,
        ),
        indexed_chunk(
            chunk_id=UUID("74000000-0000-4000-8000-000000000001"),
            sequence_no=1,
            content="差旅报销必须在三十天内提交。",
            embedding=vector(1.0),
            index_version_id=OLD_INDEX_VERSION_ID,
        ),
        indexed_chunk(
            chunk_id=UUID("75000000-0000-4000-8000-000000000001"),
            sequence_no=1,
            content="差旅报销必须在三十天内提交。",
            embedding=vector(1.0),
            department_ids=(OTHER_DEPARTMENT_ID,),
        ),
        indexed_chunk(
            chunk_id=UUID("76000000-0000-4000-8000-000000000001"),
            sequence_no=1,
            content="差旅报销必须在三十天内提交。",
            embedding=vector(1.0),
            security_level="RESTRICTED",
        ),
        indexed_chunk(
            chunk_id=UUID("77000000-0000-4000-8000-000000000001"),
            sequence_no=1,
            content="差旅报销必须在三十天内提交。",
            embedding=vector(1.0),
            active=False,
        ),
    )
    return allowed + excluded


def test_migration_creates_pgvector_and_required_indexes(database: DatabaseHarness) -> None:
    with database.engine.connect() as connection:
        extension_version = connection.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one()
        index_names = set(
            connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = :schema"),
                {"schema": database.schema},
            ).scalars()
        )

    assert extension_version == "0.8.6"
    assert {
        "ix_retrieval_chunks_scope",
        "ix_retrieval_chunks_document_sequence",
        "ix_retrieval_chunks_keyword",
        "ix_retrieval_chunks_embedding_hnsw",
    }.issubset(index_names)


def test_keyword_and_vector_search_apply_all_authorized_filters(
    database: DatabaseHarness,
) -> None:
    with database.sessions() as session:
        index = SqlAlchemySearchIndex(session)
        keyword_candidates = index.keyword_search(
            authorized_scope(),
            keyword_query("差旅报销期限"),
            limit=20,
        )
        vector_candidates = index.vector_search(
            authorized_scope(),
            vector(1.0),
            limit=20,
        )

    assert [item.chunk.chunk_id for item in keyword_candidates] == [CENTER_CHUNK_ID]
    assert vector_candidates[0].chunk.chunk_id == CENTER_CHUNK_ID
    assert {item.chunk.chunk_id for item in vector_candidates} == {
        UUID("70000000-0000-4000-8000-000000000001"),
        CENTER_CHUNK_ID,
        UUID("70000000-0000-4000-8000-000000000003"),
    }


def test_authorized_reader_cannot_cross_document_or_version_scope(
    database: DatabaseHarness,
) -> None:
    with database.sessions() as session:
        reader = AuthorizedDocumentReader(SqlAlchemySearchIndex(session))
        chunks = reader.read(
            authorized_scope(),
            DOCUMENT_VERSION_ID,
            center_sequence_no=2,
            budget=ReaderBudget(surrounding_chunks=1, max_chunks=3, max_characters=200),
        )
        unauthorized = reader.read(
            authorized_scope(),
            OTHER_DOCUMENT_VERSION_ID,
            center_sequence_no=1,
            budget=ReaderBudget(surrounding_chunks=1, max_chunks=3, max_characters=200),
        )

    assert [chunk.sequence_no for chunk in chunks] == [1, 2, 3]
    assert unauthorized == ()


def test_citation_rejects_missing_revoked_or_mismatched_source(
    database: DatabaseHarness,
) -> None:
    with database.sessions() as session:
        citations = CitationService(SqlAlchemySearchIndex(session))
        citation = citations.issue(
            authorized_scope(),
            CENTER_CHUNK_ID,
            "差旅报销必须在三十天内提交",
        )
        with pytest.raises(CitationInvalidError):
            citations.issue(
                authorized_scope(),
                UUID("74000000-0000-4000-8000-000000000001"),
                "差旅报销必须在三十天内提交",
            )
        with pytest.raises(CitationInvalidError):
            citations.issue(authorized_scope(), CENTER_CHUNK_ID, "差旅报销可以延后九十天")

    assert citation.document_version_id == DOCUMENT_VERSION_ID
    assert citation.index_version_id == INDEX_VERSION_ID
    assert citation.source_position["page_number"] == 2


def test_index_rejects_incompatible_embedding_dimension(database: DatabaseHarness) -> None:
    invalid = indexed_chunk(
        chunk_id=uuid4(),
        sequence_no=9,
        content="合成错误维度样本",
        embedding=vector(1.0)[:-1],
    )

    with database.sessions() as session, pytest.raises(ValueError, match="1024"):
        SqlAlchemySearchIndex(session).add(invalid)
