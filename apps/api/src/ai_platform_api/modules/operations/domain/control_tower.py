"""定义质量、成本、隔离和合规控制台的只读快照边界。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

ControlTowerStatus = Literal["passed", "blocked", "not_run", "not_configured", "failed"]
ControlTowerSectionKey = Literal["quality", "cost", "isolation", "compliance", "private_instance"]


@dataclass(frozen=True)
class ControlTowerFact:
    """一个不含业务正文、凭证或外部连接信息的状态字段。"""

    label: str
    value: str
    status: ControlTowerStatus


@dataclass(frozen=True)
class ControlTowerSection:
    """控制台按质量域聚合的状态和阻断原因。"""

    key: ControlTowerSectionKey
    title: str
    status: ControlTowerStatus
    summary: str
    reason_codes: tuple[str, ...]
    facts: tuple[ControlTowerFact, ...]


@dataclass(frozen=True)
class DangerousOperationNotice:
    """危险动作只暴露治理要求，不提供绕过后端授权的执行入口。"""

    operation: str
    permission_code: str
    confirmation_required: bool
    backend_reauthorization: bool


@dataclass(frozen=True)
class ControlTowerSourceContract:
    """记录控制台状态依赖的版本化来源契约。"""

    contract_id: str
    version: int
    status: Literal["frozen", "not_configured", "not_run"]


@dataclass(frozen=True)
class OperationsControlTowerSnapshot:
    """工作空间级控制台快照；所有真实外部输入缺失状态均原样保留。"""

    workspace_id: UUID
    snapshot_version: int
    generated_at: datetime
    sections: tuple[ControlTowerSection, ...]
    dangerous_operations: tuple[DangerousOperationNotice, ...]
    source_contracts: tuple[ControlTowerSourceContract, ...]


class ControlTowerSnapshotSource:
    """从冻结的 P5-12 基线加载低敏状态，拒绝运行时注入任意字段。"""

    def __init__(self, baseline_path: Path) -> None:
        document = _load_document(baseline_path)
        if document.get("schema_version") != 1 or document.get("contract_id") != "p5-12-v1":
            raise ValueError("P5-12 控制台基线版本或契约标识不匹配")
        self._sections = _parse_sections(document.get("sections"))
        self._dangerous_operations = _parse_dangerous_operations(
            document.get("dangerous_operations")
        )
        self._source_contracts = _parse_source_contracts(document.get("source_contracts"))

    def snapshot(
        self,
        workspace_id: UUID,
        *,
        generated_at: datetime,
    ) -> OperationsControlTowerSnapshot:
        """把不可变基线投影到当前可信工作空间，不暴露外部环境标识。"""

        return OperationsControlTowerSnapshot(
            workspace_id=workspace_id,
            snapshot_version=1,
            generated_at=generated_at,
            sections=self._sections,
            dangerous_operations=self._dangerous_operations,
            source_contracts=self._source_contracts,
        )


def _load_document(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取 P5-12 控制台基线: {path}") from error
    if not isinstance(value, dict):
        raise ValueError("P5-12 控制台基线顶层必须是对象")
    return cast(dict[str, object], value)


def _parse_sections(value: object) -> tuple[ControlTowerSection, ...]:
    if not isinstance(value, list):
        raise ValueError("P5-12 控制台基线缺少 sections 数组")
    sections: list[ControlTowerSection] = []
    for raw in value:
        item = _object(raw, "section")
        sections.append(
            ControlTowerSection(
                key=cast(ControlTowerSectionKey, _literal(item, "key")),
                title=_string(item, "title"),
                status=cast(ControlTowerStatus, _literal(item, "status")),
                summary=_string(item, "summary"),
                reason_codes=_strings(item, "reason_codes"),
                facts=_parse_facts(item.get("facts")),
            )
        )
    if {item.key for item in sections} != {
        "quality",
        "cost",
        "isolation",
        "compliance",
        "private_instance",
    }:
        raise ValueError("P5-12 控制台基线必须覆盖五个治理分区")
    return tuple(sections)


def _parse_facts(value: object) -> tuple[ControlTowerFact, ...]:
    if not isinstance(value, list):
        raise ValueError("P5-12 控制台分区 facts 必须是数组")
    return tuple(
        ControlTowerFact(
            label=_string(item, "label"),
            value=_string(item, "value"),
            status=cast(ControlTowerStatus, _literal(item, "status")),
        )
        for item in (_object(raw, "fact") for raw in value)
    )


def _parse_dangerous_operations(value: object) -> tuple[DangerousOperationNotice, ...]:
    if not isinstance(value, list):
        raise ValueError("P5-12 控制台基线缺少 dangerous_operations 数组")
    return tuple(
        DangerousOperationNotice(
            operation=_string(item, "operation"),
            permission_code=_string(item, "permission_code"),
            confirmation_required=_boolean(item, "confirmation_required"),
            backend_reauthorization=_boolean(item, "backend_reauthorization"),
        )
        for item in (_object(raw, "dangerous_operation") for raw in value)
    )


def _parse_source_contracts(value: object) -> tuple[ControlTowerSourceContract, ...]:
    if not isinstance(value, list):
        raise ValueError("P5-12 控制台基线缺少 source_contracts 数组")
    return tuple(
        ControlTowerSourceContract(
            contract_id=_string(item, "contract_id"),
            version=_integer(item, "version"),
            status=cast(
                Literal["frozen", "not_configured", "not_run"],
                _source_status(item, "status"),
            ),
        )
        for item in (_object(raw, "source_contract") for raw in value)
    )


def _parse_literal_status(value: object) -> ControlTowerStatus:
    if value not in {"passed", "blocked", "not_run", "not_configured", "failed"}:
        raise ValueError(f"P5-12 控制台状态无效: {value}")
    return cast(ControlTowerStatus, value)


def _object(value: object, location: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} 必须是对象")
    return cast(dict[str, object], value)


def _string(item: dict[str, object], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"P5-12 控制台字段无效: {key}")
    return value


def _integer(item: dict[str, object], key: str) -> int:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"P5-12 控制台整数无效: {key}")
    return value


def _boolean(item: dict[str, object], key: str) -> bool:
    value = item.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"P5-12 控制台布尔字段无效: {key}")
    if not value:
        raise ValueError(f"P5-12 危险动作必须要求 {key}: {key}")
    return value


def _strings(item: dict[str, object], key: str) -> tuple[str, ...]:
    value = item.get(key)
    if not isinstance(value, list) or not all(isinstance(entry, str) for entry in value):
        raise ValueError(f"P5-12 控制台字符串数组无效: {key}")
    return tuple(cast(list[str], value))


def _literal(item: dict[str, object], key: str) -> str:
    value = _string(item, key)
    if key == "status":
        return _parse_literal_status(value)
    if key == "key" and value not in {
        "quality",
        "cost",
        "isolation",
        "compliance",
        "private_instance",
    }:
        raise ValueError(f"P5-12 控制台分区无效: {value}")
    return value


def _source_status(item: dict[str, object], key: str) -> str:
    value = _string(item, key)
    if value not in {"frozen", "not_configured", "not_run"}:
        raise ValueError(f"P5-12 来源状态无效: {value}")
    return value
