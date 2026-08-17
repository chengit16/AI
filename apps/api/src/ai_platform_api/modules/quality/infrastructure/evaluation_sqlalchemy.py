"""使用工作空间复合条件持久化六层质量评估不可变事实。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import cast
from uuid import UUID

from sqlalchemy import func, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from ai_platform_api.modules.quality.domain.evaluation import (
    QualityEvaluationLayer,
    QualityEvaluationLayerResult,
    QualityEvaluationOutcome,
    QualityEvaluationReport,
    QualityEvaluationRun,
    QualityEvaluationSampleResult,
    QualityEvaluationStatus,
)
from ai_platform_api.persistence.tables import (
    quality_dataset_members,
    quality_dataset_versions,
    quality_evaluation_layer_results,
    quality_evaluation_runs,
    quality_evaluation_sample_results,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyQualityEvaluationRepository:
    """读取冻结数据集身份，并一次写入运行、层和样本摘要。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_dataset_snapshot(
        self,
        workspace_id: UUID,
        dataset_version_id: UUID,
    ) -> tuple[str, tuple[UUID, ...]] | None:
        digest = self._session.scalar(
            select(quality_dataset_versions.c.dataset_digest).where(
                quality_dataset_versions.c.workspace_id == workspace_id,
                quality_dataset_versions.c.dataset_version_id == dataset_version_id,
            )
        )
        if digest is None:
            return None
        member_ids = tuple(
            self._session.scalars(
                select(quality_dataset_members.c.sample_version_id)
                .where(
                    quality_dataset_members.c.workspace_id == workspace_id,
                    quality_dataset_members.c.dataset_version_id == dataset_version_id,
                )
                .order_by(quality_dataset_members.c.position)
            )
        )
        return cast(str, digest), member_ids

    def lock_run_identity(self, run_identity_digest: str) -> None:
        """事务级锁串行化同一评估目标，避免并发结果形成双重事实。"""

        self._session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(run_identity_digest, 0)))
        )

    def get_report_by_identity(
        self,
        workspace_id: UUID,
        run_identity_digest: str,
    ) -> QualityEvaluationReport | None:
        row = (
            self._session.execute(
                select(quality_evaluation_runs).where(
                    quality_evaluation_runs.c.workspace_id == workspace_id,
                    quality_evaluation_runs.c.run_identity_digest == run_identity_digest,
                )
            )
            .mappings()
            .one_or_none()
        )
        return self._report(row) if row is not None else None

    def get_report(
        self,
        workspace_id: UUID,
        evaluation_run_id: UUID,
    ) -> QualityEvaluationReport | None:
        row = (
            self._session.execute(
                select(quality_evaluation_runs).where(
                    quality_evaluation_runs.c.workspace_id == workspace_id,
                    quality_evaluation_runs.c.evaluation_run_id == evaluation_run_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        return self._report(row) if row is not None else None

    def add_report(self, report: QualityEvaluationReport) -> None:
        """按父子顺序插入整份报告；数据库 Trigger 阻止后续改写。"""

        # 1. 先插入运行主事实和固定六层结果，复合外键绑定同一工作空间。
        run = report.run
        self._session.execute(
            insert(quality_evaluation_runs).values(
                evaluation_run_id=run.evaluation_run_id,
                run_identity_digest=run.run_identity_digest,
                workspace_id=run.workspace_id,
                dataset_version_id=run.dataset_version_id,
                dataset_digest=run.dataset_digest,
                service_id=run.service_id,
                agent_release_id=run.agent_release_id,
                run_configuration_digest=run.run_configuration_digest,
                policy_version_id=run.policy_version_id,
                policy_digest=run.policy_digest,
                evaluator_version=run.evaluator_version,
                evaluator_identity_digest=run.evaluator_identity_digest,
                status=run.status,
                observation_count=run.observation_count,
                passed_count=run.passed_count,
                failed_count=run.failed_count,
                timeout_count=run.timeout_count,
                skipped_count=run.skipped_count,
                reason_codes=list(run.reason_codes),
                result_digest=run.result_digest,
                created_by_actor_id=run.created_by_actor_id,
                completed_at=run.completed_at,
            )
        )
        self._session.execute(
            insert(quality_evaluation_layer_results),
            [
                {
                    "evaluation_run_id": item.evaluation_run_id,
                    "workspace_id": item.workspace_id,
                    "position": position,
                    "layer": item.layer,
                    "status": item.status,
                    "sample_count": item.sample_count,
                    "passed_count": item.passed_count,
                    "score_bps": item.score_bps,
                    "reason_codes": list(item.reason_codes),
                    "evidence_digest": item.evidence_digest,
                }
                for position, item in enumerate(report.layers, start=1)
            ],
        )
        # 2. 样本结果只保存状态、原因和证据摘要；空观测失败不制造占位行。
        if report.samples:
            self._session.execute(
                insert(quality_evaluation_sample_results),
                [
                    {
                        "evaluation_run_id": item.evaluation_run_id,
                        "workspace_id": item.workspace_id,
                        "position": position,
                        "sample_version_id": item.sample_version_id,
                        "layer": item.layer,
                        "outcome": item.outcome,
                        "score_bps": item.score_bps,
                        "duration_ms": item.duration_ms,
                        "reason_codes": list(item.reason_codes),
                        "evidence_digest": item.evidence_digest,
                    }
                    for position, item in enumerate(report.samples, start=1)
                ],
            )

    def _report(self, run_row: RowMapping) -> QualityEvaluationReport:
        run_id = cast(UUID, run_row["evaluation_run_id"])
        workspace_id = cast(UUID, run_row["workspace_id"])
        layer_rows = (
            self._session.execute(
                select(quality_evaluation_layer_results)
                .where(
                    quality_evaluation_layer_results.c.workspace_id == workspace_id,
                    quality_evaluation_layer_results.c.evaluation_run_id == run_id,
                )
                .order_by(quality_evaluation_layer_results.c.position)
            )
            .mappings()
            .all()
        )
        sample_rows = (
            self._session.execute(
                select(quality_evaluation_sample_results)
                .where(
                    quality_evaluation_sample_results.c.workspace_id == workspace_id,
                    quality_evaluation_sample_results.c.evaluation_run_id == run_id,
                )
                .order_by(quality_evaluation_sample_results.c.position)
            )
            .mappings()
            .all()
        )
        return QualityEvaluationReport(
            _run(run_row),
            tuple(_layer(row) for row in layer_rows),
            tuple(_sample(row) for row in sample_rows),
        )


class SqlAlchemyQualityEvaluationUnitOfWork:
    """为质量评估查询和只追加报告提供显式短事务。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._evaluations: SqlAlchemyQualityEvaluationRepository | None = None

    @property
    def evaluations(self) -> SqlAlchemyQualityEvaluationRepository:
        if self._evaluations is None:
            raise RuntimeError("Quality Evaluation Unit of Work 尚未进入事务范围")
        return self._evaluations

    def __enter__(self) -> SqlAlchemyQualityEvaluationUnitOfWork:
        if self._session is not None:
            raise RuntimeError("Quality Evaluation Unit of Work 不允许嵌套事务")
        self._session = self._session_factory()
        self._evaluations = SqlAlchemyQualityEvaluationRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is not None:
            if exc_type is not None:
                self._session.rollback()
            self._session.close()
        self._evaluations = None
        self._session = None

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Quality Evaluation Unit of Work 尚未进入事务范围")
        self._session.commit()


def _run(row: RowMapping) -> QualityEvaluationRun:
    return QualityEvaluationRun(
        evaluation_run_id=cast(UUID, row["evaluation_run_id"]),
        run_identity_digest=cast(str, row["run_identity_digest"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        dataset_version_id=cast(UUID, row["dataset_version_id"]),
        dataset_digest=cast(str, row["dataset_digest"]),
        service_id=cast(UUID, row["service_id"]),
        agent_release_id=cast(UUID, row["agent_release_id"]),
        run_configuration_digest=cast(str, row["run_configuration_digest"]),
        policy_version_id=cast(UUID, row["policy_version_id"]),
        policy_digest=cast(str, row["policy_digest"]),
        evaluator_version=cast(str, row["evaluator_version"]),
        evaluator_identity_digest=cast(str, row["evaluator_identity_digest"]),
        status=cast(QualityEvaluationStatus, row["status"]),
        observation_count=cast(int, row["observation_count"]),
        passed_count=cast(int, row["passed_count"]),
        failed_count=cast(int, row["failed_count"]),
        timeout_count=cast(int, row["timeout_count"]),
        skipped_count=cast(int, row["skipped_count"]),
        reason_codes=tuple(cast(list[str], row["reason_codes"])),
        result_digest=cast(str, row["result_digest"]),
        created_by_actor_id=cast(UUID, row["created_by_actor_id"]),
        completed_at=cast(datetime, row["completed_at"]),
    )


def _layer(row: RowMapping) -> QualityEvaluationLayerResult:
    return QualityEvaluationLayerResult(
        cast(UUID, row["evaluation_run_id"]),
        cast(UUID, row["workspace_id"]),
        cast(QualityEvaluationLayer, row["layer"]),
        cast(QualityEvaluationStatus, row["status"]),
        cast(int, row["sample_count"]),
        cast(int, row["passed_count"]),
        cast(int, row["score_bps"]),
        tuple(cast(list[str], row["reason_codes"])),
        cast(str, row["evidence_digest"]),
    )


def _sample(row: RowMapping) -> QualityEvaluationSampleResult:
    return QualityEvaluationSampleResult(
        cast(UUID, row["evaluation_run_id"]),
        cast(UUID, row["workspace_id"]),
        cast(UUID, row["sample_version_id"]),
        cast(QualityEvaluationLayer, row["layer"]),
        cast(QualityEvaluationOutcome, row["outcome"]),
        cast(int, row["score_bps"]),
        cast(int, row["duration_ms"]),
        tuple(cast(list[str], row["reason_codes"])),
        cast(str, row["evidence_digest"]),
    )
