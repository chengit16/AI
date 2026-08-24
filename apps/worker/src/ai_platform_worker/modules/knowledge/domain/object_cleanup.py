"""定义知识文档外部对象清理的稳定结果和错误语义。"""

from __future__ import annotations

from dataclasses import dataclass

TRASH_PURGE_REQUESTED_EVENT = "knowledge.document.trash_purge_requested"


class InvalidObjectCleanupEventError(ValueError):
    """清理事件载荷或对象键不满足工作空间隔离约束。"""


class ObjectCleanupUnavailableError(Exception):
    """对象存储暂时不可用，调用方只能进行有限次数重试。"""


@dataclass(frozen=True)
class ObjectCleanupResult:
    """汇总单个清理事件的去重对象数量。"""

    requested: int
    deleted: int
