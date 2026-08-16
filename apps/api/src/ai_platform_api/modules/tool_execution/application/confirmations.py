"""编排副作用工具的个人确认、企业审批和执行前重新授权。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.tool_execution.application.definitions import verify_tool_definition
from ai_platform_api.modules.tool_execution.application.errors import (
    ToolConfirmationRequiredError,
    ToolConfirmationStaleError,
    ToolDefinitionInvalidError,
    ToolExecutionDeniedError,
    ToolRunConflictError,
    ToolVersionNotAvailableError,
)
from ai_platform_api.modules.tool_execution.application.planning import (
    ToolPlanningCatalog,
    build_tool_policy_decision_record,
)
from ai_platform_api.modules.tool_execution.domain.catalog import ToolDefinition
from ai_platform_api.modules.tool_execution.domain.confirmations import (
    ToolCallBinding,
    ToolConfirmation,
    ToolConfirmationInvalidationReason,
    ToolConfirmationRequestBasis,
    ToolConfirmationResumeResult,
    ToolConfirmationStore,
    confirmation_binding_hash,
)
from ai_platform_api.modules.tool_execution.domain.planning import ToolPolicyDecisionRecord
from ai_platform_api.modules.workflow.application.approval_runtime import (
    ApprovalCommandResult,
    ApprovalInstanceConflict,
)
from ai_platform_api.modules.workflow.domain.approvals import ApprovalRiskLevel, ApprovalSubject

_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class ToolApprovalRuntime(Protocol):
    """只暴露创建工具审批实例所需的通用审批入口。"""

    def start(
        self,
        context: RequestContext,
        *,
        subject: ApprovalSubject,
        idempotency_key: str,
    ) -> ApprovalCommandResult: ...


@dataclass(frozen=True)
class ToolConfirmationRequestResult:
    """返回工具确认事实、通用审批聚合和是否发生幂等回放。"""

    confirmation: ToolConfirmation
    approval: ApprovalCommandResult
    replayed: bool


class ToolConfirmationService:
    """保证写工具只有在摘要绑定审批和当前 PDP 同时有效时进入 ready。"""

    def __init__(
        self,
        store: ToolConfirmationStore,
        catalog: ToolPlanningCatalog,
        approvals: ToolApprovalRuntime,
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._approvals = approvals

    def request(
        self,
        context: RequestContext,
        *,
        step_id: UUID,
        idempotency_key: str,
        requested_at: datetime | None = None,
    ) -> ToolConfirmationRequestResult:
        """为当前写步骤创建个人所有者确认或企业多级审批。"""

        account_id = _browser_account(context)
        if _IDEMPOTENCY_PATTERN.fullmatch(idempotency_key) is None:
            raise ToolRunConflictError

        # 1. 先回放已提交的请求，避免重新执行 PDP 后生成不同主题摘要破坏幂等语义。
        existing = self._store.get_by_request_idempotency(
            context.workspace_id,
            account_id,
            idempotency_key,
        )
        if existing is not None:
            _require_requested_step(existing, step_id)
            approval = self._approvals.start(
                context,
                subject=_subject_from_confirmation(existing, account_id),
                idempotency_key=idempotency_key,
            )
            return ToolConfirmationRequestResult(existing, approval, True)

        basis = self._require_basis(context, step_id, account_id)
        now = requested_at or datetime.now(UTC)
        if now >= basis.run.deadline_at:
            raise ToolConfirmationStaleError

        # 2. 确认申请也执行一次当前套餐、定义和 PDP 校验，不能复用计划阶段的旧允许结论。
        definition, decision = self._catalog.authorize_available_tool(
            context,
            workspace_id=context.workspace_id,
            tool_id=basis.step.tool_id,
            tool_version=basis.step.tool_version,
        )
        _require_synthetic_write(definition)
        policy = build_tool_policy_decision_record(
            decision,
            workspace_id=context.workspace_id,
            run_id=basis.run.run_id,
            step_id=basis.step.step_id,
            definition=definition,
            arguments_hash=basis.step.canonical_arguments_hash,
            evaluated_at=now,
        )
        subject = _approval_subject(
            account_id=account_id,
            basis=basis,
            definition=definition,
            policy=policy,
        )

        # 3. 审批实例、确认绑定、PDP 证据和 Run/Step 等待状态由生命周期在同一事务提交。
        try:
            approval = self._approvals.start(
                context,
                subject=subject,
                idempotency_key=idempotency_key,
            )
        except ApprovalInstanceConflict:
            raced = self._store.get_by_request_idempotency(
                context.workspace_id,
                account_id,
                idempotency_key,
            )
            if raced is None:
                raise
            _require_requested_step(raced, step_id)
            approval = self._approvals.start(
                context,
                subject=_subject_from_confirmation(raced, account_id),
                idempotency_key=idempotency_key,
            )
            return ToolConfirmationRequestResult(raced, approval, True)
        confirmation = self._store.get_by_approval_instance(
            context.workspace_id,
            approval.state.instance.approval_instance_id,
        )
        if confirmation is None:
            raise ToolRunConflictError
        return ToolConfirmationRequestResult(confirmation, approval, approval.replayed)

    def resume(
        self,
        context: RequestContext,
        *,
        confirmation_id: UUID,
        expected_binding: ToolCallBinding,
        resumed_at: datetime | None = None,
    ) -> ToolConfirmationResumeResult:
        """审批通过后重新执行当前 PDP，并原子发布新的 ready 证据。"""

        # 1. 只允许原申请账号恢复完整绑定且仍在有效期内的批准确认。
        account_id = _browser_account(context)
        confirmation = self._require_confirmation(context, confirmation_id, account_id)
        now = resumed_at or datetime.now(UTC)
        if confirmation.state != "approved":
            _raise_unusable_confirmation(confirmation)
        if expected_binding != confirmation.binding:
            self._store.invalidate(
                context.workspace_id,
                confirmation_id,
                state="withdrawn",
                reason=_binding_invalidation_reason(confirmation.binding, expected_binding),
                occurred_at=now,
            )
            raise ToolConfirmationStaleError
        if now >= confirmation.expires_at:
            self._store.invalidate(
                context.workspace_id,
                confirmation_id,
                state="expired",
                reason="expired",
                occurred_at=now,
            )
            raise ToolConfirmationStaleError

        # 2. 重新读取当前定义、套餐和权限；撤销后历史批准只保留审计价值。
        try:
            definition, decision = self._catalog.authorize_available_tool(
                context,
                workspace_id=context.workspace_id,
                tool_id=confirmation.binding.tool_id,
                tool_version=confirmation.binding.tool_version,
            )
            _require_synthetic_write(definition)
        except (ToolExecutionDeniedError, ToolVersionNotAvailableError, ToolDefinitionInvalidError):
            self._store.invalidate(
                context.workspace_id,
                confirmation_id,
                state="withdrawn",
                reason="permission_revoked",
                occurred_at=now,
            )
            raise ToolConfirmationStaleError from None
        if decision.policy_version != confirmation.policy_version:
            self._store.invalidate(
                context.workspace_id,
                confirmation_id,
                state="withdrawn",
                reason="policy_changed",
                occurred_at=now,
            )
            raise ToolConfirmationStaleError
        # 3. 当前策略版本仍一致时追加新 PDP 证据，由存储事务原子恢复 Step 与 Run。
        policy = build_tool_policy_decision_record(
            decision,
            workspace_id=context.workspace_id,
            run_id=confirmation.binding.run_id,
            step_id=confirmation.binding.step_id,
            definition=definition,
            arguments_hash=confirmation.binding.canonical_arguments_hash,
            evaluated_at=now,
        )
        if (
            policy.resource_scope_hash != confirmation.resource_scope_hash
            or policy.field_mask_hash != confirmation.field_mask_hash
        ):
            self._store.invalidate(
                context.workspace_id,
                confirmation_id,
                state="withdrawn",
                reason="permission_revoked",
                occurred_at=now,
            )
            raise ToolConfirmationStaleError
        return self._store.resume(
            context.workspace_id,
            confirmation_id,
            expected_binding=expected_binding,
            policy=policy,
            resumed_at=now,
        )

    def expire(
        self,
        context: RequestContext,
        *,
        confirmation_id: UUID,
        occurred_at: datetime | None = None,
    ) -> ToolConfirmation:
        """把超过冻结有效期的确认关闭，审批历史仍可独立收敛。"""

        account_id = _browser_account(context)
        self._require_confirmation(context, confirmation_id, account_id)
        return self._store.expire(
            context.workspace_id,
            confirmation_id,
            occurred_at=occurred_at or datetime.now(UTC),
        )

    def _require_basis(
        self,
        context: RequestContext,
        step_id: UUID,
        account_id: UUID,
    ) -> ToolConfirmationRequestBasis:
        basis = self._store.get_request_basis(context.workspace_id, step_id)
        if (
            basis is None
            or basis.run.requested_by_account_id != account_id
            or basis.run.state != "running"
            or basis.step.state != "policy_checking"
        ):
            raise ToolExecutionDeniedError
        return basis

    def _require_confirmation(
        self,
        context: RequestContext,
        confirmation_id: UUID,
        account_id: UUID,
    ) -> ToolConfirmation:
        confirmation = self._store.get_confirmation(context.workspace_id, confirmation_id)
        basis = (
            self._store.get_request_basis(context.workspace_id, confirmation.binding.step_id)
            if confirmation is not None
            else None
        )
        if confirmation is None or basis is None or basis.run.requested_by_account_id != account_id:
            raise ToolExecutionDeniedError
        return confirmation


def _browser_account(context: RequestContext) -> UUID:
    if (
        context.authentication_method != "browser_session"
        or context.user_id is None
        or context.actor_id != context.user_id
    ):
        raise ToolExecutionDeniedError
    return context.user_id


def _require_synthetic_write(definition: ToolDefinition) -> None:
    verify_tool_definition(definition)
    if (
        definition.access_mode != "write"
        or definition.adapter_kind != "synthetic_internal_write"
        or not definition.synthetic
        or definition.risk_level not in {"high", "critical"}
    ):
        raise ToolExecutionDeniedError


def _approval_subject(
    *,
    account_id: UUID,
    basis: ToolConfirmationRequestBasis,
    definition: ToolDefinition,
    policy: ToolPolicyDecisionRecord,
) -> ApprovalSubject:
    """构造不含参数正文的封闭审批主题，字段只保存身份、版本和摘要。"""

    binding = ToolCallBinding(
        basis.run.workspace_id,
        basis.run.run_id,
        basis.step.step_id,
        basis.step.tool_id,
        basis.step.tool_version,
        basis.step.canonical_arguments_hash,
    )
    confirmation_hash = confirmation_binding_hash(binding, policy.policy_version)
    fields: dict[str, object] = {
        "canonical_arguments_hash": binding.canonical_arguments_hash,
        "confirmation_hash": confirmation_hash,
        "expires_at": basis.run.deadline_at.isoformat(),
        "field_mask_hash": policy.field_mask_hash,
        "permission_code": policy.permission_code,
        "policy_decision_id": str(policy.decision_id),
        "policy_evaluated_at": policy.evaluated_at.isoformat(),
        "policy_version": policy.policy_version,
        "resource_scope_hash": policy.resource_scope_hash,
        "run_id": str(binding.run_id),
        "step_id": str(binding.step_id),
        "tool_id": str(binding.tool_id),
        "tool_version": binding.tool_version,
    }
    return ApprovalSubject(
        workspace_id=binding.workspace_id,
        requester_account_id=account_id,
        resource_type="tool.call",
        operation="execute",
        resource_id=binding.step_id,
        department_ids=(),
        security_level="CONFIDENTIAL",
        risk_level=cast(ApprovalRiskLevel, definition.risk_level),
        fields=fields,
    )


def _subject_from_confirmation(
    confirmation: ToolConfirmation,
    requester_account_id: UUID,
) -> ApprovalSubject:
    """仅用于幂等回放，重建与已提交确认完全相同的审批主题。"""

    fields: dict[str, object] = {
        "canonical_arguments_hash": confirmation.binding.canonical_arguments_hash,
        "confirmation_hash": confirmation.confirmation_hash,
        "expires_at": confirmation.expires_at.isoformat(),
        "field_mask_hash": confirmation.field_mask_hash,
        "permission_code": confirmation.permission_code,
        "policy_decision_id": str(confirmation.policy_decision_id),
        "policy_evaluated_at": confirmation.policy_evaluated_at.isoformat(),
        "policy_version": confirmation.policy_version,
        "resource_scope_hash": confirmation.resource_scope_hash,
        "run_id": str(confirmation.binding.run_id),
        "step_id": str(confirmation.binding.step_id),
        "tool_id": str(confirmation.binding.tool_id),
        "tool_version": confirmation.binding.tool_version,
    }
    return ApprovalSubject(
        workspace_id=confirmation.binding.workspace_id,
        requester_account_id=requester_account_id,
        resource_type="tool.call",
        operation="execute",
        resource_id=confirmation.binding.step_id,
        department_ids=(),
        security_level="CONFIDENTIAL",
        risk_level=confirmation.risk_level,
        fields=fields,
    )


def _require_requested_step(confirmation: ToolConfirmation, step_id: UUID) -> None:
    if confirmation.binding.step_id != step_id:
        raise ToolRunConflictError


def _raise_unusable_confirmation(confirmation: ToolConfirmation) -> None:
    if confirmation.state in {"expired", "withdrawn"}:
        raise ToolConfirmationStaleError
    raise ToolConfirmationRequiredError


def _binding_invalidation_reason(
    expected: ToolCallBinding,
    actual: ToolCallBinding,
) -> ToolConfirmationInvalidationReason:
    if expected.canonical_arguments_hash != actual.canonical_arguments_hash:
        return "arguments_changed"
    return "tool_changed"


__all__ = [
    "ToolApprovalRuntime",
    "ToolConfirmationRequestResult",
    "ToolConfirmationService",
]
