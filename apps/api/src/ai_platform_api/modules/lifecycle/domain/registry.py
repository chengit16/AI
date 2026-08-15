"""加载并校验工作空间表生命周期分类契约。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

TableClassification = Literal["business", "governance", "retained", "lifecycle"]
CLASSIFICATIONS = frozenset({"business", "governance", "retained", "lifecycle"})


@dataclass(frozen=True)
class WorkspaceTablePolicy:
    """声明一张直接含工作空间列的表如何导出和清除。"""

    table: str
    classification: TableClassification
    export: bool
    purge: bool
    excluded_columns: frozenset[str]


@dataclass(frozen=True)
class DependentTablePolicy:
    """声明通过父表归属到工作空间的无直接空间列子表。"""

    table: str
    parent_table: str
    local_column: str
    parent_column: str
    export: bool
    purge: bool
    excluded_columns: frozenset[str]


@dataclass(frozen=True)
class WorkspaceTableRegistry:
    """集中保存版本化表分类，防止新增事实静默漏出治理范围。"""

    schema_version: int
    registry_version: int
    tables: tuple[WorkspaceTablePolicy, ...]
    dependent_tables: tuple[DependentTablePolicy, ...]

    def export_tables(self) -> tuple[WorkspaceTablePolicy, ...]:
        return tuple(item for item in self.tables if item.export)

    def purge_tables(self) -> tuple[WorkspaceTablePolicy, ...]:
        return tuple(item for item in self.tables if item.purge)


def load_workspace_table_registry(path: Path) -> WorkspaceTableRegistry:
    """从冻结 JSON 读取严格且无重复的生命周期分类。"""

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("工作空间表注册表根节点必须是对象")
    schema_version = _positive_int(document.get("schema_version"), "schema_version")
    registry_version = _positive_int(document.get("registry_version"), "registry_version")
    raw_tables = document.get("tables")
    raw_dependent = document.get("dependent_tables")
    if not isinstance(raw_tables, list) or not isinstance(raw_dependent, list):
        raise ValueError("工作空间表注册表必须包含 tables 与 dependent_tables 数组")
    tables = tuple(_table_policy(item) for item in raw_tables)
    dependent = tuple(_dependent_policy(item) for item in raw_dependent)
    names = [item.table for item in tables] + [item.table for item in dependent]
    if len(names) != len(set(names)):
        raise ValueError("工作空间表注册表存在重复表名")
    direct_names = {item.table for item in tables}
    if any(item.parent_table not in direct_names for item in dependent):
        raise ValueError("依赖表必须引用已登记的直接工作空间表")
    if any(item.classification != "business" and item.purge for item in tables):
        raise ValueError("只有 business 分类允许参与普通业务数据清除")
    return WorkspaceTableRegistry(schema_version, registry_version, tables, dependent)


def _table_policy(value: object) -> WorkspaceTablePolicy:
    if not isinstance(value, dict):
        raise ValueError("表策略必须是对象")
    classification = value.get("classification")
    if classification not in CLASSIFICATIONS:
        raise ValueError("表策略包含未知 classification")
    return WorkspaceTablePolicy(
        table=_identifier(value.get("table")),
        classification=cast(TableClassification, classification),
        export=_boolean(value.get("export"), "export"),
        purge=_boolean(value.get("purge"), "purge"),
        excluded_columns=_columns(value.get("excluded_columns")),
    )


def _dependent_policy(value: object) -> DependentTablePolicy:
    if not isinstance(value, dict):
        raise ValueError("依赖表策略必须是对象")
    return DependentTablePolicy(
        table=_identifier(value.get("table")),
        parent_table=_identifier(value.get("parent_table")),
        local_column=_identifier(value.get("local_column")),
        parent_column=_identifier(value.get("parent_column")),
        export=_boolean(value.get("export"), "export"),
        purge=_boolean(value.get("purge"), "purge"),
        excluded_columns=_columns(value.get("excluded_columns")),
    )


def _positive_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field} 必须是正整数")
    return value


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not value or not value.replace("_", "a").isalnum():
        raise ValueError("表名和列名必须是安全标识符")
    return value


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} 必须是布尔值")
    return value


def _columns(value: object) -> frozenset[str]:
    if not isinstance(value, list):
        raise ValueError("excluded_columns 必须是数组")
    columns = frozenset(_identifier(item) for item in value)
    if len(columns) != len(value):
        raise ValueError("excluded_columns 不允许重复")
    return columns
