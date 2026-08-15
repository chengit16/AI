"""清除带工作空间标识的 Valkey 派生缓存。"""

from __future__ import annotations

from typing import cast
from uuid import UUID

from redis import Redis

from ai_platform_api.modules.lifecycle.domain.ports import LifecycleDependencyError


class ValkeyWorkspaceCacheCleaner:
    """只清理已登记的角色派生缓存，不触碰账号 Session。"""

    def __init__(self, url: str) -> None:
        self._client = Redis.from_url(url, decode_responses=True)

    def delete_workspace_keys(self, workspace_id: UUID) -> int:
        try:
            keys = tuple(
                str(key)
                for key in self._client.scan_iter(
                    match=f"effective-roles:v1:{workspace_id}:*",
                    count=200,
                )
            )
            return cast(int, self._client.delete(*keys)) if keys else 0
        except Exception as error:
            raise LifecycleDependencyError from error

    def close(self) -> None:
        self._client.close()
