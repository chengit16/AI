"""验证 P1D-02 MinIO 工作空间对象隔离和受控读写。"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from ai_platform_api.modules.knowledge.domain.uploads import (
    InspectedUpload,
    InvalidUploadError,
    ObjectStorageUnavailableError,
    ScanResult,
    WorkspaceObject,
)
from ai_platform_api.modules.knowledge.infrastructure.object_storage import MinioObjectStorage
from ai_platform_backend.integration.domain import IntegrationEvent
from ai_platform_worker.modules.knowledge.application.object_cleanup import ObjectCleanupService
from ai_platform_worker.modules.knowledge.domain.object_cleanup import TRASH_PURGE_REQUESTED_EVENT
from ai_platform_worker.modules.knowledge.infrastructure.object_storage import (
    MinioObjectCleanupStorage,
)

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000202")


def storage() -> MinioObjectStorage:
    return MinioObjectStorage(
        endpoint=os.environ.get("AI_PLATFORM_TEST_MINIO_ENDPOINT", "http://127.0.0.1:9000"),
        access_key=os.environ.get("AI_PLATFORM_TEST_MINIO_ACCESS_KEY", "ai-platform-local"),
        secret_key=os.environ.get("AI_PLATFORM_TEST_MINIO_SECRET_KEY", "local-development-only"),
        bucket=os.environ.get("AI_PLATFORM_TEST_MINIO_BUCKET", "ai-platform-test-documents"),
    )


def cleanup_storage() -> MinioObjectCleanupStorage:
    """使用与上传 Adapter 相同的合成 MinIO Bucket。"""

    return MinioObjectCleanupStorage(
        endpoint=os.environ.get("AI_PLATFORM_TEST_MINIO_ENDPOINT", "http://127.0.0.1:9000"),
        access_key=os.environ.get("AI_PLATFORM_TEST_MINIO_ACCESS_KEY", "ai-platform-local"),
        secret_key=os.environ.get("AI_PLATFORM_TEST_MINIO_SECRET_KEY", "local-development-only"),
        bucket=os.environ.get("AI_PLATFORM_TEST_MINIO_BUCKET", "ai-platform-test-documents"),
    )


def cleanup_event(location: WorkspaceObject) -> IntegrationEvent:
    """生成数据库永久删除已经提交的合成清理事件。"""

    document_id = uuid4()
    trace_id = uuid4().hex
    return IntegrationEvent(
        event_id=uuid4(),
        event_type=TRASH_PURGE_REQUESTED_EVENT,
        workspace_id=location.workspace_id,
        aggregate_id=document_id,
        aggregate_version=2,
        occurred_at=datetime.now(UTC),
        trace_id=trace_id,
        traceparent=f"00-{trace_id}-{'0' * 16}-01",
        payload={
            "resource_type": "knowledge_document",
            "resource_id": str(document_id),
            "external_object_keys": [location.object_key],
            "database_facts_purged": True,
            "external_cleanup_status": "pending",
            "purge_trigger": "manual",
            "requested_by": "synthetic-integration-test",
        },
    )


def test_minio_adapter_roundtrip_and_delete() -> None:
    adapter = storage()
    location = WorkspaceObject(
        WORKSPACE_ID,
        f"workspaces/{WORKSPACE_ID}/uploads/{uuid4().hex}.txt",
    )
    content = "仅用于对象存储集成验收".encode()
    upload = InspectedUpload("synthetic.txt", "text/plain", len(content), "a" * 64, content)
    scan = ScanResult("clean", "synthetic-scanner-v1")

    try:
        adapter.put(location, upload, scan)
        assert adapter.get(location) == content
        cleanup = ObjectCleanupService(cleanup_storage())
        event = cleanup_event(location)
        assert cleanup.handle(event).deleted == 1
        assert cleanup.handle(event).deleted == 1
        with pytest.raises(ObjectStorageUnavailableError):
            adapter.get(location)
    finally:
        adapter.delete(location)


def test_minio_adapter_rejects_cross_workspace_object_key_before_network() -> None:
    adapter = storage()
    location = WorkspaceObject(WORKSPACE_ID, "workspaces/other/uploads/forged.txt")
    upload = InspectedUpload("forged.txt", "text/plain", 1, "a" * 64, b"a")

    with pytest.raises(InvalidUploadError):
        adapter.put(location, upload, ScanResult("clean", "synthetic-scanner-v1"))
