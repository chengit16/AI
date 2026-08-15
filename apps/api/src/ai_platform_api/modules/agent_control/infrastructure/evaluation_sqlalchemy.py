"""持久化 Agent 固定测试集、评估策略和不可变运行结果。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import CursorResult, Select, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_platform_api.modules.agent_control.domain.evaluation import (
    AgentEvaluationCaseResult,
    AgentEvaluationCheckResult,
    AgentEvaluationDatasetVersion,
    AgentEvaluationPolicyVersion,
    AgentEvaluationReport,
    AgentEvaluationRun,
    AgentEvaluationTestCase,
    EvaluationCheckCode,
    EvaluationEvidenceLevel,
    EvaluationOutcome,
    EvaluationStatus,
)
from ai_platform_api.modules.agent_control.domain.models import AgentWriteConflictError
from ai_platform_api.persistence.tables import (
    agent_evaluation_case_results,
    agent_evaluation_check_results,
    agent_evaluation_dataset_versions,
    agent_evaluation_policy_versions,
    agent_evaluation_runs,
    agent_evaluation_test_cases,
    agent_release_candidates,
)


class SqlAlchemyAgentEvaluationRepository:
    """在 Agent 控制面事务中维护版本化测试事实和候选测试状态。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_dataset_version(
        self,
        dataset: AgentEvaluationDatasetVersion,
        cases: tuple[AgentEvaluationTestCase, ...],
    ) -> bool:
        # 1. 先用版本唯一约束实现天然幂等，冲突时由应用层复核是否为相同内容。
        inserted = self._session.execute(
            postgresql_insert(agent_evaluation_dataset_versions)
            .values(
                dataset_version_id=dataset.dataset_version_id,
                workspace_id=dataset.workspace_id,
                name=dataset.name,
                dataset_version=dataset.dataset_version,
                dataset_hash=dataset.dataset_hash,
                created_by_account_id=dataset.created_by_account_id,
                created_at=dataset.created_at,
            )
            .on_conflict_do_nothing()
            .returning(agent_evaluation_dataset_versions.c.dataset_version_id)
        ).scalar_one_or_none()
        if inserted is None:
            return False
        # 2. 只有版本首次插入时才批量写入全部用例，避免出现无父版本的孤立事实。
        self._session.execute(
            insert(agent_evaluation_test_cases),
            [
                {
                    "case_id": item.case_id,
                    "dataset_version_id": item.dataset_version_id,
                    "workspace_id": item.workspace_id,
                    "position": item.position,
                    "case_key": item.case_key,
                    "check_code": item.check_code,
                    "input_fixture": item.input_fixture,
                    "expected_fixture": item.expected_fixture,
                    "timeout_ms": item.timeout_ms,
                    "minimum_score_bps": item.minimum_score_bps,
                    "case_hash": item.case_hash,
                }
                for item in cases
            ],
        )
        return True

    def get_dataset_version(
        self,
        workspace_id: UUID,
        dataset_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentEvaluationDatasetVersion | None:
        statement = select(agent_evaluation_dataset_versions).where(
            agent_evaluation_dataset_versions.c.workspace_id == workspace_id,
            agent_evaluation_dataset_versions.c.dataset_version_id == dataset_version_id,
        )
        row = self._session.execute(_share(statement, for_share)).one_or_none()
        return _dataset(row) if row is not None else None

    def get_dataset_by_version(
        self,
        workspace_id: UUID,
        dataset_version: str,
    ) -> AgentEvaluationDatasetVersion | None:
        row = self._session.execute(
            select(agent_evaluation_dataset_versions).where(
                agent_evaluation_dataset_versions.c.workspace_id == workspace_id,
                agent_evaluation_dataset_versions.c.dataset_version == dataset_version,
            )
        ).one_or_none()
        return _dataset(row) if row is not None else None

    def get_dataset_cases(
        self,
        workspace_id: UUID,
        dataset_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> tuple[AgentEvaluationTestCase, ...]:
        statement = (
            select(agent_evaluation_test_cases)
            .where(
                agent_evaluation_test_cases.c.workspace_id == workspace_id,
                agent_evaluation_test_cases.c.dataset_version_id == dataset_version_id,
            )
            .order_by(agent_evaluation_test_cases.c.position)
        )
        rows = self._session.execute(_share(statement, for_share))
        return tuple(_test_case(row) for row in rows)

    def get_active_policy(
        self,
        *,
        for_share: bool = False,
    ) -> AgentEvaluationPolicyVersion | None:
        statement = (
            select(agent_evaluation_policy_versions)
            .where(
                agent_evaluation_policy_versions.c.policy_key == "agent-release-gate",
                agent_evaluation_policy_versions.c.status == "active",
            )
            .order_by(agent_evaluation_policy_versions.c.version_number.desc())
            .limit(1)
        )
        row = self._session.execute(_share(statement, for_share)).one_or_none()
        return _policy(row) if row is not None else None

    def get_report(
        self,
        workspace_id: UUID,
        evaluation_run_id: UUID,
    ) -> AgentEvaluationReport | None:
        row = self._session.execute(
            select(agent_evaluation_runs).where(
                agent_evaluation_runs.c.workspace_id == workspace_id,
                agent_evaluation_runs.c.evaluation_run_id == evaluation_run_id,
            )
        ).one_or_none()
        return self._report(row) if row is not None else None

    def get_report_by_identity(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
        dataset_version_id: UUID,
        evaluation_policy_version_id: UUID,
    ) -> AgentEvaluationReport | None:
        row = self._session.execute(
            select(agent_evaluation_runs).where(
                agent_evaluation_runs.c.workspace_id == workspace_id,
                agent_evaluation_runs.c.candidate_id == candidate_id,
                agent_evaluation_runs.c.dataset_version_id == dataset_version_id,
                agent_evaluation_runs.c.evaluation_policy_version_id
                == evaluation_policy_version_id,
            )
        ).one_or_none()
        return self._report(row) if row is not None else None

    def get_latest_passing_report(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> AgentEvaluationReport | None:
        row = self._session.execute(
            select(agent_evaluation_runs)
            .where(
                agent_evaluation_runs.c.workspace_id == workspace_id,
                agent_evaluation_runs.c.candidate_id == candidate_id,
                agent_evaluation_runs.c.status == "passed",
            )
            .order_by(agent_evaluation_runs.c.completed_at.desc())
            .limit(1)
        ).one_or_none()
        return self._report(row) if row is not None else None

    def add_report(self, report: AgentEvaluationReport) -> None:
        """一次写入运行、检查和用例结果；任何唯一键竞争都回滚整份报告。"""

        try:
            # 1. 先写运行主事实，后续检查和用例通过复合外键绑定同一工作空间。
            run = report.run
            self._session.execute(
                insert(agent_evaluation_runs).values(
                    evaluation_run_id=run.evaluation_run_id,
                    candidate_id=run.candidate_id,
                    workspace_id=run.workspace_id,
                    dataset_version_id=run.dataset_version_id,
                    evaluation_policy_version_id=run.evaluation_policy_version_id,
                    candidate_hash=run.candidate_hash,
                    config_hash=run.config_hash,
                    evaluator_version=run.evaluator_version,
                    evidence_level=run.evidence_level,
                    status=run.status,
                    total_cases=run.total_cases,
                    passed_cases=run.passed_cases,
                    failed_cases=run.failed_cases,
                    timeout_cases=run.timeout_cases,
                    skipped_cases=run.skipped_cases,
                    result_hash=run.result_hash,
                    created_by_account_id=run.created_by_account_id,
                    completed_at=run.completed_at,
                )
            )
            self._session.execute(
                insert(agent_evaluation_check_results),
                [
                    {
                        "evaluation_run_id": item.evaluation_run_id,
                        "workspace_id": item.workspace_id,
                        "position": position,
                        "check_code": item.check_code,
                        "status": item.status,
                        "case_count": item.case_count,
                        "passed_count": item.passed_count,
                        "score_bps": item.score_bps,
                        "evidence_hash": item.evidence_hash,
                    }
                    for position, item in enumerate(report.checks, start=1)
                ],
            )
            # 2. 单用例只持久化状态、指标和证据摘要，执行输入与回答正文不复制到结果表。
            self._session.execute(
                insert(agent_evaluation_case_results),
                [
                    {
                        "evaluation_run_id": item.evaluation_run_id,
                        "workspace_id": item.workspace_id,
                        "dataset_version_id": report.run.dataset_version_id,
                        "case_id": item.case_id,
                        "check_code": item.check_code,
                        "outcome": item.outcome,
                        "score_bps": item.score_bps,
                        "duration_ms": item.duration_ms,
                        "evidence_hash": item.evidence_hash,
                    }
                    for item in report.cases
                ],
            )
        except IntegrityError as error:
            raise AgentWriteConflictError("write") from error

    def transition_candidate_after_evaluation(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
        *,
        expected_version: int,
        next_status: Literal["test_failed", "ready_for_approval"],
        updated_at: datetime,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(agent_release_candidates)
                .where(
                    agent_release_candidates.c.workspace_id == workspace_id,
                    agent_release_candidates.c.candidate_id == candidate_id,
                    agent_release_candidates.c.version == expected_version,
                    agent_release_candidates.c.status.in_(
                        ("created", "test_failed", "ready_for_approval")
                    ),
                )
                .values(
                    status=next_status,
                    updated_at=updated_at,
                    version=expected_version + 1,
                )
            ),
        )
        return result.rowcount == 1

    def _report(self, run_row: Row[Any]) -> AgentEvaluationReport:
        run_id = cast(UUID, run_row.evaluation_run_id)
        workspace_id = cast(UUID, run_row.workspace_id)
        check_rows = self._session.execute(
            select(agent_evaluation_check_results)
            .where(
                agent_evaluation_check_results.c.workspace_id == workspace_id,
                agent_evaluation_check_results.c.evaluation_run_id == run_id,
            )
            .order_by(agent_evaluation_check_results.c.position)
        )
        case_rows = self._session.execute(
            select(agent_evaluation_case_results)
            .join(
                agent_evaluation_test_cases,
                (agent_evaluation_test_cases.c.case_id == agent_evaluation_case_results.c.case_id)
                & (
                    agent_evaluation_test_cases.c.workspace_id
                    == agent_evaluation_case_results.c.workspace_id
                ),
            )
            .where(
                agent_evaluation_case_results.c.workspace_id == workspace_id,
                agent_evaluation_case_results.c.evaluation_run_id == run_id,
            )
            .order_by(agent_evaluation_test_cases.c.position)
        )
        return AgentEvaluationReport(
            _run(run_row),
            tuple(_check_result(row) for row in check_rows),
            tuple(_case_result(row) for row in case_rows),
        )


def _share(statement: Select[Any], enabled: bool) -> Select[Any]:
    return statement.with_for_update(read=True) if enabled else statement


def _dataset(row: Row[Any]) -> AgentEvaluationDatasetVersion:
    return AgentEvaluationDatasetVersion(
        row.dataset_version_id,
        row.workspace_id,
        row.name,
        row.dataset_version,
        row.dataset_hash,
        row.created_by_account_id,
        row.created_at,
    )


def _test_case(row: Row[Any]) -> AgentEvaluationTestCase:
    return AgentEvaluationTestCase(
        row.case_id,
        row.dataset_version_id,
        row.workspace_id,
        row.position,
        row.case_key,
        cast(EvaluationCheckCode, row.check_code),
        cast(dict[str, object], row.input_fixture),
        cast(dict[str, object], row.expected_fixture),
        row.timeout_ms,
        row.minimum_score_bps,
        row.case_hash,
    )


def _policy(row: Row[Any]) -> AgentEvaluationPolicyVersion:
    required = tuple(cast(list[EvaluationCheckCode], row.required_check_codes))
    hard_gates = tuple(cast(list[EvaluationCheckCode], row.hard_gate_check_codes))
    scores = cast(dict[str, int], row.minimum_check_scores)
    return AgentEvaluationPolicyVersion(
        row.evaluation_policy_version_id,
        row.policy_key,
        row.version_number,
        required,
        hard_gates,
        tuple((code, scores[code]) for code in required),
        row.failure_handling,
        row.timeout_handling,
        row.skipped_handling,
        row.evaluator_kind,
        row.online_llm_grading,
        row.multimodal_image_qa,
        row.policy_hash,
        row.status,
    )


def _run(row: Row[Any]) -> AgentEvaluationRun:
    return AgentEvaluationRun(
        row.evaluation_run_id,
        row.candidate_id,
        row.workspace_id,
        row.dataset_version_id,
        row.evaluation_policy_version_id,
        row.candidate_hash,
        row.config_hash,
        row.evaluator_version,
        cast(EvaluationEvidenceLevel, row.evidence_level),
        cast(EvaluationStatus, row.status),
        row.total_cases,
        row.passed_cases,
        row.failed_cases,
        row.timeout_cases,
        row.skipped_cases,
        row.result_hash,
        row.created_by_account_id,
        row.completed_at,
    )


def _check_result(row: Row[Any]) -> AgentEvaluationCheckResult:
    return AgentEvaluationCheckResult(
        row.evaluation_run_id,
        row.workspace_id,
        cast(EvaluationCheckCode, row.check_code),
        cast(EvaluationStatus, row.status),
        row.case_count,
        row.passed_count,
        row.score_bps,
        row.evidence_hash,
    )


def _case_result(row: Row[Any]) -> AgentEvaluationCaseResult:
    return AgentEvaluationCaseResult(
        row.evaluation_run_id,
        row.workspace_id,
        row.case_id,
        cast(EvaluationCheckCode, row.check_code),
        cast(EvaluationOutcome, row.outcome),
        row.score_bps,
        row.duration_ms,
        row.evidence_hash,
    )
