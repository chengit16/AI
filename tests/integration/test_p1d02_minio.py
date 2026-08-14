from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
from ai_platform_api.modules.knowledge.domain.uploads import (
    InspectedUpload,
    InvalidUploadError,
    ScanResult,
    WorkspaceObject,
)
from ai_platform_api.modules.knowledge.infrastructure.object_storage import MinioObjectStorage

WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000202")


def storage() -> MinioObjectStorage:
    return MinioObjectStorage(
        endpoint=os.environ.get("AI_PLATFORM_TEST_MINIO_ENDPOINT", "http://127.0.0.1:9000"),
        access_key=os.environ.get("AI_PLATFORM_TEST_MINIO_ACCESS_KEY", "ai-platform-local"),
        secret_key=os.environ.get("AI_PLATFORM_TEST_MINIO_SECRET_KEY", "local-development-only"),
        bucket=os.environ.get("AI_PLATFORM_TEST_MINIO_BUCKET", "ai-platform-test-documents"),
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
    finally:
        adapter.delete(location)


def test_minio_adapter_rejects_cross_workspace_object_key_before_network() -> None:
    adapter = storage()
    location = WorkspaceObject(WORKSPACE_ID, "workspaces/other/uploads/forged.txt")
    upload = InspectedUpload("forged.txt", "text/plain", 1, "a" * 64, b"a")

    with pytest.raises(InvalidUploadError):
        adapter.put(location, upload, ScanResult("clean", "synthetic-scanner-v1"))
