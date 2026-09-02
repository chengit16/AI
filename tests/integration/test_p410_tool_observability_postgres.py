"""验证 P4-10 安全结果、用量成本和可恢复进度 PostgreSQL 闭环。"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from ai_platform_api.modules.tool_execution.application.errors import ToolExecutionDeniedError
from ai_platform_api.modules.tool_execution.application.results import (
    ToolProgressService,
    ToolResultFactsService,
)
from ai_platform_api.modules.tool_execution.domain.tasks import ClaimedToolAttempt
from ai_platform_api.modules.tool_execution.infrastructure.results_sqlalchemy import (
    SqlAlchemyToolProgressStore,
)
from ai_platform_api.persistence.tables import (
    audit_records,
    outbox_events,
    tool_calls,
    tool_safe_results,
    tool_usage_records,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError

from tests.integration.test_p403_tool_task_state_postgres import (
    NOW,
    P403Database,
    _accepted_facts,
    _advance_call_to_executing,
    _database,
    _failed_facts,
    _ready_run,
)
from tests.integration.test_p403_tool_task_state_postgres import (
    migration_database as _p403_migration_database,
)

migration_database = _p403_migration_database


def _claim_executing(
    database: P403Database,
    suffix: str,
) -> tuple[UUID, ClaimedToolAttempt]:
    """创建、领取并推进到 executing，供结果事务与数据库反例复用。"""

    run_id, _ = _ready_run(database, f"p410-{suffix}")
    claim = database.service.claim_next(
        worker_id=f"p410-worker-{suffix}",
        now=NOW,
        lease_seconds=60,
    )
    assert claim is not None
    _advance_call_to_executing(database, claim, occurred_at=NOW)
    return run_id, claim


def test_accepted_result_usage_and_progress_replay_are_minimal_and_isolated(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    database = _database(migration_database)
    try:
        run_id, claim = _claim_executing(database, "accepted-0001")
        completed_at = NOW + timedelta(seconds=1)
        assert (
            database.service.finish_attempt(
                claim,
                facts=_accepted_facts(database, claim, recorded_at=completed_at),
                succeeded=True,
                completed_at=completed_at,
            )
            == "succeeded"
        )
        with database.sessions() as session:
            result = (
                session.execute(
                    select(tool_safe_results).where(
                        tool_safe_results.c.tool_call_id == claim.tool_call_id
                    )
                )
                .mappings()
                .one()
            )
            usage = (
                session.execute(
                    select(tool_usage_records).where(
                        tool_usage_records.c.tool_call_id == claim.tool_call_id
                    )
                )
                .mappings()
                .one()
            )
            column_names = set(
                session.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = current_schema() AND table_name = 'tool_safe_results'"
                    )
                )
            )
            audits = session.scalars(
                select(audit_records.c.attributes).where(
                    audit_records.c.resource_id == claim.tool_call_id,
                    audit_records.c.action == "tool.call.completed",
                )
            ).all()
            events = session.scalars(
                select(outbox_events.c.payload).where(
                    outbox_events.c.aggregate_id == claim.tool_call_id,
                    outbox_events.c.event_type == "tool.call.completed",
                )
            ).all()
        assert result["status"] == "accepted"
        assert result["eligible_for_model_context"] is True
        assert result["result_size_bytes"] == usage["result_size_bytes"] == 2
        assert usage["outcome"] == "succeeded"
        assert usage["cost_microunits"] == 0
        assert {"payload", "content", "arguments", "credential"}.isdisjoint(column_names)
        assert len(audits) == len(events) == 1
        serialized_facts = json.dumps({"audits": audits, "events": events}, sort_keys=True)
        assert all(
            forbidden not in serialized_facts
            for forbidden in ("arguments", "content", "credential", "payload", "workspace_id")
        )

        progress = ToolProgressService(SqlAlchemyToolProgressStore(database.sessions))
        first_page = progress.replay(database.context, run_id=run_id, after_cursor=0, limit=2)
        assert [event.cursor for event in first_page.events] == [1, 2]
        assert first_page.events[0].event_type == "tool.run.created"
        assert first_page.latest_cursor > first_page.events[-1].cursor
        assert first_page.has_more is True

        second_page = progress.replay(
            database.context,
            run_id=run_id,
            after_cursor=first_page.events[-1].cursor,
        )
        all_events = first_page.events + second_page.events
        assert [event.cursor for event in all_events] == list(
            range(1, second_page.latest_cursor + 1)
        )
        assert all_events[-1].event_type == "tool.call.completed"
        assert second_page.has_more is False
        assert (
            progress.replay(
                database.context,
                run_id=run_id,
                after_cursor=second_page.latest_cursor,
            ).events
            == ()
        )
        with pytest.raises(ToolExecutionDeniedError):
            progress.replay(replace(database.context, workspace_id=uuid4()), run_id=run_id)
    finally:
        database.engine.dispose()


def test_failure_usage_is_kept_and_cost_budget_violation_rolls_back_atomically(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    database = _database(migration_database)
    try:
        _, claim = _claim_executing(database, "failure-0001")
        assert (
            database.service.finish_attempt(
                claim,
                facts=_failed_facts(),
                succeeded=False,
                error_code="TOOL_ADAPTER_UNAVAILABLE",
                retryable=True,
                next_attempt_at=NOW + timedelta(seconds=2),
                completed_at=NOW + timedelta(seconds=1),
            )
            == "retry_wait"
        )
        with database.sessions() as session:
            usage = (
                session.execute(
                    select(tool_usage_records).where(
                        tool_usage_records.c.attempt_id == claim.attempt_id
                    )
                )
                .mappings()
                .one()
            )
        assert usage["outcome"] == "failed"
        assert usage["error_code"] == "TOOL_ADAPTER_UNAVAILABLE"

        _, over_budget = _claim_executing(database, "budget-0001")
        facts = ToolResultFactsService().failed(
            outcome="failed",
            duration_ms=1,
            cost_microunits=1,
            result_size_bytes=0,
            error_code="TOOL_ADAPTER_UNAVAILABLE",
        )
        with pytest.raises(DBAPIError, match="frozen cost budget"):
            database.service.finish_attempt(
                over_budget,
                facts=facts,
                succeeded=False,
                error_code="TOOL_ADAPTER_UNAVAILABLE",
                completed_at=NOW + timedelta(seconds=1),
            )
        with database.sessions() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(tool_usage_records)
                    .where(tool_usage_records.c.attempt_id == over_budget.attempt_id)
                )
                == 0
            )
    finally:
        database.engine.dispose()


def test_database_rejects_schema_bypass_fact_mutation_and_unsafe_downgrade(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    config, connection, schema, _ = migration_database
    database = _database(migration_database)
    try:
        _, claim = _claim_executing(database, "database-guards-0001")
        invalid = {
            "result_id": uuid4(),
            "tool_call_id": claim.tool_call_id,
            "workspace_id": claim.workspace_id,
            "run_id": claim.run_id,
            "step_id": claim.step_id,
            "attempt_id": claim.attempt_id,
            "output_schema_hash": "0" * 64,
            "content_hash": "1" * 64,
            "result_size_bytes": 2,
            "status": "accepted",
            "schema_check": "passed",
            "size_check": "passed",
            "sensitive_fields_check": "passed",
            "prompt_injection_check": "passed",
            "eligible_for_model_context": True,
            "credential_exposure_detected": False,
            "recorded_at": NOW,
        }
        with (
            pytest.raises(DBAPIError, match="schema hash mismatch"),
            database.sessions.begin() as session,
        ):
            session.execute(insert(tool_safe_results).values(**invalid))

        with (
            pytest.raises(DBAPIError, match="terminal tool call requires exactly one usage"),
            database.sessions.begin() as session,
        ):
            session.execute(
                update(tool_calls)
                .where(tool_calls.c.tool_call_id == claim.tool_call_id)
                .values(
                    state="failed",
                    completed_at=NOW + timedelta(seconds=1),
                    updated_at=NOW + timedelta(seconds=1),
                    error_code="TOOL_ADAPTER_UNAVAILABLE",
                )
            )

        completed_at = NOW + timedelta(seconds=1)
        database.service.finish_attempt(
            claim,
            facts=_accepted_facts(database, claim, recorded_at=completed_at),
            succeeded=True,
            completed_at=completed_at,
        )
        with (
            pytest.raises(DBAPIError, match="immutable"),
            database.sessions.begin() as session,
        ):
            session.execute(
                update(tool_usage_records)
                .where(tool_usage_records.c.attempt_id == claim.attempt_id)
                .values(cost_microunits=0)
            )

        database.engine.dispose()
        with pytest.raises(RuntimeError, match="拒绝破坏性降级"):
            command.downgrade(config, "20260816_0059")
        connection.rollback()
        assert connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version')) == (
            "20260831_0079"
        )
    finally:
        database.engine.dispose()
