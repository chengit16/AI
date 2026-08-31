"""验证 P4-03 状态事实、并发领取、取消、超时和跨空间失败关闭。"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.identity.infrastructure.security import Argon2idPasswordAdapter
from ai_platform_api.modules.identity.infrastructure.sqlalchemy import (
    SqlAlchemyIdentityReader,
    SqlAlchemyRegistrationUnitOfWork,
)
from ai_platform_api.modules.tool_execution.application.errors import (
    ToolExecutionDeniedError,
    ToolRunConflictError,
)
from ai_platform_api.modules.tool_execution.application.results import ToolResultFactsService
from ai_platform_api.modules.tool_execution.application.tasks import ToolTaskService
from ai_platform_api.modules.tool_execution.domain.adapters import ToolAdapterResult
from ai_platform_api.modules.tool_execution.domain.results import ToolAttemptOutcomeFacts
from ai_platform_api.modules.tool_execution.domain.tasks import (
    ClaimedToolAttempt,
    ToolCallState,
    ToolRunBudget,
    ToolStep,
)
from ai_platform_api.modules.tool_execution.infrastructure.tasks_sqlalchemy import (
    SqlAlchemyToolTaskStore,
)
from ai_platform_api.persistence.database import create_platform_engine
from ai_platform_api.persistence.tables import (
    agent_releases,
    agent_tool_definitions,
    agents,
    ai_runtime_config_versions,
    service_access_policy_versions,
    services,
    tool_attempts,
    tool_calls,
    tool_policy_decisions,
    tool_runs,
    tool_steps,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).parents[2]
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://ai_platform:local-development-only@127.0.0.1:5432/ai_platform"
)
NOW = datetime(2026, 8, 16, 7, 0, tzinfo=UTC)
TRACE = TraceContext("7" * 32, "8" * 16)
TOOL_ID = UUID("a7000000-0000-4000-8000-000000000001")
BUDGET = ToolRunBudget(5, 2, 120, 50_000)


@pytest.fixture
def migration_database() -> Iterator[tuple[Config, Connection, str, str]]:
    """每个场景使用独立 Schema，允许验证真实触发器、锁和 Migration 往返。"""

    database_url = os.environ.get("AI_PLATFORM_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = f"p403_test_{uuid4().hex}"
    engine = create_engine(database_url)
    connection = engine.connect()
    connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    connection.commit()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "infra/migrations"))
    config.set_main_option(
        "prepend_sys_path",
        f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
    )
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("ai_platform_schema", schema)
    try:
        yield config, connection, schema, database_url
    finally:
        connection.rollback()
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        connection.commit()
        connection.close()
        engine.dispose()


@dataclass(frozen=True)
class P403Database:
    engine: Engine
    sessions: sessionmaker[Session]
    service: ToolTaskService
    context: RequestContext
    service_id: UUID
    release_id: UUID


def _database(
    migration_database: tuple[Config, Connection, str, str],
) -> P403Database:
    config, connection, schema, database_url = migration_database
    command.upgrade(config, "head")
    connection.commit()
    engine = create_platform_engine(database_url, schema)
    sessions = sessionmaker(engine, expire_on_commit=False, class_=Session)
    registration = RegistrationService(
        SqlAlchemyIdentityReader(sessions),
        SqlAlchemyRegistrationUnitOfWork(sessions),
        Argon2idPasswordAdapter(),
    )
    account = registration.register(
        login_name=f"synthetic.p403.{uuid4().hex}@example.com",
        display_name="合成 P4-03 用户",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    context = RequestContext.trusted(
        actor_id=account.account_id,
        user_id=account.account_id,
        workspace_id=account.personal_workspace_id,
        trace=TRACE,
        authentication_method="browser_session",
    )
    service_id, release_id = _seed_service(
        sessions,
        account.account_id,
        account.personal_workspace_id,
    )
    return P403Database(
        engine,
        sessions,
        ToolTaskService(SqlAlchemyToolTaskStore(sessions)),
        context,
        service_id,
        release_id,
    )


def _seed_service(
    sessions: sessionmaker[Session],
    account_id: UUID,
    workspace_id: UUID,
) -> tuple[UUID, UUID]:
    """创建只满足复合 FK 的合成系统 Release 与 Service，不绕入 P4-04 Adapter。"""

    runtime_id = uuid4()
    agent_id = uuid4()
    release_id = uuid4()
    service_id = uuid4()
    policy_id = uuid4()
    with sessions() as session, session.begin():
        session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
        session.execute(
            insert(ai_runtime_config_versions).values(
                runtime_config_version_id=runtime_id,
                version_number=1,
                display_name="合成 P4-03 Runtime",
                content_hash="1" * 64,
                system_prompt_template="synthetic",
                system_prompt_hash="2" * 64,
                component_versions={},
                attempt_timeout_ms=1_000,
                total_timeout_ms=5_000,
                max_attempts_per_route=1,
                max_prompt_characters=10_000,
                max_output_tokens=1_000,
                max_response_characters=10_000,
                circuit_failure_threshold=2,
                circuit_recovery_ms=1_000,
                rule_degradation_message=None,
                max_estimated_cost_microunits=50_000,
                created_by_account_id=account_id,
                created_at=NOW,
            )
        )
        session.execute(
            insert(agents).values(
                agent_id=agent_id,
                workspace_id=workspace_id,
                agent_key="synthetic-p403-agent",
                agent_kind="system",
                name="合成 P4-03 Agent",
                description=None,
                status="active",
                created_by_account_id=account_id,
                created_at=NOW,
                updated_at=NOW,
                version=1,
            )
        )
        session.execute(
            insert(agent_releases).values(
                release_id=release_id,
                agent_id=agent_id,
                workspace_id=workspace_id,
                release_kind="system",
                version=1,
                status="released",
                runtime_config_version_id=runtime_id,
                config_hash="3" * 64,
                released_by_account_id=account_id,
                released_at=NOW,
            )
        )
        session.execute(
            insert(service_access_policy_versions).values(
                access_policy_version_id=policy_id,
                service_id=service_id,
                workspace_id=workspace_id,
                version=1,
                visibility="workspace",
                allowed_department_ids=[],
                allowed_account_ids=[],
                policy_hash="4" * 64,
                created_by_account_id=account_id,
                created_at=NOW,
            )
        )
        session.execute(
            insert(services).values(
                service_id=service_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                service_key="synthetic-p403-service",
                name="合成 P4-03 Service",
                service_type="system_assistant",
                status="draft",
                access_policy_version_id=policy_id,
                created_by_account_id=account_id,
                created_at=NOW,
                updated_by_account_id=account_id,
                updated_at=NOW,
                version=1,
            )
        )
    return service_id, release_id


def _ready_run(database: P403Database, key: str, *, now: datetime = NOW) -> tuple[UUID, ToolStep]:
    run = database.service.create_run(
        database.context,
        service_id=database.service_id,
        agent_release_id=database.release_id,
        idempotency_key=key,
        budget=BUDGET,
        created_at=now,
    )
    database.service.transition_run(database.context, run.run_id, "planning", occurred_at=now)
    arguments_hash = hashlib.sha256(key.encode()).hexdigest()
    step = database.service.append_step(
        database.context,
        run.run_id,
        tool_id=TOOL_ID,
        tool_version=1,
        canonical_arguments_hash=arguments_hash,
        created_at=now,
    )
    database.service.transition_step(
        database.context,
        step.step_id,
        "policy_checking",
        occurred_at=now,
    )
    # P4-05 起数据库要求 ready 前存在精确允许证据；本测试仍只验证底层状态机。
    with database.sessions.begin() as session:
        session.execute(
            insert(tool_policy_decisions).values(
                decision_id=uuid4(),
                workspace_id=database.context.workspace_id,
                run_id=run.run_id,
                step_id=step.step_id,
                tool_id=TOOL_ID,
                tool_version=1,
                canonical_arguments_hash=arguments_hash,
                permission_code="knowledge.document.read",
                policy_version=1,
                resource_scope_hash="a" * 64,
                field_mask_hash="b" * 64,
                evaluated_at=now,
            )
        )
    step = database.service.transition_step(
        database.context,
        step.step_id,
        "ready",
        occurred_at=now,
    )
    database.service.transition_run(database.context, run.run_id, "running", occurred_at=now)
    return run.run_id, step


def _advance_call_to_executing(
    database: P403Database,
    claim: ClaimedToolAttempt,
    *,
    occurred_at: datetime,
) -> None:
    assert database.service.begin_attempt(claim, started_at=occurred_at)
    states: tuple[ToolCallState, ...] = ("authorized", "confirmed", "executing")
    for state in states:
        assert database.service.transition_call(
            claim,
            state,
            occurred_at=occurred_at,
        )


def _accepted_facts(
    database: P403Database,
    claim: ClaimedToolAttempt,
    *,
    recorded_at: datetime,
) -> ToolAttemptOutcomeFacts:
    """构造不含正文且匹配数据库冻结输出 Schema 的合成成功事实。"""

    with database.sessions() as session:
        output_schema_hash = session.scalar(
            select(agent_tool_definitions.c.output_schema_hash).where(
                agent_tool_definitions.c.tool_id == claim.tool_id,
                agent_tool_definitions.c.tool_version == claim.tool_version,
            )
        )
    assert isinstance(output_schema_hash, str)
    result = ToolAdapterResult(
        tool_key="knowledge.search",
        tool_version=claim.tool_version,
        payload={},
        output_schema_hash=output_schema_hash,
        result_sha256="f" * 64,
        result_size_bytes=2,
        checks=("schema", "size", "sensitive_fields", "prompt_injection"),
    )
    return ToolResultFactsService().accepted(
        workspace_id=claim.workspace_id,
        tool_call_id=claim.tool_call_id,
        adapter_result=result,
        duration_ms=1,
        cost_microunits=0,
        recorded_at=recorded_at,
    )


def _failed_facts(error_code: str = "TOOL_ADAPTER_UNAVAILABLE") -> ToolAttemptOutcomeFacts:
    """为旧状态机回归补充 P4-10 要求的失败用量事实。"""

    return ToolResultFactsService().failed(
        outcome="failed",
        duration_ms=1,
        cost_microunits=0,
        result_size_bytes=0,
        error_code=error_code,
    )


def test_migration_empty_roundtrip_creates_tool_state_tables(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    config, connection, schema, _ = migration_database
    command.upgrade(config, "20260816_0053")
    connection.commit()
    command.upgrade(config, "head")
    connection.commit()

    assert connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version')) == (
        "20260831_0077"
    )
    tables = {
        row[0]
        for row in connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :schema "
                "AND (table_name LIKE 'tool_%' OR table_name = 'synthetic_tool_side_effects')"
            ),
            {"schema": schema},
        )
    }
    assert {
        "tool_runs",
        "tool_steps",
        "tool_policy_decisions",
        "tool_confirmations",
        "tool_confirmation_invalidations",
        "tool_credentials",
        "tool_attempts",
        "tool_calls",
        "tool_idempotency_records",
        "synthetic_tool_side_effects",
    } <= tables

    command.downgrade(config, "20260816_0053")
    connection.commit()
    command.upgrade(config, "head")
    connection.commit()
    assert connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version')) == (
        "20260831_0077"
    )


def test_idempotent_ordered_lifecycle_and_terminal_database_protection(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    database = _database(migration_database)
    try:
        run_id, first_step = _ready_run(database, "p403-lifecycle-0001")
        replay = database.service.create_run(
            database.context,
            service_id=database.service_id,
            agent_release_id=database.release_id,
            idempotency_key="p403-lifecycle-0001",
            budget=BUDGET,
            created_at=NOW,
        )
        assert replay.run_id == run_id
        with pytest.raises(ToolRunConflictError):
            database.service.create_run(
                database.context,
                service_id=database.service_id,
                agent_release_id=database.release_id,
                idempotency_key="p403-lifecycle-0001",
                budget=replace(BUDGET, max_steps=4),
                created_at=NOW,
            )

        claim = database.service.claim_next(worker_id="p403-worker-1", now=NOW, lease_seconds=60)
        assert claim is not None and claim.step_id == first_step.step_id
        _advance_call_to_executing(database, claim, occurred_at=NOW + timedelta(seconds=1))
        assert (
            database.service.finish_attempt(
                claim,
                facts=_accepted_facts(
                    database,
                    claim,
                    recorded_at=NOW + timedelta(seconds=2),
                ),
                succeeded=True,
                completed_at=NOW + timedelta(seconds=2),
            )
            == "succeeded"
        )
        completed = database.service.transition_run(
            database.context,
            run_id,
            "completed",
            occurred_at=NOW + timedelta(seconds=3),
        )
        assert completed.state == "completed"

        with database.sessions() as session:
            assert session.scalar(select(func.count()).select_from(tool_runs)) == 1
            assert session.scalar(select(func.count()).select_from(tool_steps)) == 1
            assert session.scalar(select(func.count()).select_from(tool_attempts)) == 1
            assert session.scalar(select(func.count()).select_from(tool_calls)) == 1
            with pytest.raises(DBAPIError):
                session.execute(
                    update(tool_runs)
                    .where(tool_runs.c.run_id == run_id)
                    .values(state="running", completed_at=None, version=completed.version + 1)
                )
                session.commit()
            session.rollback()
    finally:
        database.engine.dispose()


def test_concurrent_claim_cross_workspace_and_forged_generation_fail_closed(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    database = _database(migration_database)
    try:
        run_id, _ = _ready_run(database, "p403-concurrent-0001")
        barrier = Barrier(2)

        def claim(worker_id: str) -> ClaimedToolAttempt | None:
            barrier.wait()
            return database.service.claim_next(worker_id=worker_id, now=NOW, lease_seconds=60)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim, ("p403-worker-a", "p403-worker-b")))
        claims = [item for item in results if item is not None]
        assert len(claims) == 1
        current = claims[0]
        assert database.service.begin_attempt(current, started_at=NOW)
        forged = replace(current, lease_generation=2)
        assert not database.service.begin_attempt(forged, started_at=NOW)

        other_context = replace(database.context, workspace_id=uuid4())
        with pytest.raises(ToolExecutionDeniedError):
            database.service.get_run(other_context, run_id)
        with pytest.raises(ToolExecutionDeniedError):
            database.service.request_cancellation(other_context, run_id, requested_at=NOW)

        cancelled = database.service.request_cancellation(
            database.context,
            run_id,
            requested_at=NOW + timedelta(seconds=1),
        )
        assert cancelled.state == "cancellation_requested"
    finally:
        database.engine.dispose()


def test_cancellation_late_success_and_worker_restart_timeout_never_overwrite_terminal(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    database = _database(migration_database)
    try:
        run_id, _ = _ready_run(database, "p403-cancel-0001")
        claim = database.service.claim_next(worker_id="p403-worker-c", now=NOW, lease_seconds=2)
        assert claim is not None
        _advance_call_to_executing(database, claim, occurred_at=NOW + timedelta(seconds=1))
        pending = database.service.request_cancellation(
            database.context,
            run_id,
            requested_at=NOW + timedelta(seconds=2),
        )
        assert pending.state == "cancellation_requested"
        assert (
            database.service.finish_attempt(
                claim,
                facts=_accepted_facts(
                    database,
                    claim,
                    recorded_at=NOW + timedelta(seconds=3),
                ),
                succeeded=True,
                completed_at=NOW + timedelta(seconds=3),
            )
            == "ignored_late_result"
        )
        assert database.service.get_run(database.context, run_id).state == "cancelled"
        assert (
            database.service.claim_next(
                worker_id="p403-worker-d",
                now=NOW + timedelta(seconds=4),
                lease_seconds=60,
            )
            is None
        )

        timeout_run_id, _ = _ready_run(
            database,
            "p403-timeout-0001",
            now=NOW + timedelta(minutes=3),
        )
        timeout_claim = database.service.claim_next(
            worker_id="p403-worker-old",
            now=NOW + timedelta(minutes=3),
            lease_seconds=5,
        )
        assert timeout_claim is not None
        _advance_call_to_executing(
            database,
            timeout_claim,
            occurred_at=NOW + timedelta(minutes=3, seconds=1),
        )
        assert (
            database.service.claim_next(
                worker_id="p403-worker-restarted",
                now=NOW + timedelta(minutes=3, seconds=6),
                lease_seconds=60,
            )
            is None
        )
        assert database.service.get_run(database.context, timeout_run_id).state == "running"
        recovered_claim = database.service.claim_next(
            worker_id="p403-worker-restarted",
            now=NOW + timedelta(minutes=3, seconds=8),
            lease_seconds=60,
        )
        assert recovered_claim is not None
        assert recovered_claim.attempt_no == 2
        assert recovered_claim.trigger == "lease_recovery"
        assert (
            database.service.finish_attempt(
                timeout_claim,
                facts=_accepted_facts(
                    database,
                    timeout_claim,
                    recorded_at=NOW + timedelta(minutes=3, seconds=7),
                ),
                succeeded=True,
                completed_at=NOW + timedelta(minutes=3, seconds=7),
            )
            == "ignored_late_result"
        )
        assert database.service.get_run(database.context, timeout_run_id).state == "running"
    finally:
        database.engine.dispose()
