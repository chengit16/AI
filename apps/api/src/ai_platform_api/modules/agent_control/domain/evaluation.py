"""定义 Agent 固定测试集、确定性评估和不可变结果端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

EvaluationCheckCode = Literal[
    "functional",
    "authorization",
    "prompt_injection",
    "citation",
    "output_contract",
]
EvaluationOutcome = Literal["passed", "failed", "timeout", "skipped"]
EvaluationStatus = Literal["passed", "failed"]
EvaluationEvidenceLevel = Literal["core_functional"]

REQUIRED_EVALUATION_CHECKS: tuple[EvaluationCheckCode, ...] = (
    "functional",
    "authorization",
    "prompt_injection",
    "citation",
    "output_contract",
)
HARD_GATE_CHECKS: tuple[EvaluationCheckCode, ...] = (
    "authorization",
    "prompt_injection",
    "citation",
    "output_contract",
)


@dataclass(frozen=True)
class AgentEvaluationDatasetVersion:
    """冻结工作空间内一组测试用例的版本身份，不保存运行结果。"""

    dataset_version_id: UUID
    workspace_id: UUID
    name: str
    dataset_version: str
    dataset_hash: str
    created_by_account_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class AgentEvaluationTestCase:
    """表示固定测试集中的单个合成用例及确定性阈值。"""

    case_id: UUID
    dataset_version_id: UUID
    workspace_id: UUID
    position: int
    case_key: str
    check_code: EvaluationCheckCode
    input_fixture: dict[str, object]
    expected_fixture: dict[str, object]
    timeout_ms: int
    minimum_score_bps: int
    case_hash: str


@dataclass(frozen=True)
class AgentEvaluationPolicyVersion:
    """固定必需检查、硬门禁和聚合阈值，不允许在线主观评分。"""

    evaluation_policy_version_id: UUID
    policy_key: str
    version_number: int
    required_check_codes: tuple[EvaluationCheckCode, ...]
    hard_gate_check_codes: tuple[EvaluationCheckCode, ...]
    minimum_check_scores: tuple[tuple[EvaluationCheckCode, int], ...]
    failure_handling: str
    timeout_handling: str
    skipped_handling: str
    evaluator_kind: str
    online_llm_grading: bool
    multimodal_image_qa: bool
    policy_hash: str
    status: str

    def minimum_score(self, check_code: EvaluationCheckCode) -> int:
        """读取检查阈值；缺失阈值属于损坏策略并失败关闭。"""

        for configured_code, score in self.minimum_check_scores:
            if configured_code == check_code:
                return score
        raise ValueError("评估策略缺少必需检查阈值")


@dataclass(frozen=True)
class AgentEvaluationRequest:
    """向内部执行器传递不可变候选身份、配置引用和固定用例。"""

    candidate_id: UUID
    candidate_hash: str
    config_hash: str
    configuration: dict[str, object]
    dataset: AgentEvaluationDatasetVersion
    cases: tuple[AgentEvaluationTestCase, ...]


@dataclass(frozen=True)
class AgentEvaluationObservation:
    """承载内部执行器的瞬时观测；证据正文只参与摘要，不进入数据库。"""

    case_id: UUID
    outcome: EvaluationOutcome
    score_bps: int
    duration_ms: int
    evidence: dict[str, object]


@dataclass(frozen=True)
class AgentEvaluationCaseResult:
    """保存单用例的确定性结论和脱敏证据摘要。"""

    evaluation_run_id: UUID
    workspace_id: UUID
    case_id: UUID
    check_code: EvaluationCheckCode
    outcome: EvaluationOutcome
    score_bps: int
    duration_ms: int
    evidence_hash: str


@dataclass(frozen=True)
class AgentEvaluationCheckResult:
    """聚合同类用例；任何失败、超时或跳过都会令检查失败。"""

    evaluation_run_id: UUID
    workspace_id: UUID
    check_code: EvaluationCheckCode
    status: EvaluationStatus
    case_count: int
    passed_count: int
    score_bps: int
    evidence_hash: str


@dataclass(frozen=True)
class AgentEvaluationRun:
    """绑定候选、测试集、策略和结果摘要的不可变评估运行。"""

    evaluation_run_id: UUID
    candidate_id: UUID
    workspace_id: UUID
    dataset_version_id: UUID
    evaluation_policy_version_id: UUID
    candidate_hash: str
    config_hash: str
    evaluator_version: str
    evidence_level: EvaluationEvidenceLevel
    status: EvaluationStatus
    total_cases: int
    passed_cases: int
    failed_cases: int
    timeout_cases: int
    skipped_cases: int
    result_hash: str
    created_by_account_id: UUID
    completed_at: datetime


@dataclass(frozen=True)
class AgentEvaluationReport:
    """组合运行、检查和单用例事实，供控制面查询与后续审批绑定。"""

    run: AgentEvaluationRun
    checks: tuple[AgentEvaluationCheckResult, ...]
    cases: tuple[AgentEvaluationCaseResult, ...]


class AgentEvaluationExecutor(Protocol):
    """由内部 Runtime Adapter 实现固定用例执行，浏览器请求不能直接提供观测。"""

    @property
    def evaluator_version(self) -> str: ...

    def evaluate(
        self,
        request: AgentEvaluationRequest,
    ) -> tuple[AgentEvaluationObservation, ...]: ...


class AgentEvaluationRepository(Protocol):
    """维护测试集、策略、不可变运行结果和候选测试状态。"""

    def add_dataset_version(
        self,
        dataset: AgentEvaluationDatasetVersion,
        cases: tuple[AgentEvaluationTestCase, ...],
    ) -> bool: ...

    def get_dataset_version(
        self,
        workspace_id: UUID,
        dataset_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> AgentEvaluationDatasetVersion | None: ...

    def get_dataset_by_version(
        self,
        workspace_id: UUID,
        dataset_version: str,
    ) -> AgentEvaluationDatasetVersion | None: ...

    def get_dataset_cases(
        self,
        workspace_id: UUID,
        dataset_version_id: UUID,
        *,
        for_share: bool = False,
    ) -> tuple[AgentEvaluationTestCase, ...]: ...

    def get_active_policy(
        self,
        *,
        for_share: bool = False,
    ) -> AgentEvaluationPolicyVersion | None: ...

    def get_report(
        self,
        workspace_id: UUID,
        evaluation_run_id: UUID,
    ) -> AgentEvaluationReport | None: ...

    def get_report_by_identity(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
        dataset_version_id: UUID,
        evaluation_policy_version_id: UUID,
    ) -> AgentEvaluationReport | None: ...

    def get_latest_passing_report(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> AgentEvaluationReport | None: ...

    def get_latest_report(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> AgentEvaluationReport | None: ...

    def add_report(self, report: AgentEvaluationReport) -> None: ...

    def transition_candidate_after_evaluation(
        self,
        workspace_id: UUID,
        candidate_id: UUID,
        *,
        expected_version: int,
        next_status: Literal["test_failed", "ready_for_approval"],
        updated_at: datetime,
    ) -> bool: ...
