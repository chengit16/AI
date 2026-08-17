"""验证 P5-03 六层质量评估、身份漂移和 PostgreSQL 不可变边界。"""

from __future__ import annotations

import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from ai_platform_api.modules.quality.application.errors import (
    QualityConflictError,
    QualityDeniedError,
)
from ai_platform_api.modules.quality.domain.evaluation import QualityEvaluationTarget
from ai_platform_api.modules.quality.domain.models import (
    QualityDatasetSnapshot,
    QualitySampleCapture,
)
from ai_platform_api.persistence.tables import (
    quality_evaluation_layer_results,
    quality_evaluation_runs,
    quality_evaluation_sample_results,
)
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError

from tests.support.p502_quality import TRACE, RegisteredAccount, authorized_context
from tests.support.p503_quality import (
    QualityEvaluationHarness,
    StaticQualityExecutor,
    passing_observations,
    replace_observation,
)

pytest_plugins = ("tests.support.p503_quality_plugin",)


def _register(
    harness: QualityEvaluationHarness,
    identity: str,
) -> RegisteredAccount:
    result = harness.registration.register(
        login_name=f"synthetic.evaluation.{identity}.{uuid4().hex}@example.com",
        display_name=f"合成质量评估用户 {identity}",
        password="synthetic-password-123",
        request_id=uuid4(),
        trace=TRACE,
    )
    return RegisteredAccount(result.account_id, result.personal_workspace_id)


def _seed_dataset(
    harness: QualityEvaluationHarness,
    account: RegisteredAccount,
    marker: str,
) -> QualityDatasetSnapshot:
    latest = None
    for index in range(2):
        latest = harness.quality.capture(
            authorized_context(account, "assistant.feedback.manage"),
            QualitySampleCapture(
                source_type="user_feedback",
                source_workspace_id=account.workspace_id,
                source_id=uuid4(),
                source_version=1,
                resource_id=uuid4(),
                signal_code="unhelpful",
                reason_codes=("incorrect",),
                input_text=f"合成问题:{marker}:{index}",
                output_text=f"合成回答:{marker}:{index}",
                feedback_text=f"合成反馈:{marker}:{index}",
                correction_text=None,
                source_security_level="INTERNAL",
            ),
        )
    assert latest is not None
    return latest


def _target(dataset_id: UUID, dataset_digest: str, suffix: str) -> QualityEvaluationTarget:
    return QualityEvaluationTarget(
        dataset_id,
        dataset_digest,
        uuid4(),
        uuid4(),
        suffix * 64,
    )


def test_p503_persists_idempotent_report_without_evidence_body(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    account = _register(quality_evaluation_database, "passed")
    dataset = _seed_dataset(quality_evaluation_database, account, "passed")
    evidence_marker = "synthetic-p503-sensitive-evidence"
    executor = StaticQualityExecutor(evidence_marker=evidence_marker)
    service = quality_evaluation_database.service(executor)
    target = _target(
        dataset.dataset.dataset_version_id,
        dataset.dataset.dataset_digest,
        "c",
    )

    first = service.evaluate(authorized_context(account, "agent.test.execute"), target)
    repeated = service.evaluate(authorized_context(account, "agent.test.execute"), target)
    stored = service.get_report(
        authorized_context(account, "agent.test.read"),
        first.run.evaluation_run_id,
    )

    assert first.run.status == "passed"
    assert first.run.observation_count == 12
    assert repeated == first == stored
    assert len(first.layers) == 6
    assert evidence_marker not in repr(first)
    with quality_evaluation_database.sessions() as session:
        rows = {
            "runs": tuple(session.execute(select(quality_evaluation_runs)).mappings()),
            "layers": tuple(session.execute(select(quality_evaluation_layer_results)).mappings()),
            "samples": tuple(session.execute(select(quality_evaluation_sample_results)).mappings()),
        }
    assert evidence_marker not in json.dumps(rows, default=str, ensure_ascii=False)


def test_p503_result_drift_conflicts_and_hard_failure_remains_explainable(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    account = _register(quality_evaluation_database, "hard-failure")
    dataset = _seed_dataset(quality_evaluation_database, account, "hard-failure")
    executor = StaticQualityExecutor()
    service = quality_evaluation_database.service(executor)
    target = _target(
        dataset.dataset.dataset_version_id,
        dataset.dataset.dataset_digest,
        "d",
    )
    context = authorized_context(account, "agent.test.execute")
    service.evaluate(context, target)
    members = tuple(item.sample_version_id for item in dataset.samples)
    executor.observations = replace_observation(
        passing_observations(members),
        layer="tool",
        outcome="failed",
        reason_code="unauthorized_tool_call",
        score_bps=10_000,
    )

    with pytest.raises(QualityConflictError):
        service.evaluate(context, target)

    failed_target = replace(target, run_configuration_digest="e" * 64)
    failed = service.evaluate(context, failed_target)
    tool = next(item for item in failed.layers if item.layer == "tool")
    assert failed.run.status == "failed"
    assert tool.score_bps == 10_000
    assert "unauthorized_tool_call" in failed.run.reason_codes


def test_p503_identity_drift_and_cross_workspace_access_fail_closed(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    owner = _register(quality_evaluation_database, "identity-owner")
    outsider = _register(quality_evaluation_database, "identity-outsider")
    dataset = _seed_dataset(quality_evaluation_database, owner, "identity")
    target = _target(
        dataset.dataset.dataset_version_id,
        dataset.dataset.dataset_digest,
        "f",
    )
    executor = StaticQualityExecutor()
    executor.batch_target = replace(target, agent_release_id=uuid4())
    service = quality_evaluation_database.service(executor)

    failed = service.evaluate(
        authorized_context(owner, "agent.test.execute"),
        target,
    )

    assert failed.run.status == "failed"
    assert all("release_identity_drift" in item.reason_codes for item in failed.layers)
    assert (
        service.get_report(
            authorized_context(outsider, "agent.test.read"),
            failed.run.evaluation_run_id,
        )
        is None
    )
    with pytest.raises(QualityDeniedError):
        service.evaluate(authorized_context(owner, "agent.test.read"), target)


def test_p503_database_rejects_report_mutation_and_deletion(
    quality_evaluation_database: QualityEvaluationHarness,
) -> None:
    account = _register(quality_evaluation_database, "immutable")
    dataset = _seed_dataset(quality_evaluation_database, account, "immutable")
    service = quality_evaluation_database.service(StaticQualityExecutor())
    report = service.evaluate(
        authorized_context(account, "agent.test.execute"),
        _target(
            dataset.dataset.dataset_version_id,
            dataset.dataset.dataset_digest,
            "1",
        ),
    )

    statements = (
        update(quality_evaluation_runs)
        .where(quality_evaluation_runs.c.evaluation_run_id == report.run.evaluation_run_id)
        .values(status="failed"),
        delete(quality_evaluation_layer_results).where(
            quality_evaluation_layer_results.c.evaluation_run_id == report.run.evaluation_run_id
        ),
        delete(quality_evaluation_sample_results).where(
            quality_evaluation_sample_results.c.evaluation_run_id == report.run.evaluation_run_id
        ),
    )
    for statement in statements:
        with pytest.raises(DBAPIError), quality_evaluation_database.sessions.begin() as session:
            session.execute(statement)
