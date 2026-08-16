"""从不可变 PostgreSQL 工具版本和套餐映射生成目录投影。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from ai_platform_api.modules.tool_execution.domain.catalog import (
    ToolAccessMode,
    ToolAdapterKind,
    ToolCredentialRequirement,
    ToolDefinition,
    ToolDefinitionStatus,
    ToolRetryMode,
    ToolRiskLevel,
)
from ai_platform_api.persistence.tables import agent_tool_definitions, tool_plan_availability

SessionFactory = Callable[[], Session]


class SqlAlchemyToolCatalogRepository:
    """只读取最新注册版本；旧版本仍保留给不可变 Release 精确追溯。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def list_active_for_plan(self, plan_code: str) -> tuple[ToolDefinition, ...]:
        """返回套餐显式映射的每个工具最新活动版本，未知套餐自然返回空集。"""

        latest_versions = (
            select(
                agent_tool_definitions.c.tool_key,
                func.max(agent_tool_definitions.c.tool_version).label("tool_version"),
            )
            .group_by(agent_tool_definitions.c.tool_key)
            .subquery("latest_tool_versions")
        )
        statement = (
            select(agent_tool_definitions)
            .join(
                latest_versions,
                (latest_versions.c.tool_key == agent_tool_definitions.c.tool_key)
                & (latest_versions.c.tool_version == agent_tool_definitions.c.tool_version),
            )
            .join(
                tool_plan_availability,
                (tool_plan_availability.c.tool_id == agent_tool_definitions.c.tool_id)
                & (tool_plan_availability.c.tool_version == agent_tool_definitions.c.tool_version),
            )
            .where(
                tool_plan_availability.c.plan_code == plan_code,
                agent_tool_definitions.c.status == "active",
            )
            .order_by(agent_tool_definitions.c.tool_key)
        )
        with self._session_factory() as session:
            rows = session.execute(statement).mappings().all()
        return tuple(_definition(row) for row in rows)

    def get_definition(
        self,
        tool_id: UUID,
        tool_version: int,
    ) -> ToolDefinition | None:
        """按稳定身份读取精确版本，不在 Repository 内替调用方解释可用性。"""

        with self._session_factory() as session:
            row = (
                session.execute(
                    select(agent_tool_definitions).where(
                        agent_tool_definitions.c.tool_id == tool_id,
                        agent_tool_definitions.c.tool_version == tool_version,
                    )
                )
                .mappings()
                .one_or_none()
            )
        return _definition(row) if row is not None else None

    def is_available_for_plan(
        self,
        tool_id: UUID,
        tool_version: int,
        plan_code: str,
    ) -> bool:
        """验证精确版本的套餐映射，供历史 Release 继续使用冻结版本。"""

        statement = select(tool_plan_availability.c.tool_id).where(
            tool_plan_availability.c.tool_id == tool_id,
            tool_plan_availability.c.tool_version == tool_version,
            tool_plan_availability.c.plan_code == plan_code,
        )
        with self._session_factory() as session:
            return session.execute(statement).one_or_none() is not None


def _definition(row: RowMapping) -> ToolDefinition:
    """把数据库行投影为强类型事实，内容完整性由应用层统一复算。"""

    return ToolDefinition(
        tool_id=cast(UUID, row["tool_id"]),
        tool_version=cast(int, row["tool_version"]),
        tool_key=cast(str, row["tool_key"]),
        display_name=cast(str, row["display_name"]),
        description=cast(str, row["description"]),
        access_mode=cast(ToolAccessMode, row["access_mode"]),
        risk_level=cast(ToolRiskLevel, row["risk_level"]),
        adapter_kind=cast(ToolAdapterKind, row["adapter_kind"]),
        input_schema_document=cast(dict[str, object], row["input_schema_document"]),
        input_schema_hash=cast(str, row["input_schema_hash"]),
        output_schema_document=cast(dict[str, object], row["output_schema_document"]),
        output_schema_hash=cast(str, row["output_schema_hash"]),
        permission_code=cast(str, row["permission_code"]),
        credential_requirement=cast(
            ToolCredentialRequirement,
            row["credential_requirement"],
        ),
        timeout_seconds=cast(int, row["timeout_seconds"]),
        retry_mode=cast(ToolRetryMode, row["retry_mode"]),
        status=cast(ToolDefinitionStatus, row["status"]),
        definition_hash=cast(str, row["definition_hash"]),
        synthetic=cast(bool, row["synthetic"]),
        created_at=cast(datetime, row["created_at"]),
    )
