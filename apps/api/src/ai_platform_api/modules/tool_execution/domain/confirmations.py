"""定义工具确认绑定、失效投影和原子恢复端口。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_api.modules.tool_execution.domain.planning import ToolPolicyDecisionRecord
from ai_platform_api.modules.tool_execution.domain.tasks import ToolRun, ToolStep

ToolConfirmationMode = Literal["personal_owner", "enterprise_approval"]
ToolConfirmationRecordedState = Literal[
    "pending",
    "approved",
    "rejected",
    "expired",
    "withdrawn",
]
ToolConfirmationInvalidationState = Literal["expired", "withdrawn"]
ToolConfirmationInvalidationReason = Literal[
    "arguments_changed",
    "tool_changed",
    "policy_changed",
    "permission_revoked",
    "expired",
]


@dataclass(frozen=True)
class ToolCallBinding:
    """承载执行边缘必须与确认事实逐字段匹配的调用身份。"""

    workspace_id: UUID
    run_id: UUID
    step_id: UUID
    tool_id: UUID
    tool_version: int
    canonical_arguments_hash: str


@dataclass(frozen=True)
class ToolConfirmation:
    """投影一次副作用调用的审批结果和追加式失效事实。

    ``recorded_state`` 是审批事务写入的不可变终态；已批准事实遇到参数、
    工具、策略或有效期变化时不被覆盖，而由失效事实生成新的有效 ``state``。
    """

    confirmation_id: UUID
    approval_instance_id: UUID
    binding: ToolCallBinding
    mode: ToolConfirmationMode
    policy_decision_id: UUID
    permission_code: str
    policy_version: int
    resource_scope_hash: str
    field_mask_hash: str
    policy_evaluated_at: datetime
    risk_level: Literal["high", "critical"]
    confirmation_hash: str
    subject_digest: str
    chain_digest: str
    recorded_state: ToolConfirmationRecordedState
    confirmed_by_actor_id: UUID | None
    expires_at: datetime
    resolved_at: datetime | None
    invalidation_state: ToolConfirmationInvalidationState | None
    invalidation_reason: ToolConfirmationInvalidationReason | None
    invalidated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int

    @property
    def state(self) -> ToolConfirmationRecordedState:
        """优先返回追加式失效结论，避免已批准旧事实继续被执行。"""

        return self.invalidation_state or self.recorded_state


@dataclass(frozen=True)
class ToolConfirmationRequestBasis:
    """返回申请确认前需要复核的可信 Run 与 Step。"""

    run: ToolRun
    step: ToolStep


@dataclass(frozen=True)
class ToolConfirmationResumeResult:
    """返回重新授权后已进入可执行状态的确认、Run、Step 和策略证据。"""

    confirmation: ToolConfirmation
    run: ToolRun
    step: ToolStep
    policy: ToolPolicyDecisionRecord


class ToolConfirmationStore(Protocol):
    """独占工具确认读取、失效和审批后恢复写入。"""

    def get_request_basis(
        self,
        workspace_id: UUID,
        step_id: UUID,
    ) -> ToolConfirmationRequestBasis | None: ...

    def get_by_request_idempotency(
        self,
        workspace_id: UUID,
        requester_account_id: UUID,
        idempotency_key: str,
    ) -> ToolConfirmation | None: ...

    def get_by_approval_instance(
        self,
        workspace_id: UUID,
        approval_instance_id: UUID,
    ) -> ToolConfirmation | None: ...

    def get_confirmation(
        self,
        workspace_id: UUID,
        confirmation_id: UUID,
    ) -> ToolConfirmation | None: ...

    def invalidate(
        self,
        workspace_id: UUID,
        confirmation_id: UUID,
        *,
        state: ToolConfirmationInvalidationState,
        reason: ToolConfirmationInvalidationReason,
        occurred_at: datetime,
    ) -> ToolConfirmation: ...

    def expire(
        self,
        workspace_id: UUID,
        confirmation_id: UUID,
        *,
        occurred_at: datetime,
    ) -> ToolConfirmation: ...

    def resume(
        self,
        workspace_id: UUID,
        confirmation_id: UUID,
        *,
        expected_binding: ToolCallBinding,
        policy: ToolPolicyDecisionRecord,
        resumed_at: datetime,
    ) -> ToolConfirmationResumeResult: ...


def confirmation_binding_hash(binding: ToolCallBinding, policy_version: int) -> str:
    """生成不含参数正文的稳定确认摘要，审批和执行边缘共同复算。"""

    payload = json.dumps(
        {
            "canonical_arguments_hash": binding.canonical_arguments_hash,
            "policy_version": policy_version,
            "run_id": str(binding.run_id),
            "step_id": str(binding.step_id),
            "tool_id": str(binding.tool_id),
            "tool_version": binding.tool_version,
            "workspace_id": str(binding.workspace_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "ToolCallBinding",
    "ToolConfirmation",
    "ToolConfirmationInvalidationReason",
    "ToolConfirmationInvalidationState",
    "ToolConfirmationMode",
    "ToolConfirmationRecordedState",
    "ToolConfirmationRequestBasis",
    "ToolConfirmationResumeResult",
    "ToolConfirmationStore",
    "confirmation_binding_hash",
]
