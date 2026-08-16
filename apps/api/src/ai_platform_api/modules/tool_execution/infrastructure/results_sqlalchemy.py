"""用 PostgreSQL 追加安全结果、完整用量和 Run 内单调进度事实。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from ai_platform_backend.integration.domain import AuditAuthorization, AuditRecord, IntegrationEvent
from ai_platform_backend.integration.sqlalchemy import SqlAlchemyAuditWriter, SqlAlchemyOutboxWriter
from sqlalchemy import func, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from ai_platform_api.modules.tool_execution.domain.results import (
    ToolAttemptOutcomeFacts,
    ToolProgressEvent,
    ToolProgressEventType,
    ToolProgressPage,
    ToolProgressStore,
    ToolSafeResult,
    ToolUsageOutcome,
)
from ai_platform_api.modules.tool_execution.domain.tasks import ClaimedToolAttempt
from ai_platform_api.persistence.tables import (
    tool_policy_decisions,
    tool_progress_events,
    tool_runs,
    tool_safe_results,
    tool_usage_records,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyToolProgressStore(ToolProgressStore):
    """从不可变进度表执行有界游标回放，拒绝跨工作空间探测。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def replay(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        after_cursor: int,
        limit: int,
    ) -> ToolProgressPage | None:
        with self._session_factory() as session:
            run_exists = session.scalar(
                select(func.count())
                .select_from(tool_runs)
                .where(tool_runs.c.workspace_id == workspace_id, tool_runs.c.run_id == run_id)
            )
            if int(run_exists or 0) != 1:
                return None
            rows = (
                session.execute(
                    select(tool_progress_events)
                    .where(
                        tool_progress_events.c.workspace_id == workspace_id,
                        tool_progress_events.c.run_id == run_id,
                        tool_progress_events.c.cursor > after_cursor,
                    )
                    .order_by(tool_progress_events.c.cursor)
                    .limit(limit + 1)
                )
                .mappings()
                .all()
            )
            latest_cursor = int(
                session.scalar(
                    select(func.coalesce(func.max(tool_progress_events.c.cursor), 0)).where(
                        tool_progress_events.c.run_id == run_id
                    )
                )
                or 0
            )
        return ToolProgressPage(
            events=tuple(_progress_event(row) for row in rows[:limit]),
            latest_cursor=latest_cursor,
            has_more=len(rows) > limit,
        )


def append_attempt_outcome(
    session: Session,
    *,
    row: RowMapping,
    claim: ClaimedToolAttempt,
    facts: ToolAttemptOutcomeFacts,
    effective_outcome: ToolUsageOutcome,
    run_state: str,
    step_state: str,
    event_type: ToolProgressEventType,
    error_code: str | None,
    recorded_at: datetime,
    emit_lifecycle: bool = True,
) -> None:
    """在任务状态事务中追加结果、用量和进度，任何一项失败都会整体回滚。"""

    # 1. 先处理安全结果；迟到或取消优先时，通过检查的正文摘要也不能进入模型上下文。
    safe_result = facts.safe_result
    if safe_result is not None and (
        safe_result.status == "rejected" or effective_outcome == "succeeded"
    ):
        append_safe_result(session, claim=claim, safe_result=safe_result)
    # 2. 再追加每个 Attempt 恰好一条的用量和进度，成本门禁失败时整个终态事务回滚。
    effective_error = None if effective_outcome == "succeeded" else error_code
    if effective_error is None and effective_outcome != "succeeded":
        raise ValueError("非成功工具用量必须具有稳定错误码")
    session.execute(
        insert(tool_usage_records).values(
            usage_record_id=uuid4(),
            workspace_id=claim.workspace_id,
            run_id=claim.run_id,
            step_id=claim.step_id,
            attempt_id=claim.attempt_id,
            tool_call_id=claim.tool_call_id,
            tool_id=claim.tool_id,
            tool_version=claim.tool_version,
            access_mode=row["access_mode"],
            risk_level=row["risk_level"],
            outcome=effective_outcome,
            duration_ms=facts.duration_ms,
            result_size_bytes=facts.result_size_bytes,
            cost_microunits=facts.cost_microunits,
            error_code=effective_error,
            recorded_at=recorded_at,
        )
    )
    append_progress_event(
        session,
        workspace_id=claim.workspace_id,
        run_id=claim.run_id,
        event_type=event_type,
        run_state=run_state,
        step_id=claim.step_id,
        step_state=step_state,
        attempt_id=claim.attempt_id,
        tool_call_id=claim.tool_call_id,
        error_code=effective_error,
        occurred_at=recorded_at,
    )
    # 3. 最后写最小审计与 Outbox；已有专用横切事实的副作用路径显式关闭重复写入。
    if emit_lifecycle:
        _record_outcome_lifecycle(
            session,
            row=row,
            claim=claim,
            outcome=effective_outcome,
            error_code=effective_error,
            occurred_at=recorded_at,
        )


def append_safe_result(
    session: Session,
    *,
    claim: ClaimedToolAttempt,
    safe_result: ToolSafeResult,
) -> None:
    """追加一次结果摘要，调用身份与四项检查由数据库再次复核。"""

    checks = {item.check_code: item.status for item in safe_result.safety_checks}
    session.execute(
        insert(tool_safe_results).values(
            result_id=safe_result.result_id,
            tool_call_id=claim.tool_call_id,
            workspace_id=claim.workspace_id,
            run_id=claim.run_id,
            step_id=claim.step_id,
            attempt_id=claim.attempt_id,
            output_schema_hash=safe_result.output_schema_hash,
            content_hash=safe_result.content_hash,
            result_size_bytes=safe_result.result_size_bytes,
            status=safe_result.status,
            schema_check=checks["schema"],
            size_check=checks["size"],
            sensitive_fields_check=checks["sensitive_fields"],
            prompt_injection_check=checks["prompt_injection"],
            eligible_for_model_context=safe_result.eligible_for_model_context,
            credential_exposure_detected=safe_result.credential_exposure_detected,
            recorded_at=safe_result.recorded_at,
        )
    )


def append_progress_event(
    session: Session,
    *,
    workspace_id: UUID,
    run_id: UUID,
    event_type: ToolProgressEventType,
    run_state: str,
    step_id: UUID | None,
    step_state: str | None,
    attempt_id: UUID | None,
    tool_call_id: UUID | None,
    error_code: str | None,
    occurred_at: datetime,
) -> None:
    """在已锁定 Run 的事务中分配下一游标，数据库 Trigger 再次校验连续性。"""

    cursor = (
        int(
            session.scalar(
                select(func.coalesce(func.max(tool_progress_events.c.cursor), 0)).where(
                    tool_progress_events.c.run_id == run_id
                )
            )
            or 0
        )
        + 1
    )
    session.execute(
        insert(tool_progress_events).values(
            progress_event_id=uuid4(),
            workspace_id=workspace_id,
            run_id=run_id,
            cursor=cursor,
            event_type=event_type,
            run_state=run_state,
            step_id=step_id,
            step_state=step_state,
            attempt_id=attempt_id,
            tool_call_id=tool_call_id,
            error_code=error_code,
            occurred_at=occurred_at,
        )
    )


def _record_outcome_lifecycle(
    session: Session,
    *,
    row: RowMapping,
    claim: ClaimedToolAttempt,
    outcome: ToolUsageOutcome,
    error_code: str | None,
    occurred_at: datetime,
) -> None:
    """记录不含参数、结果、凭证或主体标签的最小调用终态。"""

    # 1. 从 Run 与最新 PDP 恢复可信主体、Trace 和授权证据，不接受调用方拼接横切身份。
    source = (
        session.execute(
            select(
                tool_runs.c.requested_by_actor_id,
                tool_runs.c.requested_by_account_id,
                tool_runs.c.trace_id,
                tool_runs.c.traceparent,
                tool_policy_decisions.c.decision_id,
                tool_policy_decisions.c.permission_code,
                tool_policy_decisions.c.policy_version,
            )
            .join(
                tool_policy_decisions,
                tool_policy_decisions.c.run_id == tool_runs.c.run_id,
            )
            .where(
                tool_runs.c.run_id == claim.run_id,
                tool_runs.c.workspace_id == claim.workspace_id,
                tool_policy_decisions.c.step_id == claim.step_id,
            )
            .order_by(tool_policy_decisions.c.evaluated_at.desc())
            .limit(1)
        )
        .mappings()
        .one()
    )
    event_type = "tool.call.completed" if outcome == "succeeded" else "tool.call.failed"
    event_outcome = "succeeded" if outcome == "succeeded" else "failed"
    authorization = AuditAuthorization(
        permission_code=cast(str, source["permission_code"]),
        policy_decision_id=cast(UUID, source["decision_id"]),
        policy_version=cast(int, source["policy_version"]),
    )
    # 2. 横切载荷只保留低敏分类、终态和稳定错误码，不复制参数、结果或凭证正文。
    attributes: dict[str, object] = {
        "access_mode": cast(str, row["access_mode"]),
        "outcome": outcome,
        "risk_level": cast(str, row["risk_level"]),
        "tool_version": claim.tool_version,
    }
    if error_code is not None:
        attributes["error_code"] = error_code
    # 3. 审计与 Outbox 共用业务 Session，任一失败都会阻止工具终态提交。
    SqlAlchemyAuditWriter(session).add(
        AuditRecord(
            audit_id=uuid4(),
            workspace_id=claim.workspace_id,
            actor_id=cast(UUID, source["requested_by_actor_id"]),
            user_id=cast(UUID, source["requested_by_account_id"]),
            action=event_type,
            resource_type="tool_call",
            resource_id=claim.tool_call_id,
            outcome=event_outcome,
            occurred_at=occurred_at,
            request_id=claim.tool_call_id,
            trace_id=cast(str, source["trace_id"]),
            traceparent=cast(str, source["traceparent"]),
            authorization=authorization,
            attributes=attributes,
        )
    )
    SqlAlchemyOutboxWriter(session).add(
        IntegrationEvent(
            event_id=uuid4(),
            event_type=event_type,
            workspace_id=claim.workspace_id,
            aggregate_id=claim.tool_call_id,
            aggregate_version=1,
            occurred_at=occurred_at,
            trace_id=cast(str, source["trace_id"]),
            traceparent=cast(str, source["traceparent"]),
            actor_id=cast(UUID, source["requested_by_actor_id"]),
            user_id=cast(UUID, source["requested_by_account_id"]),
            request_id=claim.tool_call_id,
            payload=attributes,
        )
    )


def _progress_event(row: RowMapping) -> ToolProgressEvent:
    return ToolProgressEvent(
        progress_event_id=cast(UUID, row["progress_event_id"]),
        workspace_id=cast(UUID, row["workspace_id"]),
        run_id=cast(UUID, row["run_id"]),
        cursor=cast(int, row["cursor"]),
        event_type=cast(ToolProgressEventType, row["event_type"]),
        run_state=cast(str, row["run_state"]),
        step_id=cast(UUID | None, row["step_id"]),
        step_state=cast(str | None, row["step_state"]),
        attempt_id=cast(UUID | None, row["attempt_id"]),
        tool_call_id=cast(UUID | None, row["tool_call_id"]),
        error_code=cast(str | None, row["error_code"]),
        occurred_at=cast(datetime, row["occurred_at"]),
    )


__all__ = [
    "SqlAlchemyToolProgressStore",
    "append_attempt_outcome",
    "append_progress_event",
    "append_safe_result",
]
