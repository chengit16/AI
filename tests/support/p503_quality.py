"""提供 P5-03 真实 PostgreSQL 测试共用的质量评估 Harness。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from ai_platform_api.modules.identity.application.registration import RegistrationService
from ai_platform_api.modules.quality.application.evaluation import QualityEvaluationService
from ai_platform_api.modules.quality.application.evaluation_policy import (
    QUALITY_EVALUATION_LAYERS,
)
from ai_platform_api.modules.quality.application.service import QualitySampleService
from ai_platform_api.modules.quality.domain.evaluation import (
    QualityEvaluationBatch,
    QualityEvaluationExecutionRequest,
    QualityEvaluationLayer,
    QualityEvaluationObservation,
    QualityEvaluationOutcome,
    QualityEvaluationTarget,
)
from ai_platform_api.modules.quality.infrastructure.evaluation_sqlalchemy import (
    SqlAlchemyQualityEvaluationUnitOfWork,
)
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


@dataclass(frozen=True)
class QualityEvaluationHarness:
    """集中持有临时 Schema 的样本入口和评估 Unit of Work。"""

    engine: Engine
    sessions: sessionmaker[Session]
    registration: RegistrationService
    quality: QualitySampleService

    def service(self, executor: StaticQualityExecutor) -> QualityEvaluationService:
        return QualityEvaluationService(
            SqlAlchemyQualityEvaluationUnitOfWork(self.sessions),
            executor,
        )


class StaticQualityExecutor:
    """在测试内生成固定观测，并允许显式注入身份或结果漂移。"""

    def __init__(self, *, evidence_marker: str = "synthetic-p503-evidence") -> None:
        self.version = "deterministic-layered-v1"
        self.evidence_marker = evidence_marker
        self.observations: tuple[QualityEvaluationObservation, ...] | None = None
        self.batch_target: QualityEvaluationTarget | None = None
        self.batch_workspace_id: UUID | None = None

    @property
    def evaluator_version(self) -> str:
        return self.version

    def evaluate(self, request: QualityEvaluationExecutionRequest) -> QualityEvaluationBatch:
        values = self.observations
        if values is None:
            values = passing_observations(
                request.sample_version_ids,
                evidence_marker=self.evidence_marker,
            )
        return QualityEvaluationBatch(
            self.batch_workspace_id or request.workspace_id,
            self.batch_target or request.target,
            self.version,
            values,
        )


def passing_observations(
    sample_version_ids: tuple[UUID, ...],
    *,
    evidence_marker: str = "synthetic-p503-evidence",
) -> tuple[QualityEvaluationObservation, ...]:
    """为每层前两个数据集成员生成满分全合成观测。"""

    if len(sample_version_ids) < 2:
        raise ValueError("P5-03 通过夹具至少需要两个样本")
    return tuple(
        QualityEvaluationObservation(
            sample_version_id,
            layer,
            "passed",
            10_000,
            10 + position,
            (),
            {"detail": f"{evidence_marker}:{layer}:{sample_version_id}"},
        )
        for position, (layer, sample_version_id) in enumerate(
            (layer, sample_version_id)
            for layer in QUALITY_EVALUATION_LAYERS
            for sample_version_id in sample_version_ids[:2]
        )
    )


def replace_observation(
    values: tuple[QualityEvaluationObservation, ...],
    *,
    layer: QualityEvaluationLayer,
    outcome: QualityEvaluationOutcome,
    reason_code: str,
    score_bps: int,
) -> tuple[QualityEvaluationObservation, ...]:
    """只替换目标层首条观测，便于验证单点失败不能被其他样本抵消。"""

    updated = list(values)
    index = next(position for position, item in enumerate(updated) if item.layer == layer)
    updated[index] = replace(
        updated[index],
        outcome=outcome,
        reason_codes=(reason_code,),
        score_bps=score_bps,
    )
    return tuple(updated)
