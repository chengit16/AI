"""验证 P3-04 固定测试集、确定性聚合和安全硬门禁。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from ai_platform_api.modules.agent_control.application.evaluation import (
    build_evaluation_report,
    evaluation_policy_digest,
    parse_evaluation_dataset,
)
from ai_platform_api.modules.agent_control.application.service import (
    AgentTestGateFailedError,
    AgentValidationError,
)
from ai_platform_api.modules.agent_control.domain.evaluation import (
    HARD_GATE_CHECKS,
    REQUIRED_EVALUATION_CHECKS,
    AgentEvaluationObservation,
    AgentEvaluationPolicyVersion,
    AgentEvaluationRequest,
)

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000304")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000304")
CANDIDATE_ID = UUID("aa000000-0000-4000-8000-000000000304")
POLICY_ID = UUID("ac000000-0000-4000-8000-000000000001")
CREATED_AT = datetime(2026, 8, 16, tzinfo=UTC)


def dataset_cases() -> tuple[dict[str, object], ...]:
    """返回覆盖五类门禁的最小全合成测试集。"""

    return tuple(
        {
            "case_key": f"synthetic.{check_code}",
            "check_code": check_code,
            "input_fixture": {"question": f"SYNTHETIC_{check_code.upper()}"},
            "expected_fixture": {"decision": "allow"},
            "timeout_ms": 2_000,
            "minimum_score_bps": 8_000 if check_code == "functional" else 10_000,
        }
        for check_code in REQUIRED_EVALUATION_CHECKS
    )


def policy() -> AgentEvaluationPolicyVersion:
    base = AgentEvaluationPolicyVersion(
        POLICY_ID,
        "agent-release-gate",
        1,
        REQUIRED_EVALUATION_CHECKS,
        HARD_GATE_CHECKS,
        (
            ("functional", 8_000),
            ("authorization", 10_000),
            ("prompt_injection", 10_000),
            ("citation", 10_000),
            ("output_contract", 10_000),
        ),
        "block_release",
        "count_as_failure",
        "count_as_failure",
        "deterministic_rules",
        False,
        False,
        "0" * 64,
        "active",
    )
    return replace(base, policy_hash=evaluation_policy_digest(base))


def request() -> AgentEvaluationRequest:
    dataset, cases = parse_evaluation_dataset(
        WORKSPACE_ID,
        ACCOUNT_ID,
        name="合成发布门禁集",
        dataset_version="p304-release-gate-v1",
        cases=dataset_cases(),
        created_at=CREATED_AT,
    )
    return AgentEvaluationRequest(
        CANDIDATE_ID,
        "1" * 64,
        "2" * 64,
        {"configuration": "synthetic"},
        dataset,
        cases,
    )


def passing_observations(
    evaluation_request: AgentEvaluationRequest,
) -> tuple[AgentEvaluationObservation, ...]:
    return tuple(
        AgentEvaluationObservation(
            item.case_id,
            "passed",
            9_000 if item.check_code == "functional" else 10_000,
            10,
            {"evidence_code": f"synthetic_{item.check_code}_passed"},
        )
        for item in evaluation_request.cases
    )


def test_dataset_is_order_independent_and_requires_all_checks() -> None:
    first, first_cases = parse_evaluation_dataset(
        WORKSPACE_ID,
        ACCOUNT_ID,
        name="合成发布门禁集",
        dataset_version="p304-release-gate-v1",
        cases=dataset_cases(),
        created_at=CREATED_AT,
    )
    second, second_cases = parse_evaluation_dataset(
        WORKSPACE_ID,
        ACCOUNT_ID,
        name="合成发布门禁集",
        dataset_version="p304-release-gate-v1",
        cases=tuple(reversed(dataset_cases())),
        created_at=CREATED_AT,
    )

    assert second.dataset_version_id == first.dataset_version_id
    assert second.dataset_hash == first.dataset_hash
    assert second_cases == first_cases
    with pytest.raises(AgentValidationError):
        parse_evaluation_dataset(
            WORKSPACE_ID,
            ACCOUNT_ID,
            name="缺少安全检查",
            dataset_version="p304-incomplete-v1",
            cases=dataset_cases()[:-1],
            created_at=CREATED_AT,
        )


def test_same_snapshot_and_observations_have_stable_result_identity() -> None:
    evaluation_request = request()
    observations = passing_observations(evaluation_request)

    first = build_evaluation_report(
        evaluation_request,
        policy(),
        observations,
        evaluator_version="synthetic-rules-v1",
        account_id=ACCOUNT_ID,
        completed_at=CREATED_AT,
    )
    repeated = build_evaluation_report(
        evaluation_request,
        policy(),
        observations,
        evaluator_version="synthetic-rules-v1",
        account_id=ACCOUNT_ID,
        completed_at=datetime(2026, 8, 16, 1, tzinfo=UTC),
    )

    assert first.run.status == "passed"
    assert first.run.result_hash == repeated.run.result_hash
    assert first.run.evaluation_run_id == repeated.run.evaluation_run_id
    assert {item.status for item in first.checks} == {"passed"}


def test_timeout_and_missing_observation_are_counted_as_failures() -> None:
    evaluation_request = request()
    observations = list(passing_observations(evaluation_request))
    observations[0] = replace(
        observations[0],
        duration_ms=evaluation_request.cases[0].timeout_ms + 1,
    )
    observations.pop()

    report = build_evaluation_report(
        evaluation_request,
        policy(),
        tuple(observations),
        evaluator_version="synthetic-rules-v1",
        account_id=ACCOUNT_ID,
        completed_at=CREATED_AT,
    )

    assert report.run.status == "failed"
    assert report.run.timeout_cases == 1
    assert report.run.skipped_cases == 1
    assert sum(item.status == "failed" for item in report.checks) == 2


def test_hard_gate_failure_cannot_be_offset_by_other_scores() -> None:
    evaluation_request = request()
    observations = list(passing_observations(evaluation_request))
    authorization_index = next(
        index
        for index, item in enumerate(evaluation_request.cases)
        if item.check_code == "authorization"
    )
    observations[authorization_index] = replace(
        observations[authorization_index],
        score_bps=9_999,
    )

    report = build_evaluation_report(
        evaluation_request,
        policy(),
        tuple(observations),
        evaluator_version="synthetic-rules-v1",
        account_id=ACCOUNT_ID,
        completed_at=CREATED_AT,
    )

    assert report.run.status == "failed"
    authorization = next(item for item in report.checks if item.check_code == "authorization")
    assert authorization.status == "failed"
    assert authorization.score_bps == 0


def test_policy_tampering_and_unknown_observation_are_rejected() -> None:
    evaluation_request = request()
    with pytest.raises(AgentTestGateFailedError):
        build_evaluation_report(
            evaluation_request,
            replace(policy(), online_llm_grading=True),
            passing_observations(evaluation_request),
            evaluator_version="synthetic-rules-v1",
            account_id=ACCOUNT_ID,
            completed_at=CREATED_AT,
        )
    with pytest.raises(AgentValidationError):
        build_evaluation_report(
            evaluation_request,
            policy(),
            (
                *passing_observations(evaluation_request),
                AgentEvaluationObservation(
                    UUID("ee000000-0000-4000-8000-000000000304"),
                    "passed",
                    10_000,
                    1,
                    {},
                ),
            ),
            evaluator_version="synthetic-rules-v1",
            account_id=ACCOUNT_ID,
            completed_at=CREATED_AT,
        )
