"""验证 P5-05 在 PostgreSQL 中的发布解析、隔离、幂等和不可变边界。"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from ai_platform_api.modules.quality.application.costs import CostAttributionService
from ai_platform_api.modules.quality.application.errors import (
    QualityConflictError,
    QualityDeniedError,
)
from ai_platform_api.modules.quality.domain.costs import (
    CostAttributionTarget,
    CostSupplierStatement,
)
from ai_platform_api.modules.quality.infrastructure.costs_sqlalchemy import (
    SqlAlchemyCostAttributionUnitOfWork,
)
from ai_platform_api.persistence.tables import (
    agent_releases,
    agents,
    ai_runtime_config_versions,
    cost_attribution_lines,
    cost_attribution_windows,
    cost_ledger_entries,
    service_access_policy_versions,
    services,
)
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import DBAPIError

from tests.support.p502_quality import TRACE, RegisteredAccount, authorized_context
from tests.support.p503_quality import QualityEvaluationHarness
from tests.support.p505_costs import (
    StaticCostUsageCollector,
    as_authorized_real,
    synthetic_observations,
)

pytest_plugins = ("tests.support.p503_quality_plugin",)


def _register(harness: QualityEvaluationHarness, identity: str) -> RegisteredAccount:
    result = harness.registration.register(
        login_name=f"synthetic.costs.{identity}.{uuid4().hex}@example.com",
        display_name=f"合成成本归因用户 {identity}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return RegisteredAccount(result.account_id, result.personal_workspace_id)


def _seed_release(
    harness: QualityEvaluationHarness,
    account: RegisteredAccount,
) -> tuple[UUID, UUID, UUID, str]:
    """建立成本归因使用的全合成 Service、Release 和运行配置身份。"""

    runtime_id, agent_id, release_id = uuid4(), uuid4(), uuid4()
    service_id, access_policy_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    run_digest = uuid4().hex * 2
    with harness.sessions.begin() as session:
        session.execute(
            insert(ai_runtime_config_versions).values(
                runtime_config_version_id=runtime_id,
                version_number=runtime_id.int % 2_000_000_000 + 1,
                display_name="合成 P5-05 运行配置",
                content_hash=run_digest,
                system_prompt_template="仅使用全合成成本事实。",
                system_prompt_hash="e" * 64,
                component_versions={"cost": "synthetic-p505"},
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
                created_by_account_id=account.account_id,
                created_at=now,
            )
        )
        session.execute(
            insert(agents).values(
                agent_id=agent_id,
                workspace_id=account.workspace_id,
                agent_key=f"synthetic-p505-{agent_id.hex[:12]}",
                agent_kind="system",
                name="合成 P5-05 Agent",
                description=None,
                status="active",
                created_by_account_id=account.account_id,
                created_at=now,
                updated_at=now,
                version=1,
            )
        )
        session.execute(
            insert(agent_releases).values(
                release_id=release_id,
                agent_id=agent_id,
                workspace_id=account.workspace_id,
                release_kind="system",
                version=1,
                status="released",
                runtime_config_version_id=runtime_id,
                config_hash="f" * 64,
                candidate_id=None,
                candidate_hash=None,
                source_draft_id=None,
                source_draft_revision=None,
                evaluation_run_id=None,
                approval_binding_id=None,
                snapshot_hash=None,
                released_by_account_id=account.account_id,
                released_at=now,
            )
        )
        # Service 与策略存在延迟外键循环，必须在同一事务一起写入。
        session.execute(
            insert(service_access_policy_versions).values(
                access_policy_version_id=access_policy_id,
                service_id=service_id,
                workspace_id=account.workspace_id,
                version=1,
                visibility="workspace",
                allowed_department_ids=[],
                allowed_account_ids=[],
                policy_hash="1" * 64,
                created_by_account_id=account.account_id,
                created_at=now,
            )
        )
        session.execute(
            insert(services).values(
                service_id=service_id,
                workspace_id=account.workspace_id,
                agent_id=agent_id,
                service_key=f"synthetic-p505-{service_id.hex[:12]}",
                name="合成 P5-05 服务",
                service_type="system_assistant",
                status="draft",
                access_policy_version_id=access_policy_id,
                created_by_account_id=account.account_id,
                created_at=now,
                updated_by_account_id=account.account_id,
                updated_at=now,
                version=1,
            )
        )
    return service_id, release_id, runtime_id, run_digest


def _target(
    service_id: UUID,
    release_id: UUID,
    run_digest: str,
    *,
    suffix: int = 0,
    real_price: bool = False,
) -> CostAttributionTarget:
    ended_at = datetime.now(UTC) + timedelta(seconds=suffix)
    return CostAttributionTarget(
        service_id,
        release_id,
        run_digest,
        "reviewed-price-v1" if real_price else "synthetic-price-v1",
        ("b" if real_price else "a") * 64,
        "CNY",
        "local.synthetic",
        ended_at - timedelta(hours=24),
        ended_at,
    )


def _service(
    harness: QualityEvaluationHarness,
    collector: StaticCostUsageCollector,
) -> CostAttributionService:
    return CostAttributionService(
        SqlAlchemyCostAttributionUnitOfWork(harness.sessions),
        collector,
    )


def test_p505_persists_synthetic_ledger_without_evidence_body(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = _register(quality_evaluation_database, "synthetic-ledger")
    service_id, release_id, _, run_digest = _seed_release(quality_evaluation_database, owner)
    evidence_marker = "synthetic-p505-sensitive-cost-evidence"
    collector = StaticCostUsageCollector(
        observations=synthetic_observations(evidence_marker=evidence_marker)
    )
    service = _service(quality_evaluation_database, collector)
    target = _target(service_id, release_id, run_digest)

    first = service.record(authorized_context(owner, "agent.test.execute"), target)
    repeated = service.record(authorized_context(owner, "agent.test.execute"), target)
    stored = service.get_report(
        authorized_context(owner, "operations.records.read"),
        first.window.cost_window_id,
    )

    assert first == repeated == stored
    assert first.window.attribution_status == "passed"
    assert first.window.price_verification_status == "not_configured"
    assert len(first.entries) == 9 and len(first.lines) == 7
    with quality_evaluation_database.sessions() as session:
        rows = {
            "windows": tuple(session.execute(select(cost_attribution_windows)).mappings()),
            "entries": tuple(session.execute(select(cost_ledger_entries)).mappings()),
            "lines": tuple(session.execute(select(cost_attribution_lines)).mappings()),
        }
    assert evidence_marker not in json.dumps(rows, default=str, ensure_ascii=False)


def test_p505_real_statement_reconciles_and_cross_workspace_is_invisible(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = _register(quality_evaluation_database, "real-reconcile")
    outsider = _register(quality_evaluation_database, "outsider")
    service_id, release_id, _, run_digest = _seed_release(quality_evaluation_database, owner)
    observations = as_authorized_real(synthetic_observations())
    collector = StaticCostUsageCollector(
        evidence_kind="authorized_real",
        observations=observations,
        supplier_statement=CostSupplierStatement("not_run", None, None, None, None, (), {}),
    )
    service = _service(quality_evaluation_database, collector)
    first_target = _target(service_id, release_id, run_digest, real_price=True)
    first = service.record(authorized_context(owner, "agent.test.execute"), first_target)
    collector.supplier_statement = CostSupplierStatement(
        "provided",
        "c" * 64,
        "d" * 64,
        first.window.recognized_amount_minor + 2,
        "CNY",
        ("provider_rounding",),
        {"marker": "synthetic-p505-statement-evidence"},
    )
    reconciled = service.record(
        authorized_context(owner, "agent.test.execute"),
        _target(service_id, release_id, run_digest, suffix=1, real_price=True),
    )

    assert first.window.reconciliation_status == "not_run"
    assert reconciled.window.price_verification_status == "passed"
    assert reconciled.window.reconciliation_status == "explained"
    assert reconciled.window.reconciliation_difference_minor == 2
    assert (
        service.get_report(
            authorized_context(outsider, "operations.records.read"),
            reconciled.window.cost_window_id,
        )
        is None
    )
    with pytest.raises(QualityDeniedError):
        service.record(authorized_context(owner, "agent.test.read"), first_target)


def test_p505_result_drift_conflicts_and_release_drift_fails_closed(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = _register(quality_evaluation_database, "drift")
    service_id, release_id, _, run_digest = _seed_release(quality_evaluation_database, owner)
    collector = StaticCostUsageCollector(observations=synthetic_observations())
    service = _service(quality_evaluation_database, collector)
    target = _target(service_id, release_id, run_digest)
    service.record(authorized_context(owner, "agent.test.execute"), target)
    collector.observations = (
        replace(collector.observations[0], quantity=2_000),
        *collector.observations[1:],
    )

    with pytest.raises(QualityConflictError):
        service.record(authorized_context(owner, "agent.test.execute"), target)

    drifted = service.record(
        authorized_context(owner, "agent.test.execute"),
        replace(
            _target(service_id, release_id, run_digest, suffix=1),
            run_configuration_digest="f" * 64,
        ),
    )
    assert drifted.window.attribution_status == "failed"
    assert "release_identity_drift" in drifted.window.reason_codes


def test_p505_database_rejects_cost_fact_mutation_and_deletion(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = _register(quality_evaluation_database, "immutable")
    service_id, release_id, _, run_digest = _seed_release(quality_evaluation_database, owner)
    report = _service(
        quality_evaluation_database,
        StaticCostUsageCollector(observations=synthetic_observations()),
    ).record(
        authorized_context(owner, "agent.test.execute"),
        _target(service_id, release_id, run_digest),
    )

    statements = (
        update(cost_attribution_windows)
        .where(cost_attribution_windows.c.cost_window_id == report.window.cost_window_id)
        .values(price_verification_status="passed"),
        delete(cost_ledger_entries).where(
            cost_ledger_entries.c.cost_window_id == report.window.cost_window_id
        ),
        delete(cost_attribution_lines).where(
            cost_attribution_lines.c.cost_window_id == report.window.cost_window_id
        ),
    )
    for statement in statements:
        with pytest.raises(DBAPIError), quality_evaluation_database.sessions.begin() as session:
            session.execute(statement)

    with quality_evaluation_database.sessions() as session:
        invalid_entry = dict(
            session.execute(
                select(cost_ledger_entries)
                .where(cost_ledger_entries.c.cost_window_id == report.window.cost_window_id)
                .limit(1)
            )
            .mappings()
            .one()
        )
    invalid_entry.update(
        ledger_entry_id=uuid4(),
        position=100,
        source_record_id=uuid4(),
        meter_key="model.invalid_usage_unit",
        usage_unit="byte",
    )
    with pytest.raises(DBAPIError), quality_evaluation_database.sessions.begin() as session:
        session.execute(insert(cost_ledger_entries).values(**invalid_entry))
