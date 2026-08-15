"""编排工作空间导出、业务数据清除和保留期执行。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from ai_platform_api.common.errors import PlatformError
from ai_platform_api.common.request_context import RequestContext
from ai_platform_api.modules.lifecycle.application.bundle import build_workspace_export_bundle
from ai_platform_api.modules.lifecycle.domain.models import (
    DeletionCertificate,
    LifecycleExport,
    LifecyclePurge,
    RetentionRun,
)
from ai_platform_api.modules.lifecycle.domain.ports import (
    LifecycleCacheCleaner,
    LifecycleDependencyError,
    LifecycleObjectStorage,
    WorkspaceLifecycleStore,
)
from ai_platform_api.modules.lifecycle.domain.registry import (
    WorkspaceTableRegistry,
    load_workspace_table_registry,
)

__all__ = [
    "DeletionCertificate",
    "LifecycleExport",
    "LifecyclePurge",
    "RetentionRun",
    "WorkspaceLifecycleService",
]

EXPORT_PERMISSION = "workspace.lifecycle.export"
PURGE_PERMISSION = "workspace.lifecycle.purge"
RETENTION_PERMISSION = "workspace.lifecycle.retention.execute"
IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
REASON_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


class LifecycleDeniedError(PlatformError):
    """当前主体没有目标工作空间的生命周期操作权限。"""

    error_code = "POLICY_DENIED"


class LifecycleNotFoundError(PlatformError):
    """目标工作空间不存在。"""

    error_code = "RESOURCE_NOT_FOUND"


class LifecycleValidationError(PlatformError):
    """幂等键、确认名称或结构化原因不符合稳定契约。"""

    error_code = "VALIDATION_ERROR"


class LifecycleIdempotencyConflictError(PlatformError):
    """幂等键已经绑定到另一份生命周期请求。"""

    error_code = "IDEMPOTENCY_CONFLICT"


class LifecycleUnavailableError(PlatformError):
    """生命周期跨存储步骤暂时失败，可使用同一幂等键重试。"""

    error_code = "DEPENDENCY_UNAVAILABLE"


class WorkspaceLifecycleService:
    """以幂等事实推进数据库、MinIO 和 Valkey 生命周期步骤。"""

    def __init__(
        self,
        store: WorkspaceLifecycleStore,
        object_storage: LifecycleObjectStorage,
        cache: LifecycleCacheCleaner,
        registry_path: Path,
    ) -> None:
        self._store = store
        self._object_storage = object_storage
        self._cache = cache
        self._registry: WorkspaceTableRegistry = load_workspace_table_registry(registry_path)

    def export_workspace(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        idempotency_key: str,
        requested_at: datetime | None = None,
    ) -> LifecycleExport:
        """构建隔离、可校验且不含凭据材料的工作空间导出包。"""

        # 1. 导出只接受已授权浏览器主体和稳定幂等键，API Key 不能触发批量数据读取。
        self._require(context, workspace_id, EXPORT_PERMISSION)
        account_id = _require_browser_subject(context)
        _require_idempotency_key(idempotency_key)
        request_hash = _request_hash({"workspace_id": str(workspace_id), "operation": "export"})
        previous = self._store.get_export(workspace_id, idempotency_key)
        if previous is not None:
            if previous.request_hash != request_hash:
                raise LifecycleIdempotencyConflictError
            return previous
        # 2. 先建立运行事实并原子认领幂等键，并发失败方只复用获胜请求的当前状态。
        now = requested_at or datetime.now(UTC)
        export = LifecycleExport(
            export_id=uuid4(),
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status="running",
            registry_version=self._registry.registry_version,
            object_key=None,
            bundle_size_bytes=None,
            bundle_sha256=None,
            object_manifest_sha256=None,
            table_count=None,
            object_count=None,
            requested_by_account_id=account_id,
            request_id=context.request_id,
            trace_id=context.trace.trace_id,
            traceparent=context.trace.traceparent,
            created_at=now,
            completed_at=None,
            error_code=None,
        )
        try:
            self._store.assert_registry_coverage(self._registry)
            claimed_export = self._store.add_export(export)
            if claimed_export.export_id != export.export_id:
                if claimed_export.request_hash != request_hash:
                    raise LifecycleIdempotencyConflictError
                return claimed_export
            # 3. 表快照先读取，再读取对象；包内 Manifest 对每个文件和对象都保存复算摘要。
            table_rows = self._store.read_export_rows(workspace_id, self._registry)
            objects = self._object_storage.read_business_objects(workspace_id)
            bundle = build_workspace_export_bundle(
                workspace_id=workspace_id,
                export_id=export.export_id,
                created_at=now,
                registry_version=self._registry.registry_version,
                table_rows=table_rows,
                objects=objects,
            )
            object_key = self._object_storage.put_export(
                workspace_id,
                export.export_id,
                bundle.content,
                bundle.sha256,
            )
            return self._store.complete_export(
                export.export_id,
                object_key=object_key,
                bundle_size_bytes=len(bundle.content),
                bundle_sha256=bundle.sha256,
                object_manifest_sha256=bundle.object_manifest_sha256,
                table_count=bundle.table_count,
                object_count=bundle.object_count,
                completed_at=datetime.now(UTC),
            )
        except LifecycleDependencyError:
            self._store.fail_export(export.export_id, "WORKSPACE_EXPORT_FAILED", datetime.now(UTC))
            raise LifecycleUnavailableError from None

    def purge_workspace_business_data(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        idempotency_key: str,
        confirmed_workspace_name: str,
        reason_code: str,
        requested_at: datetime | None = None,
    ) -> tuple[LifecyclePurge, DeletionCertificate]:
        """保留空间壳层，幂等清除业务事实、对象和派生缓存。"""

        # 1. 危险操作先验证浏览器授权、结构化原因和空间名称复述，避免误删或 API Key 调用。
        self._require(context, workspace_id, PURGE_PERMISSION)
        account_id = _require_browser_subject(context)
        _require_idempotency_key(idempotency_key)
        if REASON_PATTERN.fullmatch(reason_code) is None:
            raise LifecycleValidationError
        workspace_name = self._store.workspace_name(workspace_id)
        if workspace_name is None:
            raise LifecycleNotFoundError
        if confirmed_workspace_name != workspace_name:
            raise LifecycleValidationError
        request_hash = _request_hash(
            {
                "workspace_id": str(workspace_id),
                "confirmed_workspace_name": confirmed_workspace_name,
                "reason_code": reason_code,
                "operation": "purge",
            }
        )
        now = requested_at or datetime.now(UTC)
        purge = self._store.get_purge(workspace_id, idempotency_key)
        if purge is not None and purge.request_hash != request_hash:
            raise LifecycleIdempotencyConflictError
        if purge is None:
            # 2. 请求事实先于任何删除落库，并通过唯一键确保并发请求共享同一恢复进度。
            purge = LifecyclePurge(
                purge_request_id=uuid4(),
                workspace_id=workspace_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                reason_code=reason_code,
                confirmed_workspace_name=confirmed_workspace_name,
                status="pending",
                database_cleared=False,
                objects_cleared=False,
                cache_cleared=False,
                deleted_table_counts={},
                deleted_object_count=0,
                deleted_cache_key_count=0,
                last_error_code=None,
                requested_by_account_id=account_id,
                request_id=context.request_id,
                trace_id=context.trace.trace_id,
                traceparent=context.trace.traceparent,
                created_at=now,
                updated_at=now,
                completed_at=None,
            )
            self._store.assert_registry_coverage(self._registry)
            purge = self._store.add_purge(purge)
            if purge.request_hash != request_hash:
                raise LifecycleIdempotencyConflictError
        try:
            # 3. 数据库先撤销可见性，再逐项清理对象与缓存；每一步落库后都能用原请求续跑。
            if not purge.database_cleared:
                purge = self._store.purge_database(
                    purge.purge_request_id,
                    self._registry,
                    datetime.now(UTC),
                )
            if not purge.objects_cleared:
                object_count = self._object_storage.delete_workspace_objects(workspace_id)
                purge = self._store.mark_purge_external_step(
                    purge.purge_request_id,
                    step="objects",
                    count=object_count,
                    now=datetime.now(UTC),
                )
            if not purge.cache_cleared:
                cache_count = self._cache.delete_workspace_keys(workspace_id)
                purge = self._store.mark_purge_external_step(
                    purge.purge_request_id,
                    step="cache",
                    count=cache_count,
                    now=datetime.now(UTC),
                )
            return self._store.finalize_purge(
                purge.purge_request_id,
                self._registry.registry_version,
                context,
                datetime.now(UTC),
            )
        except LifecycleDependencyError:
            self._store.mark_purge_retryable(
                purge.purge_request_id,
                "WORKSPACE_PURGE_RETRYABLE",
                datetime.now(UTC),
            )
            raise LifecycleUnavailableError from None

    def execute_retention(
        self,
        context: RequestContext,
        *,
        workspace_id: UUID,
        idempotency_key: str,
        requested_at: datetime | None = None,
    ) -> RetentionRun:
        """执行冻结保留期，并确保同一运行只删除一次。"""

        self._require(context, workspace_id, RETENTION_PERMISSION)
        account_id = _require_browser_subject(context)
        _require_idempotency_key(idempotency_key)
        previous = self._store.get_retention_run(workspace_id, idempotency_key)
        if previous is not None:
            if previous.status == "running":
                return self._store.execute_retention(
                    previous.retention_run_id,
                    context,
                    previous.created_at,
                )
            return previous
        now = requested_at or datetime.now(UTC)
        run = RetentionRun(
            retention_run_id=uuid4(),
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
            status="running",
            cutoffs={},
            deleted_table_counts={},
            result_sha256=None,
            requested_by_account_id=account_id,
            created_at=now,
            completed_at=None,
            error_code=None,
        )
        run = self._store.add_retention_run(run)
        # 并发认领失败方使用获胜事实的创建时间，避免同一运行出现两套截止边界。
        return self._store.execute_retention(run.retention_run_id, context, run.created_at)

    @staticmethod
    def _require(context: RequestContext, workspace_id: UUID, permission_code: str) -> None:
        if (
            context.workspace_id != workspace_id
            or context.authorized_permission_code != permission_code
            or not context.authorized_workspace
        ):
            raise LifecycleDeniedError


def _require_browser_subject(context: RequestContext) -> UUID:
    if context.authentication_method != "browser_session" or context.user_id is None:
        raise LifecycleDeniedError
    return context.user_id


def _require_idempotency_key(value: str) -> None:
    if IDEMPOTENCY_PATTERN.fullmatch(value) is None:
        raise LifecycleValidationError


def _request_hash(value: object) -> str:
    content = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(content).hexdigest()
