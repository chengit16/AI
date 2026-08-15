"""编排索引巡检、安全修复、全量重建和失败派生产物清理。"""

from datetime import UTC, datetime
from uuid import UUID

from ai_platform_backend.indexing.maintenance import (
    IndexCleanupResult,
    IndexInspectionReport,
    IndexMaintenanceStore,
    IndexRebuildBatchResult,
    IndexRepairResult,
)


class IndexMaintenanceProcessor:
    """集中注入重建版本参数，确保周期巡检与人工维护使用同一构建规则。"""

    def __init__(
        self,
        store: IndexMaintenanceStore,
        *,
        max_attempts: int,
        chunker_version: str,
        embedding_model_version: str,
        tokenizer_version: str,
    ) -> None:
        self._store = store
        self._max_attempts = max_attempts
        self._chunker_version = chunker_version
        self._embedding_model_version = embedding_model_version
        self._tokenizer_version = tokenizer_version

    def inspect_and_repair(
        self,
        *,
        now: datetime | None = None,
        workspace_id: UUID | None = None,
        maintenance_run_id: UUID | None = None,
        requested_by_actor_id: UUID | None = None,
    ) -> tuple[IndexInspectionReport, IndexRepairResult]:
        """先持久化完整差异证据，再基于最新发布事实执行安全修复。"""

        inspected_at = now or datetime.now(UTC)
        report = self._store.inspect(
            now=inspected_at,
            workspace_id=workspace_id,
            maintenance_run_id=maintenance_run_id,
            requested_by_actor_id=requested_by_actor_id,
        )
        repair = self._store.repair(
            report,
            now=inspected_at,
            max_attempts=self._max_attempts,
            chunker_version=self._chunker_version,
            embedding_model_version=self._embedding_model_version,
            tokenizer_version=self._tokenizer_version,
        )
        return report, repair

    def enqueue_full_rebuild(
        self,
        maintenance_run_id: UUID,
        *,
        requested_by_actor_id: UUID | None,
        workspace_id: UUID | None = None,
        now: datetime | None = None,
    ) -> IndexRebuildBatchResult:
        """从当前发布事实创建完整新构建，调用方提供稳定运行 ID 保证重放幂等。"""

        return self._store.enqueue_full_rebuild(
            maintenance_run_id,
            requested_by_actor_id=requested_by_actor_id,
            workspace_id=workspace_id,
            now=now or datetime.now(UTC),
            max_attempts=self._max_attempts,
            chunker_version=self._chunker_version,
            embedding_model_version=self._embedding_model_version,
            tokenizer_version=self._tokenizer_version,
        )

    def cleanup_unrecoverable_chunks(
        self,
        maintenance_run_id: UUID,
        *,
        requested_by_actor_id: UUID | None,
        workspace_id: UUID | None = None,
        now: datetime | None = None,
    ) -> IndexCleanupResult:
        """只清理已不可恢复且从未对检索可见的 Chunk，并保留运行证据。"""

        return self._store.cleanup_unrecoverable_chunks(
            maintenance_run_id,
            requested_by_actor_id=requested_by_actor_id,
            workspace_id=workspace_id,
            now=now or datetime.now(UTC),
        )
