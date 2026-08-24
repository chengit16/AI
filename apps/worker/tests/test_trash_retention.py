"""验证回收站保留期清理的批次、时区和工作空间边界。"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from ai_platform_worker.modules.knowledge.application.trash_retention import (
    TrashPurgeResult,
    TrashRetentionService,
)

WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000001")


class RecordingTrashStore:
    """记录应用服务传入的清理范围，避免用 Mock 掩盖边界校验。"""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID | None, datetime, int]] = []

    def purge_expired(
        self,
        *,
        workspace_id: UUID | None,
        cutoff: datetime,
        batch_size: int,
    ) -> TrashPurgeResult:
        self.calls.append((workspace_id, cutoff, batch_size))
        return TrashPurgeResult(scanned=2, purged=2, external_cleanup_requested=2)


def test_retention_passes_workspace_cutoff_and_bounded_batch() -> None:
    store = RecordingTrashStore()
    service = TrashRetentionService(store)
    cutoff = datetime(2026, 8, 1, tzinfo=UTC)

    result = service.run(workspace_id=WORKSPACE_ID, cutoff=cutoff, batch_size=50)

    assert result.purged == 2
    assert store.calls == [(WORKSPACE_ID, cutoff, 50)]


def test_retention_rejects_naive_cutoff() -> None:
    with pytest.raises(ValueError, match="必须带时区"):
        TrashRetentionService(RecordingTrashStore()).run(
            workspace_id=WORKSPACE_ID,
            cutoff=datetime(2026, 8, 1),
            batch_size=50,
        )


@pytest.mark.parametrize("batch_size", [0, 501])
def test_retention_rejects_unbounded_batch(batch_size: int) -> None:
    with pytest.raises(ValueError, match="1 到 500"):
        TrashRetentionService(RecordingTrashStore()).run(
            workspace_id=None,
            cutoff=datetime(2026, 8, 1, tzinfo=UTC),
            batch_size=batch_size,
        )
