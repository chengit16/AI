"""构造不含工具正文的安全结果与完整用量收口事实。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from uuid import UUID, uuid4

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.tool_execution.application.errors import (
    ToolExecutionDeniedError,
    ToolResultRejectedError,
)
from ai_platform_api.modules.tool_execution.domain.adapters import ToolAdapterResult
from ai_platform_api.modules.tool_execution.domain.results import (
    ToolAttemptOutcomeFacts,
    ToolProgressEvent,
    ToolProgressPage,
    ToolProgressStore,
    ToolSafeResult,
    ToolSafetyCheck,
    ToolSafetyCheckCode,
    ToolSafetyCheckStatus,
    ToolUsageOutcome,
)

EXPECTED_CHECKS: tuple[ToolSafetyCheckCode, ...] = (
    "schema",
    "size",
    "sensitive_fields",
    "prompt_injection",
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ToolResultFactsService:
    """把 Adapter 结论收敛为可持久化摘要，拒绝遗漏失败用量。"""

    def accepted(
        self,
        *,
        workspace_id: UUID,
        tool_call_id: UUID,
        adapter_result: ToolAdapterResult,
        duration_ms: int,
        cost_microunits: int,
        recorded_at: datetime,
    ) -> ToolAttemptOutcomeFacts:
        """构造可进入模型上下文的接受结果，正文不会复制到结果事实。"""

        if adapter_result.checks != EXPECTED_CHECKS:
            raise ToolResultRejectedError
        checks = tuple(
            ToolSafetyCheck(check_code=code, status="passed") for code in EXPECTED_CHECKS
        )
        safe_result = ToolSafeResult(
            result_id=uuid4(),
            tool_call_id=tool_call_id,
            workspace_id=workspace_id,
            output_schema_hash=adapter_result.output_schema_hash,
            content_hash=adapter_result.result_sha256,
            result_size_bytes=adapter_result.result_size_bytes,
            status="accepted",
            safety_checks=checks,
            eligible_for_model_context=True,
            credential_exposure_detected=False,
            recorded_at=recorded_at,
        )
        outcome = ToolAttemptOutcomeFacts(
            outcome="succeeded",
            duration_ms=duration_ms,
            cost_microunits=cost_microunits,
            result_size_bytes=adapter_result.result_size_bytes,
            error_code=None,
            safe_result=safe_result,
        )
        self.validate(outcome)
        return outcome

    def rejected(
        self,
        *,
        workspace_id: UUID,
        tool_call_id: UUID,
        output_schema_hash: str,
        content_hash: str,
        check_statuses: Mapping[ToolSafetyCheckCode, ToolSafetyCheckStatus],
        result_size_bytes: int,
        duration_ms: int,
        cost_microunits: int,
        recorded_at: datetime,
    ) -> ToolAttemptOutcomeFacts:
        """记录被安全门禁拒绝的摘要；命中值和原始正文仍不得持久化。"""

        checks = tuple(
            ToolSafetyCheck(check_code=code, status=check_statuses.get(code, "failed"))
            for code in EXPECTED_CHECKS
        )
        safe_result = ToolSafeResult(
            result_id=uuid4(),
            tool_call_id=tool_call_id,
            workspace_id=workspace_id,
            output_schema_hash=output_schema_hash,
            content_hash=content_hash,
            result_size_bytes=result_size_bytes,
            status="rejected",
            safety_checks=checks,
            eligible_for_model_context=False,
            credential_exposure_detected=False,
            recorded_at=recorded_at,
        )
        outcome = ToolAttemptOutcomeFacts(
            outcome="failed",
            duration_ms=duration_ms,
            cost_microunits=cost_microunits,
            result_size_bytes=result_size_bytes,
            error_code="TOOL_RESULT_REJECTED",
            safe_result=safe_result,
        )
        self.validate(outcome)
        return outcome

    def failed(
        self,
        *,
        outcome: ToolUsageOutcome,
        duration_ms: int,
        cost_microunits: int,
        result_size_bytes: int,
        error_code: str,
    ) -> ToolAttemptOutcomeFacts:
        """为非结果安全类终态保留完整用量，不能只统计成功调用。"""

        facts = ToolAttemptOutcomeFacts(
            outcome=outcome,
            duration_ms=duration_ms,
            cost_microunits=cost_microunits,
            result_size_bytes=result_size_bytes,
            error_code=error_code,
            safe_result=None,
        )
        self.validate(facts)
        return facts

    @staticmethod
    def validate(facts: ToolAttemptOutcomeFacts) -> None:
        """在数据库前复核摘要、检查集合、上下文资格和非负用量。"""

        # 1. 先约束所有终态共用的非负用量与稳定错误码，失败样本不能绕开基本校验。
        if min(facts.duration_ms, facts.cost_microunits, facts.result_size_bytes) < 0:
            raise ToolResultRejectedError
        if facts.error_code is not None and not 1 <= len(facts.error_code) <= 128:
            raise ToolResultRejectedError
        result = facts.safe_result
        if facts.outcome == "succeeded" and (result is None or facts.error_code is not None):
            raise ToolResultRejectedError
        if result is None:
            if facts.outcome == "succeeded":
                raise ToolResultRejectedError
            return
        # 2. 存在结果时严格绑定摘要、大小和时间信息，正文与凭证暴露标记均不得通过。
        if (
            SHA256_PATTERN.fullmatch(result.output_schema_hash) is None
            or SHA256_PATTERN.fullmatch(result.content_hash) is None
            or result.result_size_bytes != facts.result_size_bytes
            or result.credential_exposure_detected
            or result.recorded_at.tzinfo is None
        ):
            raise ToolResultRejectedError
        statuses = {item.check_code: item.status for item in result.safety_checks}
        if tuple(statuses) != EXPECTED_CHECKS or len(result.safety_checks) != len(EXPECTED_CHECKS):
            raise ToolResultRejectedError
        all_passed = all(status == "passed" for status in statuses.values())
        # 3. 接受结果必须四项全过且只对应成功；拒绝结果必须至少命中一项失败检查。
        if result.status == "accepted":
            if (
                not all_passed
                or not result.eligible_for_model_context
                or facts.outcome != "succeeded"
            ):
                raise ToolResultRejectedError
        elif all_passed or result.eligible_for_model_context or facts.outcome == "succeeded":
            raise ToolResultRejectedError


class ToolProgressService:
    """提供工作空间隔离的 Run 进度断点回放，不接受资源正文筛选。"""

    def __init__(self, store: ToolProgressStore) -> None:
        self._store = store

    def replay(
        self,
        context: RequestContext,
        *,
        run_id: UUID,
        after_cursor: int = 0,
        limit: int = 100,
    ) -> ToolProgressPage:
        """按游标返回最多一千条固定事件，跨空间与不存在统一拒绝。"""

        if after_cursor < 0 or not 1 <= limit <= 1000:
            raise ToolExecutionDeniedError
        page = self._store.replay(
            workspace_id=context.workspace_id,
            run_id=run_id,
            after_cursor=after_cursor,
            limit=limit,
        )
        if page is None:
            raise ToolExecutionDeniedError
        return page


__all__ = [
    "EXPECTED_CHECKS",
    "ToolProgressEvent",
    "ToolProgressPage",
    "ToolProgressService",
    "ToolResultFactsService",
]
