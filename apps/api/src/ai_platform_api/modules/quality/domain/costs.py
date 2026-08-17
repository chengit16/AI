"""定义成本归因窗口、逐尝试账本、固定聚合和供应商对账端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

CostEvidenceKind = Literal["synthetic", "authorized_real"]
CostComponent = Literal[
    "model",
    "retrieval",
    "ocr",
    "indexing",
    "embedding",
    "reranker",
    "tool",
]
CostSourceKind = Literal[
    "model_attempt",
    "retrieval_run",
    "ocr_attempt",
    "index_build",
    "embedding_batch",
    "reranker_request",
    "tool_attempt",
]
CostUsageUnit = Literal["token", "request", "page", "chunk", "pair", "millisecond"]
CostOutcome = Literal["succeeded", "failed", "timed_out", "cancelled", "degraded"]
CostEstimateSource = Literal["synthetic_rate", "contract_rate", "provider_rate"]
CostAmountSource = Literal[
    "synthetic_rate",
    "contract_rate",
    "provider_rate",
    "provider_reported",
]
CostVerificationStatus = Literal["not_configured", "not_run", "passed", "failed"]
CostReconciliationStatus = Literal[
    "not_configured",
    "not_run",
    "matched",
    "explained",
    "failed",
]
CostSupplierStatementStatus = Literal["not_configured", "not_run", "provided"]


@dataclass(frozen=True)
class CostAttributionTarget:
    """冻结一次归因必须一致的发布、价格、区域和时间窗口身份。"""

    service_id: UUID
    agent_release_id: UUID
    run_configuration_digest: str
    price_version: str
    price_catalog_digest: str
    currency: str
    network_region: str
    window_started_at: datetime
    window_ended_at: datetime


@dataclass(frozen=True)
class CostUsageObservation:
    """承载单个来源 Attempt 的整数用量、价格和瞬时证据。"""

    component: CostComponent
    source_kind: CostSourceKind
    source_record_id: UUID
    meter_key: str
    attempt_no: int
    outcome: CostOutcome
    is_retry: bool
    quantity: int
    usage_unit: CostUsageUnit
    unit_size: int
    unit_price_minor: int
    price_version: str
    currency: str
    estimate_source: CostEstimateSource
    reported_amount_minor: int | None
    evidence: dict[str, object]


@dataclass(frozen=True)
class CostSupplierStatement:
    """表示供应商账单状态；正文只用于计算摘要，不进入持久化事实。"""

    status: CostSupplierStatementStatus
    supplier_account_digest: str | None
    statement_digest: str | None
    amount_minor: int | None
    currency: str | None
    difference_reason_codes: tuple[str, ...]
    evidence: dict[str, object]


@dataclass(frozen=True)
class CostCollectionRequest:
    """向受信采集器传递冻结目标，调用方不能直接提交用量或金额。"""

    workspace_id: UUID
    target: CostAttributionTarget


@dataclass(frozen=True)
class CostCollectionBatch:
    """携带采集器实际使用的完整身份，用于检测价格和目标漂移。"""

    workspace_id: UUID
    target: CostAttributionTarget
    evidence_kind: CostEvidenceKind
    observations: tuple[CostUsageObservation, ...]
    supplier_statement: CostSupplierStatement


@dataclass(frozen=True)
class CostReleaseContext:
    """从权威 Service、AgentRelease 和运行配置解析出的发布身份。"""

    service_id: UUID
    agent_release_id: UUID
    runtime_config_version_id: UUID
    run_configuration_digest: str


@dataclass(frozen=True)
class CostLedgerEntry:
    """保存单个 Attempt 的估算、供应商报告值和最终确认金额。"""

    ledger_entry_id: UUID
    cost_window_id: UUID
    workspace_id: UUID
    service_id: UUID
    agent_release_id: UUID
    component: CostComponent
    source_kind: CostSourceKind
    source_record_id: UUID
    meter_key: str
    attempt_no: int
    outcome: CostOutcome
    is_retry: bool
    quantity: int
    usage_unit: CostUsageUnit
    unit_size: int
    unit_price_minor: int
    estimated_amount_minor: int
    reported_amount_minor: int | None
    recognized_amount_minor: int
    amount_source: CostAmountSource
    price_version: str
    currency: str
    evidence_digest: str


@dataclass(frozen=True)
class CostAttributionLine:
    """按固定组件聚合条目数、失败、重试、用量和确认金额。"""

    cost_window_id: UUID
    workspace_id: UUID
    component: CostComponent
    entry_count: int
    failed_entry_count: int
    retry_entry_count: int
    quantity: int
    estimated_amount_minor: int
    reported_amount_minor: int
    recognized_amount_minor: int
    evidence_digest: str


@dataclass(frozen=True)
class CostAttributionWindow:
    """冻结归因身份、价格验证和账单差异结论，不保存账单正文。"""

    cost_window_id: UUID
    window_identity_digest: str
    workspace_id: UUID
    service_id: UUID
    agent_release_id: UUID
    runtime_config_version_id: UUID
    run_configuration_digest: str
    price_version: str
    price_catalog_digest: str
    currency: str
    network_region: str
    window_started_at: datetime
    window_ended_at: datetime
    evidence_kind: CostEvidenceKind
    collector_version: str
    attribution_status: CostVerificationStatus
    price_verification_status: CostVerificationStatus
    reconciliation_status: CostReconciliationStatus
    entry_count: int
    failed_entry_count: int
    retry_entry_count: int
    estimated_amount_minor: int
    reported_amount_minor: int
    recognized_amount_minor: int
    supplier_statement_amount_minor: int | None
    reconciliation_difference_minor: int | None
    supplier_account_digest: str | None
    supplier_statement_digest: str | None
    supplier_evidence_digest: str
    reason_codes: tuple[str, ...]
    result_digest: str
    created_by_actor_id: UUID
    completed_at: datetime


@dataclass(frozen=True)
class CostAttributionReport:
    """组合成本窗口、逐尝试账本和固定七类聚合。"""

    window: CostAttributionWindow
    entries: tuple[CostLedgerEntry, ...]
    lines: tuple[CostAttributionLine, ...]


class CostUsageCollector(Protocol):
    """由受信内部 Adapter 汇聚各模块已有的用量与成本事实。"""

    @property
    def collector_version(self) -> str: ...

    def collect(self, request: CostCollectionRequest) -> CostCollectionBatch: ...


class CostAttributionRepository(Protocol):
    """解析权威发布身份，并维护只追加成本归因报告。"""

    def get_release_context(
        self,
        workspace_id: UUID,
        service_id: UUID,
        agent_release_id: UUID,
    ) -> CostReleaseContext | None: ...

    def lock_window_identity(self, window_identity_digest: str) -> None: ...

    def get_report_by_identity(
        self,
        workspace_id: UUID,
        window_identity_digest: str,
    ) -> CostAttributionReport | None: ...

    def get_report(
        self,
        workspace_id: UUID,
        cost_window_id: UUID,
    ) -> CostAttributionReport | None: ...

    def add_report(self, report: CostAttributionReport) -> None: ...


class CostAttributionUnitOfWork(Protocol):
    """保证窗口、账本和聚合在同一短事务提交。"""

    @property
    def costs(self) -> CostAttributionRepository: ...

    def __enter__(self) -> CostAttributionUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...
