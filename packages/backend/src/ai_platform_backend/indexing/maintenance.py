"""定义索引巡检、差异修复、全量重建和失败产物清理的共享事实。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

IndexFindingCode = Literal[
    "INDEX_PUBLICATION_MISSING",
    "INDEX_PUBLICATION_VERSION_MISMATCH",
    "INDEX_VERSION_STATE_MISMATCH",
    "INDEX_SOURCE_FACT_MISMATCH",
    "INDEX_CHUNK_COUNT_MISMATCH",
    "INDEX_CHUNK_REFERENCE_MISMATCH",
    "INDEX_UNEXPECTED_ACTIVE_CHUNK",
    "INDEX_ORPHAN_ACTIVE_VERSION",
]
IndexFindingResolution = Literal["unresolved", "repaired", "rebuild_queued"]


@dataclass(frozen=True)
class IndexInspectionFinding:
    """描述一项不含正文的索引引用差异及其修复结果。"""

    finding_id: UUID
    code: IndexFindingCode
    workspace_id: UUID
    document_id: UUID
    document_version_id: UUID | None
    index_version_id: UUID | None
    resolution: IndexFindingResolution


@dataclass(frozen=True)
class IndexInspectionReport:
    """返回一次已持久化巡检的计数、摘要和最小化发现列表。"""

    maintenance_run_id: UUID
    started_at: datetime
    completed_at: datetime
    scanned_document_count: int
    inconsistency_count: int
    result_digest: str
    findings: tuple[IndexInspectionFinding, ...]


@dataclass(frozen=True)
class IndexRepairResult:
    """记录一次巡检报告经过安全修复后产生的切换和重建数量。"""

    maintenance_run_id: UUID
    repaired_document_count: int
    rebuild_queued_count: int


@dataclass(frozen=True)
class IndexRebuildBatchResult:
    """记录幂等全量重建操作扫描的文档数和新建索引版本。"""

    maintenance_run_id: UUID
    scanned_document_count: int
    rebuild_queued_count: int
    result_digest: str


@dataclass(frozen=True)
class IndexCleanupResult:
    """记录不可恢复失败构建中被清理的不可见 Chunk 数量。"""

    maintenance_run_id: UUID
    cleaned_chunk_count: int
    result_digest: str


class IndexMaintenanceStore(Protocol):
    """约束巡检应用层可使用的索引维护事实操作。"""

    def inspect(
        self,
        *,
        now: datetime,
        workspace_id: UUID | None = None,
    ) -> IndexInspectionReport: ...

    def repair(
        self,
        report: IndexInspectionReport,
        *,
        now: datetime,
        max_attempts: int,
        chunker_version: str,
        embedding_model_version: str,
        tokenizer_version: str,
    ) -> IndexRepairResult: ...

    def enqueue_full_rebuild(
        self,
        maintenance_run_id: UUID,
        *,
        requested_by_actor_id: UUID | None,
        workspace_id: UUID | None,
        now: datetime,
        max_attempts: int,
        chunker_version: str,
        embedding_model_version: str,
        tokenizer_version: str,
    ) -> IndexRebuildBatchResult: ...

    def cleanup_unrecoverable_chunks(
        self,
        maintenance_run_id: UUID,
        *,
        requested_by_actor_id: UUID | None,
        workspace_id: UUID | None,
        now: datetime,
    ) -> IndexCleanupResult: ...
