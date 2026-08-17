"""定义六层质量评估的固定身份、瞬时观测和不可变结果端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

QualityEvaluationLayer = Literal[
    "retrieval",
    "citation",
    "model",
    "prompt",
    "workflow",
    "tool",
]
QualityEvaluationOutcome = Literal["passed", "failed", "timeout", "skipped"]
QualityEvaluationStatus = Literal["passed", "failed"]


@dataclass(frozen=True)
class QualityLayerPolicy:
    """固定单层样本下限、最低得分和允许的可解释原因码。"""

    layer: QualityEvaluationLayer
    minimum_sample_count: int
    minimum_score_bps: int
    allowed_reason_codes: frozenset[str]


@dataclass(frozen=True)
class QualityEvaluationPolicy:
    """冻结六层顺序、评估器版本和不可被均分抵消的安全原因。"""

    policy_version_id: UUID
    version_number: int
    evaluator_version: str
    layers: tuple[QualityLayerPolicy, ...]
    hard_failure_reason_codes: frozenset[str]
    policy_digest: str


@dataclass(frozen=True)
class QualityEvaluationTarget:
    """声明一次评估必须绑定的数据集、服务、Release 和运行配置身份。"""

    dataset_version_id: UUID
    dataset_digest: str
    service_id: UUID
    agent_release_id: UUID
    run_configuration_digest: str


@dataclass(frozen=True)
class QualityEvaluationExecutionRequest:
    """向内部评估器提供摘要身份和成员 ID，不传递样本正文。"""

    workspace_id: UUID
    target: QualityEvaluationTarget
    sample_version_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class QualityEvaluationObservation:
    """承载评估器的瞬时证据；Application 计算摘要后不得持久化 evidence。"""

    sample_version_id: UUID
    layer: QualityEvaluationLayer
    outcome: QualityEvaluationOutcome
    score_bps: int
    duration_ms: int
    reason_codes: tuple[str, ...]
    evidence: dict[str, object]


@dataclass(frozen=True)
class QualityEvaluationBatch:
    """携带执行器实际使用的完整身份，供 Application 检测运行漂移。"""

    workspace_id: UUID
    target: QualityEvaluationTarget
    evaluator_version: str
    observations: tuple[QualityEvaluationObservation, ...]


@dataclass(frozen=True)
class QualityEvaluationSampleResult:
    """保存单样本单层结论、低基数原因和证据摘要。"""

    evaluation_run_id: UUID
    workspace_id: UUID
    sample_version_id: UUID
    layer: QualityEvaluationLayer
    outcome: QualityEvaluationOutcome
    score_bps: int
    duration_ms: int
    reason_codes: tuple[str, ...]
    evidence_digest: str


@dataclass(frozen=True)
class QualityEvaluationLayerResult:
    """聚合单层事实；非通过观测、样本不足和硬失败都会令本层失败。"""

    evaluation_run_id: UUID
    workspace_id: UUID
    layer: QualityEvaluationLayer
    status: QualityEvaluationStatus
    sample_count: int
    passed_count: int
    score_bps: int
    reason_codes: tuple[str, ...]
    evidence_digest: str


@dataclass(frozen=True)
class QualityEvaluationRun:
    """绑定全部稳定身份和聚合摘要，不保存 Prompt、回答或证据正文。"""

    evaluation_run_id: UUID
    run_identity_digest: str
    workspace_id: UUID
    dataset_version_id: UUID
    dataset_digest: str
    service_id: UUID
    agent_release_id: UUID
    run_configuration_digest: str
    policy_version_id: UUID
    policy_digest: str
    evaluator_version: str
    evaluator_identity_digest: str
    status: QualityEvaluationStatus
    observation_count: int
    passed_count: int
    failed_count: int
    timeout_count: int
    skipped_count: int
    reason_codes: tuple[str, ...]
    result_digest: str
    created_by_actor_id: UUID
    completed_at: datetime


@dataclass(frozen=True)
class QualityEvaluationReport:
    """组合运行、六层结果和单样本事实，供质量运营与后续门禁读取。"""

    run: QualityEvaluationRun
    layers: tuple[QualityEvaluationLayerResult, ...]
    samples: tuple[QualityEvaluationSampleResult, ...]


class QualityEvaluationExecutor(Protocol):
    """由受信内部 Adapter 执行固定评估，浏览器不能直接提交观测。"""

    @property
    def evaluator_version(self) -> str: ...

    def evaluate(self, request: QualityEvaluationExecutionRequest) -> QualityEvaluationBatch: ...


class QualityEvaluationRepository(Protocol):
    """按工作空间读取数据集并维护只追加评估报告。"""

    def get_dataset_snapshot(
        self,
        workspace_id: UUID,
        dataset_version_id: UUID,
    ) -> tuple[str, tuple[UUID, ...]] | None: ...

    def lock_run_identity(self, run_identity_digest: str) -> None: ...

    def get_report_by_identity(
        self,
        workspace_id: UUID,
        run_identity_digest: str,
    ) -> QualityEvaluationReport | None: ...

    def get_report(
        self,
        workspace_id: UUID,
        evaluation_run_id: UUID,
    ) -> QualityEvaluationReport | None: ...

    def add_report(self, report: QualityEvaluationReport) -> None: ...


class QualityEvaluationUnitOfWork(Protocol):
    """保证运行、六层和样本结果在同一短事务提交。"""

    @property
    def evaluations(self) -> QualityEvaluationRepository: ...

    def __enter__(self) -> QualityEvaluationUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
