"""定义副作用幂等事实、合成 Adapter 命令和唯一持久化端口。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from ai_platform_api.modules.tool_execution.domain.tasks import ClaimedToolAttempt

ToolIdempotencyState = Literal["reserved", "succeeded", "failed", "outcome_unknown"]


@dataclass(frozen=True)
class ToolSideEffectIdentity:
    """保存可跨 Attempt 复算的幂等键和完整请求摘要。"""

    idempotency_key_hash: str
    request_hash: str
    confirmation_hash: str


@dataclass(frozen=True)
class ToolIdempotencyRecord:
    """投影副作用执行前预留及其最小终态，不保存参数或结果正文。"""

    idempotency_record_id: UUID
    workspace_id: UUID
    run_id: UUID
    step_id: UUID
    tool_call_id: UUID
    tool_id: UUID
    tool_version: int
    identity: ToolSideEffectIdentity
    state: ToolIdempotencyState
    result_hash: str | None
    reserved_at: datetime
    completed_at: datetime | None
    error_code: str | None


@dataclass(frozen=True)
class ToolIdempotencyReservation:
    """区分本次新建预留与既有记录，只有新建方可以调用 Adapter。"""

    record: ToolIdempotencyRecord
    created: bool


@dataclass(frozen=True)
class SyntheticSideEffectCommand:
    """向合成 Adapter 传递最小摘要身份，不传递真实业务正文或凭证。"""

    side_effect_id: UUID
    idempotency_record_id: UUID
    workspace_id: UUID
    idempotency_key_hash: str
    request_hash: str
    canonical_arguments_hash: str


@dataclass(frozen=True)
class SyntheticSideEffectReceipt:
    """证明合成副作用已经唯一提交，并提供可持久化的稳定结果摘要。"""

    side_effect_id: UUID
    idempotency_record_id: UUID
    workspace_id: UUID
    idempotency_key_hash: str
    request_hash: str
    canonical_arguments_hash: str
    result_hash: str
    committed_at: datetime


@dataclass(frozen=True)
class ToolSideEffectExecutionResult:
    """返回成功或重放结论；结果正文由 P4-10 的安全结果事实负责。"""

    record: ToolIdempotencyRecord
    result_hash: str
    replayed: bool


class ToolSideEffectStore(Protocol):
    """独占幂等预留、任务收口以及审计和 Outbox 的事务写入。"""

    def reserve(
        self,
        claim: ClaimedToolAttempt,
        *,
        reserved_at: datetime,
    ) -> ToolIdempotencyReservation: ...

    def complete_success(
        self,
        claim: ClaimedToolAttempt,
        receipt: SyntheticSideEffectReceipt,
        *,
        completed_at: datetime,
        reconciled: bool,
    ) -> ToolSideEffectExecutionResult: ...

    def complete_failure(
        self,
        claim: ClaimedToolAttempt,
        record_id: UUID,
        *,
        error_code: str,
        completed_at: datetime,
    ) -> ToolIdempotencyRecord: ...

    def mark_outcome_unknown(
        self,
        claim: ClaimedToolAttempt,
        record_id: UUID,
        *,
        occurred_at: datetime,
    ) -> ToolIdempotencyRecord: ...


class SyntheticSideEffectAdapter(Protocol):
    """只操作合成事实的封闭 Adapter，不允许访问真实外部系统。"""

    def execute(
        self,
        command: SyntheticSideEffectCommand,
        *,
        committed_at: datetime,
    ) -> SyntheticSideEffectReceipt: ...

    def reconcile(
        self,
        record: ToolIdempotencyRecord,
    ) -> SyntheticSideEffectReceipt | None: ...


def side_effect_identity(
    claim: ClaimedToolAttempt,
    confirmation_hash: str,
) -> ToolSideEffectIdentity:
    """按冻结协议生成跨 Attempt 稳定键，并把全部请求身份纳入请求摘要。"""

    # 幂等键故意不含 Attempt/ToolCall；同一 Step 的队列重投和未来安全恢复必须命中同一记录。
    key_document = _key_document(claim)
    request_document = {
        **key_document,
        "canonical_arguments_hash": claim.canonical_arguments_hash,
        "confirmation_hash": confirmation_hash,
        "tool_id": str(claim.tool_id),
        "tool_version": claim.tool_version,
    }
    return ToolSideEffectIdentity(
        idempotency_key_hash=_digest(key_document),
        request_hash=_digest(request_document),
        confirmation_hash=confirmation_hash,
    )


def side_effect_key_hash(claim: ClaimedToolAttempt) -> str:
    """只用稳定 Step 身份计算键，供存储在读取确认前定位已有记录。"""

    return _digest(_key_document(claim))


def synthetic_result_hash(command: SyntheticSideEffectCommand) -> str:
    """生成不含参数正文的确定性合成结果摘要。"""

    return _digest(
        {
            "canonical_arguments_hash": command.canonical_arguments_hash,
            "idempotency_key_hash": command.idempotency_key_hash,
            "side_effect_id": str(command.side_effect_id),
            "status": "recorded",
            "workspace_id": str(command.workspace_id),
        }
    )


def _digest(document: object) -> str:
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _key_document(claim: ClaimedToolAttempt) -> dict[str, str]:
    return {
        "run_id": str(claim.run_id),
        "step_id": str(claim.step_id),
        "workspace_id": str(claim.workspace_id),
    }


__all__ = [
    "SyntheticSideEffectAdapter",
    "SyntheticSideEffectCommand",
    "SyntheticSideEffectReceipt",
    "ToolIdempotencyRecord",
    "ToolIdempotencyReservation",
    "ToolIdempotencyState",
    "ToolSideEffectExecutionResult",
    "ToolSideEffectIdentity",
    "ToolSideEffectStore",
    "side_effect_identity",
    "side_effect_key_hash",
    "synthetic_result_hash",
]
