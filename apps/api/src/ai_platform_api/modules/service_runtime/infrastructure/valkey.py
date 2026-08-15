"""以 Valkey 保存有租期、带摘要且可从发布事实重建的 Runtime 快照。"""

from __future__ import annotations

import json
from contextlib import suppress
from typing import Protocol, cast
from uuid import UUID

from redis import Redis
from redis.exceptions import RedisError

from ai_platform_api.modules.service_runtime.domain.models import (
    RuntimeCacheUnavailableError,
    RuntimeReleaseSnapshot,
    runtime_snapshot_digest,
    runtime_snapshot_document,
    runtime_snapshot_from_document,
)


class RuntimeCacheClient(Protocol):
    """约束 Runtime 快照缓存所需的最小 RESP 客户端能力。"""

    def get(self, key: str) -> object: ...

    def set(self, key: str, value: str, *, ex: int) -> object: ...

    def delete(self, *keys: str) -> object: ...

    def close(self) -> None: ...


class ValkeyRuntimeSnapshotCache:
    """分别缓存短租期当前 Route 与长租期不可变 Run 绑定。"""

    def __init__(
        self,
        url: str | None = None,
        *,
        client: RuntimeCacheClient | None = None,
        current_ttl_seconds: int = 300,
        bound_ttl_seconds: int = 86_400,
        timeout_seconds: float = 0.5,
    ) -> None:
        if client is None and url is None:
            raise ValueError("Runtime 快照缓存必须配置 Valkey URL 或客户端")
        self._client = client or cast(
            RuntimeCacheClient,
            Redis.from_url(
                str(url),
                decode_responses=True,
                socket_connect_timeout=timeout_seconds,
                socket_timeout=timeout_seconds,
                health_check_interval=30,
            ),
        )
        self._current_ttl_seconds = current_ttl_seconds
        self._bound_ttl_seconds = bound_ttl_seconds

    def get_current(
        self,
        workspace_id: UUID,
        service_id: UUID,
    ) -> RuntimeReleaseSnapshot | None:
        return self._get(self.current_key(workspace_id, service_id))

    def get_bound(
        self,
        workspace_id: UUID,
        service_id: UUID,
        route_id: UUID,
        service_route_version: int,
        agent_release_id: UUID,
    ) -> RuntimeReleaseSnapshot | None:
        return self._get(
            self.bound_key(
                workspace_id,
                service_id,
                route_id,
                service_route_version,
                agent_release_id,
            )
        )

    def put_current(self, snapshot: RuntimeReleaseSnapshot) -> None:
        """刷新服务当前 Route，并同步预热该版本的精确绑定。"""

        payload = _snapshot_payload(snapshot)
        try:
            self._client.set(
                self.current_key(snapshot.workspace_id, snapshot.service_id),
                payload,
                ex=self._current_ttl_seconds,
            )
            self._client.set(
                self.bound_key(
                    snapshot.workspace_id,
                    snapshot.service_id,
                    snapshot.route_id,
                    snapshot.service_route_version,
                    snapshot.agent_release_id,
                ),
                payload,
                ex=self._bound_ttl_seconds,
            )
        except RedisError as error:
            raise RuntimeCacheUnavailableError from error

    def put_bound(self, snapshot: RuntimeReleaseSnapshot) -> None:
        """只写不可变绑定，避免历史 Run 装载覆盖服务当前 Route。"""

        try:
            self._client.set(
                self.bound_key(
                    snapshot.workspace_id,
                    snapshot.service_id,
                    snapshot.route_id,
                    snapshot.service_route_version,
                    snapshot.agent_release_id,
                ),
                _snapshot_payload(snapshot),
                ex=self._bound_ttl_seconds,
            )
        except RedisError as error:
            raise RuntimeCacheUnavailableError from error

    def delete_current(self, workspace_id: UUID, service_id: UUID) -> None:
        try:
            self._client.delete(self.current_key(workspace_id, service_id))
        except RedisError as error:
            raise RuntimeCacheUnavailableError from error

    def delete_bound(
        self,
        workspace_id: UUID,
        service_id: UUID,
        route_id: UUID,
        service_route_version: int,
        agent_release_id: UUID,
    ) -> None:
        try:
            self._client.delete(
                self.bound_key(
                    workspace_id,
                    service_id,
                    route_id,
                    service_route_version,
                    agent_release_id,
                )
            )
        except RedisError as error:
            raise RuntimeCacheUnavailableError from error

    def close(self) -> None:
        self._client.close()

    def _get(self, key: str) -> RuntimeReleaseSnapshot | None:
        try:
            payload = self._client.get(key)
        except RedisError as error:
            raise RuntimeCacheUnavailableError from error
        if not isinstance(payload, str):
            return None
        try:
            envelope: object = json.loads(payload)
            if not isinstance(envelope, dict) or set(envelope) != {
                "schema_version",
                "snapshot",
                "snapshot_digest",
            }:
                raise ValueError("Runtime 缓存信封字段不完整")
            if envelope["schema_version"] != 1:
                raise ValueError("Runtime 缓存信封版本不受支持")
            snapshot = runtime_snapshot_from_document(envelope["snapshot"])
            if envelope["snapshot_digest"] != runtime_snapshot_digest(snapshot):
                raise ValueError("Runtime 缓存信封摘要不匹配")
            return snapshot
        except (json.JSONDecodeError, TypeError, ValueError):
            # 损坏值不会进入 Runtime；删除仅用于减少重复回源，删除失败仍按未命中处理。
            with suppress(RedisError):
                self._client.delete(key)
            return None

    @staticmethod
    def current_key(workspace_id: UUID, service_id: UUID) -> str:
        return f"runtime-current:v1:{workspace_id}:{service_id}"

    @staticmethod
    def bound_key(
        workspace_id: UUID,
        service_id: UUID,
        route_id: UUID,
        service_route_version: int,
        agent_release_id: UUID,
    ) -> str:
        return (
            f"runtime-bound:v1:{workspace_id}:{service_id}:{route_id}:"
            f"{service_route_version}:{agent_release_id}"
        )


def _snapshot_payload(snapshot: RuntimeReleaseSnapshot) -> str:
    """生成带完整摘要的规范信封，两个缓存写路径共享同一序列化规则。"""

    return json.dumps(
        {
            "schema_version": 1,
            "snapshot": runtime_snapshot_document(snapshot),
            "snapshot_digest": runtime_snapshot_digest(snapshot),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
