"""生成 API 与 Worker 共用的知识对象存储键。"""

from __future__ import annotations

from uuid import UUID


def parsed_artifact_object_key(
    workspace_id: UUID,
    document_version_id: UUID,
    ingestion_job_id: UUID,
) -> str:
    """生成确定性解析产物键，使失租补偿与永久删除指向同一对象。"""

    return f"workspaces/{workspace_id}/parsed/{document_version_id}/{ingestion_job_id}.json"
