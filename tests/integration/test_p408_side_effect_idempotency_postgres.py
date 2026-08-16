"""验证 P4-08 合成副作用、幂等提交、结果未知和 PostgreSQL 防绕过闭环。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock
from uuid import UUID, uuid4

import pytest
from ai_platform_api.modules.tool_execution.application.errors import (
    ToolConfirmationStaleError,
    ToolIdempotencyConflictError,
    ToolOutcomeUnknownError,
    ToolRunConflictError,
)
from ai_platform_api.modules.tool_execution.application.side_effects import ToolSideEffectService
from ai_platform_api.modules.tool_execution.domain.side_effects import (
    SyntheticSideEffectCommand,
    SyntheticSideEffectReceipt,
    ToolIdempotencyRecord,
    side_effect_identity,
)
from ai_platform_api.modules.tool_execution.domain.tasks import ClaimedToolAttempt
from ai_platform_api.modules.tool_execution.infrastructure.side_effects_sqlalchemy import (
    SqlAlchemySyntheticSideEffectAdapter,
    SqlAlchemyToolSideEffectStore,
)
from ai_platform_api.persistence.tables import (
    agent_tool_definitions,
    audit_records,
    outbox_events,
    synthetic_tool_side_effects,
    tool_attempts,
    tool_calls,
    tool_idempotency_records,
    tool_policy_decisions,
    tool_runs,
    tool_steps,
)
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError

from tests.integration.test_p304_agent_evaluation_postgres import RegisteredAccount
from tests.integration.test_p305_agent_approval_postgres import (
    ApprovalHarness,
    approval_harness,
    register,
)
from tests.integration.test_p406_tool_confirmation_postgres import (
    WRITE_DEFINITION,
    ConfirmationEnvironment,
    _approve_personal,
    _environment,
)

ROOT = Path(__file__).parents[2]


@dataclass(frozen=True)
class SideEffectDatabase:
    """集中保存共享 Schema、幂等 Store、合成 Adapter 和应用服务。"""

    harness: ApprovalHarness
    store: SqlAlchemyToolSideEffectStore
    adapter: SqlAlchemySyntheticSideEffectAdapter
    service: ToolSideEffectService


@dataclass(frozen=True)
class ReadyWrite:
    """保存已经通过确认并停在 confirmed 的合成写调用。"""

    owner: RegisteredAccount
    environment: ConfirmationEnvironment
    claim: ClaimedToolAttempt
    confirmation_id: UUID
    confirmation_hash: str


@pytest.fixture(scope="module")
def side_effect_database() -> Iterator[SideEffectDatabase]:
    """从空 Schema 迁移到 head，登记唯一的无凭证合成写工具并装配服务。"""

    for harness in approval_harness():
        with harness.sessions.begin() as session:
            session.execute(insert(agent_tool_definitions).values(**asdict(WRITE_DEFINITION)))
        store = SqlAlchemyToolSideEffectStore(harness.sessions)
        adapter = SqlAlchemySyntheticSideEffectAdapter(harness.sessions)
        yield SideEffectDatabase(harness, store, adapter, ToolSideEffectService(store, adapter))


def _ready_write(database: SideEffectDatabase, suffix: str) -> ReadyWrite:
    """完成个人确认和批准后 PDP 复核，再领取停在 confirmed 的当前调用。"""

    owner = register(database.harness, f"p408-{suffix}")
    environment = _environment(database.harness, owner, f"p408-{suffix}")
    approved = _approve_personal(environment, f"p408-{suffix}")
    environment.confirmations.resume(
        environment.request_context,
        confirmation_id=approved.confirmation_id,
        expected_binding=approved.binding,
    )
    claim = environment.tasks.claim_next(
        worker_id=f"synthetic-p408-worker-{suffix}",
        now=datetime.now(UTC),
        lease_seconds=300,
    )
    assert claim is not None
    assert environment.tasks.begin_attempt(claim, started_at=datetime.now(UTC))
    assert environment.tasks.transition_call(claim, "authorized", occurred_at=datetime.now(UTC))
    assert environment.tasks.transition_call(claim, "confirmed", occurred_at=datetime.now(UTC))
    return ReadyWrite(
        owner,
        environment,
        claim,
        approved.confirmation_id,
        approved.confirmation_hash,
    )


def _row_counts(database: SideEffectDatabase, ready: ReadyWrite) -> tuple[int, int]:
    with database.harness.sessions() as session:
        idempotency_count = session.scalar(
            select(func.count())
            .select_from(tool_idempotency_records)
            .where(tool_idempotency_records.c.step_id == ready.claim.step_id)
        )
        side_effect_count = session.scalar(
            select(func.count())
            .select_from(synthetic_tool_side_effects)
            .where(synthetic_tool_side_effects.c.workspace_id == ready.claim.workspace_id)
        )
    return int(idempotency_count or 0), int(side_effect_count or 0)


def _idempotency_row(database: SideEffectDatabase, ready: ReadyWrite) -> RowMapping:
    with database.harness.sessions() as session:
        return (
            session.execute(
                select(tool_idempotency_records).where(
                    tool_idempotency_records.c.step_id == ready.claim.step_id
                )
            )
            .mappings()
            .one()
        )


def test_success_replay_and_atomic_task_lifecycle_are_secret_free(
    side_effect_database: SideEffectDatabase,
) -> None:
    """成功调用只产生一个副作用，重复投递重放摘要且任务与横切事实一致收口。"""

    ready = _ready_write(side_effect_database, "success-replay")
    first = side_effect_database.service.execute(ready.claim, occurred_at=datetime.now(UTC))
    replayed = side_effect_database.service.execute(ready.claim, occurred_at=datetime.now(UTC))

    assert first.replayed is False
    assert replayed.replayed is True
    assert replayed.result_hash == first.result_hash
    assert _row_counts(side_effect_database, ready)[0] == 1
    with side_effect_database.harness.sessions() as session:
        side_effect_count = session.scalar(
            select(func.count())
            .select_from(synthetic_tool_side_effects)
            .where(
                synthetic_tool_side_effects.c.idempotency_record_id
                == first.record.idempotency_record_id
            )
        )
        states = session.execute(
            select(
                tool_calls.c.state,
                tool_attempts.c.state,
                tool_steps.c.state,
                tool_runs.c.state,
            )
            .join(tool_attempts, tool_attempts.c.attempt_id == tool_calls.c.attempt_id)
            .join(tool_steps, tool_steps.c.step_id == tool_calls.c.step_id)
            .join(tool_runs, tool_runs.c.run_id == tool_calls.c.run_id)
            .where(tool_calls.c.tool_call_id == ready.claim.tool_call_id)
        ).one()
        audits = session.execute(
            select(audit_records.c.action, audit_records.c.attributes).where(
                audit_records.c.resource_id == ready.claim.tool_call_id,
                audit_records.c.action.in_(("tool.call.started", "tool.call.completed")),
            )
        ).all()
        events = session.execute(
            select(outbox_events.c.event_type, outbox_events.c.payload).where(
                outbox_events.c.aggregate_id == ready.claim.tool_call_id,
                outbox_events.c.event_type.in_(("tool.call.started", "tool.call.completed")),
            )
        ).all()

    assert side_effect_count == 1
    assert states == ("succeeded", "succeeded", "completed", "completed")
    assert {row.action for row in audits} == {"tool.call.started", "tool.call.completed"}
    assert {row.event_type for row in events} == {"tool.call.started", "tool.call.completed"}
    serialized = json.dumps({"audits": audits, "events": events}, default=str, sort_keys=True)
    assert "canonical_arguments_hash" not in serialized
    assert "credential_ref" not in serialized


def test_same_key_with_different_request_is_rejected_without_second_effect(
    side_effect_database: SideEffectDatabase,
) -> None:
    """稳定 Step 键已经绑定请求摘要后，参数摘要变化必须返回冲突且副作用计数不变。"""

    ready = _ready_write(side_effect_database, "request-conflict")
    side_effect_database.service.execute(ready.claim, occurred_at=datetime.now(UTC))
    conflicting = replace(ready.claim, canonical_arguments_hash="f" * 64)

    with pytest.raises(ToolIdempotencyConflictError):
        side_effect_database.service.execute(conflicting, occurred_at=datetime.now(UTC))
    idempotency_count, _ = _row_counts(side_effect_database, ready)
    with side_effect_database.harness.sessions() as session:
        effect_count = session.scalar(
            select(func.count())
            .select_from(synthetic_tool_side_effects)
            .where(
                synthetic_tool_side_effects.c.idempotency_record_id
                == _idempotency_row(side_effect_database, ready)["idempotency_record_id"]
            )
        )
    assert idempotency_count == 1
    assert effect_count == 1


def test_concurrent_duplicate_delivery_has_one_adapter_call_owner(
    side_effect_database: SideEffectDatabase,
) -> None:
    """并发重复投递只能由预留创建者调用 Adapter，另一投递不能接管持有中的调用。"""

    ready = _ready_write(side_effect_database, "concurrent")
    started = Event()
    release = Event()
    counter_lock = Lock()
    adapter_calls = 0

    class BlockingAdapter:
        """在真实合成提交前阻塞，暴露并发预留窗口。"""

        def execute(
            self,
            command: SyntheticSideEffectCommand,
            *,
            committed_at: datetime,
        ) -> SyntheticSideEffectReceipt:
            nonlocal adapter_calls
            with counter_lock:
                adapter_calls += 1
            started.set()
            assert release.wait(timeout=5)
            return side_effect_database.adapter.execute(command, committed_at=committed_at)

        def reconcile(
            self,
            record: ToolIdempotencyRecord,
        ) -> SyntheticSideEffectReceipt | None:
            return side_effect_database.adapter.reconcile(record)

    service = ToolSideEffectService(side_effect_database.store, BlockingAdapter())
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.execute, ready.claim, occurred_at=datetime.now(UTC))
        assert started.wait(timeout=5)
        second = executor.submit(service.execute, ready.claim, occurred_at=datetime.now(UTC))
        with pytest.raises(ToolRunConflictError):
            second.result(timeout=5)
        release.set()
        assert first.result(timeout=5).replayed is False

    assert adapter_calls == 1
    assert _row_counts(side_effect_database, ready)[0] == 1


def test_committed_side_effect_with_lost_response_can_only_be_reconciled(
    side_effect_database: SideEffectDatabase,
) -> None:
    """Adapter 提交后丢失响应时先记录 outcome_unknown，人工对账只读既有事实后才能成功。"""

    ready = _ready_write(side_effect_database, "lost-response")

    class LostResponseAdapter:
        """先真实提交合成事实，再模拟响应在返回 Runtime 前丢失。"""

        def execute(
            self,
            command: SyntheticSideEffectCommand,
            *,
            committed_at: datetime,
        ) -> SyntheticSideEffectReceipt:
            side_effect_database.adapter.execute(command, committed_at=committed_at)
            raise ToolOutcomeUnknownError

        def reconcile(
            self,
            record: ToolIdempotencyRecord,
        ) -> SyntheticSideEffectReceipt | None:
            return side_effect_database.adapter.reconcile(record)

    service = ToolSideEffectService(side_effect_database.store, LostResponseAdapter())
    with pytest.raises(ToolOutcomeUnknownError):
        service.execute(ready.claim, occurred_at=datetime.now(UTC))
    assert _idempotency_row(side_effect_database, ready)["state"] == "outcome_unknown"

    with pytest.raises(ToolOutcomeUnknownError):
        service.execute(ready.claim, occurred_at=datetime.now(UTC))
    reconciled = service.reconcile(ready.claim, reconciled_at=datetime.now(UTC))
    assert reconciled.replayed is True
    assert reconciled.record.state == "succeeded"
    assert _row_counts(side_effect_database, ready)[0] == 1


def test_uncommitted_unknown_outcome_stays_unknown_without_automatic_call(
    side_effect_database: SideEffectDatabase,
) -> None:
    """无法找到提交事实时保持 outcome_unknown，对账不能把“不存在”推断为可安全重试。"""

    ready = _ready_write(side_effect_database, "unknown-without-commit")
    adapter_calls = 0

    class UnknownBeforeCommitAdapter:
        """在任何副作用提交前模拟无法判断的传输故障。"""

        def execute(
            self,
            command: SyntheticSideEffectCommand,
            *,
            committed_at: datetime,
        ) -> SyntheticSideEffectReceipt:
            del command, committed_at
            nonlocal adapter_calls
            adapter_calls += 1
            raise ToolOutcomeUnknownError

        def reconcile(
            self,
            record: ToolIdempotencyRecord,
        ) -> SyntheticSideEffectReceipt | None:
            del record
            return None

    service = ToolSideEffectService(side_effect_database.store, UnknownBeforeCommitAdapter())
    with pytest.raises(ToolOutcomeUnknownError):
        service.execute(ready.claim, occurred_at=datetime.now(UTC))
    with pytest.raises(ToolOutcomeUnknownError):
        service.reconcile(ready.claim, reconciled_at=datetime.now(UTC))

    assert adapter_calls == 1
    assert _idempotency_row(side_effect_database, ready)["state"] == "outcome_unknown"
    with side_effect_database.harness.sessions() as session:
        effect_count = session.scalar(
            select(func.count())
            .select_from(synthetic_tool_side_effects)
            .where(synthetic_tool_side_effects.c.workspace_id == ready.claim.workspace_id)
        )
    assert effect_count == 0


def test_database_blocks_direct_start_and_stale_confirmation_reservation(
    side_effect_database: SideEffectDatabase,
) -> None:
    """数据库拒绝绕过预留直接执行，也独立复核最新 PDP 与批准确认的一致性。"""

    direct = _ready_write(side_effect_database, "direct-start")
    with side_effect_database.harness.sessions() as session:
        with pytest.raises(DBAPIError):
            session.execute(
                update(tool_calls)
                .where(tool_calls.c.tool_call_id == direct.claim.tool_call_id)
                .values(state="executing", updated_at=datetime.now(UTC))
            )
            session.commit()
        session.rollback()

    stale = _ready_write(side_effect_database, "stale-confirmation")
    with side_effect_database.harness.sessions.begin() as session:
        latest = (
            session.execute(
                select(tool_policy_decisions)
                .where(tool_policy_decisions.c.step_id == stale.claim.step_id)
                .order_by(tool_policy_decisions.c.evaluated_at.desc())
                .limit(1)
            )
            .mappings()
            .one()
        )
        values = dict(latest)
        values.update(
            decision_id=uuid4(),
            policy_version=int(latest["policy_version"]) + 1,
            evaluated_at=datetime.now(UTC) + timedelta(seconds=1),
        )
        session.execute(insert(tool_policy_decisions).values(**values))

    with pytest.raises(ToolConfirmationStaleError):
        side_effect_database.store.reserve(stale.claim, reserved_at=datetime.now(UTC))

    identity = side_effect_identity(stale.claim, stale.confirmation_hash)
    with side_effect_database.harness.sessions() as session:
        with pytest.raises(DBAPIError):
            session.execute(
                insert(tool_idempotency_records).values(
                    idempotency_record_id=uuid4(),
                    workspace_id=stale.claim.workspace_id,
                    run_id=stale.claim.run_id,
                    step_id=stale.claim.step_id,
                    tool_call_id=stale.claim.tool_call_id,
                    tool_id=stale.claim.tool_id,
                    tool_version=stale.claim.tool_version,
                    confirmation_id=stale.confirmation_id,
                    confirmation_hash=stale.confirmation_hash,
                    idempotency_key_hash=identity.idempotency_key_hash,
                    request_hash=identity.request_hash,
                    state="reserved",
                    result_hash=None,
                    reserved_at=datetime.now(UTC),
                    completed_at=None,
                    error_code=None,
                )
            )
            session.commit()
        session.rollback()


def test_cross_workspace_claim_cannot_create_or_read_idempotency_fact(
    side_effect_database: SideEffectDatabase,
) -> None:
    """伪造其他工作空间的 Claim 不能创建预留，也不能命中原空间的重放结果。"""

    ready = _ready_write(side_effect_database, "cross-workspace")
    outsider = register(side_effect_database.harness, "p408-cross-workspace-outsider")
    forged = replace(ready.claim, workspace_id=outsider.workspace_id)

    with pytest.raises(ToolRunConflictError):
        side_effect_database.service.execute(forged, occurred_at=datetime.now(UTC))
    assert _row_counts(side_effect_database, ready)[0] == 0

    side_effect_database.service.execute(ready.claim, occurred_at=datetime.now(UTC))
    with pytest.raises(ToolRunConflictError):
        side_effect_database.service.execute(forged, occurred_at=datetime.now(UTC))
    assert _row_counts(side_effect_database, ready)[0] == 1


def test_lifecycle_purge_is_the_only_allowed_delete_path(
    side_effect_database: SideEffectDatabase,
) -> None:
    """普通事务不能删除副作用历史，只有事务级生命周期旁路可按工作空间完整清理。"""

    ready = _ready_write(side_effect_database, "lifecycle-purge")
    result = side_effect_database.service.execute(ready.claim, occurred_at=datetime.now(UTC))
    with side_effect_database.harness.sessions() as session:
        with pytest.raises(DBAPIError):
            session.execute(
                delete(synthetic_tool_side_effects).where(
                    synthetic_tool_side_effects.c.idempotency_record_id
                    == result.record.idempotency_record_id
                )
            )
            session.commit()
        session.rollback()

    # 生命周期服务会按外键拓扑清理；这里复用相同顺序验证两张新表只接受事务级受限旁路。
    with side_effect_database.harness.sessions.begin() as session:
        session.execute(text("SET LOCAL ai_platform.lifecycle_purge = 'on'"))
        session.execute(
            delete(synthetic_tool_side_effects).where(
                synthetic_tool_side_effects.c.idempotency_record_id
                == result.record.idempotency_record_id
            )
        )
        session.execute(
            delete(tool_idempotency_records).where(
                tool_idempotency_records.c.idempotency_record_id
                == result.record.idempotency_record_id
            )
        )
    assert _row_counts(side_effect_database, ready) == (0, 0)


def test_migration_refuses_to_drop_idempotency_or_side_effect_history(
    side_effect_database: SideEffectDatabase,
) -> None:
    """存在幂等或副作用事实时不得降级到 0057，避免删除人工恢复所需证据。"""

    ready = _ready_write(side_effect_database, "downgrade")
    side_effect_database.service.execute(ready.claim, occurred_at=datetime.now(UTC))
    schema_map = side_effect_database.harness.engine.get_execution_options().get(
        "schema_translate_map"
    )
    assert isinstance(schema_map, dict)
    schema = schema_map["ai_platform"]
    assert isinstance(schema, str)
    migration = Config(str(ROOT / "alembic.ini"))
    migration.set_main_option("script_location", str(ROOT / "infra/migrations"))
    migration.set_main_option(
        "prepend_sys_path",
        f"{ROOT / 'apps/api/src'}:{ROOT / 'packages/backend/src'}",
    )
    migration.set_main_option(
        "sqlalchemy.url",
        side_effect_database.harness.engine.url.render_as_string(hide_password=False),
    )
    migration.set_main_option("ai_platform_schema", schema)

    with pytest.raises(RuntimeError, match="拒绝破坏性降级"):
        command.downgrade(migration, "20260816_0057")
