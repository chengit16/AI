"""实现法规策略、法律保留、解除和证明的 PostgreSQL Unit of Work。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from types import TracebackType
from typing import cast
from uuid import UUID, uuid4

from ai_platform_backend.integration.sqlalchemy import (
    SqlAlchemyAuditWriter,
    SqlAlchemyOutboxWriter,
)
from sqlalchemy import func, insert, select, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ai_platform_api.modules.lifecycle.domain.compliance import (
    ExternalReviewStatus,
    JurisdictionStatus,
    LegalHold,
    LegalHoldRelease,
    LifecycleComplianceDecision,
    LifecycleComplianceOperation,
    LifecycleComplianceProof,
    LifecycleComplianceWriteConflictError,
    RegulatoryPolicyConfiguration,
    RegulatoryPolicyVersion,
)
from ai_platform_api.persistence.tables import (
    lifecycle_compliance_proofs,
    lifecycle_legal_hold_releases,
    lifecycle_legal_holds,
    lifecycle_purge_requests,
    lifecycle_regulatory_policy_versions,
    lifecycle_retention_runs,
    workspaces,
)

SessionFactory = sessionmaker[Session]


class UnconfiguredRegulatoryPolicySource:
    """在没有受审核法域配置时显式返回未配置状态，禁止生产默认值放行。"""

    def resolve(self, workspace_id: UUID) -> RegulatoryPolicyConfiguration:
        del workspace_id
        return RegulatoryPolicyConfiguration(
            jurisdiction_status="not_configured",
            jurisdiction_codes=(),
            retention_period_days={},
            external_review_status="not_configured",
            external_review_digest=None,
        )


class SqlAlchemyLifecycleComplianceRepository:
    """在调用方事务内维护 Lifecycle 模块的法规治理事实。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_workspace(self, workspace_id: UUID) -> None:
        """与删除、保留和法律保留使用同一事务锁，消除检查后使用竞态。"""

        self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:workspace_key, 509))"),
            {"workspace_key": str(workspace_id)},
        )

    def workspace_exists(self, workspace_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(workspaces.c.workspace_id).where(workspaces.c.workspace_id == workspace_id)
            )
            is not None
        )

    def next_policy_version(self, workspace_id: UUID) -> int:
        current = self._session.scalar(
            select(func.max(lifecycle_regulatory_policy_versions.c.policy_version)).where(
                lifecycle_regulatory_policy_versions.c.workspace_id == workspace_id
            )
        )
        return int(current or 0) + 1

    def add_policy(self, policy: RegulatoryPolicyVersion) -> None:
        try:
            self._session.execute(
                insert(lifecycle_regulatory_policy_versions).values(**policy.__dict__)
            )
        except IntegrityError as error:
            raise LifecycleComplianceWriteConflictError from error

    def current_policy(self, workspace_id: UUID) -> RegulatoryPolicyVersion | None:
        row = (
            self._session.execute(
                select(lifecycle_regulatory_policy_versions)
                .where(lifecycle_regulatory_policy_versions.c.workspace_id == workspace_id)
                .order_by(lifecycle_regulatory_policy_versions.c.policy_version.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        return _policy(row) if row is not None else None

    def get_hold_by_idempotency_key(
        self,
        workspace_id: UUID,
        idempotency_key: str,
    ) -> LegalHold | None:
        row = (
            self._session.execute(
                select(lifecycle_legal_holds).where(
                    lifecycle_legal_holds.c.workspace_id == workspace_id,
                    lifecycle_legal_holds.c.idempotency_key == idempotency_key,
                )
            )
            .mappings()
            .one_or_none()
        )
        return _hold(row) if row is not None else None

    def add_hold(self, hold: LegalHold) -> None:
        try:
            self._session.execute(insert(lifecycle_legal_holds).values(**hold.__dict__))
        except IntegrityError as error:
            raise LifecycleComplianceWriteConflictError from error

    def get_hold(self, workspace_id: UUID, legal_hold_id: UUID) -> LegalHold | None:
        row = (
            self._session.execute(
                select(lifecycle_legal_holds).where(
                    lifecycle_legal_holds.c.workspace_id == workspace_id,
                    lifecycle_legal_holds.c.legal_hold_id == legal_hold_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        return _hold(row) if row is not None else None

    def get_release_by_idempotency_key(
        self,
        workspace_id: UUID,
        idempotency_key: str,
    ) -> LegalHoldRelease | None:
        row = (
            self._session.execute(
                select(lifecycle_legal_hold_releases).where(
                    lifecycle_legal_hold_releases.c.workspace_id == workspace_id,
                    lifecycle_legal_hold_releases.c.idempotency_key == idempotency_key,
                )
            )
            .mappings()
            .one_or_none()
        )
        return _release(row) if row is not None else None

    def get_release(self, workspace_id: UUID, legal_hold_id: UUID) -> LegalHoldRelease | None:
        row = (
            self._session.execute(
                select(lifecycle_legal_hold_releases).where(
                    lifecycle_legal_hold_releases.c.workspace_id == workspace_id,
                    lifecycle_legal_hold_releases.c.legal_hold_id == legal_hold_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        return _release(row) if row is not None else None

    def add_release(self, release: LegalHoldRelease) -> None:
        try:
            self._session.execute(insert(lifecycle_legal_hold_releases).values(**release.__dict__))
        except IntegrityError as error:
            raise LifecycleComplianceWriteConflictError from error

    def has_active_destructive_operation(self, workspace_id: UUID) -> bool:
        purge = self._session.scalar(
            select(func.count())
            .select_from(lifecycle_purge_requests)
            .where(
                lifecycle_purge_requests.c.workspace_id == workspace_id,
                lifecycle_purge_requests.c.status.in_(("pending", "retryable")),
            )
        )
        retention = self._session.scalar(
            select(func.count())
            .select_from(lifecycle_retention_runs)
            .where(
                lifecycle_retention_runs.c.workspace_id == workspace_id,
                lifecycle_retention_runs.c.status == "running",
            )
        )
        return int(purge or 0) > 0 or int(retention or 0) > 0

    def list_proofs(
        self,
        workspace_id: UUID,
        *,
        limit: int,
    ) -> tuple[LifecycleComplianceProof, ...]:
        rows = (
            self._session.execute(
                select(lifecycle_compliance_proofs)
                .where(lifecycle_compliance_proofs.c.workspace_id == workspace_id)
                .order_by(
                    lifecycle_compliance_proofs.c.created_at.desc(),
                    lifecycle_compliance_proofs.c.compliance_proof_id,
                )
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return tuple(_proof(row) for row in rows)


def record_lifecycle_compliance_decision(
    session: Session,
    *,
    workspace_id: UUID,
    operation: LifecycleComplianceOperation,
    operation_id: UUID,
    idempotency_key: str,
    request_hash: str,
    actor_id: UUID,
    occurred_at: datetime,
) -> LifecycleComplianceProof:
    """在工作空间事务锁内追加可复用裁决，阻断结果不能被后续配置重新解释。"""

    # 1. 串行化同空间裁决并优先复用原证明，旧的阻断结果不会被新配置改写。
    repository = SqlAlchemyLifecycleComplianceRepository(session)
    repository.lock_workspace(workspace_id)
    request_key_digest = _hash_document(
        {
            "workspace_id": str(workspace_id),
            "operation": operation,
            "idempotency_key": idempotency_key,
        }
    )
    existing_row = (
        session.execute(
            select(lifecycle_compliance_proofs).where(
                lifecycle_compliance_proofs.c.workspace_id == workspace_id,
                lifecycle_compliance_proofs.c.operation == operation,
                lifecycle_compliance_proofs.c.request_key_digest == request_key_digest,
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing_row is not None:
        existing = _proof(existing_row)
        if existing.request_hash != request_hash:
            raise LifecycleComplianceWriteConflictError
        return existing

    # 2. 从当前不可变策略和活动保留集合计算裁决，只保存低敏摘要与原因码。
    policy = repository.current_policy(workspace_id)
    hold_ids = tuple(
        session.scalars(
            select(lifecycle_legal_holds.c.legal_hold_id)
            .outerjoin(
                lifecycle_legal_hold_releases,
                (
                    lifecycle_legal_hold_releases.c.workspace_id
                    == lifecycle_legal_holds.c.workspace_id
                )
                & (
                    lifecycle_legal_hold_releases.c.legal_hold_id
                    == lifecycle_legal_holds.c.legal_hold_id
                ),
            )
            .where(
                lifecycle_legal_holds.c.workspace_id == workspace_id,
                lifecycle_legal_hold_releases.c.release_id.is_(None),
            )
            .order_by(lifecycle_legal_holds.c.legal_hold_id)
        )
    )
    decision, reason_codes = _operation_decision(operation, policy, hold_ids)
    external_review_status = policy.external_review_status if policy else "not_configured"
    hold_set_digest = _hash_document([str(hold_id) for hold_id in hold_ids])
    proof_document = {
        "workspace_id": str(workspace_id),
        "operation": operation,
        "operation_id": str(operation_id),
        "request_key_digest": request_key_digest,
        "request_hash": request_hash,
        "decision": decision,
        "reason_codes": list(reason_codes),
        "regulatory_policy_id": str(policy.regulatory_policy_id) if policy else None,
        "policy_digest": policy.policy_digest if policy else None,
        "external_review_status": external_review_status,
        "active_hold_count": len(hold_ids),
        "hold_set_digest": hold_set_digest,
        "created_by_actor_id": str(actor_id),
        "created_at": occurred_at.isoformat(),
    }
    # 3. 证明与后续生命周期操作共享调用方事务，数据库外键和 Trigger 防止脱钩。
    proof = LifecycleComplianceProof(
        compliance_proof_id=uuid4(),
        workspace_id=workspace_id,
        operation=operation,
        operation_id=operation_id,
        request_key_digest=request_key_digest,
        request_hash=request_hash,
        decision=decision,
        reason_codes=reason_codes,
        regulatory_policy_id=policy.regulatory_policy_id if policy else None,
        policy_digest=policy.policy_digest if policy else None,
        external_review_status=external_review_status,
        active_hold_count=len(hold_ids),
        hold_set_digest=hold_set_digest,
        proof_digest=_hash_document(proof_document),
        created_by_actor_id=actor_id,
        created_at=occurred_at,
    )
    try:
        session.execute(insert(lifecycle_compliance_proofs).values(**proof.__dict__))
    except IntegrityError as error:
        raise LifecycleComplianceWriteConflictError from error
    return proof


class SqlAlchemyLifecycleComplianceUnitOfWork:
    """为法规治理提供短事务以及审计、Outbox 写入器。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._compliance: SqlAlchemyLifecycleComplianceRepository | None = None
        self._audit: SqlAlchemyAuditWriter | None = None
        self._outbox: SqlAlchemyOutboxWriter | None = None

    @property
    def compliance(self) -> SqlAlchemyLifecycleComplianceRepository:
        if self._compliance is None:
            raise RuntimeError("Lifecycle Compliance Unit of Work 尚未进入事务范围")
        return self._compliance

    @property
    def audit(self) -> SqlAlchemyAuditWriter:
        if self._audit is None:
            raise RuntimeError("Lifecycle Compliance Unit of Work 尚未进入事务范围")
        return self._audit

    @property
    def outbox(self) -> SqlAlchemyOutboxWriter:
        if self._outbox is None:
            raise RuntimeError("Lifecycle Compliance Unit of Work 尚未进入事务范围")
        return self._outbox

    def __enter__(self) -> SqlAlchemyLifecycleComplianceUnitOfWork:
        if self._session is not None:
            raise RuntimeError("Lifecycle Compliance Unit of Work 不允许嵌套事务")
        self._session = self._session_factory()
        self._compliance = SqlAlchemyLifecycleComplianceRepository(self._session)
        self._audit = SqlAlchemyAuditWriter(self._session)
        self._outbox = SqlAlchemyOutboxWriter(self._session)
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
        self._session = None
        self._compliance = None
        self._audit = None
        self._outbox = None

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Lifecycle Compliance Unit of Work 尚未进入事务范围")
        self._session.commit()


def _policy(row: RowMapping) -> RegulatoryPolicyVersion:
    return RegulatoryPolicyVersion(
        regulatory_policy_id=cast(UUID, row["regulatory_policy_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        policy_version=cast(int, row["policy_version"]),
        jurisdiction_status=cast(JurisdictionStatus, row["jurisdiction_status"]),
        jurisdiction_codes=tuple(cast(list[str], row["jurisdiction_codes"])),
        retention_period_days=dict(cast(dict[str, int], row["retention_period_days"])),
        external_review_status=cast(ExternalReviewStatus, row["external_review_status"]),
        external_review_digest=cast(str | None, row["external_review_digest"]),
        policy_digest=cast(str, row["policy_digest"]),
        created_by_actor_id=cast(UUID, row["created_by_actor_id"]),
        created_at=cast(datetime, row["created_at"]),
    )


def _hold(row: RowMapping) -> LegalHold:
    return LegalHold(
        legal_hold_id=cast(UUID, row["legal_hold_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        regulatory_policy_id=cast(UUID, row["regulatory_policy_id"]),
        idempotency_key=cast(str, row["idempotency_key"]),
        request_hash=cast(str, row["request_hash"]),
        scope_type="workspace",
        scope_digest=cast(str, row["scope_digest"]),
        case_reference_digest=cast(str, row["case_reference_digest"]),
        reason_code=cast(str, row["reason_code"]),
        activated_by_actor_id=cast(UUID, row["activated_by_actor_id"]),
        activated_at=cast(datetime, row["activated_at"]),
    )


def _release(row: RowMapping) -> LegalHoldRelease:
    return LegalHoldRelease(
        release_id=cast(UUID, row["release_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        legal_hold_id=cast(UUID, row["legal_hold_id"]),
        idempotency_key=cast(str, row["idempotency_key"]),
        request_hash=cast(str, row["request_hash"]),
        reason_code=cast(str, row["reason_code"]),
        release_evidence_digest=cast(str, row["release_evidence_digest"]),
        released_by_actor_id=cast(UUID, row["released_by_actor_id"]),
        released_at=cast(datetime, row["released_at"]),
    )


def _proof(row: RowMapping) -> LifecycleComplianceProof:
    return LifecycleComplianceProof(
        compliance_proof_id=cast(UUID, row["compliance_proof_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        operation=cast(LifecycleComplianceOperation, row["operation"]),
        operation_id=cast(UUID, row["operation_id"]),
        request_key_digest=cast(str, row["request_key_digest"]),
        request_hash=cast(str, row["request_hash"]),
        decision=cast(LifecycleComplianceDecision, row["decision"]),
        reason_codes=tuple(cast(list[str], row["reason_codes"])),
        regulatory_policy_id=cast(UUID | None, row["regulatory_policy_id"]),
        policy_digest=cast(str | None, row["policy_digest"]),
        external_review_status=cast(ExternalReviewStatus, row["external_review_status"]),
        active_hold_count=cast(int, row["active_hold_count"]),
        hold_set_digest=cast(str, row["hold_set_digest"]),
        proof_digest=cast(str, row["proof_digest"]),
        created_by_actor_id=cast(UUID, row["created_by_actor_id"]),
        created_at=cast(datetime, row["created_at"]),
    )


def _operation_decision(
    operation: LifecycleComplianceOperation,
    policy: RegulatoryPolicyVersion | None,
    hold_ids: tuple[UUID, ...],
) -> tuple[LifecycleComplianceDecision, tuple[str, ...]]:
    """导出保持原授权语义，破坏性操作对未知法域、拒绝审核和活动保留失败关闭。"""

    if policy is None or policy.jurisdiction_status == "not_configured":
        if operation == "export":
            return "allowed", ("authorized_export_preserved", "jurisdiction_not_configured")
        return "not_configured", ("jurisdiction_not_configured",)
    reasons = ["regulatory_policy_configured"]
    if policy.external_review_status == "not_configured":
        reasons.append("external_review_not_configured")
    elif policy.external_review_status == "rejected":
        reasons.append("external_review_rejected")
        if operation != "export":
            return "blocked", tuple(reasons)
    if operation == "export":
        return "allowed", ("authorized_export_preserved", *reasons)
    if hold_ids:
        reasons.append("active_legal_hold")
        return "blocked", tuple(reasons)
    return "allowed", tuple(reasons)


def _hash_document(document: object) -> str:
    content = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(content).hexdigest()
