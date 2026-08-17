"""定义法规策略、法律保留、解除和低敏合规证明。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import AuditWriter, OutboxWriter

JurisdictionStatus = Literal["not_configured", "configured"]
ExternalReviewStatus = Literal["not_configured", "approved", "rejected"]
LifecycleComplianceOperation = Literal["export", "purge", "retention"]
LifecycleComplianceDecision = Literal["allowed", "blocked", "not_configured"]
LegalHoldScope = Literal["workspace"]

RETENTION_CLASSES = (
    "stream_events",
    "published_outbox",
    "attempts_and_dead_letters",
    "minimum_records",
)
COMPLIANCE_REASON_CODES = frozenset(
    {
        "authorized_export_preserved",
        "jurisdiction_not_configured",
        "regulatory_policy_configured",
        "active_legal_hold",
        "external_review_not_configured",
        "external_review_rejected",
    }
)


@dataclass(frozen=True)
class RegulatoryPolicyConfiguration:
    """由受信配置源提供法域、保留期和外部复核状态。"""

    jurisdiction_status: JurisdictionStatus
    jurisdiction_codes: tuple[str, ...]
    retention_period_days: dict[str, int]
    external_review_status: ExternalReviewStatus
    external_review_digest: str | None


@dataclass(frozen=True)
class RegulatoryPolicyVersion:
    """冻结一个工作空间的法规策略版本，不保存法规或客户正文。"""

    regulatory_policy_id: UUID
    workspace_id: UUID
    policy_version: int
    jurisdiction_status: JurisdictionStatus
    jurisdiction_codes: tuple[str, ...]
    retention_period_days: dict[str, int]
    external_review_status: ExternalReviewStatus
    external_review_digest: str | None
    policy_digest: str
    created_by_actor_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class LegalHold:
    """保存工作空间级法律保留的低敏、只追加激活事实。"""

    legal_hold_id: UUID
    workspace_id: UUID
    regulatory_policy_id: UUID
    idempotency_key: str
    request_hash: str
    scope_type: LegalHoldScope
    scope_digest: str
    case_reference_digest: str
    reason_code: str
    activated_by_actor_id: UUID
    activated_at: datetime


@dataclass(frozen=True)
class LegalHoldRelease:
    """以独立事实解除法律保留，原激活记录保持不可变。"""

    release_id: UUID
    workspace_id: UUID
    legal_hold_id: UUID
    idempotency_key: str
    request_hash: str
    reason_code: str
    release_evidence_digest: str
    released_by_actor_id: UUID
    released_at: datetime


@dataclass(frozen=True)
class LifecycleComplianceProof:
    """证明一次导出、删除或保留裁决，不包含业务正文或凭证。"""

    compliance_proof_id: UUID
    workspace_id: UUID
    operation: LifecycleComplianceOperation
    operation_id: UUID
    request_key_digest: str
    request_hash: str
    decision: LifecycleComplianceDecision
    reason_codes: tuple[str, ...]
    regulatory_policy_id: UUID | None
    policy_digest: str | None
    external_review_status: ExternalReviewStatus
    active_hold_count: int
    hold_set_digest: str
    proof_digest: str
    created_by_actor_id: UUID
    created_at: datetime


class RegulatoryPolicySource(Protocol):
    """隔离浏览器输入与受信法域配置，调用方不能自行声明外部审核通过。"""

    def resolve(self, workspace_id: UUID) -> RegulatoryPolicyConfiguration: ...


class LifecycleComplianceRepository(Protocol):
    """维护 Lifecycle 模块拥有的法规、保留、解除和证明事实。"""

    def lock_workspace(self, workspace_id: UUID) -> None: ...

    def workspace_exists(self, workspace_id: UUID) -> bool: ...

    def next_policy_version(self, workspace_id: UUID) -> int: ...

    def add_policy(self, policy: RegulatoryPolicyVersion) -> None: ...

    def current_policy(self, workspace_id: UUID) -> RegulatoryPolicyVersion | None: ...

    def get_hold_by_idempotency_key(
        self,
        workspace_id: UUID,
        idempotency_key: str,
    ) -> LegalHold | None: ...

    def add_hold(self, hold: LegalHold) -> None: ...

    def get_hold(self, workspace_id: UUID, legal_hold_id: UUID) -> LegalHold | None: ...

    def get_release_by_idempotency_key(
        self,
        workspace_id: UUID,
        idempotency_key: str,
    ) -> LegalHoldRelease | None: ...

    def get_release(self, workspace_id: UUID, legal_hold_id: UUID) -> LegalHoldRelease | None: ...

    def add_release(self, release: LegalHoldRelease) -> None: ...

    def has_active_destructive_operation(self, workspace_id: UUID) -> bool: ...

    def list_proofs(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[LifecycleComplianceProof, ...]: ...


class LifecycleComplianceUnitOfWork(Protocol):
    """保证法规治理事实、审计与 Outbox 在同一事务提交。"""

    @property
    def compliance(self) -> LifecycleComplianceRepository: ...

    @property
    def audit(self) -> AuditWriter: ...

    @property
    def outbox(self) -> OutboxWriter: ...

    def __enter__(self) -> LifecycleComplianceUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class LifecycleComplianceWriteConflictError(Exception):
    """表示法规策略、法律保留或解除发生并发写入冲突。"""


class LifecycleOperationBlockedError(Exception):
    """携带已提交的失败关闭证明，阻止破坏性生命周期操作继续。"""

    def __init__(self, proof: LifecycleComplianceProof) -> None:
        super().__init__(proof.decision)
        self.proof = proof
