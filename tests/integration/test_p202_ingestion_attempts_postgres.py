"""验证 P2-02 入库阶段、Attempt、超时、取消和人工恢复的 PostgreSQL 事实。"""

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
from ai_platform_api.modules.knowledge.application.management import KnowledgeManagementService
from ai_platform_api.modules.knowledge.infrastructure.sqlalchemy import (
    SqlAlchemyKnowledgeUnitOfWork,
)
from ai_platform_api.persistence.database import create_platform_engine, create_session_factory
from ai_platform_api.persistence.tables import (
    audit_records,
    ingestion_job_attempts,
    ingestion_job_stages,
    ingestion_jobs,
    outbox_events,
)
from ai_platform_worker.modules.ingestion.domain.documents import (
    ParsedBlock,
    ParsedDocument,
    SourcePosition,
)
from ai_platform_worker.modules.ingestion.domain.jobs import ParsedArtifact
from ai_platform_worker.modules.ingestion.infrastructure.jobs_sqlalchemy import (
    SqlAlchemyIngestionJobStore,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
TRACE = TraceContext.continue_from("00-d623456789abcdef0123456789abcdef-d623456789abcdef-01")


@dataclass(frozen=True)
class P202Harness:
    engine: Engine
    sessions: sessionmaker[Session]
    account_id: UUID
    workspace_id: UUID
    context: RequestContext
    knowledge: KnowledgeFactService
    management: KnowledgeManagementService
    store: SqlAlchemyIngestionJobStore


@pytest.fixture(scope="module")
def p202_database() -> Iterator[P202Harness]:
    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p202_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    # 1. 从空 Schema 迁移到最新 Revision，确保新表、回填和不可变触发器真实生效。
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "infra/migrations"))
    config.set_main_option(
        "prepend_sys_path",
        f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
    )
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("ai_platform_schema", schema)
    command.upgrade(config, "head")

    # 2. 使用正式 Registration、Knowledge UoW 和 Worker Store，避免内存替身掩盖事务竞态。
    engine = create_platform_engine(database_url, schema)
    sessions = create_session_factory(engine)
    registration = RegistrationService(
        SqlAlchemyIdentityReader(sessions),
        SqlAlchemyRegistrationUnitOfWork(sessions),
        Argon2idPasswordAdapter(),
    )
    registered = registration.register(
        login_name=f"synthetic.p202.{uuid4().hex}@example.com",
        display_name="合成 P2-02 账号",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    context = RequestContext.trusted(
        actor_id=registered.account_id,
        user_id=registered.account_id,
        workspace_id=registered.personal_workspace_id,
        trace=TRACE,
        authentication_method="browser_session",
    )
    unit_of_work = SqlAlchemyKnowledgeUnitOfWork(sessions, SqlAlchemyEntitlementRepository)
    try:
        yield P202Harness(
            engine=engine,
            sessions=sessions,
            account_id=registered.account_id,
            workspace_id=registered.personal_workspace_id,
            context=context,
            knowledge=KnowledgeFactService(unit_of_work),
            management=KnowledgeManagementService(unit_of_work),
            store=SqlAlchemyIngestionJobStore(sessions),
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def create_job(
    harness: P202Harness,
    label: str,
    *,
    extension: str = ".txt",
) -> tuple[UUID, UUID]:
    now = datetime.now(UTC)
    knowledge_base = harness.knowledge.create_knowledge_base(
        harness.context,
        name=f"合成可靠性知识库 {label}",
    )
    _, version, _ = harness.knowledge.create_document(
        harness.context,
        knowledge_base_id=knowledge_base.knowledge_base_id,
        title=f"合成可靠性文档 {label}",
        source_kind="upload",
        source_name=f"synthetic-{label}{extension}",
        original_object_key=(f"workspaces/{harness.workspace_id}/uploads/{uuid4().hex}{extension}"),
        upload_media_type="application/pdf" if extension == ".pdf" else "text/plain",
        upload_size_bytes=128,
        upload_content_hash="a" * 64,
        upload_scan_status="clean",
        upload_scanner_version="synthetic-scanner-v1",
        upload_scanned_at=now,
    )
    with harness.engine.connect() as connection:
        job_id = connection.scalar(
            select(ingestion_jobs.c.ingestion_job_id).where(
                ingestion_jobs.c.document_version_id == version.document_version_id
            )
        )
    assert isinstance(job_id, UUID)
    return job_id, version.document_version_id


def test_parsing_and_ocr_lanes_never_claim_each_others_jobs(
    p202_database: P202Harness,
) -> None:
    parsing_id, _ = create_job(p202_database, "parsing-lane")
    ocr_id, _ = create_job(p202_database, "ocr-lane", extension=".pdf")
    now = datetime.now(UTC) + timedelta(seconds=1)

    # 1. 来源类型在 API 写入时即冻结，任务与阶段使用同一个 Lane 事实。
    with p202_database.engine.connect() as connection:
        lanes = connection.execute(
            select(
                ingestion_jobs.c.ingestion_job_id,
                ingestion_jobs.c.processing_lane,
                ingestion_job_stages.c.stage_key,
            )
            .join(
                ingestion_job_stages,
                ingestion_job_stages.c.ingestion_job_id == ingestion_jobs.c.ingestion_job_id,
            )
            .where(ingestion_jobs.c.ingestion_job_id.in_((parsing_id, ocr_id)))
        ).all()
    assert {row.ingestion_job_id: (row.processing_lane, row.stage_key) for row in lanes} == {
        parsing_id: ("parsing", "parsing"),
        ocr_id: ("ocr", "ocr"),
    }

    # 2. 两个 Worker 即使同时扫描，也只能取得自己的持久化 Lane。
    parsing_claim = p202_database.store.claim_next(
        worker_id="p203-parsing-worker",
        lane="parsing",
        now=now,
        lease_seconds=60,
    )
    ocr_claim = p202_database.store.claim_next(
        worker_id="p203-ocr-worker",
        lane="ocr",
        now=now,
        lease_seconds=60,
    )
    assert parsing_claim is not None and parsing_claim.ingestion_job_id == parsing_id
    assert ocr_claim is not None and ocr_claim.ingestion_job_id == ocr_id

    # 3. 测试结束时形成稳定终态，避免模块内后续扫描拾取遗留任务。
    for claim in (parsing_claim, ocr_claim):
        assert (
            p202_database.store.mark_failed(
                claim,
                stage="parse" if claim is parsing_claim else "ocr",
                error_code="INGESTION_SYNTHETIC_STOP",
                error_message="合成 Lane 隔离测试已结束",
                retryable=False,
                failed_at=now,
                next_attempt_at=now,
            )
            == "failed"
        )


def artifact(harness: P202Harness, job_id: UUID, version_id: UUID) -> ParsedArtifact:
    document = ParsedDocument(
        media_type="text/plain",
        parser_name="synthetic-p202-parser",
        page_count=1,
        used_ocr=False,
        blocks=(
            ParsedBlock(
                "paragraph",
                "合成 P2-02 解析结果",
                SourcePosition(line_start=1, line_end=1),
            ),
        ),
    )
    return ParsedArtifact(
        object_key=f"workspaces/{harness.workspace_id}/parsed/{version_id}/{job_id}.json",
        payload=b"synthetic-p202-artifact",
        content_hash="b" * 64,
        document=document,
    )


def test_attempt_history_prevents_stale_claim_and_duplicate_side_effect(
    p202_database: P202Harness,
) -> None:
    job_id, version_id = create_job(p202_database, "attempt-history")
    now = datetime.now(UTC) + timedelta(seconds=1)

    # 1. 首次 Attempt 进入可重试终态，终态行不能被再次修改或删除。
    first = p202_database.store.claim_next(worker_id="p202-worker", now=now, lease_seconds=60)
    assert first is not None and first.ingestion_job_id == job_id
    assert first.generation == 0 and first.attempt_count == 1
    assert (
        p202_database.store.mark_failed(
            first,
            stage="parse",
            error_code="INGESTION_PARSER_UNAVAILABLE",
            error_message="合成解析依赖暂时不可用",
            retryable=True,
            failed_at=now,
            next_attempt_at=now + timedelta(seconds=1),
        )
        == "retry_wait"
    )
    with pytest.raises(DBAPIError), p202_database.engine.begin() as connection:
        connection.execute(
            update(ingestion_job_attempts)
            .where(ingestion_job_attempts.c.job_attempt_id == first.job_attempt_id)
            .values(error_message="禁止覆盖的历史错误")
        )

    # 2. 第二次 Attempt 使用新租约身份；即使 Worker 名称相同，旧执行也不能覆盖当前执行。
    second = p202_database.store.claim_next(
        worker_id="p202-worker",
        now=now + timedelta(seconds=2),
        lease_seconds=60,
    )
    assert second is not None and second.job_attempt_id != first.job_attempt_id
    assert second.attempt_count == 2
    assert (
        p202_database.store.mark_succeeded(
            first,
            artifact(p202_database, job_id, version_id),
            completed_at=now + timedelta(seconds=3),
        )
        is False
    )
    assert (
        p202_database.store.mark_succeeded(
            second,
            artifact(p202_database, job_id, version_id),
            completed_at=now + timedelta(seconds=4),
        )
        is True
    )

    # 3. 任务、阶段和两条 Attempt 形成一致终态，重复完成不会新增事实或再次发布。
    with p202_database.engine.connect() as connection:
        job_status = connection.scalar(
            select(ingestion_jobs.c.status).where(ingestion_jobs.c.ingestion_job_id == job_id)
        )
        stage = connection.execute(
            select(
                ingestion_job_stages.c.status,
                ingestion_job_stages.c.attempt_count,
            ).where(ingestion_job_stages.c.ingestion_job_id == job_id)
        ).one()
        attempts = connection.execute(
            select(
                ingestion_job_attempts.c.trigger,
                ingestion_job_attempts.c.status,
            )
            .where(ingestion_job_attempts.c.ingestion_job_id == job_id)
            .order_by(ingestion_job_attempts.c.attempt_no)
        ).all()
    assert job_status == "succeeded"
    assert tuple(stage) == ("succeeded", 2)
    assert [tuple(row) for row in attempts] == [
        ("automatic", "retry_wait"),
        ("automatic_retry", "succeeded"),
    ]


def test_timeout_manual_recovery_and_cancel_close_active_attempt_atomically(
    p202_database: P202Harness,
) -> None:
    job_id, version_id = create_job(p202_database, "timeout-cancel")
    now = datetime.now(UTC) + timedelta(seconds=1)
    with p202_database.engine.begin() as connection:
        connection.execute(
            update(ingestion_jobs)
            .where(ingestion_jobs.c.ingestion_job_id == job_id)
            .values(max_attempts=1)
        )

    # 1. 唯一租约耗尽后形成 timed_out 稳定终态，Attempt 保留独立超时证据。
    timed_claim = p202_database.store.claim_next(
        worker_id="p202-timeout-worker",
        now=now,
        lease_seconds=10,
    )
    assert timed_claim is not None and timed_claim.ingestion_job_id == job_id
    # 即使回收扫描尚未运行，租约截止后的迟到结果也必须立即失去写入资格。
    assert (
        p202_database.store.mark_succeeded(
            timed_claim,
            artifact(p202_database, job_id, version_id),
            completed_at=now + timedelta(seconds=11),
        )
        is False
    )
    assert (
        p202_database.store.claim_next(
            worker_id="p202-timeout-worker",
            now=now + timedelta(seconds=11),
            lease_seconds=10,
        )
        is None
    )
    with p202_database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(ingestion_jobs.c.status).where(ingestion_jobs.c.ingestion_job_id == job_id)
            )
            == "timed_out"
        )
        assert (
            connection.scalar(
                select(ingestion_job_attempts.c.status).where(
                    ingestion_job_attempts.c.job_attempt_id == timed_claim.job_attempt_id
                )
            )
            == "timed_out"
        )

    # 2. 人工恢复开启新 generation，取消操作同步关闭任务、阶段、Attempt、审计和 Outbox。
    recovered = p202_database.management.retry_ingestion_job(
        p202_database.context,
        ingestion_job_id=job_id,
    )
    assert recovered.status == "queued" and recovered.manual_retry_count == 1
    recovery_claim = p202_database.store.claim_next(
        worker_id="p202-timeout-worker",
        now=now + timedelta(seconds=12),
        lease_seconds=60,
    )
    assert recovery_claim is not None
    assert recovery_claim.generation == 1 and recovery_claim.attempt_count == 1
    cancelled = p202_database.management.cancel_ingestion_job(
        p202_database.context,
        ingestion_job_id=job_id,
    )
    assert cancelled.status == "cancelled"
    assert (
        p202_database.store.mark_succeeded(
            recovery_claim,
            artifact(p202_database, job_id, version_id),
            completed_at=now + timedelta(seconds=13),
        )
        is False
    )

    # 3. 取消后的活动 Attempt 不可再变更，且高风险命令与业务事实在同一事务留下最小证据。
    with p202_database.engine.connect() as connection:
        stage_status = connection.scalar(
            select(ingestion_job_stages.c.status).where(
                ingestion_job_stages.c.ingestion_job_id == job_id
            )
        )
        attempts = connection.execute(
            select(
                ingestion_job_attempts.c.generation,
                ingestion_job_attempts.c.trigger,
                ingestion_job_attempts.c.status,
            )
            .where(ingestion_job_attempts.c.ingestion_job_id == job_id)
            .order_by(
                ingestion_job_attempts.c.generation,
                ingestion_job_attempts.c.attempt_no,
            )
        ).all()
        cancel_audits = connection.scalar(
            select(func.count())
            .select_from(audit_records)
            .where(
                audit_records.c.resource_id == job_id,
                audit_records.c.action == "knowledge.ingestion.cancel",
            )
        )
        cancel_events = connection.scalar(
            select(func.count())
            .select_from(outbox_events)
            .where(
                outbox_events.c.aggregate_id == job_id,
                outbox_events.c.event_type == "knowledge.ingestion.cancelled",
            )
        )
    assert stage_status == "cancelled"
    assert [tuple(row) for row in attempts] == [
        (0, "automatic", "timed_out"),
        (1, "manual_recovery", "cancelled"),
    ]
    assert cancel_audits == cancel_events == 1
    with pytest.raises(DBAPIError), p202_database.engine.begin() as connection:
        connection.execute(
            delete(ingestion_job_attempts).where(
                ingestion_job_attempts.c.job_attempt_id == recovery_claim.job_attempt_id
            )
        )
