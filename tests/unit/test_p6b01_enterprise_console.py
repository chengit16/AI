"""验证 P6B-01 企业控制台应用服务的授权失败关闭边界。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.identity.application.enterprise import (
    EnterpriseWorkspaceService,
    WorkspaceGovernanceDeniedError,
)
from ai_platform_api.modules.identity.domain.enterprise import (
    EnterpriseConsoleSnapshot,
    EnterpriseConsoleStatistics,
    EnterpriseUnitOfWork,
    WorkspaceMembership,
    WorkspaceRecord,
)

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000910")
OTHER_WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000911")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000910")
NOW = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)


class EnterpriseConsoleRepositoryScenario:
    """提供企业控制台所需的最小空间、成员和聚合事实。"""

    def __init__(self) -> None:
        self.workspace = WorkspaceRecord(WORKSPACE_ID, "enterprise", "合成企业", "active")
        self.membership_status = "active"
        self.arguments: dict[str, object] = {}

    def get_workspace(
        self, workspace_id: UUID, *, for_update: bool = False
    ) -> WorkspaceRecord | None:
        del for_update
        return self.workspace if workspace_id == WORKSPACE_ID else None

    def get_membership(
        self,
        workspace_id: UUID,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkspaceMembership | None:
        del for_update
        if workspace_id != WORKSPACE_ID or account_id != ACCOUNT_ID:
            return None
        return WorkspaceMembership(
            UUID("30000000-0000-4000-8000-000000000910"),
            workspace_id,
            account_id,
            "owner",
            cast(Any, self.membership_status),
            NOW,
            NOW,
            1,
        )

    def get_console_snapshot(
        self, workspace_id: UUID, **arguments: object
    ) -> EnterpriseConsoleSnapshot:
        assert workspace_id == WORKSPACE_ID
        self.arguments = arguments
        return EnterpriseConsoleSnapshot(
            workspace=self.workspace,
            statistics=EnterpriseConsoleStatistics(1, 0, 0, 0, 0, 0, 0, 0),
            trend=(),
            recent_documents=(),
            generated_at=cast(datetime, arguments["generated_at"]),
            time_window_start=NOW,
            time_window_end=NOW,
            consistency="eventually_consistent",
            profile_description=None,
            profile_logo_url=None,
        )


class EnterpriseConsoleUnitOfWorkScenario:
    """隔离数据库，只记录控制台 Repository 调用参数。"""

    def __init__(self) -> None:
        self.enterprise = EnterpriseConsoleRepositoryScenario()
        self.audit = cast(Any, None)
        self.outbox = cast(Any, None)

    def __enter__(self) -> EnterpriseConsoleUnitOfWorkScenario:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def commit(self) -> None:
        raise AssertionError("企业控制台只读查询不应提交事务")


def context() -> RequestContext:
    """构造已通过 PDP 的企业空间浏览器上下文。"""

    return replace(
        RequestContext.trusted(
            actor_id=ACCOUNT_ID,
            user_id=ACCOUNT_ID,
            workspace_id=WORKSPACE_ID,
            authentication_method="browser_session",
            trace=TraceContext("a" * 32, "b" * 16),
        ),
        authorized_permission_code="workspace.overview.access",
        authorized_workspace=True,
        authorized_maximum_security_level="CONFIDENTIAL",
    )


def service(scenario: EnterpriseConsoleUnitOfWorkScenario) -> EnterpriseWorkspaceService:
    return EnterpriseWorkspaceService(cast(EnterpriseUnitOfWork, scenario))


def test_enterprise_console_passes_clearance_and_field_mask_to_repository() -> None:
    """服务端必须把密级上限下传，并在标题受限时彻底关闭最近内容查询。"""

    scenario = EnterpriseConsoleUnitOfWorkScenario()
    masked = replace(context(), authorized_field_mask=frozenset({"document.title"}))

    snapshot = service(scenario).get_console_snapshot(
        masked,
        workspace_id=WORKSPACE_ID,
        trend_months=7,
        recent_limit=12,
    )

    assert snapshot.consistency == "eventually_consistent"
    assert scenario.enterprise.arguments["maximum_security_level"] == "CONFIDENTIAL"
    assert scenario.enterprise.arguments["include_recent_documents"] is False
    assert scenario.enterprise.arguments["trend_months"] == 7
    assert scenario.enterprise.arguments["recent_limit"] == 12


@pytest.mark.parametrize(
    "invalid_context",
    [
        replace(context(), authentication_method="open_api_key"),
        replace(context(), workspace_id=OTHER_WORKSPACE_ID),
        replace(context(), authorized_permission_code=None),
        replace(context(), authorized_permission_code="workspace.member.read"),
        replace(context(), authorized_workspace=False),
    ],
)
def test_enterprise_console_rejects_untrusted_or_narrowed_context(
    invalid_context: RequestContext,
) -> None:
    """非浏览器、跨空间、未授权或非全空间策略均不得读取企业聚合。"""

    scenario = EnterpriseConsoleUnitOfWorkScenario()
    with pytest.raises(WorkspaceGovernanceDeniedError):
        service(scenario).get_console_snapshot(invalid_context, workspace_id=WORKSPACE_ID)
    assert scenario.enterprise.arguments == {}


def test_enterprise_console_rejects_personal_space_and_inactive_member() -> None:
    """个人空间和已停用成员即使保留旧 PDP 决策也必须在事务内再次拒绝。"""

    personal = EnterpriseConsoleUnitOfWorkScenario()
    personal.enterprise.workspace = WorkspaceRecord(WORKSPACE_ID, "personal", "合成个人", "active")
    with pytest.raises(WorkspaceGovernanceDeniedError):
        service(personal).get_console_snapshot(context(), workspace_id=WORKSPACE_ID)

    inactive = EnterpriseConsoleUnitOfWorkScenario()
    inactive.enterprise.membership_status = "disabled"
    with pytest.raises(WorkspaceGovernanceDeniedError):
        service(inactive).get_console_snapshot(context(), workspace_id=WORKSPACE_ID)


def test_enterprise_console_validates_query_window_before_repository_call() -> None:
    """应用直调与 HTTP Query 必须共享趋势和最近内容数量边界。"""

    scenario = EnterpriseConsoleUnitOfWorkScenario()
    with pytest.raises(Exception) as error:
        service(scenario).get_console_snapshot(
            context(),
            workspace_id=WORKSPACE_ID,
            trend_months=13,
            recent_limit=51,
        )
    assert getattr(error.value, "error_code", None) == "VALIDATION_ERROR"
    assert scenario.enterprise.arguments == {}
