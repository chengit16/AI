"""验证 P5-04 在 PostgreSQL 中的身份解析、隔离、幂等和不可变边界。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from ai_platform_api.modules.quality.application.errors import (
    QualityConflictError,
    QualityDeniedError,
)
from ai_platform_api.modules.quality.application.evaluation import QualityEvaluationService
from ai_platform_api.modules.quality.application.operations import QualityOperationService
from ai_platform_api.modules.quality.domain.evaluation import QualityEvaluationTarget
from ai_platform_api.modules.quality.domain.models import QualitySampleCapture
from ai_platform_api.modules.quality.domain.operations import (
    QualityOperationTarget,
    QualityProviderEvidence,
)
from ai_platform_api.modules.quality.infrastructure.evaluation_sqlalchemy import (
    SqlAlchemyQualityEvaluationUnitOfWork,
)
from ai_platform_api.modules.quality.infrastructure.operations_sqlalchemy import (
    SqlAlchemyQualityOperationUnitOfWork,
)
from ai_platform_api.persistence.tables import (
    agent_releases,
    agents,
    ai_runtime_config_versions,
    ai_runtime_model_routes,
    model_provider_configurations,
    quality_operation_source_results,
    quality_operation_windows,
    service_access_policy_versions,
    services,
)
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import DBAPIError

from tests.support.p502_quality import TRACE, RegisteredAccount, authorized_context
from tests.support.p503_quality import QualityEvaluationHarness, StaticQualityExecutor
from tests.support.p504_quality import (
    StaticQualityOperationCollector,
    passing_offline_observation,
    with_unauthorized_tool_call,
)

pytest_plugins = ("tests.support.p503_quality_plugin",)


def _register(harness: QualityEvaluationHarness, identity: str) -> RegisteredAccount:
    result = harness.registration.register(
        login_name=f"synthetic.operations.{identity}.{uuid4().hex}@example.com",
        display_name=f"合成质量运营用户 {identity}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return RegisteredAccount(result.account_id, result.personal_workspace_id)


def _seed_dataset_and_release(
    harness: QualityEvaluationHarness,
    account: RegisteredAccount,
) -> tuple[UUID, UUID, UUID, UUID, str, UUID]:
    """建立十样本数据集和完整合成供应商、Service、Release 身份。"""

    dataset = None
    for index in range(10):
        dataset = harness.quality.capture(
            authorized_context(account, "assistant.feedback.manage"),
            QualitySampleCapture(
                "user_feedback",
                account.workspace_id,
                uuid4(),
                1,
                uuid4(),
                "unhelpful",
                ("incorrect",),
                f"合成 P5-04 问题 {index}",
                f"合成 P5-04 回答 {index}",
                f"合成 P5-04 反馈 {index}",
                None,
                "INTERNAL",
            ),
        )
    assert dataset is not None
    runtime_id, provider_id, agent_id = uuid4(), uuid4(), uuid4()
    release_id, service_id, access_policy_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    run_configuration_digest = uuid4().hex * 2
    with harness.sessions.begin() as session:
        # 1. 供应商、运行配置和模型路由均为隔离 Schema 内的全合成测试事实。
        session.execute(
            insert(model_provider_configurations).values(
                provider_id=provider_id,
                provider_key=f"synthetic-p504-{provider_id.hex}",
                display_name="合成 P5-04 供应商",
                adapter_kind="openai_compatible",
                base_url="https://synthetic.invalid/v1",
                probe_model_id="synthetic-quality-model",
                location="external",
                declared_capabilities=["chat"],
                policy_review_status="approved",
                max_security_level="INTERNAL",
                retention_days=0,
                training_usage_allowed=False,
                policy_url="https://synthetic.invalid/policy",
                policy_version="synthetic-v1",
                policy_reviewed_by_account_id=account.account_id,
                policy_reviewed_at=now,
                probe_status="passed",
                probed_capabilities=["chat"],
                last_probe_error_code=None,
                last_probed_at=now,
                status="active",
                created_by_account_id=account.account_id,
                created_at=now,
                updated_by_account_id=account.account_id,
                updated_at=now,
                version=7,
            )
        )
        session.execute(
            insert(ai_runtime_config_versions).values(
                runtime_config_version_id=runtime_id,
                version_number=provider_id.int % 2_000_000_000 + 1,
                display_name="合成 P5-04 运行配置",
                content_hash=run_configuration_digest,
                system_prompt_template="仅使用全合成证据回答。",
                system_prompt_hash="e" * 64,
                component_versions={"quality": "synthetic-p504"},
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
            insert(ai_runtime_model_routes).values(
                route_id=uuid4(),
                runtime_config_version_id=runtime_id,
                provider_id=provider_id,
                provider_configuration_version=7,
                priority=1,
                model_id="synthetic-quality-model",
                location="external",
                capabilities=["chat"],
                input_price_microunits_per_million_tokens=0,
                output_price_microunits_per_million_tokens=0,
                currency="CNY",
            )
        )
        # 2. Service 与访问策略是延迟外键循环，必须在同一事务同时写入。
        session.execute(
            insert(agents).values(
                agent_id=agent_id,
                workspace_id=account.workspace_id,
                agent_key=f"synthetic-p504-{agent_id.hex[:12]}",
                agent_kind="system",
                name="合成 P5-04 Agent",
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
                service_key=f"synthetic-p504-{service_id.hex[:12]}",
                name="合成 P5-04 服务",
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
    evaluation = QualityEvaluationService(
        SqlAlchemyQualityEvaluationUnitOfWork(harness.sessions),
        StaticQualityExecutor(evidence_marker="synthetic-p504-layered-evidence"),
    ).evaluate(
        authorized_context(account, "agent.test.execute"),
        QualityEvaluationTarget(
            dataset.dataset.dataset_version_id,
            dataset.dataset.dataset_digest,
            service_id,
            release_id,
            run_configuration_digest,
        ),
    )
    return (
        evaluation.run.evaluation_run_id,
        service_id,
        release_id,
        provider_id,
        run_configuration_digest,
        runtime_id,
    )


def _target(evaluation_run_id: UUID, suffix: int = 0) -> QualityOperationTarget:
    ended_at = datetime.now(UTC) + timedelta(seconds=suffix)
    return QualityOperationTarget(
        evaluation_run_id,
        "offline_release",
        ended_at - timedelta(hours=24),
        ended_at,
        "local.synthetic",
        "a" * 64,
    )


def _service(
    harness: QualityEvaluationHarness,
    collector: StaticQualityOperationCollector,
) -> QualityOperationService:
    return QualityOperationService(
        SqlAlchemyQualityOperationUnitOfWork(harness.sessions),
        collector,
    )


def test_p504_persists_blocked_synthetic_window_without_evidence_body(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = _register(quality_evaluation_database, "synthetic-blocked")
    evaluation_id, *_ = _seed_dataset_and_release(quality_evaluation_database, owner)
    evidence_marker = "synthetic-p504-sensitive-window-evidence"
    collector = StaticQualityOperationCollector(
        observations=(passing_offline_observation(evidence_marker=evidence_marker),)
    )
    service = _service(quality_evaluation_database, collector)
    target = _target(evaluation_id)

    first = service.record(authorized_context(owner, "agent.test.execute"), target)
    repeated = service.record(authorized_context(owner, "agent.test.execute"), target)
    stored = service.get_report(
        authorized_context(owner, "agent.test.read"),
        first.window.quality_window_id,
    )

    assert first == repeated == stored
    assert first.window.provider_integration_status == "not_configured"
    assert first.window.ai_quality_status == "not_configured"
    assert first.window.release_gate_status == "blocked"
    assert len(first.sources) == 3
    with quality_evaluation_database.sessions() as session:
        rows = {
            "windows": tuple(session.execute(select(quality_operation_windows)).mappings()),
            "sources": tuple(session.execute(select(quality_operation_source_results)).mappings()),
        }
    assert evidence_marker not in json.dumps(rows, default=str, ensure_ascii=False)


def test_p504_resolves_reviewed_route_and_blocks_cross_workspace_access(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = _register(quality_evaluation_database, "reviewed-route")
    outsider = _register(quality_evaluation_database, "outsider")
    evaluation_id, _, _, provider_id, _, _ = _seed_dataset_and_release(
        quality_evaluation_database,
        owner,
    )
    collector = StaticQualityOperationCollector(
        evidence_kind="authorized_real",
        provider=QualityProviderEvidence(provider_id, 7, "synthetic-quality-model"),
        observations=(passing_offline_observation(),),
    )
    service = _service(quality_evaluation_database, collector)
    report = service.record(
        authorized_context(owner, "agent.test.execute"),
        _target(evaluation_id),
    )

    assert report.window.provider_integration_status == "passed"
    assert report.window.ai_quality_status == "passed"
    assert report.window.release_gate_status == "passed"
    assert (
        service.get_report(
            authorized_context(outsider, "agent.test.read"),
            report.window.quality_window_id,
        )
        is None
    )
    with pytest.raises(QualityDeniedError):
        service.record(authorized_context(owner, "agent.test.read"), _target(evaluation_id, 1))


def test_p504_result_drift_conflicts_and_hard_failure_is_immutable(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = _register(quality_evaluation_database, "hard-failure")
    evaluation_id, _, _, provider_id, _, _ = _seed_dataset_and_release(
        quality_evaluation_database,
        owner,
    )
    collector = StaticQualityOperationCollector(
        evidence_kind="authorized_real",
        provider=QualityProviderEvidence(provider_id, 7, "synthetic-quality-model"),
        observations=(passing_offline_observation(),),
    )
    service = _service(quality_evaluation_database, collector)
    target = _target(evaluation_id)
    passed = service.record(authorized_context(owner, "agent.test.execute"), target)
    collector.observations = (with_unauthorized_tool_call(passing_offline_observation()),)

    with pytest.raises(QualityConflictError):
        service.record(authorized_context(owner, "agent.test.execute"), target)

    failed = service.record(
        authorized_context(owner, "agent.test.execute"),
        _target(evaluation_id, 1),
    )
    assert passed.window.release_gate_status == "passed"
    assert failed.window.release_gate_status == "failed"
    assert "unauthorized_tool_call" in failed.window.reason_codes


def test_p504_database_rejects_window_mutation_and_deletion(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = _register(quality_evaluation_database, "immutable")
    evaluation_id, *_ = _seed_dataset_and_release(quality_evaluation_database, owner)
    report = _service(
        quality_evaluation_database,
        StaticQualityOperationCollector(observations=(passing_offline_observation(),)),
    ).record(
        authorized_context(owner, "agent.test.execute"),
        _target(evaluation_id),
    )

    statements = (
        update(quality_operation_windows)
        .where(quality_operation_windows.c.quality_window_id == report.window.quality_window_id)
        .values(release_gate_status="passed"),
        delete(quality_operation_source_results).where(
            quality_operation_source_results.c.quality_window_id == report.window.quality_window_id
        ),
    )
    for statement in statements:
        with pytest.raises(DBAPIError), quality_evaluation_database.sessions.begin() as session:
            session.execute(statement)
