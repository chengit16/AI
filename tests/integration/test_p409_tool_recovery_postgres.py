"""验证 P4-09 安全重试、租约恢复、取消传播和人工恢复数据库闭环。"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from ai_platform_api.modules.tool_execution.application.errors import (
    ToolExecutionDeniedError,
    ToolRunBudgetExceededError,
)
from ai_platform_api.persistence.tables import (
    audit_records,
    outbox_events,
    tool_attempts,
    tool_runs,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from tests.integration.test_p403_tool_task_state_postgres import (
    NOW,
    P403Database,
    _advance_call_to_executing,
    _database,
    _ready_run,
)
from tests.integration.test_p403_tool_task_state_postgres import (
    migration_database as _p403_migration_database,
)

migration_database = _p403_migration_database


def _fail_retryable_attempt(
    database: P403Database,
    *,
    worker_id: str,
    claimed_at_offset: int,
) -> tuple[int, str]:
    """领取并失败一个安全只读 Attempt，返回编号和状态机结论。"""

    claimed_at = NOW + timedelta(seconds=claimed_at_offset)
    claim = database.service.claim_next(
        worker_id=worker_id,
        now=claimed_at,
        lease_seconds=30,
    )
    assert claim is not None
    _advance_call_to_executing(database, claim, occurred_at=claimed_at)
    result = database.service.finish_attempt(
        claim,
        succeeded=False,
        error_code="TOOL_ADAPTER_UNAVAILABLE",
        retryable=True,
        next_attempt_at=claimed_at + timedelta(seconds=1),
        completed_at=claimed_at,
    )
    return claim.attempt_no, result


def test_safe_read_retry_backoff_and_three_manual_recovery_generations(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    database = _database(migration_database)
    try:
        run_id, _ = _ready_run(database, "p409-retry-0001")
        first_no, first_result = _fail_retryable_attempt(
            database,
            worker_id="p409-retry-first",
            claimed_at_offset=0,
        )
        assert (first_no, first_result) == (1, "retry_wait")
        assert (
            database.service.claim_next(
                worker_id="p409-too-early",
                now=NOW,
                lease_seconds=30,
            )
            is None
        )
        second_no, second_result = _fail_retryable_attempt(
            database,
            worker_id="p409-retry-second",
            claimed_at_offset=2,
        )
        assert (second_no, second_result) == (2, "manual_recovery")

        # 每一代都重新从 Attempt 1 开始，但租约代际始终全局唯一。
        offset = 10
        for generation in range(1, 4):
            recovered = database.service.recover_manually(
                database.context,
                run_id,
                recovered_at=NOW + timedelta(seconds=offset),
            )
            assert recovered.recovery_generation == generation
            first = database.service.claim_next(
                worker_id=f"p409-generation-{generation}-first",
                now=NOW + timedelta(seconds=offset),
                lease_seconds=30,
            )
            assert first is not None
            assert first.attempt_no == 1
            assert first.lease_generation == generation * 10 + 1
            assert first.trigger == "manual_recovery"
            _advance_call_to_executing(database, first, occurred_at=NOW + timedelta(seconds=offset))
            assert (
                database.service.finish_attempt(
                    first,
                    succeeded=False,
                    error_code="TOOL_ADAPTER_UNAVAILABLE",
                    retryable=True,
                    next_attempt_at=NOW + timedelta(seconds=offset + 1),
                    completed_at=NOW + timedelta(seconds=offset),
                )
                == "retry_wait"
            )
            second = database.service.claim_next(
                worker_id=f"p409-generation-{generation}-second",
                now=NOW + timedelta(seconds=offset + 2),
                lease_seconds=30,
            )
            assert second is not None and second.attempt_no == 2
            _advance_call_to_executing(
                database,
                second,
                occurred_at=NOW + timedelta(seconds=offset + 2),
            )
            assert (
                database.service.finish_attempt(
                    second,
                    succeeded=False,
                    error_code="TOOL_ADAPTER_UNAVAILABLE",
                    retryable=True,
                    next_attempt_at=NOW + timedelta(seconds=offset + 3),
                    completed_at=NOW + timedelta(seconds=offset + 2),
                )
                == "manual_recovery"
            )
            offset += 10

        with pytest.raises(ToolRunBudgetExceededError):
            database.service.recover_manually(
                database.context,
                run_id,
                recovered_at=NOW + timedelta(seconds=offset),
            )
        with database.sessions() as session:
            attempts = [
                tuple(row)
                for row in session.execute(
                    select(
                        tool_attempts.c.recovery_generation,
                        tool_attempts.c.attempt_no,
                        tool_attempts.c.lease_generation,
                    )
                    .where(tool_attempts.c.run_id == run_id)
                    .order_by(
                        tool_attempts.c.recovery_generation,
                        tool_attempts.c.attempt_no,
                    )
                ).all()
            ]
            audit_count = session.scalar(
                select(func.count())
                .select_from(audit_records)
                .where(
                    audit_records.c.resource_id == run_id,
                    audit_records.c.action == "tool.run.state_changed",
                )
            )
            event_count = session.scalar(
                select(func.count())
                .select_from(outbox_events)
                .where(
                    outbox_events.c.aggregate_id == run_id,
                    outbox_events.c.event_type == "tool.run.state_changed",
                )
            )
        assert attempts == [
            (generation, attempt_no, generation * 10 + attempt_no)
            for generation in range(4)
            for attempt_no in (1, 2)
        ]
        assert audit_count == event_count == 3
    finally:
        database.engine.dispose()


def test_lease_renewal_cancellation_observation_and_late_success_precedence(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    database = _database(migration_database)
    try:
        run_id, _ = _ready_run(database, "p409-cancel-0001")
        claim = database.service.claim_next(
            worker_id="p409-cancel-worker",
            now=NOW,
            lease_seconds=10,
        )
        assert claim is not None
        _advance_call_to_executing(database, claim, occurred_at=NOW)
        renewed = database.service.renew_lease(
            claim,
            renewed_at=NOW + timedelta(seconds=2),
            lease_seconds=20,
        )
        assert renewed is not None
        assert renewed.lease_expires_at == NOW + timedelta(seconds=22)

        database.service.request_cancellation(
            database.context,
            run_id,
            requested_at=NOW + timedelta(seconds=3),
        )
        assert database.service.observe_cancellation(
            renewed,
            observed_at=NOW + timedelta(seconds=4),
        )
        assert (
            database.service.finish_attempt(
                renewed,
                succeeded=True,
                completed_at=NOW + timedelta(seconds=5),
            )
            == "ignored_late_result"
        )
        assert database.service.get_run(database.context, run_id).state == "cancelled"
        with database.sessions() as session:
            observed_at = session.scalar(
                select(tool_attempts.c.cancel_observed_at).where(
                    tool_attempts.c.attempt_id == renewed.attempt_id
                )
            )
        assert observed_at == NOW + timedelta(seconds=4)
    finally:
        database.engine.dispose()


def test_total_deadline_blocks_recovery_and_ignores_late_success(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    database = _database(migration_database)
    try:
        run_id, _ = _ready_run(database, "p409-deadline-0001")
        claim = database.service.claim_next(
            worker_id="p409-deadline-worker",
            now=NOW,
            lease_seconds=300,
        )
        assert claim is not None
        _advance_call_to_executing(database, claim, occurred_at=NOW)

        # Run 的 120 秒绝对截止时间优先于仍然有效的 300 秒租约。
        assert (
            database.service.claim_next(
                worker_id="p409-after-deadline",
                now=NOW + timedelta(seconds=121),
                lease_seconds=30,
            )
            is None
        )
        assert database.service.get_run(database.context, run_id).state == "timed_out"
        assert (
            database.service.finish_attempt(
                claim,
                succeeded=True,
                completed_at=NOW + timedelta(seconds=122),
            )
            == "ignored_late_result"
        )
        with database.sessions() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(tool_attempts)
                    .where(tool_attempts.c.run_id == run_id)
                )
                == 1
            )
    finally:
        database.engine.dispose()


def test_cross_workspace_recovery_and_direct_database_bypass_fail_closed(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    database = _database(migration_database)
    try:
        run_id, _ = _ready_run(database, "p409-db-guard-0001")
        claim = database.service.claim_next(
            worker_id="p409-db-guard-worker",
            now=NOW,
            lease_seconds=30,
        )
        assert claim is not None
        with database.sessions() as session:
            with pytest.raises(DBAPIError):
                session.execute(
                    update(tool_attempts)
                    .where(tool_attempts.c.attempt_id == claim.attempt_id)
                    .values(lease_expires_at=claim.lease_expires_at + timedelta(seconds=1))
                )
                session.commit()
            session.rollback()

        _advance_call_to_executing(database, claim, occurred_at=NOW)
        assert (
            database.service.finish_attempt(
                claim,
                succeeded=False,
                error_code="TOOL_ADAPTER_UNAVAILABLE",
                retryable=True,
                next_attempt_at=NOW + timedelta(seconds=1),
                completed_at=NOW,
            )
            == "retry_wait"
        )
        second = database.service.claim_next(
            worker_id="p409-db-guard-second",
            now=NOW + timedelta(seconds=2),
            lease_seconds=30,
        )
        assert second is not None
        _advance_call_to_executing(database, second, occurred_at=NOW + timedelta(seconds=2))
        assert (
            database.service.finish_attempt(
                second,
                succeeded=False,
                error_code="TOOL_ADAPTER_UNAVAILABLE",
                retryable=True,
                next_attempt_at=NOW + timedelta(seconds=3),
                completed_at=NOW + timedelta(seconds=2),
            )
            == "manual_recovery"
        )

        outsider = replace(database.context, workspace_id=uuid4())
        with pytest.raises(ToolExecutionDeniedError):
            database.service.recover_manually(
                outsider,
                run_id,
                recovered_at=NOW + timedelta(seconds=4),
            )
        with database.sessions() as session:
            with pytest.raises(DBAPIError):
                session.execute(
                    update(tool_runs)
                    .where(tool_runs.c.run_id == run_id)
                    .values(
                        state="running",
                        recovery_generation=1,
                        recovery_reason_code=None,
                        recovery_required_at=None,
                        last_recovered_by_actor_id=database.context.actor_id,
                        last_recovered_at=NOW + timedelta(seconds=4),
                        version=tool_runs.c.version + 1,
                    )
                )
                session.commit()
            session.rollback()
    finally:
        database.engine.dispose()


def test_migration_refuses_to_remove_recovery_history(
    migration_database: tuple[Config, Connection, str, str],
) -> None:
    config, _, _, _ = migration_database
    database = _database(migration_database)
    try:
        run_id, _ = _ready_run(database, "p409-downgrade-0001")
        _fail_retryable_attempt(
            database,
            worker_id="p409-downgrade-first",
            claimed_at_offset=0,
        )
        _fail_retryable_attempt(
            database,
            worker_id="p409-downgrade-second",
            claimed_at_offset=2,
        )
        database.service.recover_manually(
            database.context,
            run_id,
            recovered_at=NOW + timedelta(seconds=4),
        )
        with pytest.raises(RuntimeError, match="拒绝破坏性降级"):
            command.downgrade(config, "20260816_0058")
    finally:
        database.engine.dispose()
