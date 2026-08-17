"""使用工作空间复合条件持久化质量运营窗口与来源摘要。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import cast
from uuid import UUID

from sqlalchemy import exists, func, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from ai_platform_api.modules.quality.domain.operations import (
    QualityEvaluationOperationContext,
    QualityEvidenceKind,
    QualityGateStage,
    QualityObservationSource,
    QualityOperationReport,
    QualityOperationSourceResult,
    QualityOperationWindow,
    QualityProviderEvidence,
    QualityProviderSnapshot,
    QualityReleaseGateStatus,
    QualitySourceStatus,
    VerificationStatus,
)
from ai_platform_api.persistence.tables import (
    agent_releases,
    ai_runtime_config_versions,
    ai_runtime_model_routes,
    model_provider_configurations,
    quality_evaluation_runs,
    quality_operation_source_results,
    quality_operation_windows,
    services,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyQualityOperationRepository:
    """读取 P5-03 与发布配置事实，并一次写入质量运营报告。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_evaluation_context(
        self,
        workspace_id: UUID,
        evaluation_run_id: UUID,
    ) -> QualityEvaluationOperationContext | None:
        """要求评估目标确实属于同一工作空间、服务和不可变 Release。"""

        row = (
            self._session.execute(
                select(
                    quality_evaluation_runs.c.evaluation_run_id,
                    quality_evaluation_runs.c.status.label("evaluation_status"),
                    quality_evaluation_runs.c.result_digest.label("evaluation_result_digest"),
                    quality_evaluation_runs.c.dataset_version_id,
                    quality_evaluation_runs.c.dataset_digest,
                    quality_evaluation_runs.c.service_id,
                    quality_evaluation_runs.c.agent_release_id,
                    quality_evaluation_runs.c.run_configuration_digest,
                    agent_releases.c.runtime_config_version_id,
                )
                .join(
                    agent_releases,
                    (agent_releases.c.workspace_id == quality_evaluation_runs.c.workspace_id)
                    & (agent_releases.c.release_id == quality_evaluation_runs.c.agent_release_id),
                )
                .join(
                    services,
                    (services.c.workspace_id == quality_evaluation_runs.c.workspace_id)
                    & (services.c.service_id == quality_evaluation_runs.c.service_id)
                    & (services.c.agent_id == agent_releases.c.agent_id),
                )
                .join(
                    ai_runtime_config_versions,
                    ai_runtime_config_versions.c.runtime_config_version_id
                    == agent_releases.c.runtime_config_version_id,
                )
                .where(
                    quality_evaluation_runs.c.workspace_id == workspace_id,
                    quality_evaluation_runs.c.evaluation_run_id == evaluation_run_id,
                    ai_runtime_config_versions.c.content_hash
                    == quality_evaluation_runs.c.run_configuration_digest,
                )
            )
            .mappings()
            .one_or_none()
        )
        return _evaluation_context(row) if row is not None else None

    def get_provider_snapshot(
        self,
        context: QualityEvaluationOperationContext,
        evidence: QualityProviderEvidence,
    ) -> QualityProviderSnapshot | None:
        """同时读取供应商状态和 Release 冻结运行配置中的精确模型路由。"""

        route_matches = exists(
            select(ai_runtime_model_routes.c.route_id).where(
                ai_runtime_model_routes.c.runtime_config_version_id
                == context.runtime_config_version_id,
                ai_runtime_model_routes.c.provider_id == evidence.provider_id,
                ai_runtime_model_routes.c.provider_configuration_version
                == evidence.provider_configuration_version,
                ai_runtime_model_routes.c.model_id == evidence.model_id,
            )
        )
        row = (
            self._session.execute(
                select(
                    model_provider_configurations.c.provider_id,
                    model_provider_configurations.c.provider_key,
                    model_provider_configurations.c.version.label("configuration_version"),
                    model_provider_configurations.c.policy_review_status,
                    model_provider_configurations.c.probe_status,
                    model_provider_configurations.c.status,
                    route_matches.label("route_matches"),
                ).where(model_provider_configurations.c.provider_id == evidence.provider_id)
            )
            .mappings()
            .one_or_none()
        )
        return _provider_snapshot(row) if row is not None else None

    def lock_window_identity(self, window_identity_digest: str) -> None:
        """串行化相同窗口，避免并发采集产生两份相互冲突的事实。"""

        self._session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(window_identity_digest, 0)))
        )

    def get_report_by_identity(
        self,
        workspace_id: UUID,
        window_identity_digest: str,
    ) -> QualityOperationReport | None:
        row = (
            self._session.execute(
                select(quality_operation_windows).where(
                    quality_operation_windows.c.workspace_id == workspace_id,
                    quality_operation_windows.c.window_identity_digest == window_identity_digest,
                )
            )
            .mappings()
            .one_or_none()
        )
        return self._report(row) if row is not None else None

    def get_report(
        self,
        workspace_id: UUID,
        quality_window_id: UUID,
    ) -> QualityOperationReport | None:
        row = (
            self._session.execute(
                select(quality_operation_windows).where(
                    quality_operation_windows.c.workspace_id == workspace_id,
                    quality_operation_windows.c.quality_window_id == quality_window_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        return self._report(row) if row is not None else None

    def add_report(self, report: QualityOperationReport) -> None:
        """先写窗口身份，再按固定顺序写三来源结果。"""

        # 1. 主事实先冻结完整身份和四维结论，子表只能引用同一工作空间窗口。
        window = report.window
        self._session.execute(
            insert(quality_operation_windows).values(
                quality_window_id=window.quality_window_id,
                window_identity_digest=window.window_identity_digest,
                workspace_id=window.workspace_id,
                evaluation_run_id=window.evaluation_run_id,
                evaluation_result_digest=window.evaluation_result_digest,
                dataset_version_id=window.dataset_version_id,
                dataset_digest=window.dataset_digest,
                service_id=window.service_id,
                agent_release_id=window.agent_release_id,
                run_configuration_digest=window.run_configuration_digest,
                gate_stage=window.gate_stage,
                evidence_kind=window.evidence_kind,
                collector_version=window.collector_version,
                provider_id=window.provider_id,
                provider_configuration_version=window.provider_configuration_version,
                model_id=window.model_id,
                model_parameters_digest=window.model_parameters_digest,
                network_region=window.network_region,
                window_started_at=window.window_started_at,
                window_ended_at=window.window_ended_at,
                core_functional_status=window.core_functional_status,
                provider_integration_status=window.provider_integration_status,
                ai_quality_status=window.ai_quality_status,
                capacity_certification_status=window.capacity_certification_status,
                release_gate_status=window.release_gate_status,
                reason_codes=list(window.reason_codes),
                result_digest=window.result_digest,
                created_by_actor_id=window.created_by_actor_id,
                completed_at=window.completed_at,
            )
        )
        # 2. 三来源按协议顺序一次写入；后续普通事务由不可变 Trigger 全部拒绝。
        self._session.execute(
            insert(quality_operation_source_results),
            [
                {
                    "quality_window_id": item.quality_window_id,
                    "workspace_id": item.workspace_id,
                    "position": position,
                    "source": item.source,
                    "status": item.status,
                    "sample_count": item.sample_count,
                    "citation_claim_count": item.citation_claim_count,
                    "valid_citation_count": item.valid_citation_count,
                    "supported_citation_count": item.supported_citation_count,
                    "citation_presence_rate_bps": item.citation_presence_rate_bps,
                    "citation_support_rate_bps": item.citation_support_rate_bps,
                    "answer_evaluated_count": item.answer_evaluated_count,
                    "acceptable_answer_count": item.acceptable_answer_count,
                    "answer_acceptance_rate_bps": item.answer_acceptance_rate_bps,
                    "feedback_count": item.feedback_count,
                    "positive_feedback_count": item.positive_feedback_count,
                    "positive_feedback_rate_bps": item.positive_feedback_rate_bps,
                    "tool_call_count": item.tool_call_count,
                    "unauthorized_access_count": item.unauthorized_access_count,
                    "restricted_field_leakage_count": item.restricted_field_leakage_count,
                    "unauthorized_tool_call_count": item.unauthorized_tool_call_count,
                    "reason_codes": list(item.reason_codes),
                    "evidence_digest": item.evidence_digest,
                }
                for position, item in enumerate(report.sources, start=1)
            ],
        )

    def _report(self, window_row: RowMapping) -> QualityOperationReport:
        window_id = cast(UUID, window_row["quality_window_id"])
        workspace_id = cast(UUID, window_row["workspace_id"])
        rows = (
            self._session.execute(
                select(quality_operation_source_results)
                .where(
                    quality_operation_source_results.c.workspace_id == workspace_id,
                    quality_operation_source_results.c.quality_window_id == window_id,
                )
                .order_by(quality_operation_source_results.c.position)
            )
            .mappings()
            .all()
        )
        return QualityOperationReport(
            _window(window_row),
            tuple(_source(row) for row in rows),
        )


class SqlAlchemyQualityOperationUnitOfWork:
    """为质量运营查询和只追加报告提供显式短事务。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._operations: SqlAlchemyQualityOperationRepository | None = None

    @property
    def operations(self) -> SqlAlchemyQualityOperationRepository:
        if self._operations is None:
            raise RuntimeError("Quality Operation Unit of Work 尚未进入事务范围")
        return self._operations

    def __enter__(self) -> SqlAlchemyQualityOperationUnitOfWork:
        if self._session is not None:
            raise RuntimeError("Quality Operation Unit of Work 不允许重复进入")
        self._session = self._session_factory()
        self._operations = SqlAlchemyQualityOperationRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        if self._session is not None:
            self._session.rollback()
            self._session.close()
        self._session = None
        self._operations = None

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Quality Operation Unit of Work 尚未进入事务范围")
        self._session.commit()


def _evaluation_context(row: RowMapping) -> QualityEvaluationOperationContext:
    return QualityEvaluationOperationContext(
        cast(UUID, row["evaluation_run_id"]),
        cast(str, row["evaluation_status"]),  # type: ignore[arg-type]
        cast(str, row["evaluation_result_digest"]),
        cast(UUID, row["dataset_version_id"]),
        cast(str, row["dataset_digest"]),
        cast(UUID, row["service_id"]),
        cast(UUID, row["agent_release_id"]),
        cast(UUID, row["runtime_config_version_id"]),
        cast(str, row["run_configuration_digest"]),
    )


def _provider_snapshot(row: RowMapping) -> QualityProviderSnapshot:
    return QualityProviderSnapshot(
        cast(UUID, row["provider_id"]),
        cast(str, row["provider_key"]),
        cast(int, row["configuration_version"]),
        cast(str, row["policy_review_status"]),
        cast(str, row["probe_status"]),
        cast(str, row["status"]),
        cast(bool, row["route_matches"]),
    )


def _window(row: RowMapping) -> QualityOperationWindow:
    return QualityOperationWindow(
        quality_window_id=cast(UUID, row["quality_window_id"]),
        window_identity_digest=cast(str, row["window_identity_digest"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        evaluation_run_id=cast(UUID, row["evaluation_run_id"]),
        evaluation_result_digest=cast(str, row["evaluation_result_digest"]),
        dataset_version_id=cast(UUID, row["dataset_version_id"]),
        dataset_digest=cast(str, row["dataset_digest"]),
        service_id=cast(UUID, row["service_id"]),
        agent_release_id=cast(UUID, row["agent_release_id"]),
        run_configuration_digest=cast(str, row["run_configuration_digest"]),
        gate_stage=cast(QualityGateStage, row["gate_stage"]),
        evidence_kind=cast(QualityEvidenceKind, row["evidence_kind"]),
        collector_version=cast(str, row["collector_version"]),
        provider_id=cast(UUID | None, row["provider_id"]),
        provider_configuration_version=cast(int | None, row["provider_configuration_version"]),
        model_id=cast(str | None, row["model_id"]),
        model_parameters_digest=cast(str, row["model_parameters_digest"]),
        network_region=cast(str, row["network_region"]),
        window_started_at=cast(datetime, row["window_started_at"]),
        window_ended_at=cast(datetime, row["window_ended_at"]),
        core_functional_status=cast(VerificationStatus, row["core_functional_status"]),
        provider_integration_status=cast(VerificationStatus, row["provider_integration_status"]),
        ai_quality_status=cast(VerificationStatus, row["ai_quality_status"]),
        capacity_certification_status=cast(
            VerificationStatus, row["capacity_certification_status"]
        ),
        release_gate_status=cast(QualityReleaseGateStatus, row["release_gate_status"]),
        reason_codes=tuple(cast(list[str], row["reason_codes"])),
        result_digest=cast(str, row["result_digest"]),
        created_by_actor_id=cast(UUID, row["created_by_actor_id"]),
        completed_at=cast(datetime, row["completed_at"]),
    )


def _source(row: RowMapping) -> QualityOperationSourceResult:
    return QualityOperationSourceResult(
        cast(UUID, row["quality_window_id"]),
        cast(UUID, row["workspace_id"]),
        cast(QualityObservationSource, row["source"]),
        cast(QualitySourceStatus, row["status"]),
        cast(int, row["sample_count"]),
        cast(int, row["citation_claim_count"]),
        cast(int, row["valid_citation_count"]),
        cast(int, row["supported_citation_count"]),
        cast(int | None, row["citation_presence_rate_bps"]),
        cast(int | None, row["citation_support_rate_bps"]),
        cast(int, row["answer_evaluated_count"]),
        cast(int, row["acceptable_answer_count"]),
        cast(int | None, row["answer_acceptance_rate_bps"]),
        cast(int, row["feedback_count"]),
        cast(int, row["positive_feedback_count"]),
        cast(int | None, row["positive_feedback_rate_bps"]),
        cast(int, row["tool_call_count"]),
        cast(int, row["unauthorized_access_count"]),
        cast(int, row["restricted_field_leakage_count"]),
        cast(int, row["unauthorized_tool_call_count"]),
        tuple(cast(list[str], row["reason_codes"])),
        cast(str, row["evidence_digest"]),
    )
