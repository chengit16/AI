"""定义平台工具版本、治理属性和套餐目录读取端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

ToolAccessMode = Literal["read", "write"]
ToolRiskLevel = Literal["low", "medium", "high", "critical"]
ToolAdapterKind = Literal["internal_read", "synthetic_internal_write"]
ToolCredentialRequirement = Literal["none", "credential_ref"]
ToolRetryMode = Literal["none", "safe_read", "idempotent_write"]
ToolDefinitionStatus = Literal["active", "retired"]


@dataclass(frozen=True)
class ToolDefinition:
    """保存一个内容可验证、不可原地修改的平台工具版本。

    Schema 文档和治理字段共同参与 ``definition_hash``；``created_at`` 只表示
    注册时间，不改变版本语义。凭证字段只描述是否需要 ``credential_ref``，
    任何密文或明文凭证都不属于该事实。
    """

    tool_id: UUID
    tool_version: int
    tool_key: str
    display_name: str
    description: str
    access_mode: ToolAccessMode
    risk_level: ToolRiskLevel
    adapter_kind: ToolAdapterKind
    input_schema_document: dict[str, object]
    input_schema_hash: str
    output_schema_document: dict[str, object]
    output_schema_hash: str
    permission_code: str
    credential_requirement: ToolCredentialRequirement
    timeout_seconds: int
    retry_mode: ToolRetryMode
    status: ToolDefinitionStatus
    definition_hash: str
    synthetic: bool
    created_at: datetime


class ToolCatalogRepository(Protocol):
    """只读平台工具事实；工作空间不能经此端口新增或扩大目录。"""

    def list_active_for_plan(self, plan_code: str) -> tuple[ToolDefinition, ...]: ...

    def get_definition(
        self,
        tool_id: UUID,
        tool_version: int,
    ) -> ToolDefinition | None: ...

    def is_available_for_plan(
        self,
        tool_id: UUID,
        tool_version: int,
        plan_code: str,
    ) -> bool: ...
