"""验证 P1D-04 索引版本租约、构建和原子切换。"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.enterprise import EnterpriseWorkspaceService
from ai_platform_api.modules.identity.application.organization import OrganizationService
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.enterprise_sqlalchemy import (
    SqlAlchemyEnterpriseUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.entitlements_sqlalchemy import (
    SqlAlchemyEntitlementRepository,
)
from ai_platform_api.modules.identity.infrastructure.organization_sqlalchemy import (
    SqlAlchemyOrganizationUnitOfWork,
)
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.modules.knowledge.application.facts import KnowledgeFactService
from ai_platform_api.modules.knowledge.infrastructure.sqlalchemy import (
    SqlAlchemyKnowledgeUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    document_index_publications,
    document_versions,
    index_versions,
    ingestion_jobs,
    retrieval_chunks,
)
from ai_platform_backend.indexing.domain import ClaimedIndexVersion
from ai_platform_backend.indexing.tokenization import TOKENIZER_VERSION
from ai_platform_worker.modules.indexing.application.build import IndexBuildProcessor
from ai_platform_worker.modules.indexing.infrastructure.embeddings import (
    DeterministicHashEmbeddingAdapter,
)
from ai_platform_worker.modules.indexing.infrastructure.sqlalchemy import (
    SqlAlchemyIndexVersionStore,
)
from ai_platform_worker.modules.ingestion.application.chunking import StructuralChunker
from ai_platform_worker.modules.ingestion.domain.documents import IngestionLimits
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, Row, create_engine, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext.continue_from("00-d423456789abcdef0123456789abcdef-d423456789abcdef-01")


@dataclass(frozen=True)
class RegisteredAccount:
    account_id: UUID
    personal_workspace_id: UUID


@dataclass(frozen=True)
class IndexHarness:
    engine: Engine
    sessions: sessionmaker[Session]
    registration: RegistrationService
    enterprise: EnterpriseWorkspaceService
    organization: OrganizationService
    knowledge: KnowledgeFactService
    store: SqlAlchemyIndexVersionStore


@dataclass
class MemoryArtifactStorage:
    payloads: dict[str, bytes]

    def read_artifact(self, version: ClaimedIndexVersion) -> bytes:
        return self.payloads[version.artifact_object_key]


@pytest.fixture(scope="module")
def index_database() -> Iterator[IndexHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1d04_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "infra/migrations"))
    config.set_main_option(
        "prepend_sys_path",
        f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
    )
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("ai_platform_schema", schema)
    command.upgrade(config, "head")

    engine = create_platform_engine(database_url, schema)
    sessions = create_session_factory(engine)
    reader = SqlAlchemyIdentityReader(sessions)
    try:
        yield IndexHarness(
            engine=engine,
            sessions=sessions,
            registration=RegistrationService(
                reader,
                SqlAlchemyRegistrationUnitOfWork(sessions),
                Argon2idPasswordAdapter(),
            ),
            enterprise=EnterpriseWorkspaceService(SqlAlchemyEnterpriseUnitOfWork(sessions)),
            organization=OrganizationService(SqlAlchemyOrganizationUnitOfWork(sessions)),
            knowledge=KnowledgeFactService(
                SqlAlchemyKnowledgeUnitOfWork(sessions, SqlAlchemyEntitlementRepository)
            ),
            store=SqlAlchemyIndexVersionStore(sessions),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def register(harness: IndexHarness) -> RegisteredAccount:
    result = harness.registration.register(
        login_name=f"synthetic.index.{uuid4().hex}@example.com",
        display_name="合成索引所有者",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return RegisteredAccount(result.account_id, result.personal_workspace_id)


def context(account: RegisteredAccount, workspace_id: UUID) -> RequestContext:
    return RequestContext.trusted(
        actor_id=account.account_id,
        user_id=account.account_id,
        workspace_id=workspace_id,
        trace=TRACE,
        authentication_method="browser_session",
    )


def parsed_artifact(
    row: Row[Any],
    *,
    text_content: str,
) -> bytes:
    values = row._mapping
    payload = {
        "schema_version": 1,
        "workspace_id": str(values["workspace_id"]),
        "knowledge_base_id": str(values["knowledge_base_id"]),
        "document_id": str(values["document_id"]),
        "document_version_id": str(values["document_version_id"]),
        "source_id": str(values["source_id"]),
        "media_type": "text/markdown",
        "parser_name": "markdown-structural-v1",
        "page_count": 1,
        "used_ocr": False,
        "metadata": {"synthetic": "true"},
        "blocks": [
            {
                "block_type": "heading",
                "text": "合成差旅制度",
                "source_position": {"page_number": 1, "line_start": 1, "line_end": 1},
            },
            {
                "block_type": "paragraph",
                "text": text_content,
                "source_position": {"page_number": 1, "line_start": 2, "line_end": 2},
            },
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def complete_ingestion(
    harness: IndexHarness,
    document_version_id: UUID,
    *,
    text_content: str,
    completed_at: datetime,
) -> tuple[str, bytes]:
    with harness.engine.begin() as connection:
        row = connection.execute(
            select(ingestion_jobs).where(
                ingestion_jobs.c.document_version_id == document_version_id
            )
        ).one()
        payload = parsed_artifact(row, text_content=text_content)
        artifact_object_key = (
            f"workspaces/{row.workspace_id}/parsed/{document_version_id}/"
            f"{row.ingestion_job_id}.json"
        )
        connection.execute(
            update(ingestion_jobs)
            .where(ingestion_jobs.c.ingestion_job_id == row.ingestion_job_id)
            .values(
                status="succeeded",
                completed_at=completed_at,
                artifact_object_key=artifact_object_key,
                parsed_content_hash=hashlib.sha256(payload).hexdigest(),
                parser_name="markdown-structural-v1",
                ocr_used=False,
                page_count=1,
                block_count=2,
                updated_at=completed_at,
            )
        )
    return artifact_object_key, payload


def processor(harness: IndexHarness, payloads: dict[str, bytes]) -> IndexBuildProcessor:
    return IndexBuildProcessor(
        harness.store,
        MemoryArtifactStorage(payloads),
        StructuralChunker(),
        DeterministicHashEmbeddingAdapter(),
        IngestionLimits(1024 * 1024, 10, 256, 32),
        worker_id="synthetic-index-worker",
        lease_seconds=120,
        retry_base_seconds=5,
        max_attempts=3,
        chunker_version="structural-char-v1",
        tokenizer_version=TOKENIZER_VERSION,
    )


def upload_version(
    harness: IndexHarness,
    request_context: RequestContext,
    knowledge_base_id: UUID,
    document_id: UUID | None,
    *,
    source_content: bytes,
) -> tuple[UUID, UUID]:
    source_id = uuid4()
    object_key = f"workspaces/{request_context.workspace_id}/uploads/{source_id}.md"
    content_hash = hashlib.sha256(source_content).hexdigest()
    scanned_at = datetime.now(UTC)
    if document_id is None:
        document, version, _ = harness.knowledge.create_document(
            request_context,
            knowledge_base_id=knowledge_base_id,
            title="合成部门制度",
            source_kind="upload",
            source_name="synthetic-policy.md",
            original_object_key=object_key,
            permission_labels=frozenset({"finance", "synthetic"}),
            upload_media_type="text/markdown",
            upload_size_bytes=len(source_content),
            upload_content_hash=content_hash,
            upload_scan_status="clean",
            upload_scanner_version="synthetic-scanner-v1",
            upload_scanned_at=scanned_at,
        )
        return document.document_id, version.document_version_id
    version, _ = harness.knowledge.create_document_version(
        request_context,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        source_kind="upload",
        source_name="synthetic-policy.md",
        original_object_key=object_key,
        upload_media_type="text/markdown",
        upload_size_bytes=len(source_content),
        upload_content_hash=content_hash,
        upload_scan_status="clean",
        upload_scanner_version="synthetic-scanner-v1",
        upload_scanned_at=scanned_at,
    )
    return document_id, version.document_version_id


def test_index_build_publication_rebuild_and_revocation_are_atomic(
    index_database: IndexHarness,
) -> None:
    owner = register(index_database)
    workspace = index_database.enterprise.create(
        context(owner, owner.personal_workspace_id),
        name=f"合成索引企业 {uuid4().hex}",
    )
    owner_context = context(owner, workspace.workspace_id)
    department = index_database.organization.create_department(
        owner_context,
        workspace_id=workspace.workspace_id,
        name="合成财务部",
        parent_department_id=None,
    )
    knowledge_base = index_database.knowledge.create_knowledge_base(
        owner_context,
        name="合成索引知识库",
        default_visibility="departments",
        department_ids=frozenset({department.department_id}),
        default_security_level="CONFIDENTIAL",
    )
    current = datetime.now(UTC) + timedelta(seconds=1)
    document_id, first_version_id = upload_version(
        index_database,
        owner_context,
        knowledge_base.knowledge_base_id,
        None,
        source_content=b"synthetic-v1",
    )
    first_key, first_artifact = complete_ingestion(
        index_database,
        first_version_id,
        text_content="第一版差旅报销必须在三十天内提交。",
        completed_at=current,
    )
    payloads = {first_key: first_artifact}
    worker = processor(index_database, payloads)

    first_result = worker.run_batch(limit=1, now=current)

    assert (first_result.enqueued, first_result.succeeded) == (1, 1)
    with index_database.engine.connect() as connection:
        first_index = connection.execute(
            select(index_versions).where(index_versions.c.document_version_id == first_version_id)
        ).one()
        first_chunk = connection.execute(
            select(retrieval_chunks).where(
                retrieval_chunks.c.index_version_id == first_index.index_version_id
            )
        ).one()
        assert first_index.status == "ready"
        assert first_chunk.active is False
        assert tuple(first_chunk.department_ids) == (department.department_id,)
        assert tuple(first_chunk.permission_labels) == ("finance", "synthetic")
        assert first_chunk.security_level == "CONFIDENTIAL"
        assert first_chunk.ingestion_job_id == first_index.ingestion_job_id
        assert first_chunk.source_id == first_index.source_id
        assert first_chunk.parsed_content_hash == hashlib.sha256(first_artifact).hexdigest()

    index_database.knowledge.publish_document_version(
        owner_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
        document_id=document_id,
        document_version_id=first_version_id,
    )
    with index_database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(retrieval_chunks)
                .where(retrieval_chunks.c.document_id == document_id, retrieval_chunks.c.active)
            )
            == 1
        )

    _, second_version_id = upload_version(
        index_database,
        owner_context,
        knowledge_base.knowledge_base_id,
        document_id,
        source_content=b"synthetic-v2",
    )
    second_key, second_artifact = complete_ingestion(
        index_database,
        second_version_id,
        text_content="第二版差旅报销必须在十五天内提交。",
        completed_at=current + timedelta(seconds=1),
    )
    index_database.knowledge.mark_document_version_ready(
        owner_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
        document_id=document_id,
        document_version_id=second_version_id,
        content_hash=hashlib.sha256(b"synthetic-v2").hexdigest(),
    )
    index_database.knowledge.publish_document_version(
        owner_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
        document_id=document_id,
        document_version_id=second_version_id,
    )

    # 新文档版本已发布但索引未就绪时，旧版本必须立即停止召回。
    with index_database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(retrieval_chunks)
                .where(retrieval_chunks.c.document_id == document_id, retrieval_chunks.c.active)
            )
            == 0
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(document_index_publications)
                .where(document_index_publications.c.document_id == document_id)
            )
            == 0
        )

    payloads.clear()
    payloads[second_key] = second_artifact
    second_result = worker.run_batch(limit=1, now=current + timedelta(seconds=2))
    assert (second_result.enqueued, second_result.succeeded) == (1, 1)

    rebuilt_id = index_database.store.enqueue_rebuild(
        workspace_id=workspace.workspace_id,
        document_version_id=second_version_id,
        now=current + timedelta(seconds=3),
        max_attempts=3,
        chunker_version="structural-char-v1",
        embedding_model_version="deterministic-hash-1024-v1",
        tokenizer_version=TOKENIZER_VERSION,
    )
    rebuild_result = worker.run_batch(limit=1, now=current + timedelta(seconds=4))
    assert rebuild_result.succeeded == 1

    with index_database.engine.connect() as connection:
        second_indexes = connection.execute(
            select(
                index_versions.c.index_version_id,
                index_versions.c.build_no,
                index_versions.c.status,
            )
            .where(index_versions.c.document_version_id == second_version_id)
            .order_by(index_versions.c.build_no)
        ).all()
        assert [(row.build_no, row.status) for row in second_indexes] == [
            (1, "retired"),
            (2, "active"),
        ]
        assert second_indexes[1].index_version_id == rebuilt_id
        assert (
            connection.scalar(
                select(document_index_publications.c.index_version_id).where(
                    document_index_publications.c.document_id == document_id
                )
            )
            == rebuilt_id
        )

    _, third_version_id = upload_version(
        index_database,
        owner_context,
        knowledge_base.knowledge_base_id,
        document_id,
        source_content=b"synthetic-v3",
    )
    third_key, third_artifact = complete_ingestion(
        index_database,
        third_version_id,
        text_content="第三版仅用于撤权测试。",
        completed_at=current + timedelta(seconds=5),
    )
    payloads.clear()
    payloads[third_key] = third_artifact
    assert (
        index_database.store.ensure_queued(
            now=current + timedelta(seconds=6),
            max_attempts=3,
            chunker_version="structural-char-v1",
            embedding_model_version="deterministic-hash-1024-v1",
            tokenizer_version=TOKENIZER_VERSION,
        )
        == 1
    )

    index_database.knowledge.delete_document(
        owner_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
        document_id=document_id,
    )
    with index_database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(retrieval_chunks)
                .where(retrieval_chunks.c.document_id == document_id, retrieval_chunks.c.active)
            )
            == 0
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(document_index_publications)
                .where(document_index_publications.c.document_id == document_id)
            )
            == 0
        )
        revoked = connection.execute(
            select(index_versions.c.status, index_versions.c.error_code).where(
                index_versions.c.document_version_id == third_version_id
            )
        ).one()
        assert tuple(revoked) == ("failed", "INDEX_DOCUMENT_REVOKED")
        assert (
            connection.scalar(
                select(document_versions.c.status).where(
                    document_versions.c.document_version_id == third_version_id
                )
            )
            == "draft"
        )
