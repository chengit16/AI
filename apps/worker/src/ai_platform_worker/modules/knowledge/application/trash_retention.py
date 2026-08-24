"""实现回收站保留期清理的应用服务端口。"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from ai_platform_worker.modules.knowledge.domain.trash_retention import TrashPurgeResult

__all__ = ["TrashPurgeResult", "TrashRetentionService", "TrashRetentionStore"]


class TrashRetentionStore(Protocol):
    """定义保留期清理所需的窄写入端口。"""

    def purge_expired(
        self,
        *,
        workspace_id: UUID | None,
        cutoff: datetime,
        batch_size: int,
    ) -> TrashPurgeResult: ...


class TrashRetentionService:
    """校验清理边界后调用窄 Store，避免任务绕过事务和工作空间过滤。"""

    def __init__(self, store: TrashRetentionStore) -> None:
        self._store = store

    def run(
        self,
        *,
        workspace_id: UUID | None,
        cutoff: datetime,
        batch_size: int,
    ) -> TrashPurgeResult:
        """校验清理阈值和批次边界后执行有限范围的回收站清理。"""

        if cutoff.tzinfo is None:
            raise ValueError("回收站清理阈值必须带时区")
        if batch_size < 1 or batch_size > 500:
            raise ValueError("回收站清理批次必须位于 1 到 500 之间")
        return self._store.purge_expired(
            workspace_id=workspace_id,
            cutoff=cutoff,
            batch_size=batch_size,
        )
