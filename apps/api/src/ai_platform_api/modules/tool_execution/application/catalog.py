"""按平台注册、工作空间套餐与当前策略交集生成可用工具目录。"""

from __future__ import annotations

from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.authorization.domain.policy import (
    PolicyDecisionPoint,
    PolicyRequest,
    ResourceReference,
)
from ai_platform_api.modules.identity.domain.entitlements import WorkspacePlanEntitlementReader
from ai_platform_api.modules.tool_execution.application.definitions import verify_tool_definition
from ai_platform_api.modules.tool_execution.application.errors import (
    ToolDefinitionInvalidError,
    ToolExecutionDeniedError,
    ToolVersionNotAvailableError,
)
from ai_platform_api.modules.tool_execution.domain.catalog import (
    ToolCatalogRepository,
    ToolDefinition,
)

PERMISSION_RESOURCE_TYPES = {
    "knowledge.document.read": "document",
    "workflow.run.read": "workflow_instance",
    "approval.instance.read": "approval_instance",
    "workspace.entitlement.read": "workspace_entitlement",
}


class ToolCatalogService:
    """提供工作空间可见目录，不暴露注册写口或任意执行入口。"""

    def __init__(
        self,
        repository: ToolCatalogRepository,
        entitlements: WorkspacePlanEntitlementReader,
        policy: PolicyDecisionPoint,
    ) -> None:
        self._repository = repository
        self._entitlements = entitlements
        self._policy = policy

    def list_available_tools(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
    ) -> tuple[ToolDefinition, ...]:
        """返回平台注册、套餐和当前权限三层交集内的最新活动版本。"""

        plan_code = self._require_active_plan(context, workspace_id)
        definitions = self._repository.list_active_for_plan(plan_code)
        available: list[ToolDefinition] = []
        for definition in definitions:
            verify_tool_definition(definition)
            if definition.status != "active":
                raise ToolDefinitionInvalidError("活动目录返回了非活动工具版本")
            if self._is_currently_allowed(context, workspace_id, definition):
                available.append(definition)
        return tuple(available)

    def require_available_tool(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        tool_id: UUID,
        tool_version: int,
    ) -> ToolDefinition:
        """解析一个精确版本，并证明套餐与当前权限都没有收窄该版本。"""

        plan_code = self._require_active_plan(context, workspace_id)
        definition = self._repository.get_definition(tool_id, tool_version)
        if definition is None:
            raise ToolVersionNotAvailableError
        verify_tool_definition(definition)
        if definition.status != "active" or not self._repository.is_available_for_plan(
            tool_id,
            tool_version,
            plan_code,
        ):
            raise ToolVersionNotAvailableError
        if not self._is_currently_allowed(context, workspace_id, definition):
            raise ToolExecutionDeniedError
        return definition

    def _require_active_plan(self, context: RequestContext, workspace_id: UUID) -> str:
        """套餐事实缺失、停用或跨空间时统一失败，不能退化为全平台目录。"""

        if context.workspace_id != workspace_id:
            raise ToolExecutionDeniedError
        entitlement = self._entitlements.get_workspace_plan_entitlement(workspace_id)
        if (
            entitlement is None
            or entitlement.workspace_id != workspace_id
            or entitlement.workspace_status != "active"
        ):
            raise ToolExecutionDeniedError
        return entitlement.plan_code

    def _is_currently_allowed(
        self,
        context: RequestContext,
        workspace_id: UUID,
        definition: ToolDefinition,
    ) -> bool:
        """逐工具执行 PDP，拒绝未知权限到资源类型的隐式推导。"""

        resource_type = PERMISSION_RESOURCE_TYPES.get(definition.permission_code)
        if resource_type is None:
            raise ToolDefinitionInvalidError("工具权限没有登记稳定资源类型")
        decision = self._policy.decide(
            PolicyRequest(
                context=context,
                permission_code=definition.permission_code,
                resource=ResourceReference(
                    resource_type=resource_type,
                    resource_id=workspace_id,
                    workspace_id=workspace_id,
                    attributes={"risk_level": definition.risk_level},
                ),
                surface="api",
            )
        )
        return decision.allowed
