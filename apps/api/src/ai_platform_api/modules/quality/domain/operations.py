"""定义质量运营观察窗口、来源聚合和发布门禁的不可变端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

QualityGateStage = Literal["offline_release", "canary_promotion", "online_continuation"]
QualityEvidenceKind = Literal["synthetic", "authorized_real"]
QualityObservationSource = Literal["offline", "canary", "online_feedback"]
QualitySourceStatus = Literal["not_run", "passed", "failed"]
VerificationStatus = Literal["not_configured", "not_run", "passed", "failed"]
QualityReleaseGateStatus = Literal["blocked", "passed", "failed"]


@dataclass(frozen=True)
class QualityOperationTarget:
    """冻结一次质量门禁必须一致的阶段、窗口、区域和参数身份。"""

    evaluation_run_id: UUID
    gate_stage: QualityGateStage
    window_started_at: datetime
    window_ended_at: datetime
    network_region: str
    model_parameters_digest: str


@dataclass(frozen=True)
class QualityProviderEvidence:
    """声明受信采集器实际观察到的供应商配置版本和模型。"""

    provider_id: UUID
    provider_configuration_version: int
    model_id: str


@dataclass(frozen=True)
class QualitySourceObservation:
    """承载单一来源的低基数计数和瞬时证据，正文不得持久化。"""

    source: QualityObservationSource
    sample_count: int
    citation_claim_count: int
    valid_citation_count: int
    supported_citation_count: int
    answer_evaluated_count: int
    acceptable_answer_count: int
    feedback_count: int
    positive_feedback_count: int
    tool_call_count: int
    unauthorized_access_count: int
    restricted_field_leakage_count: int
    unauthorized_tool_call_count: int
    evidence: dict[str, object]


@dataclass(frozen=True)
class QualityOperationCollectionRequest:
    """向受信采集器传递目标身份，不接受浏览器直接提交聚合指标。"""

    workspace_id: UUID
    target: QualityOperationTarget


@dataclass(frozen=True)
class QualityOperationBatch:
    """携带采集器实际使用的完整身份，供 Application 检测漂移。"""

    workspace_id: UUID
    target: QualityOperationTarget
    evidence_kind: QualityEvidenceKind
    provider: QualityProviderEvidence | None
    observations: tuple[QualitySourceObservation, ...]


@dataclass(frozen=True)
class QualityEvaluationOperationContext:
    """从 P5-03 运行和正式发布事实解析出的权威质量目标身份。"""

    evaluation_run_id: UUID
    evaluation_status: Literal["passed", "failed"]
    evaluation_result_digest: str
    dataset_version_id: UUID
    dataset_digest: str
    service_id: UUID
    agent_release_id: UUID
    runtime_config_version_id: UUID
    run_configuration_digest: str


@dataclass(frozen=True)
class QualityProviderSnapshot:
    """表示平台配置与 Release 路由对指定供应商模型的权威解析结果。"""

    provider_id: UUID
    provider_key: str
    configuration_version: int
    policy_review_status: str
    probe_status: str
    status: str
    route_matches: bool


@dataclass(frozen=True)
class QualityOperationSourceResult:
    """保存单个来源的样本量、确定性比例、硬失败和证据摘要。"""

    quality_window_id: UUID
    workspace_id: UUID
    source: QualityObservationSource
    status: QualitySourceStatus
    sample_count: int
    citation_claim_count: int
    valid_citation_count: int
    supported_citation_count: int
    citation_presence_rate_bps: int | None
    citation_support_rate_bps: int | None
    answer_evaluated_count: int
    acceptable_answer_count: int
    answer_acceptance_rate_bps: int | None
    feedback_count: int
    positive_feedback_count: int
    positive_feedback_rate_bps: int | None
    tool_call_count: int
    unauthorized_access_count: int
    restricted_field_leakage_count: int
    unauthorized_tool_call_count: int
    reason_codes: tuple[str, ...]
    evidence_digest: str


@dataclass(frozen=True)
class QualityOperationWindow:
    """冻结质量运行身份、四维验证状态和不可改写的发布门禁结论。"""

    quality_window_id: UUID
    window_identity_digest: str
    workspace_id: UUID
    evaluation_run_id: UUID
    evaluation_result_digest: str
    dataset_version_id: UUID
    dataset_digest: str
    service_id: UUID
    agent_release_id: UUID
    run_configuration_digest: str
    gate_stage: QualityGateStage
    evidence_kind: QualityEvidenceKind
    collector_version: str
    provider_id: UUID | None
    provider_configuration_version: int | None
    model_id: str | None
    model_parameters_digest: str
    network_region: str
    window_started_at: datetime
    window_ended_at: datetime
    core_functional_status: VerificationStatus
    provider_integration_status: VerificationStatus
    ai_quality_status: VerificationStatus
    capacity_certification_status: VerificationStatus
    release_gate_status: QualityReleaseGateStatus
    reason_codes: tuple[str, ...]
    result_digest: str
    created_by_actor_id: UUID
    completed_at: datetime


@dataclass(frozen=True)
class QualityOperationReport:
    """组合质量窗口和固定三来源结果，供发布与后续控制台读取。"""

    window: QualityOperationWindow
    sources: tuple[QualityOperationSourceResult, ...]


class QualityOperationCollector(Protocol):
    """由受信内部 Adapter 汇聚离线、灰度和线上反馈计数。"""

    @property
    def collector_version(self) -> str: ...

    def collect(self, request: QualityOperationCollectionRequest) -> QualityOperationBatch: ...


class QualityOperationRepository(Protocol):
    """解析权威发布身份，并维护只追加质量运营窗口。"""

    def get_evaluation_context(
        self,
        workspace_id: UUID,
        evaluation_run_id: UUID,
    ) -> QualityEvaluationOperationContext | None: ...

    def get_provider_snapshot(
        self,
        context: QualityEvaluationOperationContext,
        evidence: QualityProviderEvidence,
    ) -> QualityProviderSnapshot | None: ...

    def lock_window_identity(self, window_identity_digest: str) -> None: ...

    def get_report_by_identity(
        self,
        workspace_id: UUID,
        window_identity_digest: str,
    ) -> QualityOperationReport | None: ...

    def get_report(
        self,
        workspace_id: UUID,
        quality_window_id: UUID,
    ) -> QualityOperationReport | None: ...

    def add_report(self, report: QualityOperationReport) -> None: ...


class QualityOperationUnitOfWork(Protocol):
    """保证窗口与三来源结果在同一短事务提交。"""

    @property
    def operations(self) -> QualityOperationRepository: ...

    def __enter__(self) -> QualityOperationUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
