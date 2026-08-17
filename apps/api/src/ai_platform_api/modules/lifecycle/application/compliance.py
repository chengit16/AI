"""编排版本化法规策略、法律保留、解除和低敏证明读取。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditRecord, IntegrationEvent

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.lifecycle.domain.compliance import (
    COMPLIANCE_REASON_CODES,
    RETENTION_CLASSES,
    LegalHold,
    LegalHoldRelease,
    LifecycleComplianceProof,
    LifecycleComplianceUnitOfWork,
    LifecycleComplianceWriteConflictError,
    RegulatoryPolicyConfiguration,
    RegulatoryPolicySource,
    RegulatoryPolicyVersion,
)

POLICY_MANAGE_PERMISSION = "workspace.lifecycle.regulatory_policy.manage"
HOLD_CREATE_PERMISSION = "workspace.lifecycle.legal_hold.create"
HOLD_RELEASE_PERMISSION = "workspace.lifecycle.legal_hold.release"
PROOF_READ_PERMISSION = "workspace.lifecycle.compliance_proof.read"
IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
REASON_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
JURISDICTION_PATTERN = re.compile(r"^[A-Z][A-Z0-9._-]{1,31}$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")

__all__ = [
    "LegalHold",
    "LegalHoldRelease",
    "LifecycleComplianceProof",
    "RegulatoryComplianceService",
    "RegulatoryPolicyVersion",
]


class LifecycleComplianceDeniedError(PlatformError):
    """当前主体没有目标工作空间的独立法规治理权限。"""

    error_code = "POLICY_DENIED"


class LifecycleComplianceNotFoundError(PlatformError):
    """目标工作空间或法律保留事实不存在。"""

    error_code = "RESOURCE_NOT_FOUND"


class LifecycleComplianceValidationError(PlatformError):
    """法规配置、低敏摘要或结构化原因不满足冻结契约。"""

    error_code = "VALIDATION_ERROR"


class LifecycleComplianceConflictError(PlatformError):
    """并发操作、幂等键或法律保留状态发生冲突。"""

    error_code = "STATE_CONFLICT"


class RegulatoryComplianceService:
    """只从受信配置源发布策略，并以独立权限治理保留和解除。"""

    def __init__(
        self,
        unit_of_work: LifecycleComplianceUnitOfWork,
        policy_source: RegulatoryPolicySource,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy_source = policy_source

    def publish_policy(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
    ) -> RegulatoryPolicyVersion:
        """从受信源读取配置并追加新版本，浏览器不能提交审核结论。"""

        # 1. 只接受权限投影与受信配置源，调用方不能直接声明法域或审核结果。
        _require(context, workspace_id, POLICY_MANAGE_PERMISSION)
        configuration = self._policy_source.resolve(workspace_id)
        _validate_configuration(configuration)
        now = datetime.now(UTC)
        # 2. 工作空间锁内冻结版本，并让策略、审计和事件保持同一事务。
        with self._unit_of_work as unit_of_work:
            unit_of_work.compliance.lock_workspace(workspace_id)
            if not unit_of_work.compliance.workspace_exists(workspace_id):
                raise LifecycleComplianceNotFoundError
            if unit_of_work.compliance.has_active_destructive_operation(workspace_id):
                raise LifecycleComplianceConflictError
            policy = _policy(
                context,
                configuration,
                policy_version=unit_of_work.compliance.next_policy_version(workspace_id),
                occurred_at=now,
            )
            try:
                unit_of_work.compliance.add_policy(policy)
            except LifecycleComplianceWriteConflictError as error:
                raise LifecycleComplianceConflictError from error
            _add_facts(
                unit_of_work,
                context,
                aggregate_id=policy.regulatory_policy_id,
                event_type="workspace.lifecycle.regulatory_policy_published",
                action="workspace.lifecycle.regulatory_policy.publish",
                occurred_at=now,
                attributes={
                    "policy_version": policy.policy_version,
                    "jurisdiction_status": policy.jurisdiction_status,
                    "external_review_status": policy.external_review_status,
                    "policy_digest": policy.policy_digest,
                },
            )
            unit_of_work.commit()
            return policy

    def activate_legal_hold(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        idempotency_key: str,
        case_reference_digest: str,
        reason_code: str,
    ) -> LegalHold:
        """激活工作空间级保留，不读取业务数据也不改变既有读取授权。"""

        # 1. 先验证独立权限与摘要输入，保留请求不会携带或读取案件正文。
        _require(context, workspace_id, HOLD_CREATE_PERMISSION)
        _require_browser(context)
        _validate_operation_input(idempotency_key, case_reference_digest, reason_code)
        request_hash = _hash_document(
            {
                "workspace_id": str(workspace_id),
                "case_reference_digest": case_reference_digest,
                "reason_code": reason_code,
                "scope_type": "workspace",
            }
        )
        now = datetime.now(UTC)
        # 2. 锁内复用幂等事实，并在法规策略有效且无破坏性操作时追加保留。
        with self._unit_of_work as unit_of_work:
            unit_of_work.compliance.lock_workspace(workspace_id)
            previous = unit_of_work.compliance.get_hold_by_idempotency_key(
                workspace_id,
                idempotency_key,
            )
            if previous is not None:
                if previous.request_hash != request_hash:
                    raise LifecycleComplianceConflictError
                return previous
            policy = unit_of_work.compliance.current_policy(workspace_id)
            if policy is None or policy.jurisdiction_status != "configured":
                raise LifecycleComplianceConflictError
            if unit_of_work.compliance.has_active_destructive_operation(workspace_id):
                raise LifecycleComplianceConflictError
            hold = LegalHold(
                legal_hold_id=uuid4(),
                workspace_id=workspace_id,
                regulatory_policy_id=policy.regulatory_policy_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                scope_type="workspace",
                scope_digest=_hash_document(
                    {"workspace_id": str(workspace_id), "scope_type": "workspace"}
                ),
                case_reference_digest=case_reference_digest,
                reason_code=reason_code,
                activated_by_actor_id=context.actor_id,
                activated_at=now,
            )
            try:
                unit_of_work.compliance.add_hold(hold)
            except LifecycleComplianceWriteConflictError as error:
                raise LifecycleComplianceConflictError from error
            _add_facts(
                unit_of_work,
                context,
                aggregate_id=hold.legal_hold_id,
                event_type="workspace.lifecycle.legal_hold_activated",
                action="workspace.lifecycle.legal_hold.activate",
                occurred_at=now,
                attributes={
                    "regulatory_policy_id": str(policy.regulatory_policy_id),
                    "scope_digest": hold.scope_digest,
                    "reason_code": reason_code,
                },
            )
            unit_of_work.commit()
            return hold

    def release_legal_hold(
        self,
        context: RequestContext,
        legal_hold_id: UUID,
        *,
        workspace_id: UUID,
        idempotency_key: str,
        release_evidence_digest: str,
        reason_code: str,
    ) -> LegalHoldRelease:
        """以独立权限和只追加事实解除本空间法律保留。"""

        # 1. 解除必须使用独立权限和摘要证据，禁止以保留创建权限隐式替代。
        _require(context, workspace_id, HOLD_RELEASE_PERMISSION)
        _require_browser(context)
        _validate_operation_input(idempotency_key, release_evidence_digest, reason_code)
        request_hash = _hash_document(
            {
                "workspace_id": str(workspace_id),
                "legal_hold_id": str(legal_hold_id),
                "release_evidence_digest": release_evidence_digest,
                "reason_code": reason_code,
            }
        )
        now = datetime.now(UTC)
        # 2. 锁内校验保留归属与幂等身份，再原子追加解除、审计和事件事实。
        with self._unit_of_work as unit_of_work:
            unit_of_work.compliance.lock_workspace(workspace_id)
            previous = unit_of_work.compliance.get_release_by_idempotency_key(
                workspace_id,
                idempotency_key,
            )
            if previous is not None:
                if previous.request_hash != request_hash:
                    raise LifecycleComplianceConflictError
                return previous
            hold = unit_of_work.compliance.get_hold(workspace_id, legal_hold_id)
            if hold is None:
                raise LifecycleComplianceNotFoundError
            if unit_of_work.compliance.get_release(workspace_id, legal_hold_id) is not None:
                raise LifecycleComplianceConflictError
            release = LegalHoldRelease(
                release_id=uuid4(),
                workspace_id=workspace_id,
                legal_hold_id=legal_hold_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                reason_code=reason_code,
                release_evidence_digest=release_evidence_digest,
                released_by_actor_id=context.actor_id,
                released_at=now,
            )
            try:
                unit_of_work.compliance.add_release(release)
            except LifecycleComplianceWriteConflictError as error:
                raise LifecycleComplianceConflictError from error
            _add_facts(
                unit_of_work,
                context,
                aggregate_id=legal_hold_id,
                event_type="workspace.lifecycle.legal_hold_released",
                action="workspace.lifecycle.legal_hold.release",
                occurred_at=now,
                aggregate_version=2,
                attributes={
                    "release_id": str(release.release_id),
                    "release_evidence_digest": release_evidence_digest,
                    "reason_code": reason_code,
                },
            )
            unit_of_work.commit()
            return release

    def list_compliance_proofs(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        limit: int = 100,
    ) -> tuple[LifecycleComplianceProof, ...]:
        """按原工作空间权限返回低敏证明，不查询任何业务正文。"""

        _require(context, workspace_id, PROOF_READ_PERMISSION)
        if not 1 <= limit <= 500:
            raise LifecycleComplianceValidationError
        with self._unit_of_work as unit_of_work:
            return unit_of_work.compliance.list_proofs(workspace_id, limit=limit)


def _validate_configuration(configuration: RegulatoryPolicyConfiguration) -> None:
    codes = configuration.jurisdiction_codes
    periods = configuration.retention_period_days
    if len(codes) != len(set(codes)) or any(
        JURISDICTION_PATTERN.fullmatch(code) is None for code in codes
    ):
        raise LifecycleComplianceValidationError
    if configuration.jurisdiction_status == "not_configured":
        if codes or periods or configuration.external_review_status != "not_configured":
            raise LifecycleComplianceValidationError
    elif set(periods) != set(RETENTION_CLASSES) or any(
        not 1 <= value <= 3650 for value in periods.values()
    ):
        raise LifecycleComplianceValidationError
    review_digest = configuration.external_review_digest
    if configuration.external_review_status == "not_configured":
        if review_digest is not None:
            raise LifecycleComplianceValidationError
    elif review_digest is None or DIGEST_PATTERN.fullmatch(review_digest) is None:
        raise LifecycleComplianceValidationError


def _validate_operation_input(idempotency_key: str, digest: str, reason_code: str) -> None:
    if (
        IDEMPOTENCY_PATTERN.fullmatch(idempotency_key) is None
        or DIGEST_PATTERN.fullmatch(digest) is None
        or REASON_PATTERN.fullmatch(reason_code) is None
    ):
        raise LifecycleComplianceValidationError


def _policy(
    context: RequestContext,
    configuration: RegulatoryPolicyConfiguration,
    *,
    policy_version: int,
    occurred_at: datetime,
) -> RegulatoryPolicyVersion:
    jurisdiction_codes = tuple(sorted(configuration.jurisdiction_codes))
    retention_period_days = dict(sorted(configuration.retention_period_days.items()))
    document = {
        "workspace_id": str(context.workspace_id),
        "policy_version": policy_version,
        "jurisdiction_status": configuration.jurisdiction_status,
        "jurisdiction_codes": jurisdiction_codes,
        "retention_period_days": retention_period_days,
        "external_review_status": configuration.external_review_status,
        "external_review_digest": configuration.external_review_digest,
    }
    return RegulatoryPolicyVersion(
        regulatory_policy_id=uuid4(),
        workspace_id=context.workspace_id,
        policy_version=policy_version,
        jurisdiction_status=configuration.jurisdiction_status,
        jurisdiction_codes=jurisdiction_codes,
        retention_period_days=retention_period_days,
        external_review_status=configuration.external_review_status,
        external_review_digest=configuration.external_review_digest,
        policy_digest=_hash_document(document),
        created_by_actor_id=context.actor_id,
        created_at=occurred_at,
    )


def _add_facts(
    unit_of_work: LifecycleComplianceUnitOfWork,
    context: RequestContext,
    *,
    aggregate_id: UUID,
    event_type: str,
    action: str,
    occurred_at: datetime,
    attributes: dict[str, object],
    aggregate_version: int = 1,
) -> None:
    """只把标识、摘要和低基数原因写入审计与事件。"""

    unit_of_work.audit.add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            action=action,
            resource_type="workspace_lifecycle_compliance",
            resource_id=aggregate_id,
            outcome="succeeded",
            occurred_at=occurred_at,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            attributes=attributes,
        )
    )
    unit_of_work.outbox.add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type=event_type,
            workspace_id=context.workspace_id,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            occurred_at=occurred_at,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            actor_id=context.actor_id,
            user_id=context.user_id,
            request_id=context.request_id,
            payload=attributes,
        )
    )


def _require(context: RequestContext, workspace_id: UUID, permission_code: str) -> None:
    if (
        context.workspace_id != workspace_id
        or context.authorized_permission_code != permission_code
        or not context.authorized_workspace
    ):
        raise LifecycleComplianceDeniedError


def _require_browser(context: RequestContext) -> None:
    if context.authentication_method != "browser_session" or context.user_id is None:
        raise LifecycleComplianceDeniedError


def _hash_document(document: object) -> str:
    content = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


assert COMPLIANCE_REASON_CODES
