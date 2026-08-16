"""从工具任务事实生成控制台所需的最小脱敏投影。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from ai_platform_api.modules.tool_execution.domain.confirmations import ToolConfirmationMode
from ai_platform_api.modules.tool_execution.domain.console import (
    ToolAttemptView,
    ToolConfirmationView,
    ToolConsoleStore,
    ToolRunDetail,
    ToolRunSummary,
    ToolStepView,
)
from ai_platform_api.modules.tool_execution.domain.tasks import ToolRunBudget
from ai_platform_api.persistence.tables import (
    agent_releases,
    agent_tool_definitions,
    approval_assignments,
    services,
    tool_attempts,
    tool_calls,
    tool_confirmation_invalidations,
    tool_confirmations,
    tool_progress_events,
    tool_runs,
    tool_safe_results,
    tool_steps,
    tool_usage_records,
)

SessionFactory = Callable[[], Session]


class SqlAlchemyToolConsoleStore(ToolConsoleStore):
    """使用短只读事务拼装 Run、Step、确认与 Attempt 投影。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def list_runs(
        self,
        *,
        workspace_id: UUID,
        account_id: UUID,
        workspace_scope: bool,
        resource_ids: frozenset[UUID],
        limit: int,
    ) -> tuple[ToolRunSummary, ...]:
        """按创建时间倒序列出授权 Run；资源级列表为空时返回空集。"""

        del account_id  # 账号只参与确认动作，读取范围完全服从 PDP 决策结果。
        if not workspace_scope and not resource_ids:
            return ()
        statement = _summary_statement().where(tool_runs.c.workspace_id == workspace_id)
        if not workspace_scope:
            statement = statement.where(tool_runs.c.run_id.in_(resource_ids))
        with self._session_factory() as session:
            rows = (
                session.execute(statement.order_by(tool_runs.c.created_at.desc()).limit(limit))
                .mappings()
                .all()
            )
        return tuple(_summary(row) for row in rows)

    def get_run(
        self,
        *,
        workspace_id: UUID,
        account_id: UUID,
        run_id: UUID,
    ) -> ToolRunDetail | None:
        """在同一只读事务中读取详情，避免 Router 拼接不同时间点的私有表。"""

        with self._session_factory() as session, session.begin():
            # 1. 先按工作空间和 Run 锁定可见主事实，不存在与跨空间都不继续读取子表。
            run_row = (
                session.execute(
                    _summary_statement().where(
                        tool_runs.c.workspace_id == workspace_id,
                        tool_runs.c.run_id == run_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if run_row is None:
                return None
            # 2. 在同一快照内读取步骤、确认、尝试和最新游标，避免控制台展示混合时点状态。
            step_rows = (
                session.execute(
                    select(
                        tool_steps,
                        agent_tool_definitions.c.tool_key,
                        agent_tool_definitions.c.display_name,
                        agent_tool_definitions.c.access_mode,
                        agent_tool_definitions.c.risk_level,
                    )
                    .join(
                        agent_tool_definitions,
                        (agent_tool_definitions.c.tool_id == tool_steps.c.tool_id)
                        & (agent_tool_definitions.c.tool_version == tool_steps.c.tool_version),
                    )
                    .where(
                        tool_steps.c.workspace_id == workspace_id,
                        tool_steps.c.run_id == run_id,
                    )
                    .order_by(tool_steps.c.sequence_no)
                )
                .mappings()
                .all()
            )
            confirmations = _confirmations(session, workspace_id, run_id, account_id)
            attempts = _attempts(session, workspace_id, run_id)
            latest_cursor = int(
                session.scalar(
                    select(func.coalesce(func.max(tool_progress_events.c.cursor), 0)).where(
                        tool_progress_events.c.workspace_id == workspace_id,
                        tool_progress_events.c.run_id == run_id,
                    )
                )
                or 0
            )
        # 3. 事务外只组装已脱敏值对象，ORM Row 和 Session 不逃逸到 Application 或 API。
        return ToolRunDetail(
            run=_summary(run_row),
            steps=tuple(
                _step(
                    row,
                    confirmations.get(cast(UUID, row["step_id"])),
                    attempts.get(cast(UUID, row["step_id"]), ()),
                )
                for row in step_rows
            ),
            latest_cursor=latest_cursor,
        )


def _summary_statement() -> Select[tuple[Any, ...]]:
    """构造不复制参数正文的 Run 聚合查询。"""

    step_count = (
        select(func.count())
        .select_from(tool_steps)
        .where(tool_steps.c.run_id == tool_runs.c.run_id)
        .correlate(tool_runs)
        .scalar_subquery()
    )
    completed_steps = (
        select(func.count())
        .select_from(tool_steps)
        .where(tool_steps.c.run_id == tool_runs.c.run_id, tool_steps.c.state == "completed")
        .correlate(tool_runs)
        .scalar_subquery()
    )
    total_cost = (
        select(func.coalesce(func.sum(tool_usage_records.c.cost_microunits), 0))
        .where(tool_usage_records.c.run_id == tool_runs.c.run_id)
        .correlate(tool_runs)
        .scalar_subquery()
    )
    pending_confirmations = (
        select(func.count())
        .select_from(tool_confirmations)
        .where(
            tool_confirmations.c.run_id == tool_runs.c.run_id,
            tool_confirmations.c.state == "pending",
        )
        .correlate(tool_runs)
        .scalar_subquery()
    )
    return (
        select(
            tool_runs,
            services.c.name.label("service_name"),
            agent_releases.c.version.label("agent_release_version"),
            step_count.label("step_count"),
            completed_steps.label("completed_step_count"),
            total_cost.label("total_cost_microunits"),
            pending_confirmations.label("pending_confirmation_count"),
        )
        .join(
            services,
            (services.c.service_id == tool_runs.c.service_id)
            & (services.c.workspace_id == tool_runs.c.workspace_id),
        )
        .join(
            agent_releases,
            (agent_releases.c.release_id == tool_runs.c.agent_release_id)
            & (agent_releases.c.workspace_id == tool_runs.c.workspace_id),
        )
    )


def _confirmations(
    session: Session,
    workspace_id: UUID,
    run_id: UUID,
    account_id: UUID,
) -> dict[UUID, ToolConfirmationView]:
    pending_assignment = (
        select(approval_assignments.c.approval_assignment_id)
        .where(
            approval_assignments.c.approval_instance_id
            == tool_confirmations.c.approval_instance_id,
            approval_assignments.c.approver_account_id == account_id,
            approval_assignments.c.status == "pending",
        )
        .exists()
    )
    rows = (
        session.execute(
            select(
                tool_confirmations,
                tool_confirmation_invalidations.c.state.label("invalidation_state"),
                pending_assignment.label("can_respond"),
            )
            .outerjoin(
                tool_confirmation_invalidations,
                tool_confirmation_invalidations.c.confirmation_id
                == tool_confirmations.c.confirmation_id,
            )
            .where(
                tool_confirmations.c.workspace_id == workspace_id,
                tool_confirmations.c.run_id == run_id,
            )
        )
        .mappings()
        .all()
    )
    return {
        cast(UUID, row["step_id"]): ToolConfirmationView(
            confirmation_id=cast(UUID, row["confirmation_id"]),
            approval_instance_id=cast(UUID, row["approval_instance_id"]),
            mode=cast(ToolConfirmationMode, row["mode"]),
            state=cast(str, row["invalidation_state"] or row["state"]),
            risk_level=cast(str, row["risk_level"]),
            confirmation_hash=cast(str, row["confirmation_hash"]),
            expires_at=row["expires_at"],
            resolved_at=row["resolved_at"],
            can_respond=bool(row["can_respond"]),
            version=cast(int, row["version"]),
        )
        for row in rows
    }


def _attempts(
    session: Session,
    workspace_id: UUID,
    run_id: UUID,
) -> dict[UUID, tuple[ToolAttemptView, ...]]:
    # 1. 一次查询关联 Attempt、Call、用量和安全结果，仅选择控制台允许展示的最小字段。
    rows = (
        session.execute(
            select(
                tool_attempts,
                tool_calls.c.tool_call_id,
                tool_calls.c.state.label("call_state"),
                tool_calls.c.error_code.label("call_error_code"),
                tool_usage_records.c.duration_ms,
                tool_usage_records.c.result_size_bytes,
                tool_usage_records.c.cost_microunits,
                tool_safe_results.c.status.label("result_status"),
            )
            .join(tool_calls, tool_calls.c.attempt_id == tool_attempts.c.attempt_id)
            .outerjoin(
                tool_usage_records,
                tool_usage_records.c.attempt_id == tool_attempts.c.attempt_id,
            )
            .outerjoin(
                tool_safe_results,
                tool_safe_results.c.tool_call_id == tool_calls.c.tool_call_id,
            )
            .where(
                tool_attempts.c.workspace_id == workspace_id,
                tool_attempts.c.run_id == run_id,
            )
            .order_by(
                tool_attempts.c.step_id,
                tool_attempts.c.recovery_generation,
                tool_attempts.c.attempt_no,
            )
        )
        .mappings()
        .all()
    )
    # 2. 按 Step 聚合并保留恢复代际与尝试序号顺序，失败详情不包含 Worker 或结果正文。
    grouped: dict[UUID, list[ToolAttemptView]] = {}
    for row in rows:
        grouped.setdefault(cast(UUID, row["step_id"]), []).append(
            ToolAttemptView(
                attempt_id=cast(UUID, row["attempt_id"]),
                tool_call_id=cast(UUID, row["tool_call_id"]),
                attempt_no=cast(int, row["attempt_no"]),
                recovery_generation=cast(int, row["recovery_generation"]),
                trigger=cast(str, row["trigger"]),
                state=cast(str, row["state"]),
                call_state=cast(str, row["call_state"]),
                error_code=cast(str | None, row["call_error_code"] or row["error_code"]),
                duration_ms=cast(int | None, row["duration_ms"]),
                result_size_bytes=cast(int | None, row["result_size_bytes"]),
                cost_microunits=cast(int | None, row["cost_microunits"]),
                result_status=cast(str | None, row["result_status"]),
                started_at=row["started_at"],
                completed_at=row["completed_at"],
            )
        )
    return {step_id: tuple(items) for step_id, items in grouped.items()}


def _summary(row: RowMapping) -> ToolRunSummary:
    return ToolRunSummary(
        run_id=cast(UUID, row["run_id"]),
        service_id=cast(UUID, row["service_id"]),
        service_name=cast(str, row["service_name"]),
        agent_release_id=cast(UUID, row["agent_release_id"]),
        agent_release_version=cast(int, row["agent_release_version"]),
        state=cast(str, row["state"]),
        budget=ToolRunBudget(
            max_steps=cast(int, row["max_steps"]),
            max_attempts_per_step=cast(int, row["max_attempts_per_step"]),
            max_execution_seconds=cast(int, row["max_execution_seconds"]),
            max_cost_microunits=cast(int, row["max_cost_microunits"]),
        ),
        requested_by_account_id=cast(UUID, row["requested_by_account_id"]),
        step_count=int(row["step_count"]),
        completed_step_count=int(row["completed_step_count"]),
        total_cost_microunits=int(row["total_cost_microunits"]),
        pending_confirmation_count=int(row["pending_confirmation_count"]),
        cancel_requested_at=row["cancel_requested_at"],
        deadline_at=row["deadline_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        version=cast(int, row["version"]),
    )


def _step(
    row: RowMapping,
    confirmation: ToolConfirmationView | None,
    attempts: tuple[ToolAttemptView, ...],
) -> ToolStepView:
    return ToolStepView(
        step_id=cast(UUID, row["step_id"]),
        sequence_no=cast(int, row["sequence_no"]),
        tool_id=cast(UUID, row["tool_id"]),
        tool_version=cast(int, row["tool_version"]),
        tool_key=cast(str, row["tool_key"]),
        display_name=cast(str, row["display_name"]),
        access_mode=cast(str, row["access_mode"]),
        risk_level=cast(str, row["risk_level"]),
        canonical_arguments_hash=cast(str, row["canonical_arguments_hash"]),
        state=cast(str, row["state"]),
        timeout_seconds=cast(int, row["timeout_seconds"]),
        max_attempts=cast(int, row["max_attempts"]),
        max_result_bytes=cast(int, row["max_result_bytes"]),
        current_attempt_no=cast(int | None, row["current_attempt_no"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        confirmation=confirmation,
        attempts=attempts,
    )


__all__ = ["SqlAlchemyToolConsoleStore"]
