"""实现发布事实优先、可信缓存降级的 Runtime 快照装载。"""

from __future__ import annotations

import logging
from contextlib import suppress
from uuid import UUID

from ai_platform_api.modules.service_runtime.application.errors import (
    AgentRuntimeReleaseRequiredError,
    RuntimeServiceRouteUnavailableError,
)
from ai_platform_api.modules.service_runtime.domain.models import (
    RuntimeCacheUnavailableError,
    RuntimeReleaseSnapshot,
    RuntimeSnapshotCache,
    RuntimeSnapshotSource,
    RuntimeSourceUnavailableError,
    document_digest,
    route_assignment_bucket,
    route_digest,
    selected_route_release,
)

logger = logging.getLogger(__name__)


class RuntimeReleaseLoader:
    """从发布事实装载当前或精确绑定，并把 Valkey 限定为可重建派生缓存。"""

    def __init__(self, source: RuntimeSnapshotSource, cache: RuntimeSnapshotCache) -> None:
        self._source = source
        self._cache = cache

    def resolve_current(
        self,
        workspace_id: UUID,
        service_id: UUID,
        assignment_key: str,
    ) -> RuntimeReleaseSnapshot:
        """优先观察 PostgreSQL 当前 Route，只有 Source 故障时才使用短租期缓存。"""

        assignment_bucket = _assignment_bucket(service_id, assignment_key)
        try:
            snapshot = self._source.get_current(workspace_id, service_id, assignment_bucket)
        except RuntimeSourceUnavailableError:
            return self._current_from_cache(workspace_id, service_id, assignment_bucket)
        if snapshot is None:
            self._delete_current_best_effort(workspace_id, service_id)
            raise RuntimeServiceRouteUnavailableError
        verified = _require_current_identity(
            _require_current_selection(_require_valid_snapshot(snapshot), assignment_bucket),
            workspace_id,
            service_id,
        )
        self._put_current_best_effort(verified, assignment_bucket)
        return verified

    def resolve_bound(
        self,
        workspace_id: UUID,
        service_id: UUID,
        route_id: UUID,
        service_route_version: int,
        agent_release_id: UUID,
    ) -> RuntimeReleaseSnapshot:
        """装载 Run 的不可变精确绑定；缓存命中后不依赖控制面当前指针。"""

        cached = self._bound_from_cache(
            workspace_id,
            service_id,
            route_id,
            service_route_version,
            agent_release_id,
        )
        if cached is not None:
            return cached
        try:
            snapshot = self._source.get_bound(
                workspace_id,
                service_id,
                route_id,
                service_route_version,
                agent_release_id,
            )
        except RuntimeSourceUnavailableError as error:
            raise RuntimeServiceRouteUnavailableError from error
        if snapshot is None:
            raise AgentRuntimeReleaseRequiredError
        verified = _require_bound_identity(
            _require_valid_snapshot(snapshot),
            workspace_id,
            service_id,
            route_id,
            service_route_version,
            agent_release_id,
        )
        self._put_bound_best_effort(verified)
        return verified

    def close(self) -> None:
        """释放 Runtime 快照缓存持有的进程级连接。"""

        self._cache.close()

    def invalidate_current(self, workspace_id: UUID, service_id: UUID) -> None:
        """提交后清除全部 current 分桶；失败只告警，不伪装数据库发布失败。"""

        try:
            self._cache.delete_current(workspace_id, service_id)
        except RuntimeCacheUnavailableError:
            logger.warning(
                "Runtime current 缓存主动失效失败",
                extra={"workspace_id": str(workspace_id), "service_id": str(service_id)},
                exc_info=True,
            )

    def _current_from_cache(
        self,
        workspace_id: UUID,
        service_id: UUID,
        assignment_bucket: int,
    ) -> RuntimeReleaseSnapshot:
        try:
            snapshot = self._cache.get_current(workspace_id, service_id, assignment_bucket)
        except RuntimeCacheUnavailableError as error:
            raise RuntimeServiceRouteUnavailableError from error
        if snapshot is None:
            raise RuntimeServiceRouteUnavailableError
        try:
            return _require_current_identity(
                _require_current_selection(
                    _require_valid_snapshot(snapshot),
                    assignment_bucket,
                ),
                workspace_id,
                service_id,
            )
        except AgentRuntimeReleaseRequiredError as error:
            self._delete_current_best_effort(workspace_id, service_id)
            raise RuntimeServiceRouteUnavailableError from error

    def _bound_from_cache(
        self,
        workspace_id: UUID,
        service_id: UUID,
        route_id: UUID,
        service_route_version: int,
        agent_release_id: UUID,
    ) -> RuntimeReleaseSnapshot | None:
        try:
            snapshot = self._cache.get_bound(
                workspace_id,
                service_id,
                route_id,
                service_route_version,
                agent_release_id,
            )
        except RuntimeCacheUnavailableError:
            return None
        if snapshot is None:
            return None
        try:
            return _require_bound_identity(
                _require_valid_snapshot(snapshot),
                workspace_id,
                service_id,
                route_id,
                service_route_version,
                agent_release_id,
            )
        except AgentRuntimeReleaseRequiredError:
            # 键与信封身份不一致视为损坏缓存；删除后只能回源不可变发布事实。
            self._delete_bound_best_effort(
                workspace_id,
                service_id,
                route_id,
                service_route_version,
                agent_release_id,
            )
            return None

    def _put_current_best_effort(
        self,
        snapshot: RuntimeReleaseSnapshot,
        assignment_bucket: int,
    ) -> None:
        # PostgreSQL 发布事实已通过验证；缓存故障只失去降级能力，不能伪造主链路失败。
        with suppress(RuntimeCacheUnavailableError):
            self._cache.put_current(snapshot, assignment_bucket)

    def _put_bound_best_effort(self, snapshot: RuntimeReleaseSnapshot) -> None:
        # 历史绑定只补齐自己的不可变键，不能把旧 Route 反向写成服务当前版本。
        with suppress(RuntimeCacheUnavailableError):
            self._cache.put_bound(snapshot)

    def _delete_current_best_effort(self, workspace_id: UUID, service_id: UUID) -> None:
        with suppress(RuntimeCacheUnavailableError):
            self._cache.delete_current(workspace_id, service_id)

    def _delete_bound_best_effort(
        self,
        workspace_id: UUID,
        service_id: UUID,
        route_id: UUID,
        service_route_version: int,
        agent_release_id: UUID,
    ) -> None:
        with suppress(RuntimeCacheUnavailableError):
            self._cache.delete_bound(
                workspace_id,
                service_id,
                route_id,
                service_route_version,
                agent_release_id,
            )


def _require_valid_snapshot(snapshot: RuntimeReleaseSnapshot) -> RuntimeReleaseSnapshot:
    """拒绝草稿旁路、失效状态、摘要损坏和非法灰度或回滚结构。"""

    release_snapshot = snapshot.release_snapshot
    configuration = release_snapshot.get("configuration") if release_snapshot is not None else None
    valid_custom = (
        snapshot.release_kind == "custom"
        and release_snapshot is not None
        and snapshot.release_snapshot_hash is not None
        and document_digest(release_snapshot) == snapshot.release_snapshot_hash
        and isinstance(configuration, dict)
        and configuration.get("runtime_config_version_id")
        == str(snapshot.runtime_config_version_id)
    )
    valid_system = (
        snapshot.release_kind == "system"
        and release_snapshot is None
        and snapshot.release_snapshot_hash is None
    )
    valid_single_release_route = (
        snapshot.route_mode in {"active", "rollback"}
        and snapshot.agent_release_id == snapshot.primary_release_id
        and snapshot.canary_release_id is None
        and snapshot.canary_percent == 0
    )
    valid_canary_route = (
        snapshot.route_mode == "canary"
        and snapshot.canary_release_id is not None
        and snapshot.canary_release_id != snapshot.primary_release_id
        and 1 <= snapshot.canary_percent <= 99
        and snapshot.agent_release_id in {snapshot.primary_release_id, snapshot.canary_release_id}
    )
    if (
        snapshot.service_status != "active"
        or snapshot.agent_status != "active"
        or snapshot.release_status != "released"
        or not (valid_single_release_route or valid_canary_route)
        or snapshot.service_route_version < 1
        or snapshot.release_version < 1
        or not _is_sha256(snapshot.config_hash)
        or not _is_sha256(snapshot.route_hash)
        or route_digest(snapshot) != snapshot.route_hash
        or not (valid_system or valid_custom)
    ):
        raise AgentRuntimeReleaseRequiredError
    return snapshot


def _assignment_bucket(service_id: UUID, assignment_key: str) -> int:
    try:
        return route_assignment_bucket(service_id, assignment_key)
    except ValueError as error:
        raise AgentRuntimeReleaseRequiredError from error


def _require_current_selection(
    snapshot: RuntimeReleaseSnapshot,
    assignment_bucket: int,
) -> RuntimeReleaseSnapshot:
    """复核 Source 或 current 缓存没有返回其他百分位对应的 Release。"""

    if selected_route_release(snapshot, assignment_bucket) != snapshot.agent_release_id:
        raise AgentRuntimeReleaseRequiredError
    return snapshot


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and set(value) <= set("0123456789abcdef")


def _require_current_identity(
    snapshot: RuntimeReleaseSnapshot,
    workspace_id: UUID,
    service_id: UUID,
) -> RuntimeReleaseSnapshot:
    if snapshot.workspace_id != workspace_id or snapshot.service_id != service_id:
        raise AgentRuntimeReleaseRequiredError
    return snapshot


def _require_bound_identity(
    snapshot: RuntimeReleaseSnapshot,
    workspace_id: UUID,
    service_id: UUID,
    route_id: UUID,
    service_route_version: int,
    agent_release_id: UUID,
) -> RuntimeReleaseSnapshot:
    if (
        snapshot.workspace_id != workspace_id
        or snapshot.service_id != service_id
        or snapshot.route_id != route_id
        or snapshot.service_route_version != service_route_version
        or snapshot.agent_release_id != agent_release_id
    ):
        raise AgentRuntimeReleaseRequiredError
    return snapshot
