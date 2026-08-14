from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from ai_platform_api.modules.knowledge.application.facts import (
    KnowledgeConflictError,
    KnowledgeDeniedError,
    KnowledgeFactService,
    KnowledgeNotFoundError,
    KnowledgeQuotaExceededError,
    KnowledgeValidationError,
)
from ai_platform_api.modules.knowledge.infrastructure.sqlalchemy import (
    SqlAlchemyKnowledgeUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    audit_records,
    document_publications,
    document_sources,
    document_versions,
    documents,
    ingestion_jobs,
    knowledge_bases,
    outbox_events,
    workspace_entitlements,
    workspace_usage_counters,
)
from ai_platform_worker.modules.ingestion.infrastructure.jobs_sqlalchemy import (
    SqlAlchemyIngestionJobStore,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext.continue_from("00-d123456789abcdef0123456789abcdef-d123456789abcdef-01")
CONTENT_HASH_1 = "1" * 64
CONTENT_HASH_2 = "2" * 64
NOW = datetime(2026, 8, 14, 9, 30, tzinfo=UTC)


@dataclass(frozen=True)
class RegisteredAccount:
    account_id: UUID
    personal_workspace_id: UUID
    login_name: str


@dataclass(frozen=True)
class KnowledgeHarness:
    engine: Engine
    sessions: sessionmaker[Session]
    registration: RegistrationService
    enterprise: EnterpriseWorkspaceService
    organization: OrganizationService
    knowledge: KnowledgeFactService


@pytest.fixture(scope="module")
def knowledge_database() -> Iterator[KnowledgeHarness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p1d01_test_{uuid4().hex}"
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
        yield KnowledgeHarness(
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
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def register(harness: KnowledgeHarness, *, identity: str) -> RegisteredAccount:
    login_name = f"synthetic.knowledge.{identity}.{uuid4().hex}@example.com"
    result = harness.registration.register(
        login_name=login_name,
        display_name=f"合成知识用户 {identity}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return RegisteredAccount(result.account_id, result.personal_workspace_id, login_name)


def context(account: RegisteredAccount, workspace_id: UUID | None = None) -> RequestContext:
    return RequestContext.trusted(
        actor_id=account.account_id,
        user_id=account.account_id,
        workspace_id=workspace_id or account.personal_workspace_id,
        trace=TRACE,
        authentication_method="browser_session",
    )


def join_enterprise(
    harness: KnowledgeHarness,
    owner: RegisteredAccount,
    member: RegisteredAccount,
) -> tuple[UUID, RequestContext, RequestContext]:
    workspace = harness.enterprise.create(context(owner), name=f"合成知识企业 {uuid4().hex}")
    owner_context = context(owner, workspace.workspace_id)
    invitation = harness.enterprise.invite(
        owner_context,
        workspace_id=workspace.workspace_id,
        login_name=member.login_name,
    )
    harness.enterprise.accept_invitation(
        context(member),
        invitation_id=invitation.invitation_id,
    )
    return workspace.workspace_id, owner_context, context(member, workspace.workspace_id)


def create_ready_version(
    harness: KnowledgeHarness,
    owner_context: RequestContext,
    knowledge_base_id: UUID,
    document_id: UUID,
    *,
    content_hash: str,
) -> UUID:
    version, _ = harness.knowledge.create_document_version(
        owner_context,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        source_kind="manual",
        source_name="合成修订",
    )
    harness.knowledge.mark_document_version_ready(
        owner_context,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        document_version_id=version.document_version_id,
        content_hash=content_hash,
    )
    return version.document_version_id


def test_personal_fact_lifecycle_publication_and_transaction_records(
    knowledge_database: KnowledgeHarness,
) -> None:
    owner = register(knowledge_database, identity="personal-owner")
    owner_context = context(owner)
    knowledge_base = knowledge_database.knowledge.create_knowledge_base(
        owner_context,
        name=" 合成本地知识库 ",
        default_visibility="workspace",
    )
    document, first_version, source = knowledge_database.knowledge.create_document(
        owner_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
        title="合成制度文档",
        source_kind="upload",
        source_name="synthetic-policy.pdf",
        original_object_key=(
            f"workspaces/{owner.personal_workspace_id}/uploads/00000000000000000000000000000001.pdf"
        ),
        upload_media_type="application/pdf",
        upload_size_bytes=1024,
        upload_content_hash=CONTENT_HASH_1,
        upload_scan_status="clean",
        upload_scanner_version="synthetic-scanner-v1",
        upload_scanned_at=NOW,
    )
    first_ready = knowledge_database.knowledge.mark_document_version_ready(
        owner_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
        document_id=document.document_id,
        document_version_id=first_version.document_version_id,
        content_hash=CONTENT_HASH_1,
    )
    first_published = knowledge_database.knowledge.publish_document_version(
        owner_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
        document_id=document.document_id,
        document_version_id=first_ready.document_version_id,
    )
    second_version_id = create_ready_version(
        knowledge_database,
        owner_context,
        knowledge_base.knowledge_base_id,
        document.document_id,
        content_hash=CONTENT_HASH_2,
    )
    second_published = knowledge_database.knowledge.publish_document_version(
        owner_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
        document_id=document.document_id,
        document_version_id=second_version_id,
    )

    expected_object_key = (
        f"workspaces/{owner.personal_workspace_id}/uploads/00000000000000000000000000000001.pdf"
    )
    assert source.original_object_key == expected_object_key
    assert first_published.status == second_published.status == "published"
    with knowledge_database.engine.connect() as connection:
        stored_versions = [
            tuple(row)
            for row in connection.execute(
                select(
                    document_versions.c.document_version_id,
                    document_versions.c.version_number,
                    document_versions.c.status,
                    document_versions.c.content_hash,
                )
                .where(document_versions.c.document_id == document.document_id)
                .order_by(document_versions.c.version_number)
            )
        ]
        assert stored_versions == [
            (first_version.document_version_id, 1, "superseded", CONTENT_HASH_1),
            (second_version_id, 2, "published", CONTENT_HASH_2),
        ]
        assert (
            connection.scalar(
                select(document_publications.c.current_document_version_id).where(
                    document_publications.c.document_id == document.document_id
                )
            )
            == second_version_id
        )
        assert (
            connection.scalar(
                select(document_sources.c.original_object_key).where(
                    document_sources.c.source_id == source.source_id
                )
            )
            == expected_object_key
        )
        stored_job = connection.execute(
            select(
                ingestion_jobs.c.workspace_id,
                ingestion_jobs.c.document_version_id,
                ingestion_jobs.c.source_id,
                ingestion_jobs.c.status,
                ingestion_jobs.c.attempt_count,
                ingestion_jobs.c.max_attempts,
                ingestion_jobs.c.source_object_key,
                ingestion_jobs.c.traceparent,
            ).where(ingestion_jobs.c.document_version_id == first_version.document_version_id)
        ).one()
        assert tuple(stored_job) == (
            owner.personal_workspace_id,
            first_version.document_version_id,
            source.source_id,
            "queued",
            0,
            3,
            expected_object_key,
            owner_context.trace.traceparent,
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(audit_records)
                .where(
                    audit_records.c.resource_id.in_(
                        (knowledge_base.knowledge_base_id, document.document_id)
                    )
                )
            )
            == 7
        )
        assert (
            connection.scalar(
                select(workspace_usage_counters.c.used_value).where(
                    workspace_usage_counters.c.workspace_id == owner.personal_workspace_id,
                    workspace_usage_counters.c.metric == "knowledge_bases",
                    workspace_usage_counters.c.period_key == "lifetime",
                )
            )
            == 1
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(outbox_events)
                .where(
                    outbox_events.c.aggregate_id.in_(
                        (knowledge_base.knowledge_base_id, document.document_id)
                    )
                )
            )
            == 7
        )

    with pytest.raises(KnowledgeConflictError):
        knowledge_database.knowledge.delete_knowledge_base(
            owner_context,
            knowledge_base_id=knowledge_base.knowledge_base_id,
        )
    knowledge_database.knowledge.delete_document(
        owner_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
        document_id=document.document_id,
    )
    deleted_base = knowledge_database.knowledge.delete_knowledge_base(
        owner_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
    )
    assert deleted_base.status == "deleted"
    with knowledge_database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(workspace_usage_counters.c.used_value).where(
                    workspace_usage_counters.c.workspace_id == owner.personal_workspace_id,
                    workspace_usage_counters.c.metric == "knowledge_bases",
                    workspace_usage_counters.c.period_key == "lifetime",
                )
            )
            == 0
        )


def test_ingestion_job_lease_and_retry_are_bounded(
    knowledge_database: KnowledgeHarness,
) -> None:
    owner = register(knowledge_database, identity="ingestion-retry-owner")
    owner_context = context(owner)
    knowledge_base = knowledge_database.knowledge.create_knowledge_base(
        owner_context,
        name="合成重试知识库",
        default_visibility="workspace",
    )
    document, version, _ = knowledge_database.knowledge.create_document(
        owner_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
        title="合成重试文档",
        source_kind="upload",
        source_name="synthetic.txt",
        original_object_key=(
            f"workspaces/{owner.personal_workspace_id}/uploads/00000000000000000000000000000002.txt"
        ),
        upload_media_type="text/plain",
        upload_size_bytes=32,
        upload_content_hash=CONTENT_HASH_1,
        upload_scan_status="clean",
        upload_scanner_version="synthetic-scanner-v1",
        upload_scanned_at=NOW,
    )
    store = SqlAlchemyIngestionJobStore(knowledge_database.sessions)
    current = datetime.now(UTC) + timedelta(seconds=1)
    with knowledge_database.engine.begin() as connection:
        connection.execute(
            update(ingestion_jobs)
            .where(ingestion_jobs.c.document_version_id != version.document_version_id)
            .values(available_at=current + timedelta(days=1))
        )
        connection.execute(
            update(ingestion_jobs)
            .where(ingestion_jobs.c.document_version_id == version.document_version_id)
            .values(available_at=datetime(2026, 1, 1, tzinfo=UTC))
        )

    for attempt in range(1, 4):
        claimed = store.claim_next(
            worker_id="synthetic-worker",
            now=current,
            lease_seconds=30,
        )
        assert claimed is not None
        assert claimed.document_id == document.document_id
        assert claimed.document_version_id == version.document_version_id
        assert claimed.attempt_count == attempt
        status = store.mark_failed(
            claimed,
            stage="parse",
            error_code="INGESTION_PARSER_UNAVAILABLE",
            error_message="合成解析服务不可用",
            retryable=True,
            failed_at=current,
            next_attempt_at=current + timedelta(seconds=5),
        )
        assert status == ("failed" if attempt == 3 else "retry_wait")
        current += timedelta(seconds=5)

    with knowledge_database.engine.connect() as connection:
        stored = connection.execute(
            select(
                ingestion_jobs.c.status,
                ingestion_jobs.c.attempt_count,
                ingestion_jobs.c.failure_stage,
                ingestion_jobs.c.error_code,
                ingestion_jobs.c.completed_at,
            ).where(ingestion_jobs.c.document_version_id == version.document_version_id)
        ).one()
    assert stored.status == "failed"
    assert stored.attempt_count == 3
    assert stored.failure_stage == "parse"
    assert stored.error_code == "INGESTION_PARSER_UNAVAILABLE"
    assert stored.completed_at is not None
    knowledge_database.knowledge.delete_document(
        owner_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
        document_id=document.document_id,
    )
    deleted_base = knowledge_database.knowledge.delete_knowledge_base(
        owner_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
    )
    assert deleted_base.status == "deleted"
    with knowledge_database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(workspace_usage_counters.c.used_value).where(
                    workspace_usage_counters.c.workspace_id == owner.personal_workspace_id,
                    workspace_usage_counters.c.metric == "knowledge_bases",
                    workspace_usage_counters.c.period_key == "lifetime",
                )
            )
            == 0
        )


def test_enterprise_owner_department_scope_and_member_denial(
    knowledge_database: KnowledgeHarness,
) -> None:
    owner = register(knowledge_database, identity="enterprise-owner")
    member = register(knowledge_database, identity="enterprise-member")
    workspace_id, owner_context, member_context = join_enterprise(knowledge_database, owner, member)
    department = knowledge_database.organization.create_department(
        owner_context,
        workspace_id=workspace_id,
        name="合成研发部",
        parent_department_id=None,
    )
    knowledge_base = knowledge_database.knowledge.create_knowledge_base(
        owner_context,
        name="企业部门知识库",
        default_visibility="departments",
        department_ids=frozenset({department.department_id}),
        default_security_level="CONFIDENTIAL",
    )
    document, _, _ = knowledge_database.knowledge.create_document(
        owner_context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
        title="部门合成文档",
        source_kind="manual",
        source_name="人工合成来源",
    )

    assert document.department_ids == frozenset({department.department_id})
    assert document.security_level == "CONFIDENTIAL"
    with pytest.raises(KnowledgeDeniedError):
        knowledge_database.knowledge.create_knowledge_base(
            member_context,
            name="普通成员越权知识库",
        )
    with pytest.raises(KnowledgeValidationError):
        knowledge_database.knowledge.create_knowledge_base(
            owner_context,
            name="跨空间部门知识库",
            default_visibility="departments",
            department_ids=frozenset({uuid4()}),
        )


def test_quota_duplicate_name_cross_workspace_and_wrong_parent_are_rejected(
    knowledge_database: KnowledgeHarness,
) -> None:
    first_owner = register(knowledge_database, identity="constraint-first")
    second_owner = register(knowledge_database, identity="constraint-second")
    first_context = context(first_owner)
    first_base = knowledge_database.knowledge.create_knowledge_base(
        first_context,
        name="唯一合成知识库",
    )
    with pytest.raises(KnowledgeConflictError):
        knowledge_database.knowledge.create_knowledge_base(
            first_context,
            name="唯一合成知识库",
        )

    with knowledge_database.engine.begin() as connection:
        connection.execute(
            update(workspace_entitlements)
            .where(workspace_entitlements.c.workspace_id == first_owner.personal_workspace_id)
            .values(max_knowledge_bases=1)
        )
    with pytest.raises(KnowledgeQuotaExceededError):
        knowledge_database.knowledge.create_knowledge_base(
            first_context,
            name="超过配额的知识库",
        )

    with pytest.raises(KnowledgeNotFoundError):
        knowledge_database.knowledge.create_document(
            context(second_owner),
            knowledge_base_id=first_base.knowledge_base_id,
            title="跨空间服务读取",
            source_kind="manual",
            source_name="合成来源",
        )

    now = first_base.created_at
    with knowledge_database.engine.connect() as connection:
        transaction = connection.begin()
        with pytest.raises(IntegrityError):
            connection.execute(
                insert(documents).values(
                    document_id=uuid4(),
                    workspace_id=second_owner.personal_workspace_id,
                    knowledge_base_id=first_base.knowledge_base_id,
                    title="跨空间复合外键",
                    visibility="private",
                    department_ids=[],
                    security_level="INTERNAL",
                    permission_labels=[],
                    status="active",
                    created_by_account_id=second_owner.account_id,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                    version=1,
                )
            )
        transaction.rollback()

    other_base = knowledge_database.knowledge.create_knowledge_base(
        context(second_owner),
        name="另一合成知识库",
    )
    other_document, _, _ = knowledge_database.knowledge.create_document(
        context(second_owner),
        knowledge_base_id=other_base.knowledge_base_id,
        title="另一文档",
        source_kind="manual",
        source_name="合成来源",
    )
    with pytest.raises(KnowledgeNotFoundError):
        knowledge_database.knowledge.create_document_version(
            context(second_owner),
            knowledge_base_id=uuid4(),
            document_id=other_document.document_id,
            source_kind="manual",
            source_name="错误知识库父级",
        )

    with knowledge_database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(knowledge_bases)
                .where(
                    knowledge_bases.c.workspace_id == first_owner.personal_workspace_id,
                    knowledge_bases.c.status == "active",
                )
            )
            == 1
        )
