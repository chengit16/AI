"""定义回收站保留期清理的领域结果。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrashPurgeResult:
    """汇总一批清理结果，外部对象清理始终单独标记为待处理。"""

    scanned: int
    purged: int
    external_cleanup_requested: int
