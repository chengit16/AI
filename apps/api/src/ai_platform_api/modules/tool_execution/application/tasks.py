"""编排受控工具任务，并把全部状态写入收敛到唯一 Store。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.tool_execution.application.errors import (
    ToolExecutionDeniedError,
    ToolRunBudgetExceededError,
    ToolRunConflictError,
)
from ai_platform_api.modules.tool_execution.application.results import ToolResultFactsService
from ai_platform_api.modules.tool_execution.domain.results import ToolAttemptOutcomeFacts
from ai_platform_api.modules.tool_execution.domain.tasks import (
    AttemptResult,
    ClaimedToolAttempt,
    ToolCallState,
    ToolRun,
    ToolRunBudget,
    ToolRunState,
    ToolStep,
    ToolStepState,
    ToolTaskStore,
)

IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ToolTaskService:
    """提供内部任务状态入口，不在 P4-03 暴露 HTTP、菜单或 Adapter。"""

    def __init__(self, store: ToolTaskStore) -> None:
        self._store = store

    def create_run(
        self,
        context: RequestContext,
        *,
        service_id: UUID,
        agent_release_id: UUID,
        idempotency_key: str,
        budget: ToolRunBudget,
        created_at: datetime | None = None,
    ) -> ToolRun:
        """用可信 Actor 和账号创建幂等 Run，客户端不能替换身份或工作空间。"""

        account_id = _require_principal(context)
        _require_idempotency_key(idempotency_key)
        _require_budget(budget)
        occurred_at = created_at or datetime.now(UTC)
        return self._store.create_run(
            run_id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            account_id=account_id,
            service_id=service_id,
            agent_release_id=agent_release_id,
            idempotency_key=idempotency_key,
            request_hash=_run_request_hash(service_id, agent_release_id, budget),
            budget=budget,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            created_at=occurred_at,
        )

    def get_run(self, context: RequestContext, run_id: UUID) -> ToolRun:
        """读取当前工作空间 Run；跨空间与不存在事实使用同一失败结果。"""

        _require_principal(context)
        run = self._store.get_run(context.workspace_id, run_id)
        if run is None:
            raise ToolExecutionDeniedError
        return run

    def transition_run(
        self,
        context: RequestContext,
        run_id: UUID,
        target_state: ToolRunState,
        *,
        occurred_at: datetime,
    ) -> ToolRun:
        """推进 Run 状态，合法边由应用与数据库重复校验。"""

        _require_principal(context)
        return self._store.transition_run(
            context.workspace_id,
            run_id,
            target_state,
            occurred_at=occurred_at,
        )

    def append_step(
        self,
        context: RequestContext,
        run_id: UUID,
        *,
        tool_id: UUID,
        tool_version: int,
        canonical_arguments_hash: str,
        created_at: datetime,
    ) -> ToolStep:
        """追加冻结 Step；参数正文始终留在受控调用边界之外。"""

        _require_principal(context)
        if tool_version < 1 or SHA256_PATTERN.fullmatch(canonical_arguments_hash) is None:
            raise ToolRunConflictError
        return self._store.append_step(
            step_id=uuid4(),
            workspace_id=context.workspace_id,
            run_id=run_id,
            tool_id=tool_id,
            tool_version=tool_version,
            canonical_arguments_hash=canonical_arguments_hash,
            created_at=created_at,
        )

    def transition_step(
        self,
        context: RequestContext,
        step_id: UUID,
        target_state: ToolStepState,
        *,
        occurred_at: datetime,
    ) -> ToolStep:
        """推进策略前置状态；`ready → running` 只能由租约领取原子完成。"""

        _require_principal(context)
        return self._store.transition_step(
            context.workspace_id,
            step_id,
            target_state,
            occurred_at=occurred_at,
        )

    def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimedToolAttempt | None:
        """由内部 Worker 领取下一顺序步骤，外部请求不能指定工作空间或 Step。"""

        if not worker_id.strip() or len(worker_id) > 120 or not 1 <= lease_seconds <= 300:
            raise ToolRunConflictError
        return self._store.claim_next(
            worker_id=worker_id,
            now=now,
            lease_seconds=lease_seconds,
        )

    def begin_attempt(
        self,
        claim: ClaimedToolAttempt,
        *,
        started_at: datetime,
    ) -> bool:
        """让持有当前租约的 Worker 开始执行，伪造或失效 Claim 返回失败。"""

        return self._store.begin_attempt(claim, started_at=started_at)

    def renew_lease(
        self,
        claim: ClaimedToolAttempt,
        *,
        renewed_at: datetime,
        lease_seconds: int,
    ) -> ClaimedToolAttempt | None:
        """在冻结步骤时限内续租，失租、取消或超时统一返回空结果。"""

        if not 1 <= lease_seconds <= 300:
            raise ToolRunConflictError
        return self._store.renew_lease(
            claim,
            renewed_at=renewed_at,
            lease_seconds=lease_seconds,
        )

    def observe_cancellation(
        self,
        claim: ClaimedToolAttempt,
        *,
        observed_at: datetime,
    ) -> bool:
        """供 Adapter 尽力传播取消，并留下不含参数正文的观察时间。"""

        return self._store.observe_cancellation(claim, observed_at=observed_at)

    def transition_call(
        self,
        claim: ClaimedToolAttempt,
        target_state: ToolCallState,
        *,
        occurred_at: datetime,
    ) -> bool:
        """按冻结 Claim 推进 ToolCall，后续节点在每条状态边前接入确定性门禁。"""

        return self._store.transition_call(claim, target_state, occurred_at=occurred_at)

    def finish_attempt(
        self,
        claim: ClaimedToolAttempt,
        *,
        facts: ToolAttemptOutcomeFacts,
        succeeded: bool,
        completed_at: datetime,
        error_code: str | None = None,
        retryable: bool = False,
        next_attempt_at: datetime | None = None,
    ) -> AttemptResult:
        """按完整租约身份提交结果，失租或父级终止时只返回迟到结论。"""

        ToolResultFactsService.validate(facts)
        if succeeded != (facts.outcome == "succeeded"):
            raise ToolRunConflictError
        return self._store.finish_attempt(
            claim,
            facts=facts,
            succeeded=succeeded,
            completed_at=completed_at,
            error_code=error_code,
            retryable=retryable,
            next_attempt_at=next_attempt_at,
        )

    def require_manual_recovery(
        self,
        claim: ClaimedToolAttempt,
        *,
        facts: ToolAttemptOutcomeFacts,
        error_code: str,
        occurred_at: datetime,
    ) -> AttemptResult:
        """关闭当前 Attempt 并把父级置为人工恢复，禁止 Worker 自动接管。"""

        if not error_code or len(error_code) > 128:
            raise ToolRunConflictError
        ToolResultFactsService.validate(facts)
        if facts.outcome == "succeeded":
            raise ToolRunConflictError
        return self._store.require_manual_recovery(
            claim,
            facts=facts,
            error_code=error_code,
            occurred_at=occurred_at,
        )

    def recover_manually(
        self,
        context: RequestContext,
        run_id: UUID,
        *,
        recovered_at: datetime,
    ) -> ToolRun:
        """由可信主体开启下一恢复代际，结果未知的副作用仍只能走对账入口。"""

        account_id = _require_principal(context)
        return self._store.recover_manually(
            workspace_id=context.workspace_id,
            run_id=run_id,
            actor_id=context.actor_id,
            account_id=account_id,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            authorization=context.audit_authorization,
            recovered_at=recovered_at,
        )

    def request_cancellation(
        self,
        context: RequestContext,
        run_id: UUID,
        *,
        requested_at: datetime,
    ) -> ToolRun:
        """提交当前工作空间取消事实；执行中 Attempt 由迟到结果路径收敛。"""

        _require_principal(context)
        return self._store.request_cancellation(
            context.workspace_id,
            run_id,
            requested_at=requested_at,
        )


def _require_principal(context: RequestContext) -> UUID:
    """浏览器和 Open API 均必须保留可追溯账号，Actor 仍单独冻结。"""

    if context.user_id is None or context.authentication_method not in {
        "browser_session",
        "open_api_key",
        "test",
    }:
        raise ToolExecutionDeniedError
    if context.authentication_method == "browser_session" and context.actor_id != context.user_id:
        raise ToolExecutionDeniedError
    return context.user_id


def _require_idempotency_key(value: str) -> None:
    if IDEMPOTENCY_PATTERN.fullmatch(value) is None:
        raise ToolRunConflictError


def _require_budget(budget: ToolRunBudget) -> None:
    if not (
        1 <= budget.max_steps <= 50
        and 1 <= budget.max_attempts_per_step <= 5
        and 1 <= budget.max_execution_seconds <= 1800
        and budget.max_cost_microunits >= 0
    ):
        raise ToolRunBudgetExceededError


def _run_request_hash(
    service_id: UUID,
    agent_release_id: UUID,
    budget: ToolRunBudget,
) -> str:
    """摘要只覆盖会改变 Run 语义的冻结字段，重放时可判定异载荷冲突。"""

    document = {
        "agent_release_id": str(agent_release_id),
        "budget": {
            "max_attempts_per_step": budget.max_attempts_per_step,
            "max_cost_microunits": budget.max_cost_microunits,
            "max_execution_seconds": budget.max_execution_seconds,
            "max_steps": budget.max_steps,
        },
        "service_id": str(service_id),
    }
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


__all__ = ["ToolRunBudget", "ToolTaskService"]
