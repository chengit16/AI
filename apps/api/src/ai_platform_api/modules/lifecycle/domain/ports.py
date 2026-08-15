"""定义工作空间生命周期跨存储端口与稳定失败语义。"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.lifecycle.domain.models import (
    DeletionCertificate,
    ExportObject,
    LifecycleExport,
    LifecyclePurge,
    RetentionRun,
)
from ai_platform_api.modules.lifecycle.domain.registry import WorkspaceTableRegistry


class LifecycleDependencyError(Exception):
    """表示数据库、对象存储或缓存步骤暂时无法完成。"""


class LifecycleRegistryDriftError(LifecycleDependencyError):
    """表示数据库中的工作空间表与冻结分类不一致。"""


class LifecycleWriteConflictError(LifecycleDependencyError):
    """表示幂等请求或生命周期状态发生并发冲突。"""


class WorkspaceLifecycleStore(Protocol):
    """定义生命周期应用层所需的工作空间隔离持久化能力。"""

    def assert_registry_coverage(self, registry: WorkspaceTableRegistry) -> None: ...

    def workspace_name(self, workspace_id: UUID) -> str | None: ...

    def read_export_rows(
        self,
        workspace_id: UUID,
        registry: WorkspaceTableRegistry,
    ) -> dict[str, tuple[dict[str, object], ...]]: ...

    def get_export(self, workspace_id: UUID, idempotency_key: str) -> LifecycleExport | None: ...

    def add_export(self, export: LifecycleExport) -> LifecycleExport: ...

    def complete_export(
        self,
        export_id: UUID,
        *,
        object_key: str,
        bundle_size_bytes: int,
        bundle_sha256: str,
        object_manifest_sha256: str,
        table_count: int,
        object_count: int,
        completed_at: datetime,
    ) -> LifecycleExport: ...

    def fail_export(self, export_id: UUID, error_code: str, completed_at: datetime) -> None: ...

    def get_purge(self, workspace_id: UUID, idempotency_key: str) -> LifecyclePurge | None: ...

    def add_purge(self, purge: LifecyclePurge) -> LifecyclePurge: ...

    def purge_database(
        self,
        purge_request_id: UUID,
        registry: WorkspaceTableRegistry,
        now: datetime,
    ) -> LifecyclePurge: ...

    def mark_purge_external_step(
        self,
        purge_request_id: UUID,
        *,
        step: str,
        count: int,
        now: datetime,
    ) -> LifecyclePurge: ...

    def mark_purge_retryable(
        self,
        purge_request_id: UUID,
        error_code: str,
        now: datetime,
    ) -> None: ...

    def finalize_purge(
        self,
        purge_request_id: UUID,
        registry_version: int,
        context: RequestContext,
        now: datetime,
    ) -> tuple[LifecyclePurge, DeletionCertificate]: ...

    def get_retention_run(
        self,
        workspace_id: UUID,
        idempotency_key: str,
    ) -> RetentionRun | None: ...

    def add_retention_run(self, run: RetentionRun) -> RetentionRun: ...

    def execute_retention(
        self,
        retention_run_id: UUID,
        context: RequestContext,
        now: datetime,
    ) -> RetentionRun: ...


class LifecycleObjectStorage(Protocol):
    """限定生命周期服务所需的窄对象存储能力。"""

    def read_business_objects(self, workspace_id: UUID) -> tuple[ExportObject, ...]: ...

    def put_export(
        self,
        workspace_id: UUID,
        export_id: UUID,
        content: bytes,
        sha256: str,
    ) -> str: ...

    def delete_workspace_objects(self, workspace_id: UUID) -> int: ...


class LifecycleCacheCleaner(Protocol):
    """限定生命周期服务所需的工作空间派生缓存清理能力。"""

    def delete_workspace_keys(self, workspace_id: UUID) -> int: ...
