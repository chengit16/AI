"""提供工具控制台所需的脱敏历史投影与动作范围校验。"""

from __future__ import annotations

from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.tool_execution.application.errors import ToolExecutionDeniedError
from ai_platform_api.modules.tool_execution.domain.console import (
    ToolAttemptView,
    ToolConfirmationView,
    ToolConsoleStore,
    ToolRunDetail,
    ToolRunSummary,
    ToolStepView,
)


class ToolConsoleService:
    """执行浏览器身份、权限码和资源范围复核后返回脱敏投影。"""

    def __init__(self, store: ToolConsoleStore) -> None:
        self._store = store

    def list_runs(self, context: RequestContext, *, limit: int) -> tuple[ToolRunSummary, ...]:
        """列出当前授权范围内的 Run；资源级授权不会扩大为整个工作空间。"""

        account_id = _require_browser_permission(context, "tool.run.read")
        if not 1 <= limit <= 200:
            raise ToolExecutionDeniedError
        return self._store.list_runs(
            workspace_id=context.workspace_id,
            account_id=account_id,
            workspace_scope=context.authorized_workspace,
            resource_ids=context.authorized_resource_ids,
            limit=limit,
        )

    def get_run(self, context: RequestContext, *, run_id: UUID) -> ToolRunDetail:
        """读取一个 Run；跨空间、不存在和超出资源范围统一拒绝。"""

        account_id = _require_browser_permission(context, "tool.run.read")
        detail = self._store.get_run(
            workspace_id=context.workspace_id,
            account_id=account_id,
            run_id=run_id,
        )
        if detail is None or not _resource_allowed(context, run_id):
            raise ToolExecutionDeniedError
        return detail

    def get_action_run(
        self,
        context: RequestContext,
        *,
        run_id: UUID,
        permission_code: str,
        target_id: UUID | None = None,
    ) -> ToolRunDetail:
        """为取消或确认动作解析 Run，并复核路由授权的精确资源范围。"""

        account_id = _require_browser_permission(context, permission_code)
        detail = self._store.get_run(
            workspace_id=context.workspace_id,
            account_id=account_id,
            run_id=run_id,
        )
        if detail is None or not _resource_allowed(context, target_id or run_id):
            raise ToolExecutionDeniedError
        return detail


def _require_browser_permission(context: RequestContext, permission_code: str) -> UUID:
    if (
        context.authentication_method != "browser_session"
        or context.user_id is None
        or context.actor_id != context.user_id
        or context.authorized_permission_code != permission_code
    ):
        raise ToolExecutionDeniedError
    return context.user_id


def _resource_allowed(context: RequestContext, resource_id: UUID) -> bool:
    return context.authorized_workspace or resource_id in context.authorized_resource_ids


__all__ = [
    "ToolAttemptView",
    "ToolConfirmationView",
    "ToolConsoleService",
    "ToolConsoleStore",
    "ToolRunDetail",
    "ToolRunSummary",
    "ToolStepView",
]
