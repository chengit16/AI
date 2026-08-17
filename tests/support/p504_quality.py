"""提供 P5-04 质量运营测试使用的固定受信采集器。"""

from __future__ import annotations

from dataclasses import replace

from ai_platform_api.modules.quality.application.operations_policy import (
    QUALITY_OPERATION_COLLECTOR_VERSION,
)
from ai_platform_api.modules.quality.domain.operations import (
    QualityEvidenceKind,
    QualityOperationBatch,
    QualityOperationCollectionRequest,
    QualityOperationTarget,
    QualityProviderEvidence,
    QualitySourceObservation,
)


class StaticQualityOperationCollector:
    """返回固定聚合，并允许测试显式制造身份或结果漂移。"""

    def __init__(
        self,
        *,
        evidence_kind: QualityEvidenceKind = "synthetic",
        provider: QualityProviderEvidence | None = None,
        observations: tuple[QualitySourceObservation, ...] = (),
    ) -> None:
        self.version = QUALITY_OPERATION_COLLECTOR_VERSION
        self.evidence_kind = evidence_kind
        self.provider = provider
        self.observations = observations
        self.batch_target: QualityOperationTarget | None = None

    @property
    def collector_version(self) -> str:
        return self.version

    def collect(self, request: QualityOperationCollectionRequest) -> QualityOperationBatch:
        return QualityOperationBatch(
            request.workspace_id,
            self.batch_target or request.target,
            self.evidence_kind,
            self.provider,
            self.observations,
        )


def passing_offline_observation(
    *,
    evidence_marker: str = "synthetic-p504-postgres-evidence",
) -> QualitySourceObservation:
    """生成达到全部离线阈值且不含安全失败的全合成聚合。"""

    return QualitySourceObservation(
        "offline",
        10,
        20,
        20,
        19,
        10,
        9,
        0,
        0,
        4,
        0,
        0,
        0,
        {"marker": evidence_marker},
    )


def with_unauthorized_tool_call(
    observation: QualitySourceObservation,
) -> QualitySourceObservation:
    """制造高质量比例下的未授权工具调用，验证硬失败不可被均分抵消。"""

    return replace(
        observation,
        tool_call_count=max(1, observation.tool_call_count),
        unauthorized_tool_call_count=1,
    )
