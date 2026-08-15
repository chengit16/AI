"""验证 P1E-02 权限前过滤和不可变检索计划的 PostgreSQL 闭环。"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.assistant.application.service import AssistantConversationService
from ai_platform_api.modules.assistant.infrastructure.sqlalchemy import (
    SqlAlchemyAssistantUnitOfWork,
)
from ai_platform_api.modules.authorization.application.field_registry import (
    load_field_policy_registry,
)
from ai_platform_api.modules.authorization.domain.policy import (
    PolicyDecision,
    PolicyRequest,
    ResourceScope,
)
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.entitlements_sqlalchemy import (
    SqlAlchemyEntitlementRepository,
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
from ai_platform_api.modules.retrieval.application.planning import (
    BoundedRetrievalPlanningService,
)
from ai_platform_api.modules.retrieval.infrastructure.planning_sqlalchemy import (
    SqlAlchemyRetrievalPlanningUnitOfWork,
)
from ai_platform_api.modules.retrieval.infrastructure.reranking import (
    DeterministicLexicalReranker,
)
from ai_platform_api.modules.service_governance.infrastructure.sqlalchemy import (
    SqlAlchemyServiceRepository,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    ai_runtime_config_publication,
    ai_runtime_config_versions,
    document_index_publications,
    index_versions,
    ingestion_jobs,
    retrieval_candidate_snapshots,
    retrieval_chunks,
    retrieval_plans,
    retrieval_query_variants,
)
from ai_platform_backend.indexing.embeddings import DeterministicHashEmbeddingAdapter
from ai_platform_backend.indexing.tokenization import TOKENIZER_VERSION, keyword_document
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext.continue_from("00-e523456789abcdef0123456789abcdef-e523456789abcdef-01")


@dataclass(frozen=True)
class RegisteredAccount:
    account_id: UUID
    workspace_id: UUID


@dataclass(frozen=True)
class RetrievalHarness:
    engine: Engine
    sessions: sessionmaker[Session]
    registration: RegistrationService
    assistant: AssistantConversationService
    knowledge: KnowledgeFactService
    planning: BoundedRetrievalPlanningService


class AllowInternalWorkspacePolicy:
    """模拟已由 PDP 签发的工作空间级内部密级授权。"""

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            decision_id=UUID("90000000-0000-4000-8000-000000000502"),
            decision="allow",
            permission_code=request.permission_code,
            workspace_id=request.context.workspace_id,
            resource_scope=ResourceScope(workspace=True),
            field_mask=frozenset(),
            policy_version=1,
            cache_ttl_seconds=0,
            reason="synthetic_workspace_access",
            maximum_security_level="INTERNAL",
        )


def create_retrieval_harness(*, schema_prefix: str) -> Iterator[RetrievalHarness]:
    """创建可由相邻检索节点复用且相互隔离的 PostgreSQL 测试环境。"""

    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"{schema_prefix}_{uuid4().hex}"
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
        yield RetrievalHarness(
            engine=engine,
            sessions=sessions,
            registration=RegistrationService(
                reader,
                SqlAlchemyRegistrationUnitOfWork(sessions),
                Argon2idPasswordAdapter(),
            ),
            assistant=AssistantConversationService(
                SqlAlchemyAssistantUnitOfWork(sessions, SqlAlchemyServiceRepository)
            ),
            knowledge=KnowledgeFactService(
                SqlAlchemyKnowledgeUnitOfWork(sessions, SqlAlchemyEntitlementRepository)
            ),
            planning=BoundedRetrievalPlanningService(
                SqlAlchemyRetrievalPlanningUnitOfWork(sessions),
                AllowInternalWorkspacePolicy(),
                load_field_policy_registry(
                    ROOT / "contracts/authorization/field-policy-registry.v1.json"
                ),
                DeterministicHashEmbeddingAdapter(),
            ),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture(scope="module")
def retrieval_database() -> Iterator[RetrievalHarness]:
    """为 P1E-02 提供独占 Schema，避免并行节点共享可变测试事实。"""

    yield from create_retrieval_harness(schema_prefix="p1e02_test")


def register(harness: RetrievalHarness, identity: str) -> RegisteredAccount:
    result = harness.registration.register(
        login_name=f"synthetic.retrieval.{identity}.{uuid4().hex}@example.com",
        display_name=f"合成检索用户 {identity}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return RegisteredAccount(result.account_id, result.personal_workspace_id)


def context(account: RegisteredAccount) -> RequestContext:
    return RequestContext.trusted(
        actor_id=account.account_id,
        user_id=account.account_id,
        workspace_id=account.workspace_id,
        trace=TRACE,
        authentication_method="browser_session",
    )


def publish_runtime_config(harness: RetrievalHarness, account_id: UUID) -> None:
    """发布只依赖本地确定性组件的合成运行配置。"""

    runtime_config_version_id = uuid4()
    now = datetime.now(UTC)
    with harness.sessions.begin() as session:
        if session.scalar(select(func.count()).select_from(ai_runtime_config_versions)):
            return
        session.execute(
            insert(ai_runtime_config_versions).values(
                runtime_config_version_id=runtime_config_version_id,
                version_number=1,
                display_name="P1E-02 合成运行配置",
                content_hash="1" * 64,
                system_prompt_template="只使用经过授权的合成知识证据回答。",
                system_prompt_hash="2" * 64,
                component_versions={
                    "embedding": DeterministicHashEmbeddingAdapter.model_version,
                    "retrieval": "hybrid-rrf-v1",
                    "reranker": DeterministicLexicalReranker.model_version,
                    "source_ranking": "source-priority-v1",
                    "safety": "rag-safety-v2",
                },
                attempt_timeout_ms=500,
                total_timeout_ms=2_000,
                max_attempts_per_route=1,
                max_prompt_characters=4_000,
                max_output_tokens=256,
                max_response_characters=8_000,
                circuit_failure_threshold=3,
                circuit_recovery_ms=30_000,
                rule_degradation_message=None,
                max_estimated_cost_microunits=5_000_000,
                created_by_account_id=account_id,
                created_at=now,
            )
        )
        session.execute(
            insert(ai_runtime_config_publication).values(
                publication_key="current",
                runtime_config_version_id=runtime_config_version_id,
                generation=1,
                published_by_account_id=account_id,
                published_at=now,
            )
        )


def create_indexed_document(
    harness: RetrievalHarness,
    account: RegisteredAccount,
    knowledge_base_id: UUID,
    *,
    title: str,
    content: str,
    visibility: str,
    security_level: str,
) -> UUID:
    """建立满足索引约束的合成文档、激活版本与检索 Chunk。"""

    request_context = context(account)
    upload_hash = hashlib.sha256(content.encode()).hexdigest()
    scanned_at = datetime.now(UTC)
    upload_object_key = f"workspaces/{account.workspace_id}/uploads/{uuid4()}.md"
    document, version, source = harness.knowledge.create_document(
        request_context,
        knowledge_base_id=knowledge_base_id,
        title=title,
        source_kind="upload",
        source_name=f"{title}.md",
        original_object_key=upload_object_key,
        visibility=visibility,  # type: ignore[arg-type]
        security_level=security_level,  # type: ignore[arg-type]
        upload_media_type="text/markdown",
        upload_size_bytes=len(content.encode()),
        upload_content_hash=upload_hash,
        upload_scan_status="clean",
        upload_scanner_version="synthetic-scanner-v1",
        upload_scanned_at=scanned_at,
    )
    now = datetime.now(UTC)
    index_version_id = uuid4()
    chunk_id = uuid4()
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    embedding = DeterministicHashEmbeddingAdapter().embed((content,))[0]
    with harness.sessions.begin() as session:
        ingestion_job_id = session.scalar(
            select(ingestion_jobs.c.ingestion_job_id).where(
                ingestion_jobs.c.document_version_id == version.document_version_id
            )
        )
        assert isinstance(ingestion_job_id, UUID)
        session.execute(
            insert(index_versions).values(
                index_version_id=index_version_id,
                workspace_id=account.workspace_id,
                knowledge_base_id=knowledge_base_id,
                document_id=document.document_id,
                document_version_id=version.document_version_id,
                ingestion_job_id=ingestion_job_id,
                source_id=source.source_id,
                build_no=1,
                artifact_object_key=f"synthetic/{index_version_id}.json",
                source_content_hash=content_hash,
                parsed_content_hash=content_hash,
                chunker_version="synthetic-char-v1",
                embedding_model_version=DeterministicHashEmbeddingAdapter.model_version,
                tokenizer_version=TOKENIZER_VERSION,
                department_ids=[],
                visibility=visibility,
                security_level=security_level,
                permission_labels=[],
                status="active",
                processing_lane="indexing",
                attempt_count=1,
                embedding_attempt_count=0,
                indexing_attempt_count=1,
                max_attempts=3,
                available_at=now,
                claimed_by=None,
                claim_until=None,
                started_at=now,
                completed_at=now,
                activated_at=now,
                chunk_count=1,
                staged_chunk_count=1,
                failure_stage=None,
                error_code=None,
                error_message=None,
                manual_recovery_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        session.execute(
            insert(document_index_publications).values(
                workspace_id=account.workspace_id,
                document_id=document.document_id,
                document_version_id=version.document_version_id,
                index_version_id=index_version_id,
                activated_at=now,
            )
        )
        session.execute(
            insert(retrieval_chunks).values(
                index_version_id=index_version_id,
                chunk_id=chunk_id,
                workspace_id=account.workspace_id,
                knowledge_base_id=knowledge_base_id,
                document_id=document.document_id,
                document_version_id=version.document_version_id,
                ingestion_job_id=None,
                source_id=None,
                sequence_no=1,
                content=content,
                content_hash=content_hash,
                embedding=list(embedding),
                keyword_text=keyword_document(content),
                department_ids=[],
                visibility=visibility,
                security_level=security_level,
                permission_labels=[],
                source_position={"page_number": 1},
                parsed_content_hash=None,
                parser_name=None,
                ocr_used=None,
                active=True,
            )
        )
    harness.knowledge.mark_document_version_ready(
        request_context,
        knowledge_base_id=knowledge_base_id,
        document_id=document.document_id,
        document_version_id=version.document_version_id,
        content_hash=content_hash,
    )
    harness.knowledge.publish_document_version(
        request_context,
        knowledge_base_id=knowledge_base_id,
        document_id=document.document_id,
        document_version_id=version.document_version_id,
    )
    return document.document_id


def test_permissions_bounded_search_and_immutable_snapshot(
    retrieval_database: RetrievalHarness,
) -> None:
    owner = register(retrieval_database, "owner")
    outsider = register(retrieval_database, "outsider")
    publish_runtime_config(retrieval_database, owner.account_id)
    owner_context = context(owner)
    knowledge_base = retrieval_database.knowledge.create_knowledge_base(
        owner_context,
        name="合成授权检索知识库",
        default_visibility="workspace",
        default_security_level="INTERNAL",
    )
    allowed_workspace_id = create_indexed_document(
        retrieval_database,
        owner,
        knowledge_base.knowledge_base_id,
        title="空间差旅制度",
        content="差旅报销必须在三十天内提交。",
        visibility="workspace",
        security_level="INTERNAL",
    )
    allowed_private_id = create_indexed_document(
        retrieval_database,
        owner,
        knowledge_base.knowledge_base_id,
        title="个人差旅补充",
        content="差旅报销必须附带合成票据。",
        visibility="private",
        security_level="INTERNAL",
    )
    restricted_id = create_indexed_document(
        retrieval_database,
        owner,
        knowledge_base.knowledge_base_id,
        title="受限差旅制度",
        content="差旅报销受限规则不得进入内部级检索。",
        visibility="workspace",
        security_level="RESTRICTED",
    )
    outsider_base = retrieval_database.knowledge.create_knowledge_base(
        context(outsider),
        name="外部合成知识库",
        default_visibility="workspace",
        default_security_level="INTERNAL",
    )
    outsider_id = create_indexed_document(
        retrieval_database,
        outsider,
        outsider_base.knowledge_base_id,
        title="其他空间差旅制度",
        content="差旅报销其他空间规则不得跨空间召回。",
        visibility="workspace",
        security_level="INTERNAL",
    )
    conversation = retrieval_database.assistant.create_conversation(
        owner_context,
        title="P1E-02 合成检索会话",
    )
    submitted = retrieval_database.assistant.create_user_message(
        owner_context,
        conversation_id=conversation.conversation_id,
        texts=("请总结差旅报销制度",),
        idempotency_key="p1e02-synthetic-message-0001",
    )

    first = retrieval_database.planning.retrieve(owner_context, submitted.run.run_id)
    repeated = retrieval_database.planning.retrieve(owner_context, submitted.run.run_id)

    candidate_document_ids = {candidate.document_id for candidate in first.candidates}
    assert repeated == first
    assert candidate_document_ids == {allowed_workspace_id, allowed_private_id}
    assert restricted_id not in candidate_document_ids
    assert outsider_id not in candidate_document_ids
    assert len(first.variants) <= first.budget.max_query_variants == 3
    assert first.search_operation_count <= first.budget.max_search_operations == 6
    assert all(candidate.source_position == {} for candidate in first.candidates)
    assert all(not hasattr(candidate, "content") for candidate in first.candidates)

    with retrieval_database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(retrieval_plans)) == 1
        assert session.scalar(select(func.count()).select_from(retrieval_query_variants)) == 2
        assert session.scalar(select(func.count()).select_from(retrieval_candidate_snapshots)) == 2

    # 三类检索事实均由数据库触发器保护，维护 SQL 也不能覆盖或删除历史证据。
    with pytest.raises(DBAPIError), retrieval_database.sessions.begin() as session:
        session.execute(
            update(retrieval_plans)
            .where(retrieval_plans.c.retrieval_plan_id == first.retrieval_plan_id)
            .values(duration_ms=999)
        )
    with pytest.raises(DBAPIError), retrieval_database.sessions.begin() as session:
        session.execute(
            delete(retrieval_candidate_snapshots).where(
                retrieval_candidate_snapshots.c.retrieval_plan_id == first.retrieval_plan_id
            )
        )
