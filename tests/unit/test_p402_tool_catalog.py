"""验证 P4-02 工具定义治理与工作空间目录收窄规则。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.common.trace import TraceContext
from ai_platform_api.modules.authorization.infrastructure.static_policy import (
    PolicyGrant,
    StaticPolicyDecisionPoint,
)
from ai_platform_api.modules.identity.domain.entitlements import WorkspacePlanEntitlement
from ai_platform_api.modules.tool_execution.application import (
    ToolCatalogService,
    ToolDefinitionInvalidError,
    ToolExecutionDeniedError,
    ToolVersionNotAvailableError,
    parse_tool_definition,
    verify_tool_definition,
)
from ai_platform_api.modules.tool_execution.domain import ToolDefinition

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000402")
OTHER_WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000499")
ACCOUNT_ID = UUID("10000000-0000-4000-8000-000000000402")
KNOWLEDGE_TOOL_ID = UUID("a7000000-0000-4000-8000-000000000402")
QUOTA_TOOL_ID = UUID("a7000000-0000-4000-8000-000000000403")
CREATED_AT = datetime(2026, 8, 16, tzinfo=UTC)
TRACE = TraceContext("4" * 32, "2" * 16)


class StubToolCatalogRepository:
    """模拟平台目录与套餐映射，工作空间没有写入或追加能力。"""

    def __init__(self, definitions: tuple[ToolDefinition, ...]) -> None:
        self.definitions = definitions

    def list_active_for_plan(self, plan_code: str) -> tuple[ToolDefinition, ...]:
        return self.definitions if plan_code == "personal_local" else ()

    def get_definition(
        self,
        tool_id: UUID,
        tool_version: int,
    ) -> ToolDefinition | None:
        return next(
            (
                item
                for item in self.definitions
                if item.tool_id == tool_id and item.tool_version == tool_version
            ),
            None,
        )

    def is_available_for_plan(
        self,
        tool_id: UUID,
        tool_version: int,
        plan_code: str,
    ) -> bool:
        return (
            plan_code == "personal_local" and self.get_definition(tool_id, tool_version) is not None
        )


class StubWorkspacePlanReader:
    """返回可替换的当前套餐事实，用于验证缺失和停用时失败关闭。"""

    def __init__(self) -> None:
        self.entitlement: WorkspacePlanEntitlement | None = WorkspacePlanEntitlement(
            WORKSPACE_ID,
            "personal_local",
            "active",
        )

    def get_workspace_plan_entitlement(
        self,
        workspace_id: UUID,
    ) -> WorkspacePlanEntitlement | None:
        if self.entitlement is None or self.entitlement.workspace_id != workspace_id:
            return None
        return self.entitlement


def _context(*, workspace_id: UUID = WORKSPACE_ID) -> RequestContext:
    return RequestContext.trusted(
        actor_id=ACCOUNT_ID,
        user_id=ACCOUNT_ID,
        workspace_id=workspace_id,
        trace=TRACE,
        authentication_method="browser_session",
    )


def _schema() -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["query"],
        "properties": {"query": {"type": "string", "minLength": 1}},
    }


def _definition_document(
    *,
    tool_id: UUID = KNOWLEDGE_TOOL_ID,
    tool_key: str = "knowledge.search",
    permission_code: str = "knowledge.document.read",
) -> dict[str, object]:
    return {
        "tool_id": str(tool_id),
        "tool_version": 1,
        "tool_key": tool_key,
        "display_name": "合成工具",
        "description": "仅用于验证工具定义与目录收窄。",
        "access_mode": "read",
        "risk_level": "medium",
        "adapter_kind": "internal_read",
        "input_schema_document": _schema(),
        "output_schema_document": _schema(),
        "permission_code": permission_code,
        "credential_requirement": "none",
        "timeout_seconds": 15,
        "retry_mode": "safe_read",
        "status": "active",
        "synthetic": False,
    }


def _definitions() -> tuple[ToolDefinition, ToolDefinition]:
    knowledge = parse_tool_definition(_definition_document(), created_at=CREATED_AT)
    quota = parse_tool_definition(
        _definition_document(
            tool_id=QUOTA_TOOL_ID,
            tool_key="quota.get_usage",
            permission_code="workspace.entitlement.read",
        ),
        created_at=CREATED_AT,
    )
    return knowledge, quota


def test_definition_hashes_are_reproducible_and_tampering_fails_closed() -> None:
    definition = parse_tool_definition(_definition_document(), created_at=CREATED_AT)

    verify_tool_definition(definition)
    assert definition.input_schema_hash == definition.output_schema_hash
    assert len(definition.definition_hash) == 64
    with pytest.raises(ToolDefinitionInvalidError):
        verify_tool_definition(replace(definition, timeout_seconds=16))


@pytest.mark.parametrize(
    "mutation",
    ["unknown_field", "arbitrary_adapter", "open_schema", "wrong_dialect", "write_mismatch"],
)
def test_unknown_or_unsafe_governance_fields_are_rejected(mutation: str) -> None:
    document = _definition_document()
    if mutation == "unknown_field":
        document["base_url"] = "https://synthetic.invalid"
    elif mutation == "arbitrary_adapter":
        document["adapter_kind"] = "http"
    elif mutation == "open_schema":
        document["input_schema_document"] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
        }
    elif mutation == "wrong_dialect":
        input_schema = _schema()
        input_schema["$schema"] = "http://json-schema.org/draft-07/schema#"
        document["input_schema_document"] = input_schema
    else:
        document["access_mode"] = "write"

    with pytest.raises(ToolDefinitionInvalidError):
        parse_tool_definition(document, created_at=CREATED_AT)


def test_catalog_is_platform_plan_and_current_permission_intersection() -> None:
    definitions = _definitions()
    entitlements = StubWorkspacePlanReader()
    policy = StaticPolicyDecisionPoint(
        {
            (ACCOUNT_ID, WORKSPACE_ID): PolicyGrant(
                permissions=frozenset({"knowledge.document.read"})
            )
        }
    )
    service = ToolCatalogService(
        StubToolCatalogRepository(definitions),
        entitlements,
        policy,
    )

    result = service.list_available_tools(_context(), workspace_id=WORKSPACE_ID)

    assert [item.tool_key for item in result] == ["knowledge.search"]
    assert (
        service.require_available_tool(
            _context(),
            workspace_id=WORKSPACE_ID,
            tool_id=KNOWLEDGE_TOOL_ID,
            tool_version=1,
        )
        == definitions[0]
    )
    assert not hasattr(service, "execute")
    assert not hasattr(service, "register")


def test_catalog_cross_workspace_inactive_plan_and_unknown_version_fail_closed() -> None:
    definitions = _definitions()
    entitlements = StubWorkspacePlanReader()
    service = ToolCatalogService(
        StubToolCatalogRepository(definitions),
        entitlements,
        StaticPolicyDecisionPoint({}),
    )

    with pytest.raises(ToolExecutionDeniedError):
        service.list_available_tools(_context(), workspace_id=OTHER_WORKSPACE_ID)
    current_entitlement = entitlements.entitlement
    assert current_entitlement is not None
    entitlements.entitlement = replace(current_entitlement, workspace_status="suspended")
    with pytest.raises(ToolExecutionDeniedError):
        service.list_available_tools(_context(), workspace_id=WORKSPACE_ID)
    current_entitlement = entitlements.entitlement
    assert current_entitlement is not None
    entitlements.entitlement = replace(current_entitlement, workspace_status="active")
    with pytest.raises(ToolVersionNotAvailableError):
        service.require_available_tool(
            _context(),
            workspace_id=WORKSPACE_ID,
            tool_id=UUID("a7000000-0000-4000-8000-000000000499"),
            tool_version=1,
        )


def test_repository_cannot_smuggle_retired_or_unknown_permission_definition() -> None:
    knowledge, _ = _definitions()
    entitlements = StubWorkspacePlanReader()
    policy = StaticPolicyDecisionPoint(
        {
            (ACCOUNT_ID, WORKSPACE_ID): PolicyGrant(
                permissions=frozenset({"knowledge.document.read"})
            )
        }
    )

    retired_service = ToolCatalogService(
        StubToolCatalogRepository((replace(knowledge, status="retired"),)),
        entitlements,
        policy,
    )
    with pytest.raises(ToolDefinitionInvalidError):
        retired_service.list_available_tools(_context(), workspace_id=WORKSPACE_ID)

    unknown_permission = parse_tool_definition(
        _definition_document(permission_code="unknown.resource.read"),
        created_at=CREATED_AT,
    )
    unknown_service = ToolCatalogService(
        StubToolCatalogRepository((unknown_permission,)),
        entitlements,
        StaticPolicyDecisionPoint(
            {
                (ACCOUNT_ID, WORKSPACE_ID): PolicyGrant(
                    permissions=frozenset({"unknown.resource.read"})
                )
            }
        ),
    )
    with pytest.raises(ToolDefinitionInvalidError):
        unknown_service.list_available_tools(_context(), workspace_id=WORKSPACE_ID)
