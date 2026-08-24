"""验证永久删除对象清理的隔离、幂等和有限重试边界。"""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from ai_platform_backend.integration.domain import IntegrationEvent
from ai_platform_worker.consumers.integration_events import consume_integration_event
from ai_platform_worker.modules.knowledge.application.object_cleanup import ObjectCleanupService
from ai_platform_worker.modules.knowledge.domain.object_cleanup import (
    TRASH_PURGE_REQUESTED_EVENT,
    InvalidObjectCleanupEventError,
    ObjectCleanupUnavailableError,
)
from ai_platform_worker.modules.knowledge.infrastructure.object_storage import (
    MinioObjectCleanupStorage,
)

WORKSPACE_ID = UUID("10000000-0000-4000-8000-000000000101")
DOCUMENT_ID = UUID("40000000-0000-4000-8000-000000000101")
NOW = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)


class RecordingStorage:
    """记录删除调用，并用集合模拟 S3 对缺失对象的幂等删除。"""

    def __init__(self, existing: set[str] | None = None) -> None:
        self.existing = set(existing or ())
        self.calls: list[tuple[UUID, str]] = []

    def delete(self, *, workspace_id: UUID, object_key: str) -> None:
        self.calls.append((workspace_id, object_key))
        self.existing.discard(object_key)


class UnavailableStorage:
    """模拟对象存储暂时不可用。"""

    def delete(self, *, workspace_id: UUID, object_key: str) -> None:
        del workspace_id, object_key
        raise ObjectCleanupUnavailableError


class FailingMinioClient:
    """模拟 MinIO SDK 在删除阶段抛出供应商异常。"""

    def remove_object(self, bucket: str, object_key: str) -> None:
        del bucket, object_key
        raise RuntimeError("synthetic MinIO unavailable")


def cleanup_event(*, object_keys: object) -> IntegrationEvent:
    """生成已提交数据库永久删除事实的合成事件。"""

    return IntegrationEvent(
        event_id=UUID("70000000-0000-4000-8000-000000000101"),
        event_type=TRASH_PURGE_REQUESTED_EVENT,
        workspace_id=WORKSPACE_ID,
        aggregate_id=DOCUMENT_ID,
        aggregate_version=2,
        occurred_at=NOW,
        trace_id="a" * 32,
        traceparent=f"00-{'a' * 32}-{'b' * 16}-01",
        payload={
            "resource_type": "knowledge_document",
            "resource_id": str(DOCUMENT_ID),
            "external_object_keys": object_keys,
            "database_facts_purged": True,
            "external_cleanup_status": "pending",
            "purge_trigger": "manual",
            "requested_by": "synthetic-owner",
        },
    )


def test_cleanup_deletes_unique_workspace_objects() -> None:
    upload = f"workspaces/{WORKSPACE_ID}/uploads/source.txt"
    artifact = f"workspaces/{WORKSPACE_ID}/parsed/version/artifact.json"
    storage = RecordingStorage({upload, artifact})

    result = ObjectCleanupService(storage).handle(
        cleanup_event(object_keys=[upload, artifact, upload])
    )

    assert result.requested == 3
    assert result.deleted == 2
    assert storage.existing == set()
    assert storage.calls == [(WORKSPACE_ID, artifact), (WORKSPACE_ID, upload)]


def test_cleanup_accepts_empty_object_collection() -> None:
    storage = RecordingStorage()

    result = ObjectCleanupService(storage).handle(cleanup_event(object_keys=[]))

    assert result.requested == result.deleted == 0
    assert storage.calls == []


@pytest.mark.parametrize(
    "object_keys",
    [
        ["workspaces/10000000-0000-4000-8000-000000000999/uploads/other.txt"],
        [f"workspaces/{WORKSPACE_ID}/uploads/../other.txt"],
        [f"workspaces/{WORKSPACE_ID}/uploads\\other.txt"],
        [f"workspaces/{WORKSPACE_ID}/uploads/"],
        [123],
        "not-a-list",
    ],
)
def test_cleanup_rejects_cross_workspace_and_invalid_payload(object_keys: object) -> None:
    storage = RecordingStorage()

    with pytest.raises(InvalidObjectCleanupEventError):
        ObjectCleanupService(storage).handle(cleanup_event(object_keys=object_keys))

    assert storage.calls == []


def test_cleanup_can_replay_same_event_without_restoring_objects() -> None:
    object_key = f"workspaces/{WORKSPACE_ID}/uploads/replay.txt"
    storage = RecordingStorage({object_key})
    service = ObjectCleanupService(storage)
    event = cleanup_event(object_keys=[object_key])

    first = service.handle(event)
    second = service.handle(event)

    assert first.deleted == second.deleted == 1
    assert storage.existing == set()
    assert storage.calls == [(WORKSPACE_ID, object_key), (WORKSPACE_ID, object_key)]


def test_storage_failure_maps_to_retryable_error_and_task_has_finite_retries() -> None:
    object_key = f"workspaces/{WORKSPACE_ID}/uploads/unavailable.txt"
    with pytest.raises(ObjectCleanupUnavailableError):
        ObjectCleanupService(UnavailableStorage()).handle(cleanup_event(object_keys=[object_key]))

    assert consume_integration_event.max_retries == 3
    assert consume_integration_event.retry_backoff == 5


def test_minio_adapter_hides_provider_exception() -> None:
    storage = MinioObjectCleanupStorage.__new__(MinioObjectCleanupStorage)
    storage._client = cast(Any, FailingMinioClient())
    storage._bucket = "synthetic-bucket"

    with pytest.raises(ObjectCleanupUnavailableError):
        storage.delete(
            workspace_id=WORKSPACE_ID,
            object_key=f"workspaces/{WORKSPACE_ID}/uploads/unavailable.txt",
        )


def test_minio_adapter_rechecks_workspace_prefix() -> None:
    storage = MinioObjectCleanupStorage.__new__(MinioObjectCleanupStorage)

    with pytest.raises(InvalidObjectCleanupEventError):
        storage.delete(
            workspace_id=WORKSPACE_ID,
            object_key="workspaces/10000000-0000-4000-8000-000000000999/uploads/other.txt",
        )
