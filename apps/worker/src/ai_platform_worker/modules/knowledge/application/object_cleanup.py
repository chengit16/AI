"""校验永久删除事件并执行可重放的外部对象清理。"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from ai_platform_backend.integration.domain import IntegrationEvent

from ai_platform_worker.modules.knowledge.domain.object_cleanup import (
    TRASH_PURGE_REQUESTED_EVENT,
    InvalidObjectCleanupEventError,
    ObjectCleanupResult,
)

__all__ = ["ObjectCleanupService", "ObjectCleanupStorage"]

_MAX_OBJECT_KEYS = 10_000


class ObjectCleanupStorage(Protocol):
    """删除已通过工作空间边界校验的对象键。"""

    def delete(self, *, workspace_id: UUID, object_key: str) -> None: ...


class ObjectCleanupService:
    """把清理载荷收窄为当前空间对象键，再调用幂等存储端口。"""

    def __init__(self, storage: ObjectCleanupStorage) -> None:
        self._storage = storage

    def handle(self, event: IntegrationEvent) -> ObjectCleanupResult:
        """验证删除事实和对象键，任一键越界时整次操作失败关闭。"""

        object_keys, requested = _validated_object_keys(event)
        # S3 删除不存在对象仍为成功；中途失败后重放全部键不会恢复或覆盖任何内容。
        for object_key in object_keys:
            self._storage.delete(workspace_id=event.workspace_id, object_key=object_key)
        return ObjectCleanupResult(requested=requested, deleted=len(object_keys))


def _validated_object_keys(event: IntegrationEvent) -> tuple[tuple[str, ...], int]:
    """严格校验内部事件，防止签名正确但业务载荷越过工作空间边界。"""

    payload = event.payload
    if (
        event.event_type != TRASH_PURGE_REQUESTED_EVENT
        or payload.get("resource_type") != "knowledge_document"
        or payload.get("resource_id") != str(event.aggregate_id)
        or payload.get("database_facts_purged") is not True
        or payload.get("external_cleanup_status") != "pending"
        or payload.get("purge_trigger") not in {"manual", "retention"}
        or not isinstance(payload.get("requested_by"), str)
        or not payload["requested_by"]
    ):
        raise InvalidObjectCleanupEventError("对象清理事件的删除事实不完整")
    raw_keys = payload.get("external_object_keys")
    if not isinstance(raw_keys, list) or len(raw_keys) > _MAX_OBJECT_KEYS:
        raise InvalidObjectCleanupEventError("对象清理键集合类型或数量不合法")
    if any(not isinstance(value, str) for value in raw_keys):
        raise InvalidObjectCleanupEventError("对象清理键必须全部为字符串")
    object_keys = tuple(sorted(set(raw_keys)))
    for object_key in object_keys:
        _assert_workspace_object_key(event.workspace_id, object_key)
    return object_keys, len(raw_keys)


def _assert_workspace_object_key(workspace_id: UUID, object_key: str) -> None:
    """只接受当前工作空间下的规范对象键，不对路径做自动修复。"""

    prefix = f"workspaces/{workspace_id}/"
    relative_key = object_key.removeprefix(prefix)
    parts = relative_key.split("/")
    if (
        not object_key.startswith(prefix)
        or len(object_key) > 1_024
        or len(parts) < 2
        or any(part in {"", ".", ".."} for part in parts)
        or "\\" in object_key
        or "\x00" in object_key
    ):
        raise InvalidObjectCleanupEventError("对象清理键不属于当前工作空间")
